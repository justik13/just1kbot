# Release Notes — just1kbot v2.5.0

## Что изменилось

### Интерфейс и уведомления
- Добавили кнопки закрытия (`dismiss_notification`) в клавиатуры реферальных бонусов, онбординга и предупреждений о трафике (`bot/keyboards/notifications.py`).
- Добавили кнопку закрытия системного алерта (`admin_dismiss_alert:general`) при падении фоновых воркеров (`services/workers/__init__.py`).
- Исправили счетчик устройств на Главном экране (`bot/handlers/start.py`): теперь он суммирует AWG-профили и активные привязки VLESS HWID, а не показывает ноль при подключении по VLESS.
- Переименовали константу кнопки удаления устройства в `BTN_CONFIRM_DELETE = "🗑 Да, удалить"` (`bot/texts/connection/devices.py`), старый алиас сохранили для совместимости.

### Среда и зависимости
- Добавили `pythonpath = ["."]` в `pyproject.toml`, чтобы `pytest` находил модули бота без ручной настройки путей на Windows.
- Добавили fallback таймзоны на `MSK` (`UTC+3`) в `utils/datetime_helpers.py`, если в системе нет `tzdata`.
- Обновили пин `certifi==2026.10.18` с точными хэшами в `requirements.lock`, устранив падение проверки зависимостей на CI.
- Подняли версию бота до `2.5.0` в `pyproject.toml`.

## Инварианты
- Биллинг: транзакционные блокировки `pg_advisory_xact_lock` и строчные блокировки `SELECT ... FOR UPDATE`, баланс считается строго по журналу `account_ledger_entries`.
- Протоколы: AmneziaWG (ChaCha20 Nonce $\ge 12$ байт, подсеть `/22`), VLESS TLS с учетом HWID, Белый Интернет через XHTTP с каскадом Origin/Relay.
- Внешний контур: стоп-слова отсутствуют, тесты `test_user_text_audit.py` проходят без замечаний.
