export function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

let _toastTimer = null;

export function showToast(message) {
    const toast = document.getElementById("toastRoot");
    if (!toast) return;
    toast.textContent = String(message || "");
    toast.hidden = false;
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
    const tg = window.Telegram && window.Telegram.WebApp;
    if (tg && typeof tg.openLink === "function") {
        try {
            tg.openLink(url);
            return;
        } catch {
            // Fallback
        }
    }
    window.open(url, "_blank", "noopener,noreferrer");
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

export function openModal(title, bodyNode) {
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
    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "btn btn-sm";
    closeBtn.textContent = "Закрыть";

    const close = () => {
        backdrop.hidden = true;
        backdrop.innerHTML = "";
    };
    closeBtn.addEventListener("click", close);
    backdrop.addEventListener("click", (e) => {
        if (e.target === backdrop) close();
    });

    header.append(h, closeBtn);
    card.append(header, bodyNode);
    backdrop.appendChild(card);
    backdrop.hidden = false;
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
