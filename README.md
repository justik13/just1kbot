Telegram-бот для продажи VPN-доступа на базе **AmneziaWG 2.0**.

Пользователь покупает подписку → создаёт устройства → получает конфиги для подключения.
Админ управляет серверами, тарифами, пользователями и платежами прямо из бота.

---

## Возможности

### Для пользователя

- **Подписка** — покупка, продление, смена тарифа через YooKassa
- **Устройства** — создание VPN-профилей на выбранном сервере, переименование, удаление
- **Конфиги** — выдача `.vpn` (AmneziaVPN) и `.conf` (AmneziaWG) файлов + ключ текстом
- **Реферальная программа** — бонусные дни за приглашённых друзей
- **Профиль** — статистика, история оплат, список рефералов
- **Поддержка** — FAQ, условия сервиса, политика конфиденциальности
- **Уведомления** — напоминания за 3 дня / 1 день / 2 часа до истечения, grace-период 48 ч

### Для админа

- **Дашборд** — статистика: пользователи, подписки, свободные IP
- **Пользователи** — поиск, карточка, бан/разбан, управление подпиской (продление, уменьшение, смена тарифа, выдача доступа), удаление устройств
- **Серверы** — добавление, редактирование (имя, флаг, URL, ключ, лимит), вкл/выкл, удаление с очисткой пиров
- **Тарифы** — изменение цены, вкл/выкл на витрине
- **Платежи** — список, карточка, ручная выдача подписки
- **Рассылка** — всем / только активным, с медиа, с возобновлением после рестарта
- **Аудит-лог** — последние действия администраторов
- **Техработы** — ограничение действий для пользователей без остановки сервиса

---

## Стек

