let _onInitDataExpired = null;

export function setOnInitDataExpired(cb) {
    _onInitDataExpired = typeof cb === "function" ? cb : null;
}

function getInitData() {
    const tg = window.Telegram && window.Telegram.WebApp;
    return tg && tg.initData ? tg.initData : "";
}

async function requestJson(path, options = {}) {
    const { timeoutMs = 15000, ...fetchOptions } = options;
    const headers = {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": getInitData(),
        ...(fetchOptions.headers || {}),
    };
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    let resp;
    try {
        resp = await fetch(path, {
            ...fetchOptions,
            headers,
            signal: controller.signal,
        });
    } catch (err) {
        if (err && err.name === "AbortError") {
            const timeoutErr = new Error("Превышено время ожидания ответа сервера");
            timeoutErr.code = "timeout";
            throw timeoutErr;
        }
        if (err instanceof TypeError) {
            const netErr = new Error("Нет связи с сервером");
            netErr.code = "network_error";
            throw netErr;
        }
        throw err;
    } finally {
        clearTimeout(timer);
    }

    let data = {};
    try {
        data = await resp.json();
    } catch {
        data = { ok: false, error: `HTTP ${resp.status}` };
    }
    if (!resp.ok || data.ok === false) {
        const msg = data.error || `HTTP ${resp.status}`;
        const err = new Error(msg);
        err.status = resp.status;
        err.code = data.code || "";
        err.data = data;
        err.payload = data;
        if (resp.status === 401 && data.code === "init_data_expired") {
            if (_onInitDataExpired) _onInitDataExpired(err);
        }
        throw err;
    }
    return data;
}

