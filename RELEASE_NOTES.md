# Release Notes — just1kbot v2.5.0

## Что изменилось

### Интерфейс и уведомления
- Добавили кнопки закрытия (`dismiss_notification`) в клавиатуры реферальных бонусов, онбординга и предупреждений о трафике (`bot/keyboards/notifications.py`).
- Добавили кнопку закрытия системного алерта (`admin_dismiss_alert:general`) при падении фоновых воркеров (`services/workers/__init__.py`).
- Исправили счетчик устройств на Главном экране (`bot/handlers/start.py`): теперь он суммирует AWG-профили и активные привязки VLESS HWID, а не показывает ноль при подключении по VLESS.
- Переименовали константу кнопки удаления устройства в `BTN_CONFIRM_DELETE = "🗑 Да, удалить"` (`bot/texts/connection/devices.py`), старый алиас сохранили для совместимости.
- Привели нативные стили кнопок к стандарту Telegram Bot API (`success`, `primary`, `danger`), заменив неподдерживаемый стиль `warning` на кнопках сброса привязок устройств (`bot/handlers/connection/common.py`, `bot/handlers/white_internet.py`).
- Реализовали прямой переход в поддержку по URL-ссылке с контекстным предзаполненным сообщением на экранах оплаты и блокировки финансовых споров без сброса карточки платежа (`bot/keyboards/payment.py`, `utils/support.py`).
- Ликвидировали глубокие навигационные тупики в разделах инструкций, FAQ и добавления устройств (`bot/handlers/support.py`, `bot/handlers/connection/device_create_routes.py`).
- Синхронизировали часовой пояс отображения окончания подписки Белого Интернета по московскому времени (`Europe/Moscow`).

### Среда и зависимости
- Обновили базовый стек библиотек Telegram до `aiogram 3.31.0` и `aiohttp 3.14.5`.
- Добавили тест валидации стилей кнопок Telegram Bot API (`tests/test_keyboard_styles.py`).
- Добавили `pythonpath = ["."]` в `pyproject.toml`, чтобы `pytest` находил модули бота без ручной настройки путей на Windows.
- Добавили fallback таймзоны на `MSK` (`UTC+3`) в `utils/datetime_helpers.py`, если в системе нет `tzdata`.
- Обновили пин `certifi==2026.10.18` с точными хэшами в `requirements.lock`, устранив падение проверки зависимостей на CI.
- Подняли версию бота до `2.5.0` в `pyproject.toml`.

## Инварианты
- Биллинг: транзакционные блокировки `pg_advisory_xact_lock` и строчные блокировки `SELECT ... FOR UPDATE`, баланс считается строго по журналу `account_ledger_entries`.
- Протоколы: AmneziaWG (ChaCha20 Nonce $\ge 12$ байт, подсеть `/22`), VLESS TLS с учетом HWID, Белый Интернет через XHTTP с каскадом Origin/Relay.
- Внешний контур: стоп-слова отсутствуют, тесты `test_user_text_audit.py` проходят без замечаний.
