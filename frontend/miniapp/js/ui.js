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

let _modalStack = [];
let _modalTitleSeq = 0;

function _focusFirstInModal(card) {
    const focusable = card.querySelector(
        "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"
    );
    if (focusable && typeof focusable.focus === "function") {
        focusable.focus({ preventScroll: true });
    }
}

export function openModal(title, bodyNode, { showCloseBtn = true } = {}) {
    const backdrop = document.getElementById("modalRoot");
    if (!backdrop) return () => {};

    const prevCard = backdrop.querySelector(".modal-card");
    const prevKeyDown = backdrop._activeKeyDown || null;
    const prevTrigger = document.activeElement;
    if (prevCard && !backdrop.hidden) {
        if (prevKeyDown) {
            document.removeEventListener("keydown", prevKeyDown);
        }
        _modalStack.push({ card: prevCard, onKeyDown: prevKeyDown, trigger: prevTrigger });
    } else {
        _modalStack = [];
    }

    backdrop.innerHTML = "";
    const card = document.createElement("div");
    card.className = "modal-card";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");

    _modalTitleSeq += 1;
    const titleId = `modalDialogTitle_${_modalTitleSeq}`;
    const header = document.createElement("div");
    header.className = "row";
    const h = document.createElement("div");
    h.className = "card-title";
    h.id = titleId;
    h.textContent = title;
    card.setAttribute("aria-labelledby", titleId);

    let closed = false;
    const onKeyDown = (e) => {
        if (e.key === "Escape") {
            close();
        }
    };

    const close = () => {
        if (closed) return;
        closed = true;
        document.removeEventListener("keydown", onKeyDown);

        if (_modalStack.length > 0 && backdrop.contains(card)) {
            const parent = _modalStack.pop();
            backdrop.innerHTML = "";
            backdrop.appendChild(parent.card);
            backdrop._activeKeyDown = parent.onKeyDown;
            if (parent.onKeyDown) {
                document.addEventListener("keydown", parent.onKeyDown);
            }
            backdrop.onclick = (e) => {
                if (e.target === backdrop && parent.onKeyDown) {
                    parent.onKeyDown({ key: "Escape" });
                }
            };
            if (parent.trigger && parent.card.contains(parent.trigger) && typeof parent.trigger.focus === "function") {
                parent.trigger.focus({ preventScroll: true });
            } else {
                _focusFirstInModal(parent.card);
            }
            return;
        }

        _modalStack = [];
        backdrop._activeKeyDown = null;
        document.body.style.overflow = "";
        backdrop.hidden = true;
        backdrop.innerHTML = "";
        if (prevTrigger && document.contains(prevTrigger) && typeof prevTrigger.focus === "function") {
            prevTrigger.focus({ preventScroll: true });
        }
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

    backdrop.onclick = (e) => {
        if (e.target === backdrop) close();
    };

    card.append(header, bodyNode);
    backdrop.appendChild(card);
    backdrop.hidden = false;
    backdrop._activeKeyDown = onKeyDown;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", onKeyDown);
    _focusFirstInModal(card);
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
            close();
            if (onConfirm) await onConfirm();
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

function _extractCountryCode(flagOrCode) {
    const raw = String(flagOrCode || "").trim();
    if (!raw) return "";
    if (/^[a-zA-Z]{2}$/.test(raw)) return raw.toUpperCase();
    const cps = Array.from(raw).map((ch) => ch.codePointAt(0) || 0);
    if (cps.length >= 2 && cps[0] >= 0x1f1e6 && cps[0] <= 0x1f1ff && cps[1] >= 0x1f1e6 && cps[1] <= 0x1f1ff) {
        return String.fromCharCode(65 + (cps[0] - 0x1f1e6), 65 + (cps[1] - 0x1f1e6));
    }
    return "";
}

export function renderFlagHtml(flagOrCode, width = 20) {
    const code = _extractCountryCode(flagOrCode);
    const height = Math.round((width * 15) / 21);
    const wrap = (inner) =>
        `<svg width="${width}" height="${height}" viewBox="0 0 21 15" aria-label="${escapeHtml(code)}" style="display:inline-block; vertical-align:-2px; border-radius:2px; flex-shrink:0; box-shadow:0 0 0 1px rgba(255,255,255,0.12);">${inner}</svg>`;
    const hStripes = (c1, c2, c3) =>
        wrap(
            `<rect width="21" height="5" fill="${c1}"/><rect y="5" width="21" height="5" fill="${c2}"/><rect y="10" width="21" height="5" fill="${c3}"/>`
        );
    const vStripes = (c1, c2, c3) =>
        wrap(
            `<rect width="7" height="15" fill="${c1}"/><rect x="7" width="7" height="15" fill="${c2}"/><rect x="14" width="7" height="15" fill="${c3}"/>`
        );
    const svgs = {
        DE: () => hStripes("#111", "#D80027", "#FFDA44"),
        NL: () => hStripes("#AE1C28", "#FFF", "#21468B"),
        RU: () => hStripes("#FFF", "#0039A6", "#D52B1E"),
        AT: () => hStripes("#ED2939", "#FFF", "#ED2939"),
        EE: () => hStripes("#0072CE", "#111", "#FFF"),
        LV: () => wrap(`<rect width="21" height="6" fill="#9E3039"/><rect y="6" width="21" height="3" fill="#FFF"/><rect y="9" width="21" height="6" fill="#9E3039"/>`),
        LT: () => hStripes("#FDB913", "#006A44", "#C1272D"),
        ES: () => wrap(`<rect width="21" height="4" fill="#AA151B"/><rect y="4" width="21" height="7" fill="#F1BF00"/><rect y="11" width="21" height="4" fill="#AA151B"/>`),
        PL: () => wrap(`<rect width="21" height="7.5" fill="#FFF"/><rect y="7.5" width="21" height="7.5" fill="#DC143C"/>`),
        UA: () => wrap(`<rect width="21" height="7.5" fill="#005BBB"/><rect y="7.5" width="21" height="7.5" fill="#FFD500"/>`),
        FR: () => vStripes("#0055A4", "#FFF", "#EF4135"),
        IT: () => vStripes("#009246", "#FFF", "#CE2B37"),
        FI: () => wrap(`<rect width="21" height="15" fill="#FFF"/><rect x="6" width="3" height="15" fill="#003580"/><rect y="6" width="21" height="3" fill="#003580"/>`),
        SE: () => wrap(`<rect width="21" height="15" fill="#006AA7"/><rect x="6" width="3" height="15" fill="#FECC00"/><rect y="6" width="21" height="3" fill="#FECC00"/>`),
        NO: () => wrap(`<rect width="21" height="15" fill="#BA0C2F"/><rect x="5" width="5" height="15" fill="#FFF"/><rect y="5" width="21" height="5" fill="#FFF"/><rect x="6" width="3" height="15" fill="#00205B"/><rect y="6" width="21" height="3" fill="#00205B"/>`),
        DK: () => wrap(`<rect width="21" height="15" fill="#C60C30"/><rect x="6" width="3" height="15" fill="#FFF"/><rect y="6" width="21" height="3" fill="#FFF"/>`),
        CH: () => wrap(`<rect width="21" height="15" fill="#D52B1E"/><rect x="9" y="3" width="3" height="9" fill="#FFF"/><rect x="6" y="6" width="9" height="3" fill="#FFF"/>`),
        CZ: () => wrap(`<rect width="21" height="7.5" fill="#FFF"/><rect y="7.5" width="21" height="7.5" fill="#D7141A"/><polygon points="0,0 10.5,7.5 0,15" fill="#11457E"/>`),
        TR: () => wrap(`<rect width="21" height="15" fill="#E30A17"/><circle cx="8.5" cy="7.5" r="3.6" fill="#FFF"/><circle cx="9.6" cy="7.5" r="2.9" fill="#E30A17"/><polygon points="12.5,7.5 14.3,6.9 13.7,8.7 13.7,6.3 14.3,8.1" fill="#FFF"/>`),
        US: () => wrap(`<rect width="21" height="15" fill="#FFF"/><rect width="21" height="2.2" fill="#B22234"/><rect y="4.3" width="21" height="2.2" fill="#B22234"/><rect y="8.6" width="21" height="2.2" fill="#B22234"/><rect y="12.8" width="21" height="2.2" fill="#B22234"/><rect width="10" height="8" fill="#3C3B6E"/>`),
        GB: () => wrap(`<rect width="21" height="15" fill="#012169"/><path d="M0,0 L21,15 M21,0 L0,15" stroke="#FFF" stroke-width="2.6"/><path d="M0,0 L21,15 M21,0 L0,15" stroke="#C8102E" stroke-width="1.2"/><rect x="8.5" width="4" height="15" fill="#FFF"/><rect y="5.5" width="21" height="4" fill="#FFF"/><rect x="9.3" width="2.4" height="15" fill="#C8102E"/><rect y="6.3" width="21" height="2.4" fill="#C8102E"/>`),
        JP: () => wrap(`<rect width="21" height="15" fill="#FFF"/><circle cx="10.5" cy="7.5" r="4.2" fill="#BC002D"/>`),
        SG: () => wrap(`<rect width="21" height="7.5" fill="#EF3340"/><rect y="7.5" width="21" height="7.5" fill="#FFF"/><circle cx="5" cy="3.8" r="2.2" fill="#FFF"/><circle cx="5.9" cy="3.8" r="1.9" fill="#EF3340"/>`),
        KZ: () => wrap(`<rect width="21" height="15" fill="#00AFCA"/><circle cx="10.5" cy="7.5" r="3.2" fill="#FEC50C"/>`),
        AE: () => wrap(`<rect x="5" width="16" height="5" fill="#00732F"/><rect x="5" y="5" width="16" height="5" fill="#FFF"/><rect x="5" y="10" width="16" height="5" fill="#111"/><rect width="5" height="15" fill="#FF0000"/>`),
        CA: () => wrap(`<rect width="5" height="15" fill="#FF0000"/><rect x="5" width="11" height="15" fill="#FFF"/><rect x="16" width="5" height="15" fill="#FF0000"/><polygon points="10.5,4 12,7 14,7 12.2,8.8 12.8,11 10.5,9.6 8.2,11 8.8,8.8 7,7 9,7" fill="#FF0000"/>`),
    };
    if (code && svgs[code]) {
        return svgs[code]();
    }
    return `<span style="font-size:${Math.max(14, width - 2)}px; line-height:1;">${escapeHtml(flagOrCode || "🌐")}</span>`;
}

export function renderPagination({
    container,
    total = 0,
    offset = 0,
    limit = 25,
    onPage,
    scrollTarget = null,
    entityName = "записей",
}) {
    if (!container) return;
    const curTotal = Math.max(0, Number(total) || 0);
    const curOffset = Math.max(0, Number(offset) || 0);
    const curLimit = Math.max(1, Number(limit) || 25);
    const currentPage = Math.floor(curOffset / curLimit) + 1;
    const totalPages = Math.max(1, Math.ceil(curTotal / curLimit));

    if (curTotal <= curLimit && curOffset === 0) {
        container.innerHTML = `<span class="pagination-info">Всего: ${curTotal} ${escapeHtml(entityName)}</span>`;
        return;
    }

    const hasPrev = curOffset > 0;
    const hasNext = curOffset + curLimit < curTotal;
    const startIdx = curOffset + 1;
    const endIdx = Math.min(curOffset + curLimit, curTotal);

    container.className = "pagination-bar";
    container.innerHTML = `
        <span class="pagination-info">${startIdx}–${endIdx} из ${curTotal} · Стр. ${currentPage}/${totalPages}</span>
        <div class="pagination-controls">
            <button type="button" class="btn btn-sm" data-pg="prev" ${hasPrev ? "" : "disabled"}>← Назад</button>
            <button type="button" class="btn btn-sm" data-pg="next" ${hasNext ? "" : "disabled"}>Далее →</button>
        </div>
    `;

    const prevBtn = container.querySelector('[data-pg="prev"]');
    if (prevBtn && hasPrev) {
        prevBtn.addEventListener("click", () => {
            const nextOffset = Math.max(0, curOffset - curLimit);
            onPage(nextOffset);
            if (scrollTarget) {
                scrollTarget.scrollIntoView({ behavior: "smooth", block: "start" });
            }
        });
    }

    const nextBtn = container.querySelector('[data-pg="next"]');
    if (nextBtn && hasNext) {
        nextBtn.addEventListener("click", () => {
            const nextOffset = curOffset + curLimit;
            onPage(nextOffset);
            if (scrollTarget) {
                scrollTarget.scrollIntoView({ behavior: "smooth", block: "start" });
            }
        });
    }
}