export const api = {
    getMe: () => requestJson("/api/app/me"),

    // Admin overview, nodes & users
    getAdminOverview: () => requestJson("/api/app/admin/overview"),
    getIncySettings: () => requestJson("/api/app/admin/incy-settings"),
    updateIncySettings: (payload) =>
        requestJson("/api/app/admin/incy-settings", {
            method: "PUT",
            body: JSON.stringify(payload),
        }),
    listNodes: () => requestJson("/api/app/admin/nodes"),
    addNode: (payload) =>
        requestJson("/api/app/admin/nodes/add", {
            method: "POST",
            body: JSON.stringify(payload),
            timeoutMs: 25000,
        }),
    patchNode: (id, payload) =>
        requestJson(`/api/app/admin/nodes/${id}`, {
            method: "PATCH",
            body: JSON.stringify(payload),
            timeoutMs: 25000,
        }),
    setNodeLifecycle: (serverId, lifecycleStatus) =>
        requestJson(`/api/app/admin/nodes/${serverId}/lifecycle`, {
            method: "POST",
            body: JSON.stringify({ lifecycle_status: lifecycleStatus }),
        }),
    toggleNode: (id, isActive = null) =>
        requestJson(`/api/app/admin/nodes/${id}/toggle`, {
            method: "POST",
            body: JSON.stringify(isActive === null ? {} : { is_active: isActive }),
        }),
    deleteNode: (id, force = false) =>
        requestJson(`/api/app/admin/nodes/${id}${force ? "?force=true" : ""}`, {
            method: "DELETE",
        }),
    listUsers: (q = "", offset = 0, limit = 10, filter = "all") =>
        requestJson(
            `/api/app/admin/users?q=${encodeURIComponent(q)}&offset=${offset}&limit=${limit}&filter=${encodeURIComponent(filter || "all")}`
        ),
    getUserDetails: (userId) =>
        requestJson(`/api/app/admin/users/${userId}/details`),
    deleteUserDevice: (userId, hwid) =>
        requestJson(`/api/app/admin/users/${userId}/devices/${encodeURIComponent(hwid)}`, {
            method: "DELETE",
        }),
    resetUserDevices: (userId, payload = {}) =>
        requestJson(`/api/app/admin/users/${userId}/devices/reset`, {
            method: "POST",
            body: JSON.stringify(payload),
        }),
    rotateUserToken: (userId, payload = {}) =>
        requestJson(`/api/app/admin/users/${userId}/token/rotate`, {
            method: "POST",
            body: JSON.stringify(payload),
        }),
    adjustUser: (userId, payload) =>
        requestJson(`/api/app/admin/users/${userId}/adjust`, {
            method: "POST",
            body: JSON.stringify(payload),
        }),
    sendUserMessage: (userId, text) =>
        requestJson(`/api/app/admin/users/${userId}/message`, {
            method: "POST",
            body: JSON.stringify({ text }),
        }),
    listBroadcasts: (offset = 0, limit = 15) => requestJson(`/api/app/admin/broadcasts?offset=${encodeURIComponent(offset)}&limit=${encodeURIComponent(limit)}`),
    previewBroadcast: (targetAudience, extra = {}) =>
        requestJson("/api/app/admin/broadcasts/preview", {
            method: "POST",
            body: JSON.stringify({ target_audience: targetAudience, ...extra }),
        }),
    startBroadcast: (payload) =>
        requestJson("/api/app/admin/broadcasts", {
            method: "POST",
            body: JSON.stringify(payload),
        }),
    cancelBroadcast: (broadcastId) =>
        requestJson(`/api/app/admin/broadcasts/${broadcastId}/cancel`, {
            method: "POST",
        }),
    massBonus: (payload) =>
        requestJson("/api/app/admin/mass/bonus", {
            method: "POST",
            body: JSON.stringify(payload),
        }),
    massExtend: (payload) =>
        requestJson("/api/app/admin/mass/extend", {
            method: "POST",
            body: JSON.stringify(payload),
        }),
    listFinances: (kind = "orders", status = "", q = "", offset = 0, limit = 25, attentionOnly = false) =>
        requestJson(
            `/api/app/admin/finances?kind=${encodeURIComponent(kind)}&status=${encodeURIComponent(
                status
            )}&q=${encodeURIComponent(q)}&offset=${offset}&limit=${limit}${attentionOnly ? "&attention_only=1" : ""}`
        ),
    ackOrderReview: (orderId) =>
        requestJson(`/api/app/admin/orders/${encodeURIComponent(orderId)}/ack-review`, {
            method: "POST",
        }),
    listAuditLogs: (action = "", q = "", offset = 0, limit = 40, targetType = "") =>
        requestJson(
            `/api/app/admin/audit-logs?action=${encodeURIComponent(action)}&q=${encodeURIComponent(
                q
            )}&offset=${offset}&limit=${limit}${targetType ? `&target_type=${encodeURIComponent(targetType)}` : ""}`
        ),
    getPaymentsMaintenance: () => requestJson("/api/app/admin/settings/payments-maintenance"),
    setPaymentsMaintenance: (enabled) =>
        requestJson("/api/app/admin/settings/payments-maintenance", {
            method: "POST",
            body: JSON.stringify({ enabled }),
        }),

    // User cabinet
    getUserDashboard: () => requestJson("/api/app/user/dashboard"),
    quoteStandard: (deviceLimit, durationDays, operation = null) =>
        requestJson("/api/app/user/standard/quote", {
            method: "POST",
            body: JSON.stringify({
                device_limit: deviceLimit,
                duration_days: durationDays,
                ...(operation ? { operation } : {}),
            }),
        }),
    checkoutStandard: (
        deviceLimit,
        durationDays,
        useBalance = true,
        idempotencyKey = null,
        operation = null
    ) =>
        requestJson("/api/app/user/standard/checkout", {
            method: "POST",
            body: JSON.stringify({
                device_limit: deviceLimit,
                duration_days: durationDays,
                use_balance: useBalance,
                ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
                ...(operation ? { operation } : {}),
            }),
        }),
    downgradeStandard: (targetDeviceLimit) =>
        requestJson("/api/app/user/standard/downgrade", {
            method: "POST",
            body: JSON.stringify({ target_device_limit: targetDeviceLimit }),
        }),
    checkoutWi: (payload) =>
        requestJson("/api/app/user/wi/checkout", {
            method: "POST",
            body: JSON.stringify(payload),
        }),
    scheduleWiDowngrade: (targetDeviceLimit) =>
        requestJson("/api/app/user/wi/downgrade", {
            method: "POST",
            body: JSON.stringify({ target_device_limit: targetDeviceLimit }),
        }),
    cancelWiDowngrade: () =>
        requestJson("/api/app/user/wi/downgrade", {
            method: "DELETE",
        }),
    topupBalance: (amountRub, idempotencyKey = null) =>
        requestJson("/api/app/user/topup", {
            method: "POST",
            body: JSON.stringify({
                amount_rub: amountRub,
                ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
            }),
        }),
    checkOrderStatus: (orderId) =>
        requestJson(`/api/app/user/orders/${encodeURIComponent(orderId)}/check`, {
            method: "POST",
        }),
    setAutoRenew: (scope, enabled) =>
        requestJson("/api/app/user/auto-renew", {
            method: "POST",
            body: JSON.stringify({ scope, enabled }),
        }),
    deleteDevice: (hwid) =>
        requestJson(`/api/app/user/devices/${encodeURIComponent(hwid)}`, {
            method: "DELETE",
        }),
    resetAllDevices: () =>
        requestJson("/api/app/user/devices/reset", {
            method: "POST",
        }),
    setDevicePriority: (hwid, priority, replaceHwid = null) =>
        requestJson(`/api/app/user/devices/${encodeURIComponent(hwid)}/priority`, {
            method: "POST",
            body: JSON.stringify(
                replaceHwid ? { priority, replace_hwid: replaceHwid } : { priority }
            ),
        }),
    setDeviceStdPriority: (hwid, priority, replaceHwid = null) =>
        requestJson(`/api/app/user/devices/${encodeURIComponent(hwid)}/std-priority`, {
            method: "POST",
            body: JSON.stringify(
                replaceHwid ? { priority, replace_hwid: replaceHwid } : { priority }
            ),
        }),
};
