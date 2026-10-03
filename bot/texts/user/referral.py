"""Domain texts for user/referral.py."""
from __future__ import annotations

def mask_telegram_id(telegram_id: int | str) -> str:
    """Mask Telegram ID showing stars in the middle, e.g. 8141287721 -> 814***21."""
    s = str(telegram_id).strip()
    if len(s) <= 4:
        return f"{s[:1]}***{s[-1:]}" if len(s) > 1 else "***"
    return f"{s[:3]}***{s[-2:]}"


BTN_REFERRAL_LEADERBOARD = "🏆 Топ-5 лидеров"

REFERRAL_DISCOUNT_BADGE = "\n🎁 Скидка 25% по пригласительной ссылке: <b>-{discount} ₽</b> (вместо <s>{original_price} ₽</s>)\n<i>Скидка действует на первый заказ тарифа. Пополнение баланса на неё не влияет. Продление будет по обычной цене ({original_price} ₽).</i>"

REFERRAL_LEADERBOARD_EMPTY = """<i>В топе пока пусто.

Пригласите пользователей по ссылке, чтобы занять первое место.</i>"""

REFERRAL_LEADERBOARD_ITEM = "{pos}. {medal}{user} — <b>{count}</b> {noun}"

REFERRAL_LEADERBOARD_MEDALS: dict[int, str] = {1: "🥇 ", 2: "🥈 ", 3: "🥉 "}

REFERRAL_LEADERBOARD_NOT_RANKED = "\n➖➖➖➖➖➖➖➖\nВаше место: <b>вне рейтинга</b> (нет активных приглашённых)"

REFERRAL_LEADERBOARD_TITLE = "🏆 <b>Топ-5 лидеров</b>\n"

REFERRAL_LEADERBOARD_USER_OTHER = "<code>{masked}</code>"

REFERRAL_LEADERBOARD_USER_YOU = "<b>{masked}</b> (Вы)"

REFERRAL_LEADERBOARD_YOUR_RANK = "\n➖➖➖➖➖➖➖➖\nВаше место: <b>#{rank}</b> ({count} {noun})"

REFERRAL_LIST_EMPTY = """<i>Список рефералов пока пуст.</i>

Пригласите пользователей по вашей ссылке, чтобы они появились здесь."""

REFERRAL_LIST_FOOTER = """
Всего приглашено: {count}"""

REFERRAL_LIST_HEADER = """👥 <b>Ваши рефералы</b>
"""

REFERRAL_LIST_ITEM_FORMAT = "\n{idx}. <b>{user}</b> ({date})"

REFERRAL_MAIN_RANK_NOT_RANKED = "\n🏆 Место в рейтинге: <b>вне рейтинга</b>"

REFERRAL_MAIN_RANK_RANKED = "\n🏆 Место в рейтинге: <b>#{rank}</b>"

REFERRAL_SHARE_TEXT = "🎁 Приглашаю в just1kbot! Получи скидку 25% на первый тариф по моей ссылке:"

REFERRAL_TEXT_BALANCE = """🤝 <b>Реферальная программа</b>

💰 Бонусный баланс: <b>{bonus_balance} ₽</b>
🎖 Ваш статус: <b>{tier_name} ({rate_pct}%)</b>{rank_line}
👥 Активных приглашённых: <b>{active_count}</b> (всего приглашено: {invited_count}){tier_progress_line}

🎁 <b>Условия программы:</b>
• Вы получаете <b>от 15% до 30%</b> с каждой оплаты приглашённых пользователей на бонусный баланс.
• <b>Лестница статусов:</b>
  ▫️ 0–2 приглашённых — <b>15% (Standard)</b>
  ▫️ 3–6 приглашённых — <b>20% (Silver)</b>
  ▫️ 7–14 приглашённых — <b>25% (Gold)</b>
  ▫️ 15+ приглашённых — <b>30% (Platinum)</b>
• Приглашённый получает <b>скидку 25%</b> на первый тариф по вашей ссылке (пополнение баланса на скидку не влияет).

<i>Бонусы списываются при оплате и продлении подписки.</i>

🔗 <b>Ваша ссылка для приглашения:</b>
<code>{referral_link}</code>{inviter_line}"""
REFERRAL_TIER_PROGRESS_NEXT = "\nДо статуса <b>{next_tier_name} ({next_rate_pct}%)</b>: ещё {needed_count}"

REFERRAL_TIER_PROGRESS_MAX = "\n🏆 <b>Максимальный статус</b>"

