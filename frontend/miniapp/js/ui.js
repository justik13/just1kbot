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
    const normalized = type === "error" ? "error" : type === "warn" || type === "warning" ? "warn" : "success";
    toast.textContent = String(message || "");
    toast.className = `toast toast-${normalized}`;
    toast.hidden = false;
    toast.onclick = () => {
        if (_toastTimer) {
            clearTimeout(_toastTimer);
            _toastTimer = null;
        }
        toast.hidden = true;
    };
    const hapticType = normalized === "error" ? "error" : normalized === "warn" ? "warning" : "success";
    triggerHaptic(hapticType);
    if (_toastTimer) clearTimeout(_toastTimer);
    const duration = normalized === "error" ? 4500 : 3200;
    _toastTimer = setTimeout(() => {
        toast.hidden = true;
        _toastTimer = null;
    }, duration);
}

export async function copyText(text, successMsg = "Скопировано") {
    const val = String(text || "");
    if (!val) return false;
    try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(val);
            showToast(successMsg);
            return true;
        }
    } catch {
        // Fallback below
    }
    const ta = document.createElement("textarea");
    ta.value = val;
    ta.readOnly = true;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
        const ok = document.execCommand("copy");
        if (ok) {
            showToast(successMsg);
            return true;
        }
        showToast("Не удалось скопировать", "error");
        return false;
    } catch {
        showToast("Не удалось скопировать", "error");
        return false;
    } finally {
        document.body.removeChild(ta);
    }
}

