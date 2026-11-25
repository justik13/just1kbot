export const ORDER_STATUS_MAP = {
    paid: { text: "Оплачен", cls: "chip-ok" },
    pending: { text: "Ожидает", cls: "chip-warn" },
    refunded: { text: "Возврат", cls: "chip-warn" },
    partially_refunded: { text: "Част. возврат", cls: "chip-warn" },
    canceled: { text: "Отменён", cls: "chip-muted" },
};

export const ORDER_KIND_MAP = {
    standard: "Стандарт",
    white_internet: "Белый интернет",
    topup: "Пополнение",
};

export const METADATA_FLAG_MAP = {
    requires_manual_review: { text: "Ручная проверка", cls: "chip-err" },
    settlement_held: { text: "Холд", cls: "chip-warn" },
    payment_amount_mismatch: { text: "Расхождение суммы оплаты", cls: "chip-err" },
    refund_amount_mismatch: { text: "Расхождение суммы возврата", cls: "chip-err" },
    payment_id_mismatch: { text: "Несовпадение ID платежа", cls: "chip-err" },
    refund_payment_id_mismatch: { text: "Несовпадение ID возврата", cls: "chip-err" },
    late_payment_after_refund_on_canceled: { text: "Оплата после отмены/возврата", cls: "chip-err" },
    underpaid: { text: "Недоплата", cls: "chip-err" },
    mixed_fallback_credited: { text: "Возврат на баланс (fallback)", cls: "chip-warn" },
    fulfillment_error: { text: "Ошибка выдачи", cls: "chip-err" },
    negative_balance_rub: { text: "Отрицательный баланс", cls: "chip-err" },
    partial_refund_amount_rub: { text: "Частичный возврат", cls: "chip-warn" },
};

export const AUDIT_ACTION_MAP = {
    ACCOUNT_PURCHASE_SETTLED: "Оплата подписки",
    ACCOUNT_TARIFF_CHANGE_SETTLED: "Смена тарифа",
    ADD_SERVER: "Добавление ноды",
    ADMIN_2FA_LOCKED: "Блокировка 2FA",
    ADMIN_2FA_LOGOUT_ALL: "Сброс 2FA-сессий",
    ADMIN_2FA_VERIFY_FAILED: "Неудачная 2FA",
    ADMIN_BALANCE_DEDUCT: "Списание баланса",
    ADMIN_BALANCE_TOPUP: "Пополнение баланса",
    ADMIN_DIRECT_MESSAGE_SENT: "Сообщение пользователю",
    ADMIN_SUB_CHANGE: "Изменение подписки",
    ADMIN_SUB_EXTEND: "Продление подписки",
    ADMIN_SUB_GRANT: "Выдача подписки",
    ADMIN_SUB_REDUCE: "Сокращение подписки",
    BALANCE_REFUND_REQUESTED: "Запрос возврата",
    BROADCAST: "Запуск рассылки",
    BROADCAST_COMPLETED: "Рассылка завершена",
    CHANGE_TARIFF: "Смена тарифа",
    CLEANUP_DEVICE_DELETE: "Очистка устройства",
    DEDUCT_USER_BALANCE: "Списание баланса",
    DELETE_SERVER: "Удаление ноды",
    EDIT_SERVER: "Редактирование ноды",
    EXTEND: "Продление",
    GRANT: "Выдача доступа",
    INCY_SETTINGS_UPDATED: "Настройки INCY",
    MANUAL_GRANT: "Ручная выдача",
    MASS_BONUS_GRANTED: "Массовый бонус",
    MASS_SUB_EXTEND: "Массовое продление",
    PAID_AFTER_CANCEL: "Оплата после отмены",
    PAYMENT_CANCELLED: "Платёж отменён",
    PAYMENT_CANCEL_AFTER_COMPLETED: "Отмена после оплаты",
    PAYMENT_CHARGEBACK: "Чарджбэк",
    PAYMENT_DISPUTE_MANUAL_REVIEW: "Спор: ручная проверка",
    PAYMENT_DISPUTE_OPENED: "Спор открыт",
    PAYMENT_DISPUTE_RESOLVED: "Спор закрыт",
    PAYMENT_QUEUE_MANUAL_RETRY: "Повтор очереди платежа",
    PAYMENT_SUCCESS: "Успешная оплата",
    REDUCE: "Сокращение срока",
    REFERRAL_ATTACHED: "Привязка реферала",
    REFERRAL_BONUS_GRANTED: "Реферальный бонус",
    SERVER_FORCE_PURGE: "Принудительное удаление ноды",
    TOGGLE_MAINTENANCE: "Техработы платежей",
    TOGGLE_SERVER: "Переключение ноды",
    USER_REGISTER: "Регистрация",
    USER_RESTORED: "Восстановление пользователя",
    VLESS_HWID_RESET: "Сброс устройств (Стандарт)",
    VLESS_TOKEN_RESET: "Ротация токена",
    WELCOME_BONUS_GRANTED: "Приветственный бонус",
    WHITE_INTERNET_DEVLIMIT_SET: "Лимит устройств БИ",
    WHITE_INTERNET_EXTEND: "Продление БИ",
    WHITE_INTERNET_HWID_RESET: "Сброс устройств БИ",
    WHITE_INTERNET_QUOTA_SET: "Квота трафика БИ",
    WHITE_INTERNET_TOKEN_RESET: "Ротация токена БИ",
    WHITE_INTERNET_TRAFFIC_ADDED: "Добавлен трафик БИ",
    WHITE_INTERNET_TRAFFIC_RESET: "Сброс трафика БИ",
};

export function formatMetadataFlag(flag) {
    return METADATA_FLAG_MAP[flag] || { text: String(flag || ""), cls: "chip-warn" };
}

export function formatAuditAction(action) {
    const key = String(action || "").toUpperCase();
    return AUDIT_ACTION_MAP[key] || action || "—";
}
