# Release Notes — just1kbot v2.5.0

## Обзор релиза / Overview
Официальный релиз контроллера Telegram-бота **just1kbot** версии **v2.5.0** в рамках комплексной стабилизации платформы just1k.

## Изменения в версии v2.5.0 / Changelog

### Keyboards & UX Polish
- **Закрываемые уведомления (Чистый чат):**
  - В `bot/keyboards/notifications.py` добавлены кнопки закрытия (`BTN_DISMISS_NOTIFICATION` / `dismiss_notification`) в клавиатуры `get_referral_bonus_keyboard`, `get_referral_onboarding_keyboard` и `get_traffic_alert_keyboard`.
  - В `services/workers/__init__.py` метод `_send_alert` при аварийном падении воркеров оснащен инлайн-клавиатурой с кнопкой закрытия алерта для администраторов (`admin_dismiss_alert:general`).
- **Рефакторинг констант устройств:**
  - В `bot/texts/connection/devices.py` константа подтверждения удаления переименована в семантически корректное `BTN_CONFIRM_DELETE = "🗑 Да, удалить"` с сохранением легаси-алиаса `DEVICE_STATUS_CREATING_LABEL` для полной обратной совместимости.
  - В `bot/keyboards/device.py` функция `get_device_delete_confirm_keyboard` переведена на использование `BTN_CONFIRM_DELETE`.

### Pytest & Build Configuration
- В `pyproject.toml` в секцию `[tool.pytest.ini_options]` добавлен параметр `pythonpath = ["."]`, что гарантирует предсказуемое обнаружение модулей при запуске `pytest` на Windows и Linux.

### Синхронизация версий
- Версия пакета в `pyproject.toml` обновлена до `2.5.0`.

---

## Архитектурные инварианты
- **Биллинг и фин. машина:** Обработка вебхуков ЮKassa с двухуровневой фильтрацией IP, SHA-256 хешированием в `webhook_inbox`, строчными блокировками заказов и динамическим расчетом баланса по append-only журналу `account_ledger_entries`.
- **Протоколы:** AmneziaWG (AWG 2.0–3.1, ChaCha20 $S1..S4 \ge 12$, `vpn://` URI), Direct VLESS TLS (`/sub/access/{token}`, квотирование HWID), White Internet (2-Hop каскад Origin/Relay, Bodiless GET Uplink, Nginx Zero Buffering, Epoch rotation).
- **Юридический комплаенс:** 100% отсутствие запрещенных стоп-слов во внешнем контуре (`test_user_text_audit.py`).
