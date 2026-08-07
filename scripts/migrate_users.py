#!/usr/bin/env python3
"""
scripts/migrate_users.py — Перенос пользователей из awg-tgbot (SQLite) в just1kbot (PostgreSQL)

Логика:
  - Берёт 35 пользователей из vpn_bot.db
  - Привязывает активных/компенсированных пользователей к тарифу "Базовый" 30 дней
  - Продлевает активные подписки на +7 дней бонуса
  - Для истекших до 02.08 выдаёт 7 дней компенсации
  - Пропускает дубликаты (админа)

Запуск внутри Docker-контейнера:
  docker compose exec bot python scripts/migrate_users.py --sqlite /tmp/vpn_bot.db --pg "$DATABASE_URL"
"""

import argparse
import asyncio
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

CUTOFF_DATE       = datetime(2026, 8, 2, tzinfo=timezone.utc)
COMPENSATION_DAYS = 7
BONUS_DAYS        = 7


def parse_sub_until(val: str | None) -> datetime | None:
    if not val or val == "0":
        return None
    try:
        dt = datetime.fromisoformat(val)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return None


def compute_subscription_end(sub_raw: str | None, now: datetime) -> datetime | None:
    sub_until = parse_sub_until(sub_raw)
    if sub_until is None:
        return None
    if sub_until <= now:
        return (now + timedelta(days=COMPENSATION_DAYS)) if sub_until < CUTOFF_DATE else None
    return sub_until + timedelta(days=BONUS_DAYS)


async def find_base_tariff(conn) -> int:
    row = await conn.fetchrow(
        "SELECT id, name, duration_days, price_rub FROM tariffs WHERE name = $1 AND duration_days = $2",
        "Базовый", 30
    )
    if not row:
        row = await conn.fetchrow(
            "SELECT id, name, duration_days, price_rub FROM tariffs WHERE is_active = true ORDER BY sort_order LIMIT 1"
        )
    if not row:
        print("[ERROR] Тарифы не найдены в БД. Запустите бота хотя бы один раз.")
        sys.exit(1)
    print(f"[INFO] Тариф найден: id={row['id']}, name='{row['name']}' ({row['duration_days']}д, {row['price_rub']}руб)")
    return row['id']


async def run_migration_async(sqlite_path: str, pg_dsn: str, dry_run: bool = False):
    try:
        import asyncpg
    except ImportError:
        print("[ERROR] Библиотека asyncpg не найдена.")
        sys.exit(1)

    now = datetime.now(timezone.utc)
    print(f"[INFO] Время запуска: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"[INFO] Режим dry-run: {dry_run}\n")

    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT user_id, tg_username, first_name, sub_until, created_at FROM users ORDER BY user_id")
    old_users = cur.fetchall()
    conn.close()
    print(f"[INFO] Пользователей в бэкапе: {len(old_users)}\n")

    rows = []
    stats = dict(with_sub=0, no_sub=0)

    for row in old_users:
        d          = dict(row)
        tg_id      = d["user_id"]
        username   = d["tg_username"]
        first_name = d["first_name"]
        sub_raw    = d["sub_until"]

        try:
            created_at = datetime.fromisoformat(d["created_at"])
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
        except Exception:
            created_at = now

        new_end = compute_subscription_end(sub_raw, now)
        has_sub = new_end is not None

        if has_sub:
            stats["with_sub"] += 1
            end_str = new_end.strftime("%Y-%m-%d")
            label   = "С ПОДПИСКОЙ"
        else:
            stats["no_sub"] += 1
            end_str = "NULL"
            label   = "БЕЗ ПОДПИСКИ"

        print(f"  [{label:13}] tg={tg_id:13} @{str(username or ''):22} sub_end={end_str}")
        rows.append((tg_id, username, first_name, new_end, created_at, has_sub))

    print("\n" + "=" * 60)
    print(f"  С подпиской:    {stats['with_sub']} чел. -> тариф Базовый 30д")
    print(f"  Без подписки:   {stats['no_sub']} чел. -> NULL")
    print(f"  Итого:          {len(rows)}")
    print("=" * 60 + "\n")

    if dry_run:
        print("[DRY RUN] Данные НЕ записаны в PostgreSQL.")
        return

    # Подготовка DSN для asyncpg внутри Docker сети
    clean_dsn = pg_dsn
    for pref in ("postgresql+asyncpg://", "postgresql+psycopg2://", "postgresql://"):
        if clean_dsn.startswith(pref):
            clean_dsn = "postgres://" + clean_dsn[len(pref):]
            break

    # Замена хоста на db (контейнер PostgreSQL в docker compose)
    clean_dsn = clean_dsn.replace("@localhost:", "@db:").replace("@127.0.0.1:", "@db:")

    pg_conn = await asyncpg.connect(clean_dsn)
    try:
        async with pg_conn.transaction():
            tariff_id = await find_base_tariff(pg_conn)
            existing_rows = await pg_conn.fetch("SELECT telegram_id FROM users")
            existing = {r["telegram_id"] for r in existing_rows}
            if existing:
                print(f"[INFO] Уже есть в БД (пропускаем): {len(existing)} чел.\n")

            inserted = skipped = 0
            for rec in rows:
                tg_id, username, first_name, new_end, created_at, has_sub = rec
                if tg_id in existing:
                    print(f"  [SKIP] tg={tg_id} (@{username}) — уже существует")
                    skipped += 1
                    continue

                await pg_conn.execute(
                    """
                    INSERT INTO users (
                        telegram_id, username, first_name,
                        subscription_end, device_limit, current_tariff_id,
                        created_at,
                        is_banned, is_bot_blocked, is_deleted,
                        notification_retry_count,
                        notified_3d, notified_1d, notified_2h,
                        notified_expired, notified_grace_12h,
                        device_creations_today
                    ) VALUES (
                        $1, $2, $3,
                        $4, $5, $6,
                        $7,
                        false, false, false,
                        0, false, false, false, false, false, 0
                    )
                    """,
                    tg_id, username, first_name,
                    new_end,
                    2 if has_sub else 0,
                    tariff_id if has_sub else None,
                    created_at,
                )
                inserted += 1

        print(f"\n[OK] Успешно занесено: {inserted} чел., пропущено (дубли): {skipped} чел.")
    finally:
        await pg_conn.close()


def main():
    parser = argparse.ArgumentParser(description="Миграция пользователей из awg-tgbot SQLite в just1kbot PostgreSQL")
    parser.add_argument("--sqlite", required=True, help="Путь к vpn_bot.db")
    parser.add_argument("--pg", required=True, help="DSN базы данных PostgreSQL")
    parser.add_argument("--dry-run", action="store_true", help="Запуск без внесения изменений")
    args = parser.parse_args()

    asyncio.run(run_migration_async(args.sqlite, args.pg, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
