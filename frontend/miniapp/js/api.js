function getInitData() {
    const tg = window.Telegram && window.Telegram.WebApp;
    return tg && tg.initData ? tg.initData : "";
}

async function requestJson(path, options = {}) {
    const headers = {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": getInitData(),
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
        const msg = data.error || `HTTP ${resp.status}`;
        const err = new Error(msg);
        err.status = resp.status;
        err.payload = data;
        throw err;
    }
    return data;
}

export const api = {
    getMe: () => requestJson("/api/app/me"),
    verify2FAStub: (code = "") =>
        requestJson("/api/app/admin/2fa/verify-stub", {
            method: "POST",
            body: JSON.stringify({ code }),
        }),
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
    deployNodeCert: (id, payload) =>
        requestJson(`/api/app/admin/nodes/${id}/certificate`, {
            method: "POST",
            body: JSON.stringify(payload),
        }),
};
