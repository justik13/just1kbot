export function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

export function triggerHaptic(type = "light") {
    try {
        const tg = window.Telegram && window.Telegram.WebApp;
        if (tg && tg.HapticFeedback) {
            if (type === "success" || type === "warning" || type === "error") {
                tg.HapticFeedback.notificationOccurred(type);
            } else if (type === "medium" || type === "heavy" || type === "rigid" || type === "soft") {
                tg.HapticFeedback.impactOccurred(type);
            } else {
                tg.HapticFeedback.impactOccurred("light");
            }
        }
    } catch {
        // Safe fallback if Telegram WebApp haptic API is unsupported
    }
}

let _toastTimer = null;

export function showToast(message, type = "success") {
    const toast = document.getElementById("toastRoot");
    if (!toast) return;
    toast.textContent = String(message || "");
    toast.hidden = false;
    triggerHaptic(type);
    if (_toastTimer) clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => {
        toast.hidden = true;
    }, 3200);
}

export async function copyText(text, successMsg = "Скопировано") {
    const val = String(text || "");
    if (!val) return;
    try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(val);
            showToast(successMsg);
            return;
        }
    } catch {
        // Fallback below
    }
    const ta = document.createElement("textarea");
    ta.value = val;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
        document.execCommand("copy");
        showToast(successMsg);
    } catch {
        showToast("Не удалось скопировать");
    } finally {
        document.body.removeChild(ta);
    }
}

export function openExternalLink(url) {
    if (!url) return;
    let target = String(url).trim();
    if (/^tg:\/\/resolve\?domain=/i.test(target)) {
        const m = target.match(/domain=([^&]+)/i);
        if (m && m[1]) {
            target = `https://t.me/${m[1].replace(/^@/, "")}`;
        }
    }
    const tg = window.Telegram && window.Telegram.WebApp;
    const isTgLink = /^https?:\/\/t\.me\//i.test(target);
    if (isTgLink && tg && typeof tg.openTelegramLink === "function") {
        try {
            tg.openTelegramLink(target);
            return;
        } catch {
            // Fallback to openLink / window.open
        }
    }
    if (tg && typeof tg.openLink === "function" && /^https?:\/\//i.test(target)) {
        try {
            tg.openLink(target);
            return;
        } catch {
            // Fallback
        }
    }
    window.open(target, "_blank", "noopener,noreferrer");
}

export function formatDate(iso) {
    if (!iso) return "—";
    try {
        return new Date(iso).toLocaleString("ru-RU", {
            day: "2-digit",
            month: "2-digit",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
        });
    } catch {
        return String(iso);
    }
}

export function formatBytesGb(bytes) {
    const val = Number(bytes || 0);
    return (val / (1024 * 1024 * 1024)).toFixed(1);
}

export function openModal(title, bodyNode, { showCloseBtn = true } = {}) {
    const backdrop = document.getElementById("modalRoot");
    if (!backdrop) return () => {};
    backdrop.innerHTML = "";
    const card = document.createElement("div");
    card.className = "modal-card";

    const header = document.createElement("div");
    header.className = "row";
    const h = document.createElement("div");
    h.className = "card-title";
    h.textContent = title;

    const close = () => {
        backdrop.hidden = true;
        backdrop.innerHTML = "";
    };

    header.appendChild(h);

    if (showCloseBtn) {
        const closeBtn = document.createElement("button");
        closeBtn.type = "button";
        closeBtn.className = "btn btn-sm";
        closeBtn.textContent = "Закрыть";
        closeBtn.addEventListener("click", close);
        header.appendChild(closeBtn);
    }

    backdrop.addEventListener("click", (e) => {
        if (e.target === backdrop) close();
    });

    card.append(header, bodyNode);
    backdrop.appendChild(card);
    backdrop.hidden = false;
    return close;
}

export function showConfirmModal({
    title = "Подтверждение",
    message = "",
    confirmText = "Подтвердить",
    cancelText = "Отмена",
    isDestructive = false,
    onConfirm,
}) {
    const box = document.createElement("div");
    box.className = "confirm-modal-box";
    box.style.display = "flex";
    box.style.flexDirection = "column";
    box.style.gap = "14px";

    const p = document.createElement("p");
    p.className = "muted";
    p.style.lineHeight = "1.5";
    p.style.fontSize = "13px";
    p.style.margin = "0";
    p.textContent = message;

    const btnGroup = document.createElement("div");
    btnGroup.className = "btn-group";
    btnGroup.style.display = "flex";
    btnGroup.style.justifyContent = "flex-end";
    btnGroup.style.gap = "8px";

    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "btn btn-sm";
    cancelBtn.textContent = cancelText;

    const okBtn = document.createElement("button");
    okBtn.type = "button";
    okBtn.className = `btn btn-sm ${isDestructive ? "btn-danger" : "btn-primary"}`;
    okBtn.textContent = confirmText;

    btnGroup.append(cancelBtn, okBtn);
    box.append(p, btnGroup);

    const close = openModal(title, box, { showCloseBtn: false });
    cancelBtn.addEventListener("click", close);

    okBtn.addEventListener("click", async () => {
        okBtn.disabled = true;
        cancelBtn.disabled = true;
        try {
            if (onConfirm) await onConfirm();
            close();
        } catch (err) {
            console.error("Modal confirmation error:", err);
        } finally {
            okBtn.disabled = false;
            cancelBtn.disabled = false;
        }
    });

    return close;
}

export function roleLabel(role) {
    const map = {
        ru_hub: "RU Hub",
        eu_hub: "EU Hub",
        exit_node: "Exit Node",
        unconfigured: "Без роли",
    };
    return map[role] || role || "Неизвестно";
}

export function healthChipInfo(node) {
    if (!node.is_active || node.health_state === "MANUAL_DISABLED") {
        return { text: "Отключена", cls: "chip-muted" };
    }
    if (node.health_state === "ONLINE") {
        return { text: "Онлайн", cls: "chip-ok" };
    }
    if (node.health_state === "WAITING_CONFIRMATION") {
        return { text: "Проверка", cls: "chip-warn" };
    }
    return { text: "Проблема", cls: "chip-err" };
}
