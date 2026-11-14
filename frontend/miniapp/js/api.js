const ADMIN_SESSION_KEY = "just1k_admin_session";

function getInitData() {
    const tg = window.Telegram && window.Telegram.WebApp;
    return tg && tg.initData ? tg.initData : "";
}

export function getAdminSessionToken() {
    try {
        return sessionStorage.getItem(ADMIN_SESSION_KEY) || "";
    } catch {
        return "";
    }
}

export function setAdminSessionToken(token) {
    try {
        if (token) {
            sessionStorage.setItem(ADMIN_SESSION_KEY, token);
        } else {
            sessionStorage.removeItem(ADMIN_SESSION_KEY);
        }
    } catch {
        // Ignore storage errors
    }
}

async function requestJson(path, options = {}) {
    const adminToken = getAdminSessionToken();
    const headers = {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": getInitData(),
        ...(adminToken ? { "X-Admin-Session": adminToken } : {}),
        ...(options.headers || {}),
    };
    const resp = await fetch(path, {
        ...options,
        headers,
    });
    let data = {};
    try {
        data = await resp.json();
    } catch {
        data = { ok: false, error: `HTTP ${resp.status}` };
    }
    if (!resp.ok || data.ok === false) {
        if (resp.status === 403 && data.code === "2fa_required") {
            setAdminSessionToken("");
        }
        const msg = data.error || `HTTP ${resp.status}`;
        const err = new Error(msg);
        err.status = resp.status;
        err.code = data.code || "";
        err.payload = data;
        throw err;
    }
    return data;
}

export const api = {
    getMe: () => requestJson("/api/app/me"),

    // Admin 2FA
    setup2FA: () => requestJson("/api/app/admin/2fa/setup", { method: "POST" }),
    setup2FAPin: (pin) =>
        requestJson("/api/app/admin/2fa/setup-pin", {
            method: "POST",
            body: JSON.stringify({ pin }),
        }),
    verify2FA: (code, setupToken = null) =>
        requestJson("/api/app/admin/2fa/verify", {
            method: "POST",
            body: JSON.stringify(setupToken ? { code, setup_token: setupToken } : { code }),
        }),

    // Admin nodes & users
    listNodes: () => requestJson("/api/app/admin/nodes"),
    addNode: (payload) =>
        requestJson("/api/app/admin/nodes/add", {
            method: "POST",
            body: JSON.stringify(payload),
        }),
    refreshNode: (id) =>
        requestJson(`/api/app/admin/nodes/${id}/refresh`, {
            method: "POST",
        }),
    patchNode: (id, payload) =>
        requestJson(`/api/app/admin/nodes/${id}`, {
            method: "PATCH",
            body: JSON.stringify(payload),
        }),
    toggleNode: (id, isActive = null) =>
        requestJson(`/api/app/admin/nodes/${id}/toggle`, {
            method: "POST",
            body: JSON.stringify(isActive === null ? {} : { is_active: isActive }),
        }),
    deleteNode: (id) =>
        requestJson(`/api/app/admin/nodes/${id}`, {
            method: "DELETE",
        }),
    updateNodeLinks: (id, payload) =>
        requestJson(`/api/app/admin/nodes/${id}/links`, {
            method: "PUT",
            body: JSON.stringify(payload),
        }),
    deployNodeCert: (id, payload) =>
        requestJson(`/api/app/admin/nodes/${id}/certificate`, {
            method: "POST",
            body: JSON.stringify(payload),
        }),
    listUsers: (q = "", offset = 0, limit = 25) =>
        requestJson(`/api/app/admin/users?q=${encodeURIComponent(q)}&offset=${offset}&limit=${limit}`),
    adjustUser: (userId, payload) =>
        requestJson(`/api/app/admin/users/${userId}/adjust`, {
            method: "POST",
            body: JSON.stringify(payload),
        }),

    // User cabinet
    getUserDashboard: () => requestJson("/api/app/user/dashboard"),
    quoteStandard: (deviceLimit, durationDays) =>
        requestJson("/api/app/user/standard/quote", {
            method: "POST",
            body: JSON.stringify({ device_limit: deviceLimit, duration_days: durationDays }),
        }),
    checkoutStandard: (deviceLimit, durationDays, useBalance = true) =>
        requestJson("/api/app/user/standard/checkout", {
            method: "POST",
            body: JSON.stringify({
                device_limit: deviceLimit,
                duration_days: durationDays,
                use_balance: useBalance,
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
    topupBalance: (amountRub) =>
        requestJson("/api/app/user/topup", {
            method: "POST",
            body: JSON.stringify({ amount_rub: amountRub }),
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
    setDevicePriority: (hwid, priority) =>
        requestJson(`/api/app/user/devices/${encodeURIComponent(hwid)}/priority`, {
            method: "POST",
            body: JSON.stringify({ priority }),
        }),
};