| Компонент | Технология |
|---|---|
| Язык | Python 3.11+ |
| Бот | aiogram 3 |
| ORM | SQLAlchemy 2 (async) |
| БД | PostgreSQL (asyncpg) |
| Кэш / FSM | Redis |
| Платежи | YooKassa (aioyookassa) |
| VPN API | [kyoresuas/amnezia-api](https://github.com/kyoresuas/amnezia-api) |
| Шифрование | cryptography (Fernet) |
| HTTP | aiohttp |

---

## Структура проекта

```
├── bot/
│   ├── handlers/           # Роутеры aiogram
│   │   ├── admin/          #   Админка (dashboard, users, servers, tariffs, payments, broadcast)
│   │   ├── connection/     #   Устройства пользователя
│   │   ├── payment/        #   Витрина, YooKassa
│   │   ├── start.py        #   /start, главное меню
│   │   ├── profile.py      #   Профиль, рефералы, история
│   │   ├── support.py      #   Поддержка, FAQ
│   │   ├── fallback.py     #   Обработка неизвестных сообщений
│   │   └── webhook.py      #   YooKassa webhook + /health
│   ├── keyboards/          # Inline-клавиатуры
│   ├── middlewares/        # Throttling, ban-check, DB-session, user-context, clean-chat, action-lock
│   ├── texts_data/         # Все тексты бота (user / admin / overrides)
│   ├── states.py           # FSM-состояния
│   ├── constants.py        # Константы
│   └── main.py             # Точка входа
├── config/
│   └── settings.py         # pydantic-settings (.env)
├── database/
│   ├── connection.py       # Engine, session_scope, сиды, индексы
│   ├── models.py           # SQLAlchemy-модели
│   └── repositories/       # CRUD-функции
├── services/
│   ├── amnezia_client.py   # Клиент Amnezia API (circuit breaker, rate limiter)
│   ├── payment_service/    # Платежи: создание, webhook, chargeback, manual review
│   ├── subscription.py     # Подписка: onboarding, продление, sync доступа
│   ├── device_service.py   # Создание/удаление устройств (Redis-локи, daily limit)
│   ├── ban_service.py      # Бан с удалением устройств
│   ├── referral_service.py # Реферальные бонусы
│   ├── maintenance_service.py  # Режим техработ
│   ├── profile_deletion_service.py  # Фоновое удаление пиров
│   ├── slots_cache.py      # Кэш свободных слотов на серверах
│   ├── yookassa_service.py # Обёртка над aioyookassa
│   └── workers/            # Фоновые воркеры
│       ├── traffic.py      #   Синхронизация трафика + self-healing
│       ├── notifications.py#   Уведомления о подписке
│       ├── cleanup.py      #   Очистка dangling peers, grace-период
│       ├── payments.py     #   Проверка зависших платежей
│       └── heartbeat.py    #   Heartbeat для healthcheck
├── utils/
│   ├── encryption.py       # EncryptedString (Fernet) для SQLAlchemy
│   ├── vpn_parser.py       # Декодирование vpn:// URI, генерация .conf
│   ├── security.py         # SSRF-защита, SafeResolver
│   ├── datetime_helpers.py # UTC / MSK хелперы
│   └── ...
├── deploy.sh               # Полный деплой на чистый сервер
├── setup-amnezia-api.sh    # Настройка HTTPS для Amnezia API
├── uninstall.sh            # Деинсталляция
└── requirements.txt
```

---

## Требования

- Ubuntu / Debian
- Python 3.11+
- PostgreSQL 14+
- Redis 7+
- Работающий [Amnezia API](https://github.com/kyoresuas/amnezia-api) с протоколом `amneziawg2`
- (Опционально) YooKassa-аккаунт для приёма платежей

---

## Быстрый старт

### 1. Клонирование и зависимости

```bash
git clone <repo-url> && cd projectx-main
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Переменные окружения

Создай `.env` в корне проекта:

```env
# Telegram
BOT_TOKEN=123456:ABC-DEF...
ADMIN_IDS=[123456789]
SUPPORT_USERNAME=your_support

# Database
DATABASE_URL=postgresql+asyncpg://just1kbot:password@localhost:5432/just1kbot_bot
DB_ENCRYPTION_KEY=<base64url-ключ-32-байта>

# Redis
REDIS_URL=redis://:password@localhost:6379/0
REDIS_PASSWORD=password

# YooKassa (опционально — без неё платежи не работают)
YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
YOOKASSA_RETURN_URL=https://t.me/{bot_username}
YOOKASSA_WEBHOOK_PORT=8080

# Безопасность
ALLOW_LOCAL_HTTP=false
ALLOW_LOCAL_HTTPS=false
```

Генерация `DB_ENCRYPTION_KEY`:

```bash
python3 -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

> ⚠️ **Не теряй этот ключ.** Им зашифрованы API-ключи серверов и конфиги пользователей в БД. Смена ключа без перешифровки = потеря данных.

### 3. Запуск

```bash
python -m bot.main
```

При первом старте автоматически:
- Создаются таблицы
- Сидятся тарифы по умолчанию (Базовый / Семейный / Pro)
- Создаётся запись режима техработ

---

## Деплой на продакшн

Полный автоматический деплой на чистый сервер (Ubuntu/Debian):

```bash
sudo bash deploy.sh
```

Скрипт делает:
1. Ставит зависимости (Python, PostgreSQL, Redis, Nginx, UFW)
2. Создаёт БД и пользователя PostgreSQL
3. Настраивает Redis с паролем
4. Настраивает UFW (SSH, HTTP, HTTPS; блокирует 8080, 6379 извне)
5. Синхронизирует код в `/opt/just1kbot-bot`
6. Создаёт venv и ставит pip-зависимости
7. Конфигурирует `.env` (интерактивно)
8. Инициализирует схему БД
9. Создаёт systemd-сервис с рестартом
10. (При YooKassa) Настраивает Nginx reverse proxy + SSL через certbot
11. Настраивает автобэкапы (cron, 03:00) и healthcheck (каждые 5 мин)

Управление после деплоя:

```bash
sudo bash deploy.sh --status     # Статус сервиса
sudo bash deploy.sh --logs       # Логи (journalctl -f)
sudo bash deploy.sh --restart    # Перезапуск
sudo bash deploy.sh --backup     # Ручной бэкап
sudo bash deploy.sh --restore <stamp>  # Восстановление
```

---

## Amnezia API

Бот работает **только** с протоколом `amneziawg2`.

Для настройки публичного HTTPS-доступа к API на сервере с VPN:

```bash
sudo bash setup-amnezia-api.sh --domain api.example.com --email admin@example.com
```

После этого добавь сервер в админке бота: **🛠 Админка → 🌍 Серверы → ➕ Добавить сервер**.

Бот проверит healthcheck, получит `server info`, убедится в наличии `amneziawg2` и сохранит сервер.

---

## Платежи (YooKassa)

- Webhook принимает события на `POST /webhook/yookassa` (только IP YooKassa)
- Healthcheck: `GET /health` (проверяет DB + Redis)
- Поддерживаются: `payment.succeeded`, `payment.canceled`, `refund.succeeded`
- Защита от stale webhook: проверка `created_at` + верификация через API
- Ручная проверка (`requires_manual_review`) при несовпадении суммы / валюты / payload
- Chargeback: отзыв доступа, удаление устройств, откат реферальных бонусов

---

## Фоновые воркеры

| Воркер | Интервал | Что делает |
|---|---|---|
| `traffic` | 15 мин | Синхронизация трафика из API, self-healing (вкл/выкл пиров), алерты при >1 TiB |
| `notifications` | 30 мин | Уведомления за 3д / 1д / 2ч до истечения + grace-уведомления |
| `cleanup` | 15 мин | Удаление dangling peers, grace-очистка (48 ч), обработка pending-удалений |
| `stale_payments` | 1 ч | Проверка зависших платежей через YooKassa API, алерты админам |
| `heartbeat` | 60 с | Запись heartbeat-файла, мониторинг circuit breaker'ов |

Все воркеры работают под supervisor'ом: при падении — автоматический рестарт с exponential backoff + алерт админам.

---

## Безопасность

- **Шифрование БД** — API-ключи серверов и VPN-конфиги хранятся зашифрованными (Fernet)
- **SSRF-защита** — `SafeResolver` блокирует приватные IP, loopback, metadata endpoints
- **Rate limiting** — throttling на сообщения и callback'и, token bucket для API и рассылок
- **Action lock** — защита от двойных нажатий на критичные кнопки
- **Ban middleware** — заблокированные пользователи не могут выполнять действия
- **Private chat only** — бот игнорирует группы и каналы
- **Callback валидация** — проверка на SQL/command injection в callback data
- **Секреты в логах** — санитизация traceback'ов, редатирование callback'ов и сообщений

---

## Тарифы по умолчанию

| Тариф | Устройства | 7 дн. | 30 дн. | 90 дн. |
|---|---|---|---|---|
| 📱 Базовый | 2 | 35 ₽ | 90 ₽ | 240 ₽ |
| 👨‍👩‍👧‍👦 Семейный | 5 | — | 180 ₽ | 480 ₽ |
| 🚀 Pro | 10 | — | 320 ₽ | 850 ₽ |

Цены меняются в админке. Структура тарифов (дни / лимиты) захардкожена в `database/connection.py`.
