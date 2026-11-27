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

export const LEDGER_ENTRY_TYPE_MAP = {
    payment_credit: "Зачисление",
    purchase_debit: "Оплата подписки",
    refund_debit: "Возврат",
    admin_adjustment: "Коррекция админом",
    debt_forgiveness: "Погашение долга",
};

export function formatLedgerEntryType(entryType) {
    return LEDGER_ENTRY_TYPE_MAP[entryType] || entryType || "Операция";
}

export function formatMetadataFlag(flag) {
    return METADATA_FLAG_MAP[flag] || { text: String(flag || ""), cls: "chip-warn" };
}

export function formatAuditAction(action) {
    const key = String(action || "").toUpperCase();
    return AUDIT_ACTION_MAP[key] || action || "—";
}

export const AUDIT_TARGET_TYPE_MAP = {
    user: "Пользователь",
    server: "Нода",
    broadcast: "Рассылка",
    system_setting: "Настройки",
    system: "Система",
    admin: "Админ",
    mass_bonus: "Массовый бонус",
    mass_extend: "Массовое продление",
    webhook_inbox: "Вебхук",
    order: "Заказ (пользователь)",
};

export function formatAuditTarget(targetType, targetId = null) {
    if (!targetType) return "—";
    const key = String(targetType).toLowerCase();
    const label = AUDIT_TARGET_TYPE_MAP[key] || targetType;
    const hasId = targetId !== null && targetId !== undefined && targetId !== "";
    return hasId ? `${label} #${targetId}` : label;
}

export const NODE_DISABLED_REASON_MAP = {
    HOSTING_TRAFFIC_LIMIT: "Лимит трафика хостинга",
    MANUAL: "Отключена вручную",
    PROBLEM: "Недоступна",
};

export const NODE_HEALTH_STATE_MAP = {
    ONLINE: "Онлайн",
    WAITING_CONFIRMATION: "Проверка",
    PROBLEM: "Проблема",
    MANUAL_DISABLED: "Отключена вручную",
    DEGRADED: "Сбой",
    OFFLINE: "Недоступна",
};

export function formatNodeDisabledReason(reason) {
    if (!reason) return "";
    const key = String(reason).trim().toUpperCase();
    return NODE_DISABLED_REASON_MAP[key] || String(reason);
}

export function formatNodeHealthState(state) {
    if (!state) return "";
    const key = String(state).trim().toUpperCase();
    return NODE_HEALTH_STATE_MAP[key] || String(state);
}

export const WI_PROVISIONING_STATUS_MAP = {
    PENDING_CREATE: "Ожидает создания",
    ACTIVE: "Активен",
    PENDING_UPDATE: "Ожидает обновления",
    PENDING_DELETE: "Ожидает удаления",
    SYNCED_INACTIVE: "Отключён",
    FAILED: "Ошибка синхронизации",
};

export const WI_NODE_SYNC_STATUS_MAP = {
    active: "активен",
    synced: "синхронизирован",
    synced_active: "активен",
    synced_inactive: "отключён",
    disabled: "отключён",
    inactive: "неактивен",
    pending: "ожидает",
    pending_create: "ожидает создания",
    pending_update: "ожидает обновления",
    pending_delete: "ожидает удаления",
    already_newer: "актуально",
    not_found: "не найден",
    error: "ошибка",
    failed: "ошибка",
};

export function formatWiProvisioningStatus(status) {
    if (!status) return "—";
    const key = String(status).trim().toUpperCase();
    return WI_PROVISIONING_STATUS_MAP[key] || String(status);
}

export function formatWiNodeSyncStatus(status) {
    if (!status) return "—";
    const key = String(status).trim().toLowerCase();
    return WI_NODE_SYNC_STATUS_MAP[key] || String(status);
}

function _fmtRub(val) {
    const n = Number(val || 0);
    if (!Number.isFinite(n)) return "0";
    return Number.isInteger(n) ? String(n) : String(Math.round(n * 100) / 100);
}

export function formatOrderPaymentMethod(method) {
    if (method === "yookassa") return "ЮKassa";
    if (method === "wallet") return "Баланс";
    return method || "";
}

export function formatOrderPaymentBreakdown(item) {
    if (!item || typeof item !== "object") return "";
    const method = String(item.payment_method || "");
    const ext = Math.max(0, Number(item.external_paid_rub || 0));
    const bonus = Math.max(0, Number(item.bonus_paid_rub || 0));
    const real = Math.max(0, Number(item.real_balance_paid_rub || 0));
    const wallet = Math.max(0, Number(item.wallet_debit_rub || 0));
    const extLabel = formatOrderPaymentMethod(method && method !== "wallet" ? method : "yookassa") || "ЮKassa";

    if (ext > 0 && wallet > 0) {
        const parts = [`${_fmtRub(ext)} ₽ ${extLabel}`];
        if (real > 0 && bonus > 0) {
            parts.push(`${_fmtRub(real)} ₽ осн.`);
            parts.push(`${_fmtRub(bonus)} ₽ бонусы`);
        } else if (bonus > 0) {
            parts.push(`${_fmtRub(bonus)} ₽ бонусы`);
        } else if (real > 0) {
            parts.push(`${_fmtRub(real)} ₽ осн.`);
        } else {
            parts.push(`${_fmtRub(wallet)} ₽ баланс`);
        }
        return parts.join(" + ");
    }

    if (method === "wallet" || (ext === 0 && wallet > 0)) {
        if (real > 0 && bonus > 0) {
            return `${_fmtRub(real)} ₽ осн. + ${_fmtRub(bonus)} ₽ бонусы`;
        }
        if (bonus > 0) return "Бонусы";
        if (real > 0) return "Основной баланс";
        return "Баланс";
    }

    if (ext > 0 || method) {
        return formatOrderPaymentMethod(method) || extLabel;
    }
    return "";
}

export function formatOrderAmountLabel(item) {
    if (!item || typeof item !== "object") return "0 ₽";
    const amt = Number(item.amount_rub || 0);
    const total = Number(item.total_rub ?? amt);
    if (Number.isFinite(total) && total > amt + 0.001) {
        return `${_fmtRub(total)} ₽`;
    }
    return `${_fmtRub(amt)} ₽`;
}