export function formatRelativeTime(iso) {
    if (!iso) return "—";
    const ts = new Date(iso).getTime();
    if (!Number.isFinite(ts)) return String(iso);
    const diffSec = Math.floor((Date.now() - ts) / 1000);
    if (diffSec < 0) return formatDate(iso);
    if (diffSec < 60) return "только что";
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin} мин назад`;
    const diffHours = Math.floor(diffMin / 60);
    if (diffHours < 24) return `${diffHours} ч назад`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays < 30) return `${diffDays} дн назад`;
    return formatDate(iso);
}

export async function withButtonLoading(btn, loadingText, fn) {
    if (!btn) return fn();
    const prevDisabled = btn.disabled;
    const prevText = btn.textContent;
    btn.disabled = true;
    if (loadingText) btn.textContent = loadingText;
    try {
        return await fn();
    } finally {
        btn.disabled = prevDisabled;
        btn.textContent = prevText;
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
let _fieldIdSeq = 0;
let _pageBackHandler = null;
let _globalBackListenerBound = false;

function _hasOpenModal() {
    const backdrop = document.getElementById("modalRoot");
    return Boolean(backdrop && !backdrop.hidden && typeof backdrop._activeClose === "function");
}

function _handleGlobalBackButton() {
    if (_hasOpenModal()) {
        const backdrop = document.getElementById("modalRoot");
        if (backdrop && typeof backdrop._activeClose === "function") {
            backdrop._activeClose();
        }
        return;
    }
    if (typeof _pageBackHandler === "function") {
        _pageBackHandler();
    }
}

function _syncTelegramBackButton() {
    const tg = window.Telegram && window.Telegram.WebApp;
    if (!tg || !tg.BackButton) return;
    try {
        if (!_globalBackListenerBound) {
            tg.BackButton.onClick(_handleGlobalBackButton);
            _globalBackListenerBound = true;
        }
        if (_hasOpenModal() || Boolean(_pageBackHandler)) {
            tg.BackButton.show();
        } else {
            tg.BackButton.hide();
        }
    } catch {
        // Safe fallback if BackButton API is unsupported
    }
}

export function setPageBackButton(fn) {
    _pageBackHandler = typeof fn === "function" ? fn : null;
    _syncTelegramBackButton();
}

const _FOCUSABLE_SELECTOR =
    "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

function _getModalFocusable(card) {
    return Array.from(card.querySelectorAll(_FOCUSABLE_SELECTOR)).filter(
        (el) => !el.hidden && el.offsetParent !== null
    );
}

function _setBackgroundInert(isInert) {
    const shell = document.querySelector(".app-shell");
    if (shell && "inert" in shell) {
        shell.inert = Boolean(isInert);
    }
}

function _focusFirstInModal(card) {
    const focusables = _getModalFocusable(card);
    const preferred =
        focusables.find((el) => el.hasAttribute("autofocus") || el.autofocus) ||
        focusables[0];
    if (preferred && typeof preferred.focus === "function") {
        preferred.focus({ preventScroll: true });
    } else if (typeof card.focus === "function") {
        card.focus({ preventScroll: true });
    }
}

export function closeAllModals() {
    const backdrop = document.getElementById("modalRoot");
    if (!backdrop) return;
    if (backdrop._activeKeyDown) {
        document.removeEventListener("keydown", backdrop._activeKeyDown);
    }
    for (const item of _modalStack) {
        if (item.onKeyDown) {
            document.removeEventListener("keydown", item.onKeyDown);
        }
    }
    _modalStack = [];
    backdrop._activeKeyDown = null;
    backdrop._activeClose = null;
    backdrop.onclick = null;
    document.body.style.overflow = "";
    _setBackgroundInert(false);
    backdrop.hidden = true;
    backdrop.innerHTML = "";
    _syncTelegramBackButton();
}

export function openModal(title, bodyNode, { showCloseBtn = true, canClose = null } = {}) {
    const backdrop = document.getElementById("modalRoot");
    if (!backdrop) return () => {};

    const prevCard = backdrop.querySelector(".modal-card");
    const prevKeyDown = backdrop._activeKeyDown || null;
    const prevClose = backdrop._activeClose || null;
    const prevTrigger = document.activeElement;
    if (prevCard && !backdrop.hidden) {
        if (prevKeyDown) {
            document.removeEventListener("keydown", prevKeyDown);
        }
        _modalStack.push({
            card: prevCard,
            onKeyDown: prevKeyDown,
            onClose: prevClose,
            trigger: prevTrigger,
        });
    } else {
        _modalStack = [];
    }

    backdrop.innerHTML = "";
    const card = document.createElement("div");
    card.className = "modal-card";
    card.tabIndex = -1;
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");

    _modalTitleSeq += 1;
    const titleId = `modalDialogTitle_${_modalTitleSeq}`;
    const header = document.createElement("div");
    header.className = "row";
    const h = document.createElement("h1");
    h.className = "card-title";
    h.id = titleId;
    h.textContent = title;
    card.setAttribute("aria-labelledby", titleId);

    let closed = false;
    const onKeyDown = (e) => {
        if (e.key === "Escape") {
            close();
            return;
        }
        if (e.key !== "Tab") return;
        const focusables = _getModalFocusable(card);
        if (focusables.length === 0) {
            e.preventDefault();
            card.focus({ preventScroll: true });
            return;
        }
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = document.activeElement;
        if (e.shiftKey) {
            if (active === first || !card.contains(active)) {
                e.preventDefault();
                last.focus({ preventScroll: true });
            }
        } else if (active === last || !card.contains(active)) {
            e.preventDefault();
            first.focus({ preventScroll: true });
        }
    };

    const close = ({ force = false } = {}) => {
        if (closed) return;
        if (!force && canClose && !canClose()) return;
        closed = true;
        document.removeEventListener("keydown", onKeyDown);

        if (!backdrop.contains(card)) {
            const idx = _modalStack.findIndex((item) => item.card === card);
            if (idx !== -1) {
                _modalStack.splice(idx, 1);
            }
            _syncTelegramBackButton();
            return;
        }

        if (_modalStack.length > 0) {
            const parent = _modalStack.pop();
            backdrop.innerHTML = "";
            backdrop.appendChild(parent.card);
            backdrop._activeKeyDown = parent.onKeyDown;
            backdrop._activeClose = parent.onClose;
            if (parent.onKeyDown) {
                document.addEventListener("keydown", parent.onKeyDown);
            }
            backdrop.onclick = (e) => {
                if (e.target === backdrop && parent.onClose) {
                    parent.onClose();
                }
            };
            _syncTelegramBackButton();
            if (parent.trigger && parent.card.contains(parent.trigger) && typeof parent.trigger.focus === "function") {
                parent.trigger.focus({ preventScroll: true });
            } else {
                _focusFirstInModal(parent.card);
            }
            return;
        }

        _modalStack = [];
        backdrop._activeKeyDown = null;
        backdrop._activeClose = null;
        backdrop.onclick = null;
        document.body.style.overflow = "";
        _setBackgroundInert(false);
        backdrop.hidden = true;
        backdrop.innerHTML = "";
        _syncTelegramBackButton();
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
        closeBtn.addEventListener("click", () => close());
        header.appendChild(closeBtn);
    }

    backdrop.onclick = (e) => {
        if (e.target === backdrop) close();
    };

    card.append(header, bodyNode);
    backdrop.appendChild(card);
    backdrop.hidden = false;
    backdrop._activeKeyDown = onKeyDown;
    backdrop._activeClose = close;
    document.body.style.overflow = "hidden";
    _setBackgroundInert(true);
    document.addEventListener("keydown", onKeyDown);
    _syncTelegramBackButton();
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
    cancelBtn.className = "btn";
    cancelBtn.textContent = cancelText;
    if (isDestructive) {
        cancelBtn.autofocus = true;
        cancelBtn.setAttribute("autofocus", "");
    }

    const okBtn = document.createElement("button");
    okBtn.type = "button";
    okBtn.className = `btn ${isDestructive ? "btn-danger-filled" : "btn-primary"}`;
    okBtn.textContent = confirmText;

    btnGroup.append(cancelBtn, okBtn);
    box.append(p, btnGroup);

    let busy = false;
    const close = openModal(title, box, {
        showCloseBtn: false,
        canClose: () => !busy,
    });
    cancelBtn.addEventListener("click", () => {
        if (!busy) close();
    });

    okBtn.addEventListener("click", async () => {
        if (busy) return;
        busy = true;
        okBtn.disabled = true;
        cancelBtn.disabled = true;
        okBtn.textContent = "Выполнение...";
        try {
            if (onConfirm) await onConfirm();
            busy = false;
            close({ force: true });
        } catch (err) {
            busy = false;
            okBtn.disabled = false;
            cancelBtn.disabled = false;
            okBtn.textContent = confirmText;
            showToast((err && err.message) || "Ошибка", "error");
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
    if (String(node.lifecycle_status || "").toUpperCase() === "DECOMMISSIONING") {
        return { text: "Выводится", cls: "chip-warn" };
    }
    if (!node.is_active || node.health_state === "MANUAL_DISABLED") {
        return { text: "Отключена", cls: "chip-muted" };
    }
    if (node.disabled_reason === "HOSTING_TRAFFIC_LIMIT") {
        return { text: "Лимит трафика", cls: "chip-err" };
    }
    if (node.ingress_problem || node.cdn_problem) {
        return { text: "Проблема CDN/Ingress", cls: "chip-warn" };
    }
    if (node.health_state === "ONLINE") {
        return { text: "Онлайн", cls: "chip-ok" };
    }
    if (node.health_state === "WAITING_CONFIRMATION") {
        return { text: "Проверка", cls: "chip-warn" };
    }
    return { text: "Проблема", cls: "chip-err" };
}

export const COUNTRY_OPTIONS = [
    { code: "AT", name: "Австрия" },
    { code: "GB", name: "Великобритания" },
    { code: "DE", name: "Германия" },
    { code: "DK", name: "Дания" },
    { code: "ES", name: "Испания" },
    { code: "IT", name: "Италия" },
    { code: "KZ", name: "Казахстан" },
    { code: "CA", name: "Канада" },
    { code: "LV", name: "Латвия" },
    { code: "LT", name: "Литва" },
    { code: "NL", name: "Нидерланды" },
    { code: "NO", name: "Норвегия" },
    { code: "AE", name: "ОАЭ" },
    { code: "PL", name: "Польша" },
    { code: "RU", name: "Россия" },
    { code: "SG", name: "Сингапур" },
    { code: "US", name: "США" },
    { code: "TR", name: "Турция" },
    { code: "UA", name: "Украина" },
    { code: "FI", name: "Финляндия" },
    { code: "FR", name: "Франция" },
    { code: "CZ", name: "Чехия" },
    { code: "CH", name: "Швейцария" },
    { code: "SE", name: "Швеция" },
    { code: "EE", name: "Эстония" },
    { code: "JP", name: "Япония" },
];

const _COUNTRY_CODE_SET = new Set(COUNTRY_OPTIONS.map((c) => c.code));

export function countryCodeToEmoji(code) {
    const clean = String(code || "").trim().toUpperCase();
    if (!/^[A-Z]{2}$/.test(clean)) return "";
    return String.fromCodePoint(
        0x1f1e6 + clean.charCodeAt(0) - 65,
        0x1f1e6 + clean.charCodeAt(1) - 65
    );
}

export function renderCountryFlagField({
    name = "country_flag",
    id = "",
    label = "Страна / флаг",
    value = "",
    emptyLabel = "Не задан",
} = {}) {
    const raw = String(value || "").trim();
    const code = _extractCountryCode(raw);
    const isKnown = Boolean(code && _COUNTRY_CODE_SET.has(code));
    const isCustom = Boolean(raw && raw !== "\u{1F310}" && !isKnown);
    const selectVal = isKnown ? code : isCustom ? "__custom__" : "";
    const effectiveVal = isKnown ? countryCodeToEmoji(code) : isCustom ? raw : "";
    _fieldIdSeq += 1;
    const selectId = id ? `${id}_select` : `flagSelect_${_fieldIdSeq}`;

    const optionsHtml = COUNTRY_OPTIONS.map(
        (c) =>
            `<option value="${c.code}" ${selectVal === c.code ? "selected" : ""}>${escapeHtml(c.name)} (${c.code})</option>`
    ).join("");

    return `
        <div class="field" data-flag-field>
            <label for="${escapeHtml(selectId)}">${escapeHtml(label)}</label>
            <div class="flag-picker">
                <span class="flag-picker-preview" data-flag-preview>${renderFlagHtml(effectiveVal, 22)}</span>
                <select id="${escapeHtml(selectId)}" class="select flag-picker-select" data-flag-select>
                    <option value="" ${selectVal === "" ? "selected" : ""}>${escapeHtml(emptyLabel)}</option>
                    ${optionsHtml}
                    <option value="__custom__" ${selectVal === "__custom__" ? "selected" : ""}>Другая (код или флаг)</option>
                </select>
                <input type="hidden" name="${escapeHtml(name)}" ${id ? `id="${escapeHtml(id)}"` : ""} value="${escapeHtml(effectiveVal)}" data-flag-value>
            </div>
            <input type="text" class="input" data-flag-custom maxlength="10" value="${escapeHtml(isCustom ? raw : "")}" placeholder="Код (напр. AU) или эмодзи" aria-label="Свой код или символ страны" ${isCustom ? "" : "hidden"}>
        </div>
    `;
}

export function wireCountryFlagFields(root) {
    if (!root) return;
    root.querySelectorAll("[data-flag-field]").forEach((field) => {
        const select = field.querySelector("[data-flag-select]");
        const customInput = field.querySelector("[data-flag-custom]");
        const hiddenInput = field.querySelector("[data-flag-value]");
        const preview = field.querySelector("[data-flag-preview]");
        if (!select || !customInput || !hiddenInput || !preview) return;

        const sync = (nextVal, emit = true) => {
            hiddenInput.value = nextVal;
            preview.innerHTML = renderFlagHtml(nextVal, 22);
            if (emit) {
                hiddenInput.dispatchEvent(new Event("input", { bubbles: true }));
            }
        };

        select.addEventListener("change", () => {
            const val = select.value;
            if (val === "__custom__") {
                customInput.hidden = false;
                customInput.focus();
                sync(customInput.value.trim());
            } else if (val) {
                customInput.hidden = true;
                sync(countryCodeToEmoji(val));
            } else {
                customInput.hidden = true;
                sync("");
            }
        });

        customInput.addEventListener("input", () => {
            if (select.value === "__custom__") {
                sync(customInput.value.trim());
            }
        });

        const form = field.closest("form");
        if (form) {
            form.addEventListener("reset", () => {
                setTimeout(() => {
                    customInput.hidden = select.value !== "__custom__";
                    const v = select.value === "__custom__"
                        ? customInput.value.trim()
                        : select.value
                            ? countryCodeToEmoji(select.value)
                            : "";
                    sync(v, false);
                }, 0);
            });
        }
    });
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
        `<svg width="${width}" height="${height}" viewBox="0 0 21 15" aria-label="${escapeHtml(code)}" style="display:inline-block; vertical-align:-2px; border-radius:2px; flex-shrink:0; outline:1px solid rgba(255,255,255,0.12);">${inner}</svg>`;
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
    if (flagOrCode && flagOrCode !== "\u{1F310}") {
        return `<span style="font-size:${Math.max(14, width - 2)}px; line-height:1;">${escapeHtml(flagOrCode)}</span>`;
    }
    return `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>`;
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
