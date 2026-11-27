import { api } from "../api.js?v=2.8.0";
import {
    copyText,
    escapeHtml,
    formatBytesGb,
    formatDate,
    openExternalLink,
    setPageBackButton,
    showConfirmModal,
    showToast,
} from "../ui.js?v=2.8.0";
import {
    calcStdPrice as _calcStdPrice,
    openSlotReplaceModal as _openSlotReplaceModal,
    openStandardDevicesModal as _openStandardDevicesModal,
    openStandardPlanModal as _openStandardPlanModal,
    openTopupModal as _openTopupModal,
    openWiManageModal as _openWiManageModal,
    openWiPurchaseModal as _openWiPurchaseModal,
} from "./user_cabinet_modals.js?v=2.8.0";
import { getPlatformApps, loadGuidesData, renderUserGuidesView } from "./user_guides.js?v=2.8.0";

const _state = {
    tab: null,
    platform: _detectPlatform(),
    data: null,
    serverClockOffsetMs: 0,
    avatarFailed: false,
    guidesReturnTab: "connect",
    guidesReturnScrollY: 0,
    guideTab: null,
    guideIosMethod: "region",
    guideAndroidMethod: "play",
    guideDesktopMethod: null,
};

let _dashReqId = 0;

function _syncServerClock(d) {
    const serverMs = d && d.server_now ? Date.parse(d.server_now) : NaN;
    _state.serverClockOffsetMs = Number.isFinite(serverMs) ? (serverMs - Date.now()) : 0;
}

function _serverNowMs() {
    return Date.now() + (_state.serverClockOffsetMs || 0);
}

function _getStdGraceInfo(d, std) {
    const stdExpMs = std && std.expires_at ? new Date(std.expires_at).getTime() : 0;
    const nowMs = _serverNowMs();
    const rawGrace = d && (d.grace_hours ?? (d.pricing && d.pricing.grace_hours));
    const graceHours = Number.isFinite(Number(rawGrace)) && Number(rawGrace) > 0 ? Number(rawGrace) : 4;
    const isStdGrace = Boolean(std && std.active && Number.isFinite(stdExpMs) && stdExpMs > 0 && stdExpMs < nowMs);
    const graceEndMs = stdExpMs + (graceHours * 3600 * 1000);
    const graceHoursLeft = Math.max(1, Math.ceil((graceEndMs - nowMs) / (3600 * 1000)));
    return { stdExpMs, isStdGrace, graceHoursLeft };
}

function _scrollTopSmooth() {
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" });
}

function _safeFormatDate(iso) {
    if (!iso) return "";
    const formatted = formatDate(iso);
    if (!formatted || formatted === "—" || /invalid/i.test(String(formatted))) return "";
    return String(formatted);
}

const _ICONS = {
    copy: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`,
    bolt: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`,
    share: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>`,
    plus: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`,
    phone: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="2" width="14" height="20" rx="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>`,
    desktop: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>`,
    refresh: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>`,
    trash: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>`,
    alert: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
    wrench: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>`,
    send: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>`,
    message: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
    card: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/></svg>`,
    more: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/></svg>`,
    file: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,
    book: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>`,
    shield: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
    globe: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3.6 9h16.8M3.6 15h16.8"/><path d="M11.5 3a15 15 0 0 0 0 18M12.5 3a15 15 0 0 1 0 18"/></svg>`,
    chevronRight: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>`,
    chevronDown: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>`,
};

function _detectPlatform() {
    const ua = navigator.userAgent || "";
    const isMac = /(Macintosh)|(MacIntel)/i.test(ua);
    const isTouch = ("ontouchend" in document) || (navigator.maxTouchPoints > 0);
    if (/(iPad|iPhone|iPod)/i.test(ua) || (isMac && isTouch)) return "ios";
    if (/Android/i.test(ua)) return "android";
    if (/Windows/i.test(ua)) return "windows";
    if (isMac) return "macos";
    return "ios";
}

function _wiHasActivePeriod(wi) {
    if (!wi) return false;
    if (wi.has_period || wi.active) return true;
    const status = String(wi.status || "").toUpperCase();
    return Boolean(
        ["ACTIVE", "PENDING", "EXHAUSTED"].includes(status)
        && wi.expires_at
        && new Date(wi.expires_at).getTime() > _serverNowMs()
    );
}

export async function loadAndRenderUserCabinet(root, options = {}) {
    _state.loadOptions = options;
    if (options.tab) {
        _state.tab = options.tab;
    } else if (options.resetTab) {
        _state.tab = null;
    }
    const isActive = () => (typeof options.isActive === "function" ? options.isActive() : true);
    const reqId = ++_dashReqId;
    try {
        const [data] = await Promise.all([
            api.getUserDashboard(),
            loadGuidesData().catch(() => null),
        ]);
        if (reqId !== _dashReqId || !isActive() || !root.isConnected) return;
        _state.data = data;
        _syncServerClock(data);
        const stdActive = Boolean(data.standard && data.standard.active);
        const wiActive = _wiHasActivePeriod(data.wi);
        if (!_state.tab) {
            const urlParams = new URLSearchParams(window.location.search);
            const requestedTab = urlParams.get("tab");
            if (requestedTab && ["connect", "tariffs", "profile"].includes(requestedTab)) {
                _state.tab = requestedTab;
            } else {
                _state.tab = (stdActive || wiActive) ? "connect" : "tariffs";
            }
        }
        _render(root, { loadOptions: options });
    } catch (err) {
        if (reqId !== _dashReqId || !isActive() || !root.isConnected) return;
        root.innerHTML = `
            <section class="card card-center">
                <div class="card-title">Не удалось загрузить кабинет</div>
                <p class="muted">${escapeHtml(err.message || "Попробуйте обновить экран")}</p>
                <button id="retryDashBtn" type="button" class="btn btn-primary btn-sm">Повторить</button>
            </section>
        `;
        const retry = root.querySelector("#retryDashBtn");
        if (retry) retry.addEventListener("click", () => loadAndRenderUserCabinet(root, options));
    }
}

function _getProfileMeta() {
    const tgUser = (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initDataUnsafe && window.Telegram.WebApp.initDataUnsafe.user) || {};
    const rawName = [tgUser.first_name, tgUser.last_name].filter(Boolean).join(" ") || (tgUser.username ? `@${tgUser.username}` : "");
    const displayName = rawName || "Профиль";
    const initial = String(displayName.replace(/^@/, "").trim().charAt(0) || "П").toUpperCase();
    const photoUrl = tgUser.photo_url || "";
    return {
        displayName,
        hasName: Boolean(rawName),
        username: tgUser.username ? `@${tgUser.username}` : "",
        userId: tgUser.id || "",
        initial,
        photoUrl,
    };
}

function _renderAvatarInner(meta) {
    const fallback = `<span>${escapeHtml(meta.initial)}</span>`;
    if (meta.photoUrl && !_state.avatarFailed) {
        return `${fallback}<img class="avatar-img" src="${escapeHtml(meta.photoUrl)}" alt="">`;
    }
    return fallback;
}

function _bindAvatarImg(container, key) {
    const img = container && container.querySelector("img.avatar-img");
    if (!img) return;
    img.addEventListener("load", () => img.classList.add("is-loaded"));
    img.addEventListener("error", () => {
        _state.avatarFailed = true;
        img.remove();
        if (container.dataset) container.dataset.renderedKey = key;
    });
}

function _syncTopbarProfile(d, root) {
    const pill = document.getElementById("profilePillBtn");
    const avatarEl = document.getElementById("profileAvatar");
    const balEl = document.getElementById("profileBalanceText");
    if (!pill) return;

    const bal = d.balance || { total: 0 };
    const meta = _getProfileMeta();
    const avatarKey = meta.photoUrl && !_state.avatarFailed ? meta.photoUrl : meta.initial;
    if (avatarEl && avatarEl.dataset.renderedKey !== avatarKey) {
        avatarEl.dataset.renderedKey = avatarKey;
        avatarEl.innerHTML = _renderAvatarInner(meta);
        _bindAvatarImg(avatarEl, meta.initial);
    }
    if (balEl) balEl.textContent = `${bal.total ?? 0} ₽`;
    pill.classList.toggle("active", _state.tab === "profile");
    pill.setAttribute("aria-label", `Профиль: ${meta.displayName}`);
    if (_state.tab === "profile") pill.setAttribute("aria-current", "page");
    else pill.removeAttribute("aria-current");

    pill.onclick = () => {
        if (_state.tab === "profile") return;
        _state.profileReturnTab = ["connect", "tariffs", "more"].includes(_state.tab) ? _state.tab : "connect";
        _state.tab = "profile";
        _render(root, { preserveScroll: false });
        _scrollTopSmooth();
    };
}

function _setCabinetBackButton(onBack) {
    setPageBackButton(onBack);
}

function _render(root, { preserveScroll = true, loadOptions = _state.loadOptions } = {}) {
    const d = _state.data;
    if (!d) return;
    const prevGuideOpen = Boolean(root.querySelector("#connectGuide")?.open);
    const prevScrollY = preserveScroll ? window.scrollY : 0;
    const stdActive = Boolean(d.standard && d.standard.active);
    const wiActive = _wiHasActivePeriod(d.wi);
    const hasAny = stdActive || wiActive;

    if (wiActive && !(d.wi && String(d.wi.status || "").toUpperCase() === "EXHAUSTED")) {
        document.body.dataset.tier = "white_internet";
    } else if (stdActive) {
        document.body.dataset.tier = "standard";
    } else {
        delete document.body.dataset.tier;
    }

    const navTabs = [
        { id: "connect", label: "Доступ", icon: _ICONS.shield },
        { id: "tariffs", label: "Тарифы", icon: _ICONS.card },
        { id: "more", label: "Ещё", icon: _ICONS.more },
    ];
    const validTabs = ["connect", "tariffs", "more", "profile", "guides"];

    if (!validTabs.includes(_state.tab)) {
        _state.tab = hasAny ? "connect" : "tariffs";
    }

    _syncTopbarProfile(d, root);

    root.innerHTML = `
        <div id="userTabBody" class="app-main"></div>
        <nav class="bottom-nav" aria-label="Разделы кабинета">
            ${navTabs.map((t) => `
                <button type="button" class="nav-item ${_state.tab === t.id ? "active" : ""}" ${_state.tab === t.id ? 'aria-current="page"' : ""} data-utab="${t.id}">
                    ${t.icon}
                    <span>${escapeHtml(t.label)}</span>
                </button>
            `).join("")}
        </nav>
    `;

    root.querySelectorAll("[data-utab]").forEach((b) => {
        b.addEventListener("click", () => {
            if (_state.tab === b.dataset.utab) return;
            _state.tab = b.dataset.utab;
            _render(root, { preserveScroll: false });
            _scrollTopSmooth();
        });
    });

    const body = root.querySelector("#userTabBody");
    const reload = (extraOpts = {}) => loadAndRenderUserCabinet(root, {
        ...(loadOptions || {}),
        resetTab: false,
        ...extraOpts,
    });

    if (_state.tab === "connect") {
        _setCabinetBackButton(null);
        _renderConnectTab(body, d, reload, root);
    } else if (_state.tab === "tariffs") {
        _setCabinetBackButton(null);
        _renderTariffsTab(body, d, reload);
    } else if (_state.tab === "more") {
        _setCabinetBackButton(null);
        _renderMoreTab(body, d, root);
    } else if (_state.tab === "guides") {
        const goBack = () => {
            body.querySelectorAll("video").forEach((v) => {
                try {
                    if (!v.paused) v.pause();
                } catch {
                    // Non-critical
                }
            });
            _setCabinetBackButton(null);
            _state.tab = _state.guidesReturnTab || "connect";
            _render(root, { preserveScroll: false });
            requestAnimationFrame(() => window.scrollTo(0, _state.guidesReturnScrollY || 0));
        };
        _setCabinetBackButton(goBack);
        renderUserGuidesView(body, _state, d, goBack);
    } else {
        const goBackProfile = () => {
            _setCabinetBackButton(null);
            _state.tab = _state.profileReturnTab || "connect";
            _render(root, { preserveScroll: false });
        };
        _setCabinetBackButton(goBackProfile);
        _renderProfileTab(body, d, reload, goBackProfile);
    }

    if (prevGuideOpen) {
        const nextGuide = root.querySelector("#connectGuide");
        if (nextGuide) nextGuide.open = true;
    }
    if (preserveScroll && prevScrollY > 0 && _state.tab !== "profile" && _state.tab !== "guides") {
        window.scrollTo(0, prevScrollY);
    }
}

function _buildGuideHtml(subUrl, backupSubUrl, isActive) {
    const platformLabels = { ios: "iOS", android: "Android", windows: "Windows", macos: "macOS" };
    const platformIcons = {
        ios: `<svg class="pill-btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="6" y="2" width="12" height="20" rx="2.5"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>`,
        android: `<svg class="pill-btn-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6 18c0 .55.45 1 1 1h1v3.5c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5V19h2v3.5c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5V19h1c.55 0 1-.45 1-1V8H6v10zM3.5 8C2.67 8 2 8.67 2 9.5v7c0 .83.67 1.5 1.5 1.5S5 17.33 5 16.5v-7C5 8.67 4.33 8 3.5 8zm17 0c-.83 0-1.5.67-1.5 1.5v7c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5v-7c0-.83-.67-1.5-1.5-1.5zm-4.97-4.84l1.3-1.3c.2-.2.2-.51 0-.71-.2-.2-.51-.2-.71 0l-1.48 1.48C13.85 2.23 12.95 2 12 2c-.96 0-1.86.23-2.66.63L7.85 1.15c-.2-.2-.51-.2-.71 0-.2.2-.2.51 0 .71l1.31 1.31C6.97 4.26 6 5.92 6 7.8h12c0-1.88-.97-3.54-2.47-4.64zM9 6c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1zm6 0c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1z"/></svg>`,
        windows: `<svg class="pill-btn-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M3 5.45L10.5 4.36V11.27H3V5.45ZM10.5 12.73V19.64L3 18.55V12.73H10.5ZM12 4.14L21 2.85V11.27H12V4.14ZM21 12.73V21.15L12 19.86V12.73H21Z"/></svg>`,
        macos: `<svg class="pill-btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="2"/><line x1="2" y1="20" x2="22" y2="20"/></svg>`,
    };
    const clients = getPlatformApps(_state.platform);

    return `
        <details class="guide-details guide-details-prominent" id="connectGuide">
            <summary class="guide-summary">
                <span>Скачать приложение (${platformLabels[_state.platform] || "iOS"})</span>
                <span class="disclosure-pill"><span>Открыть</span>${_ICONS.chevronDown}</span>
            </summary>
            <div class="guide-body">
                <div class="filter-pills" role="group" aria-label="Выбор платформы">
                    ${Object.entries(platformLabels).map(([key, label]) => `
                        <button type="button" class="pill-btn pill-btn-with-icon ${_state.platform === key ? "active" : ""}" aria-pressed="${_state.platform === key ? "true" : "false"}" data-platform="${key}">${platformIcons[key] || ""}<span>${label}</span></button>
                    `).join("")}
                </div>
                <div class="field" style="gap:6px;">
                    ${clients.map((c) => `
                        <div class="client-row">
                            <div>
                                <div style="font-weight:600; font-size:13px;">${escapeHtml(c.name)}</div>
                                <div class="muted" style="font-size:11px;">${escapeHtml(c.sub)}</div>
                            </div>
                            <button type="button" class="btn btn-sm" data-download="${escapeHtml(c.url)}">Скачать</button>
                        </div>
                    `).join("")}
                </div>
                ${isActive && subUrl ? `
                    <div class="field">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span style="font-size:12px; color:var(--text-secondary);">Основная ссылка подключения</span>
                            <button id="copyInlineSubBtn" type="button" class="btn btn-sm" style="padding:4px 10px; font-size:12px;">Скопировать</button>
                        </div>
                        <div class="sub-url-box">${escapeHtml(subUrl)}</div>
                    </div>
                ` : ""}
                ${isActive && backupSubUrl ? `
                    <div class="field" style="margin-top:8px;">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span style="font-size:12px; color:var(--text-secondary);">Резервная ссылка (прямая)</span>
                            <button id="copyBackupSubBtn" type="button" class="btn btn-sm" style="padding:4px 10px; font-size:12px;">Скопировать</button>
                        </div>
                        <div class="sub-url-box" style="font-size:12px;">${escapeHtml(backupSubUrl)}</div>
                        <span class="muted" style="font-size:12px; margin-top:4px;">Используйте прямое подключение, если по основной ссылке есть задержки</span>
                    </div>
                ` : ""}
            </div>
        </details>
    `;
}

function _bindGuideEvents(container, root) {
    container.querySelectorAll("[data-platform]").forEach((btn) => {
        btn.addEventListener("click", () => {
            _state.platform = btn.dataset.platform;
            const guideWasOpen = Boolean(container.querySelector("#connectGuide")?.open);
            _render(root);
            if (guideWasOpen) {
                const nextGuide = root.querySelector("#connectGuide");
                if (nextGuide) nextGuide.open = true;
            }
            root.querySelector(`[data-platform="${_state.platform}"]`)?.focus({ preventScroll: true });
        });
    });
    container.querySelectorAll("[data-download]").forEach((btn) => {
        btn.addEventListener("click", () => openExternalLink(btn.dataset.download));
    });
}

function _renderConnectTab(body, d, reload, root) {
    if (d.financial_hold) {
        body.innerHTML = `
            <section class="hero-section">
                <div class="hero-title-group">
                    <span class="hero-status err">Ограничение доступа</span>
                    <h1 class="hero-headline-lg" style="color:var(--accent-red);">Обслуживание приостановлено</h1>
                    <p class="hero-sub">Доступ к сервису временно ограничен администратором. Если у вас возникли вопросы по оплате или настройке, напишите нам в поддержку.</p>
                </div>
                ${d.support && d.support.support_url ? `
                    <button id="holdContactSupportBtn" type="button" class="btn btn-primary btn-lg">Написать в поддержку</button>
                ` : ""}
            </section>
            ${(d.devices || []).length ? _buildDevicesSectionHtml(d) : ""}
        `;
        const supBtn = body.querySelector("#holdContactSupportBtn");
        if (supBtn) {
            supBtn.addEventListener("click", () => openExternalLink(d.support.support_url));
        }
        _bindDevicesEvents(body, d, reload);
        return;
    }

    const std = d.standard || {};
    const wi = d.wi || {};
    const wiHasPeriod = _wiHasActivePeriod(wi);
    const isAnyActive = Boolean(std.active || wiHasPeriod);
    const subUrl = d.subscription_url || "";
    const backupSubUrl = d.subscription_backup_url || "";
    const desktopFirst = _state.platform === "windows" || _state.platform === "macos";

    if (!isAnyActive) {
        body.innerHTML = `
            <section class="hero-section">
                <div class="hero-title-group">
                    <span class="hero-status muted">Не подключено</span>
                    <h1 class="hero-headline-lg">Нет активных подписок</h1>
                    <p class="hero-sub">Оформите «Стандарт» или «Белый интернет»: ссылка подключения активируется сразу после оплаты</p>
                </div>
                <button id="goTariffsBtn" type="button" class="btn btn-primary btn-lg">Перейти к тарифам</button>
                ${_buildGuideHtml(subUrl, backupSubUrl, false)}
                <button id="openGuidesFromInactiveBtn" type="button" class="guides-trigger-card">
                    <span class="settings-left">
                        <span class="guides-trigger-icon" aria-hidden="true">${_ICONS.book}</span>
                        <span>
                            <span style="display:block; font-weight:600; font-size:14px; color:var(--text-primary);">Пошаговая инструкция</span>
                            <span class="muted" style="font-size:12px;">iOS, Android, ПК / Mac, видео и частые вопросы</span>
                        </span>
                    </span>
                    <span class="node-chevron" style="display:inline-flex;">${_ICONS.chevronRight}</span>
                </button>
            </section>
            ${(d.devices || []).length ? _buildDevicesSectionHtml(d) : ""}
        `;
        body.querySelector("#goTariffsBtn").addEventListener("click", () => {
            _state.tab = "tariffs";
            _render(root, { preserveScroll: false });
            _scrollTopSmooth();
        });
        body.querySelector("#openGuidesFromInactiveBtn")?.addEventListener("click", () => {
            _state.guidesReturnTab = "connect";
            _state.guidesReturnScrollY = window.scrollY;
            _state.tab = "guides";
            _render(root, { preserveScroll: false });
            window.scrollTo(0, 0);
        });
        _bindGuideEvents(body, root);
        _bindDevicesEvents(body, d, reload);
        return;
    }

    const { isStdGrace, graceHoursLeft } = _getStdGraceInfo(d, std);

    const wiStatus = String(wi.status || "").toUpperCase();
    const wiPending = Boolean(wiHasPeriod && wiStatus === "PENDING");
    const wiLimitBytes = Number(wi.traffic_limit_bytes || 0);
    const wiUsedBytes = Number(wi.traffic_used_bytes || 0);
    const wiRemainingBytes = Math.max(0, wiLimitBytes - wiUsedBytes);
    const wiRemainingGb = formatBytesGb(wiRemainingBytes);
    const wiRemainingPct = wiLimitBytes > 0 ? Math.min(100, Math.max(0, Math.round((wiRemainingBytes / wiLimitBytes) * 100))) : 0;
    const wiExhausted = Boolean(wiHasPeriod && (wiStatus === "EXHAUSTED" || (wiLimitBytes > 0 && wiUsedBytes >= wiLimitBytes)));

    const _shortDate = (iso) => {
        const dStr = _safeFormatDate(iso);
        return dStr ? dStr.split(",")[0].trim() : "";
    };
    const stdShortDate = _shortDate(std.expires_at);
    const wiShortDate = _shortDate(wi.expires_at);

    const stdTileHtml = std.active
        ? `
            <button type="button" class="channel-tile ${isStdGrace ? "is-warn" : "is-active"}" data-jump-tariffs="1">
                <div class="channel-tile-head">
                    <span class="channel-name">Стандарт</span>
                    <span class="channel-indicator"></span>
                </div>
                <div class="channel-metric">∞ <span class="channel-metric-unit">ГБ</span></div>
                <div class="channel-foot">
                    <div class="progress-track" aria-hidden="true">
                        <div class="progress-fill" style="background:${isStdGrace ? "var(--accent-amber)" : "var(--accent-emerald)"}; transform:scaleX(1);"></div>
                    </div>
                    <div class="channel-meta">
                        <span style="white-space:nowrap;">${Number(std.device_limit || 2)} устр.</span>
                        <span style="white-space:nowrap;">${isStdGrace ? `~${graceHoursLeft} ч.` : (stdShortDate ? `до ${stdShortDate}` : "")}</span>
                    </div>
                    <span class="channel-action">Тарифы →</span>
                </div>
            </button>
        `
        : `
            <button type="button" class="channel-tile is-inactive" data-jump-tariffs="1">
                <div class="channel-tile-head">
                    <span class="channel-name">Стандарт</span>
                    <span class="channel-indicator"></span>
                </div>
                <div class="channel-metric">—</div>
                <div class="channel-foot">
                    <div class="channel-meta">
                        <span>Не подключён</span>
                        <span>Подключить</span>
                    </div>
                </div>
            </button>
        `;

    const wiTileHtml = wiHasPeriod
        ? `
            <button type="button" class="channel-tile is-wi ${wiExhausted ? "is-warn" : (wiPending ? "" : "is-active")}" data-jump-tariffs="1">
                <div class="channel-tile-head">
                    <span class="channel-name">Белый интернет</span>
                    <span class="channel-indicator"></span>
                </div>
                <div class="channel-metric">${wiPending ? `<span style="font-size:16px;">Настраивается</span>` : `${wiExhausted ? "0" : wiRemainingGb} <span class="channel-metric-unit">ГБ осталось</span>`}</div>
                <div class="channel-foot">
                    <div class="progress-track">
                        <div class="progress-fill" style="background:var(--accent-white); transform:scaleX(${wiExhausted || wiPending ? 0 : (Number(wiRemainingPct || 0) / 100).toFixed(3)});"></div>
                    </div>
                    <div class="channel-meta">
                        <span style="white-space:nowrap;">${Number(wi.device_limit || 1)} устр.</span>
                        <span style="white-space:nowrap;">${wiPending ? "Настраивается" : (wiShortDate ? `до ${wiShortDate}` : "")}</span>
                    </div>
                    <span class="channel-action">Тарифы →</span>
                </div>
            </button>
        `
        : `
            <button type="button" class="channel-tile is-wi is-inactive" data-jump-tariffs="1">
                <div class="channel-tile-head">
                    <span class="channel-name">Белый интернет</span>
                    <span class="channel-indicator"></span>
                </div>
                <div class="channel-metric">—</div>
                <div class="channel-foot">
                    <div class="channel-meta">
                        <span>Не подключён</span>
                        <span>Подключить</span>
                    </div>
                </div>
            </button>
        `;

    const isWiOnlyExhausted = Boolean(!std.active && wiExhausted);
    const isWiOnlyPending = Boolean(!std.active && wiPending);
    const hasBothActive = Boolean(std.active && wiHasPeriod && !wiExhausted && !wiPending);

    body.innerHTML = `
        <section class="hero-section">
            <div class="hero-title-group">
                <span class="hero-status ${isStdGrace || isWiOnlyExhausted ? "warn" : (isWiOnlyPending ? "muted" : "")}">${isStdGrace ? `Льготный период · ~${graceHoursLeft} ч.` : (isWiOnlyExhausted ? "Белый интернет" : (isWiOnlyPending ? "Белый интернет" : "Подписка активна"))}</span>
                <h1 class="hero-headline-lg">${isStdGrace ? "Подписка закончилась" : (isWiOnlyExhausted ? "Трафик закончился" : (isWiOnlyPending ? "Настраивается" : "Подключение готово"))}</h1>
                <p class="hero-sub">${isStdGrace
                    ? `Основной срок подписки закончился, но доступ сохранён ещё на ~${graceHoursLeft} ч., чтобы вы успели продлить подписку без отключения.`
                    : (isWiOnlyExhausted
                        ? "Пополните пакет трафика или выберите тариф на вкладке «Тарифы»"
                        : (isWiOnlyPending
                            ? "Подписка активируется автоматически. Доступ появится сразу после завершения настройки."
                            : (hasBothActive ? "Стандарт и Белый интернет работают по одной ссылке" : "Выберите приложение ниже или скопируйте ссылку")))}</p>
            </div>

            ${isStdGrace ? `
                <button id="graceRenewBtn" type="button" class="btn btn-primary btn-lg">Продлить подписку</button>
            ` : ""}

            <div class="channels-grid">
                ${stdTileHtml}
                ${wiTileHtml}
            </div>

            <div class="connect-flow-step">
                <div class="connect-flow-head">
                    <span class="connect-step-badge" aria-hidden="true">1</span>
                    <h2 class="connect-step-title">Установите приложение</h2>
                </div>
                <p class="connect-step-sub">Скачайте клиент для вашего устройства или откройте пошаговую видеоинструкцию</p>

                ${_buildGuideHtml(subUrl, backupSubUrl, true)}

                <button id="openGuidesFromConnectBtn" type="button" class="guides-trigger-card">
                    <span class="settings-left">
                        <span class="guides-trigger-icon" aria-hidden="true">${_ICONS.book}</span>
                        <span>
                            <span style="display:block; font-weight:600; font-size:14px; color:var(--text-primary);">Пошаговая инструкция</span>
                            <span style="display:block; font-size:12px; color:var(--text-secondary);">iOS, Android, ПК / Mac, видео и частые вопросы</span>
                        </span>
                    </span>
                    <span class="node-chevron" style="display:inline-flex; flex-shrink:0;">${_ICONS.chevronRight}</span>
                </button>
            </div>

            <div class="connect-flow-step">
                <div class="connect-flow-head">
                    <span class="connect-step-badge" aria-hidden="true">2</span>
                    <h2 class="connect-step-title">Подключите профиль</h2>
                </div>
                <p class="connect-step-sub">После установки нажмите кнопку ниже, и профиль добавится автоматически</p>

                <div class="connect-dock">
                    ${desktopFirst ? `
                        <button type="button" data-open-client="happ" class="btn btn-primary btn-lg">${_ICONS.bolt} Подключить в Happ</button>
                        <button type="button" data-open-client="incy" class="btn btn-secondary" style="width:100%;">${_ICONS.globe} Подключить в INCY</button>
                    ` : `
                        <button type="button" data-open-client="incy" class="btn btn-primary btn-lg">${_ICONS.globe} Подключить в INCY</button>
                        <button type="button" data-open-client="happ" class="btn btn-secondary" style="width:100%;">${_ICONS.bolt} Подключить в Happ</button>
                    `}
                    ${subUrl ? `
                        <button id="copyUrlStripBtn" type="button" class="sub-link-strip" title="Нажмите, чтобы скопировать">
                            <span class="sub-link-text">${escapeHtml(subUrl)}</span>
                            <span style="display:inline-flex; align-items:center; gap:6px; flex-shrink:0; color:var(--text-primary); font-size:12px; font-weight:500;">${_ICONS.copy}<span id="copyUrlStripLabel">Копировать</span></span>
                        </button>
                    ` : ""}
                </div>
            </div>

            <div class="troubleshoot-box" style="margin-top:0;">
                <div class="troubleshoot-title">
                    <span class="status-dot warn" style="width:6px; height:6px;"></span>
                    <span>Если нет подключения:</span>
                </div>
                <span>• Выберите другую локацию в списке приложения</span>
                <span>• Обновите подписку (потяните список серверов вниз)</span>
                ${d.support && d.support.support_url ? `
                    <button id="connectSupportBtn" type="button" class="btn btn-sm" style="margin-top:6px; width:100%;">${_ICONS.message} Написать в поддержку</button>
                ` : `<span>• Напишите в поддержку (раздел «Ещё»), если ничего не изменилось</span>`}
            </div>
        </section>

        ${_buildDevicesSectionHtml(d)}
    `;

    body.querySelectorAll("[data-jump-tariffs]").forEach((btn) => {
        btn.addEventListener("click", () => {
            _state.tab = "tariffs";
            _render(root, { preserveScroll: false });
            _scrollTopSmooth();
        });
    });
    body.querySelectorAll("[data-open-client]").forEach((btn) => {
        btn.addEventListener("click", () => {
            const client = btn.dataset.openClient;
            if (!subUrl) return;
            const targetUrl = `${subUrl}${subUrl.includes("?") ? "&" : "?"}open=${encodeURIComponent(client)}`;
            openExternalLink(targetUrl);
        });
    });
    const stripBtn = body.querySelector("#copyUrlStripBtn");
    if (stripBtn) {
        stripBtn.addEventListener("click", () => {
            copyText(subUrl, "Ссылка скопирована");
            const lbl = stripBtn.querySelector("#copyUrlStripLabel");
            if (lbl) {
                lbl.textContent = "Скопировано";
                setTimeout(() => { lbl.textContent = "Копировать"; }, 1800);
            }
        });
    }
    const copyInlineBtn = body.querySelector("#copyInlineSubBtn");
    if (copyInlineBtn) {
        copyInlineBtn.addEventListener("click", () => copyText(subUrl, "Основная ссылка скопирована"));
    }
    const copyBackupBtn = body.querySelector("#copyBackupSubBtn");
    if (copyBackupBtn) {
        copyBackupBtn.addEventListener("click", () => copyText(backupSubUrl, "Резервная ссылка скопирована"));
    }
    const graceBtn = body.querySelector("#graceRenewBtn");
    if (graceBtn) {
        graceBtn.addEventListener("click", () => {
            _state.tab = "tariffs";
            _render(root, { preserveScroll: false });
            _scrollTopSmooth();
        });
    }
    _bindGuideEvents(body, root);
    _bindDevicesEvents(body, d, reload);
    body.querySelector("#openGuidesFromConnectBtn")?.addEventListener("click", () => {
        _state.guidesReturnTab = "connect";
        _state.guidesReturnScrollY = window.scrollY;
        _state.tab = "guides";
        _render(root, { preserveScroll: false });
        window.scrollTo(0, 0);
    });
    body.querySelector("#connectSupportBtn")?.addEventListener("click", () => {
        if (d.support && d.support.support_url) openExternalLink(d.support.support_url);
    });
}

function _renderTariffsTab(body, d, reload) {
    const bal = d.balance || { real: 0, bonus: 0, total: 0 };
    const std = d.standard || {};
    const wi = d.wi || {};
    const pricing = d.pricing || {};
    const minTopup = pricing.min_topup_rub || 20;

    const stdLimit = Number(std.device_limit || 2);
    const stdMin7 = _calcStdPrice(d, 2, 7);
    const stdMin30 = _calcStdPrice(d, 2, 30);
    const { isStdGrace, graceHoursLeft } = _getStdGraceInfo(d, std);
    const stdExpStr = _safeFormatDate(std.expires_at);
    const wiExpStr = _safeFormatDate(wi.expires_at);

    const wiBase = Number(pricing.wi_base_price_rub || 250);
    const wiUsedGb = formatBytesGb(wi.traffic_used_bytes);
    const wiTotalGb = formatBytesGb(wi.traffic_limit_bytes);
    const wiPct = wi.traffic_limit_bytes > 0
        ? Math.min(100, Math.round((wi.traffic_used_bytes / wi.traffic_limit_bytes) * 100))
        : 0;
    const wiCurLimit = Number(wi.device_limit || 1);
    const wiMaxDevices = pricing.wi_max_devices || 3;
    const wiStatus = String(wi.status || "").toUpperCase();
    const wiPending = wiStatus === "PENDING";

    body.innerHTML = `
        <section class="hero-title-group">
            <h1 class="hero-headline-lg">Тарифы</h1>
            <p class="hero-sub">Две независимые подписки на одну ссылку: выберите нужную или подключите обе</p>
        </section>

        ${d.financial_hold ? `
            <div class="notice-box notice-warn" style="margin-bottom:8px;">
                <span style="display:flex; align-items:center; gap:8px;">${_ICONS.alert}<span>Оплата и изменение подписок временно недоступны: профиль находится на проверке. Свяжитесь с поддержкой.</span></span>
            </div>
        ` : (d.payments_maintenance ? `
            <div class="notice-box notice-warn" style="margin-bottom:8px;">
                <span style="display:flex; align-items:center; gap:8px;">${_ICONS.wrench}<span>Приём платежей временно приостановлен для проведения технических работ. Действующие подписки работают в штатном режиме.</span></span>
            </div>
        ` : "")}

        <section class="balance-card">
            <div>
                <div class="muted" style="font-size:12px;">Баланс аккаунта</div>
                <div class="balance-value" style="white-space:nowrap;">${bal.total}&nbsp;₽</div>
                <div class="balance-sub">Основной: <span style="white-space:nowrap;">${bal.real}&nbsp;₽</span> · Бонусы: <span style="white-space:nowrap;">${bal.bonus}&nbsp;₽</span></div>
            </div>
            <button id="topupBtn" type="button" class="btn btn-secondary btn-sm">${_ICONS.plus} Пополнить</button>
        </section>

        <section class="plan-card ${std.active && !isStdGrace ? "plan-active" : ""}" ${isStdGrace ? 'style="border-color: rgba(245, 158, 11, 0.4);"' : ""}>
            <div class="row">
                <div>
                    <h2 class="card-title">Стандарт</h2>
                    <div class="muted" style="font-size:12px;">Для дома, Wi-Fi и повседневного использования</div>
                </div>
                <span class="chip ${isStdGrace ? "chip-warn" : (std.active ? "chip-ok" : "chip-muted")}">
                    ${isStdGrace ? `Продлить · ~${graceHoursLeft} ч.` : (std.active ? (stdExpStr ? `до ${stdExpStr}` : "Активен") : "Не подключён")}
                </span>
            </div>
            <div class="plan-features">
                <span>• Без ограничений по трафику, от 2 до 10 устройств</span>
                <span>• Работает через приложение по единой ссылке подключения</span>
            </div>
            ${std.active ? `
                <label class="toggle-row" for="stdAutoRenewChk">
                    <span class="toggle-label">Автопродление с баланса (${stdLimit} устр.)</span>
                    <span class="switch">
                        <input id="stdAutoRenewChk" type="checkbox" ${std.auto_renew_enabled ? "checked" : ""}>
                        <span class="switch-track"></span>
                    </span>
                </label>
                ${isStdGrace ? `
                    <button id="stdRenewBtn" type="button" class="btn btn-primary btn-lg">Продлить подписку</button>
                ` : `
                    <div class="connect-pair">
                        <button id="stdRenewBtn" type="button" class="btn btn-primary">Продлить</button>
                        <button id="stdDevicesBtn" type="button" class="btn">Устройств: ${stdLimit} · Изменить</button>
                    </div>
                `}
            ` : `
                <div class="plan-meta-row">
                    <span class="plan-price-tag">${stdMin7 != null ? `от ${stdMin7} ₽ / 7 дн.` : "7 дн."} · ${stdMin30 != null ? `от ${stdMin30} ₽ / 30 дн.` : "30 дн."}</span>
                    <button id="stdBuyBtn" type="button" class="btn btn-primary">Выбрать</button>
                </div>
            `}
        </section>

        <section class="plan-card plan-wi ${wi.active && !wiPending ? "plan-active" : ""}">
            <div class="row">
                <div>
                    <h2 class="card-title">Белый интернет</h2>
                    <div class="muted" style="font-size:12px;">Для мобильной сети при жёстких ограничениях доступа</div>
                </div>
                <span class="chip ${
                    wiStatus === "EXHAUSTED"
                        ? "chip-warn"
                        : wiPending
                            ? "chip-muted"
                            : wi.active
                                ? "chip-wi"
                                : "chip-muted"
                }">
                    ${
                        wiStatus === "EXHAUSTED"
                            ? "Трафик исчерпан"
                            : wiPending
                                ? "Активация..."
                                : wi.active
                                    ? (wiExpStr ? `до ${wiExpStr}` : "Активен")
                                    : wiStatus === "EXPIRED"
                                        ? "Истёк"
                                        : "Не подключён"
                    }
                </span>
            </div>
            <div class="plan-features">
                <span>• Работает отдельно от «Стандарта» или вместе с ним</span>
                <span>• 50 ГБ трафика на каждое устройство, срок 30 дней</span>
            </div>
            ${_wiHasActivePeriod(wi) ? `
                <div class="field">
                    <div class="row muted" style="font-size:12px;">
                        <span class="tabular">Трафик: <strong style="color:var(--text-primary);">${wiUsedGb} / ${wiTotalGb} ГБ</strong></span>
                        <span class="tabular">Устройств: <strong style="color:var(--text-primary);">${wiCurLimit}</strong></span>
                    </div>
                    <div class="progress-track"><div class="progress-fill" style="transform:scaleX(${(wiPct / 100).toFixed(3)});"></div></div>
                </div>
                <label class="toggle-row" for="wiAutoRenewChk">
                    <span class="toggle-label">Автопродление с баланса</span>
                    <span class="switch switch-wi">
                        <input id="wiAutoRenewChk" type="checkbox" ${wi.auto_renew_enabled ? "checked" : ""}>
                        <span class="switch-track"></span>
                    </span>
                </label>
                <div class="connect-pair">
                    <button id="wiRenewBtn" type="button" class="btn btn-primary" ${wiPending ? "disabled" : ""}>Продлить +30 дн.</button>
                    <button id="wiManageBtn" type="button" class="btn" ${wiPending ? "disabled" : ""}>Докупить ГБ / Слоты</button>
                </div>
                ${wiPending ? `<div class="muted" style="font-size:12px;">Доступно после завершения активации</div>` : ""}
            ` : `
                <div class="plan-meta-row">
                    <span class="plan-price-tag">от ${wiBase} ₽ / 30 дн.</span>
                    <button id="wiBuyBtn" type="button" class="btn btn-primary">Выбрать</button>
                </div>
            `}
        </section>
    `;

    const checkCanPay = () => {
        if (d.financial_hold) {
            showToast("Профиль на проверке, свяжитесь с поддержкой", "error");
            return false;
        }
        if (d.payments_maintenance) {
            showToast("Приём платежей временно приостановлен (техработы)", "error");
            return false;
        }
        return true;
    };

    const modalHelpers = {
        getStdGraceInfo: _getStdGraceInfo,
        safeFormatDate: _safeFormatDate,
        serverNowMs: _serverNowMs,
        onTabSwitch: (tab) => { _state.tab = tab; },
    };

    body.querySelector("#topupBtn").addEventListener("click", () => {
        if (checkCanPay()) _openTopupModal(minTopup, reload, modalHelpers.onTabSwitch);
    });

    const stdBuyBtn = body.querySelector("#stdBuyBtn");
    if (stdBuyBtn) stdBuyBtn.addEventListener("click", () => {
        if (checkCanPay()) _openStandardPlanModal(d, "buy", reload, modalHelpers);
    });

    const stdRenewBtn = body.querySelector("#stdRenewBtn");
    if (stdRenewBtn) stdRenewBtn.addEventListener("click", () => {
        if (checkCanPay()) _openStandardPlanModal(d, "renew", reload, modalHelpers);
    });

    const stdDevBtn = body.querySelector("#stdDevicesBtn");
    if (stdDevBtn) stdDevBtn.addEventListener("click", () => {
        if (checkCanPay()) _openStandardDevicesModal(d, reload, modalHelpers);
    });

    const stdAuto = body.querySelector("#stdAutoRenewChk");
    if (stdAuto) {
        stdAuto.addEventListener("change", async () => {
            const nextVal = stdAuto.checked;
            stdAuto.disabled = true;
            try {
                await api.setAutoRenew("standard", nextVal);
                if (_state.data && _state.data.standard) {
                    _state.data.standard.auto_renew_enabled = nextVal;
                }
                showToast(nextVal ? "Автопродление включено" : "Автопродление выключено");
            } catch (err) {
                stdAuto.checked = !nextVal;
                if (_state.data && _state.data.standard) {
                    _state.data.standard.auto_renew_enabled = !nextVal;
                }
                showToast(err.message || "Ошибка настройки автопродления", "error");
            } finally {
                stdAuto.disabled = false;
            }
        });
    }

    const wiBuyBtn = body.querySelector("#wiBuyBtn");
    if (wiBuyBtn) wiBuyBtn.addEventListener("click", () => {
        if (checkCanPay()) _openWiPurchaseModal(d, "buy", reload, modalHelpers);
    });

    const wiAuto = body.querySelector("#wiAutoRenewChk");
    if (wiAuto) {
        wiAuto.addEventListener("change", async () => {
            const nextVal = wiAuto.checked;
            wiAuto.disabled = true;
            try {
                await api.setAutoRenew("wi", nextVal);
                if (_state.data && _state.data.wi) {
                    _state.data.wi.auto_renew_enabled = nextVal;
                }
                showToast(nextVal ? "Автопродление включено" : "Автопродление выключено");
            } catch (err) {
                wiAuto.checked = !nextVal;
                if (_state.data && _state.data.wi) {
                    _state.data.wi.auto_renew_enabled = !nextVal;
                }
                showToast(err.message || "Ошибка настройки автопродления", "error");
            } finally {
                wiAuto.disabled = false;
            }
        });
    }

    const wiRenewBtn = body.querySelector("#wiRenewBtn");
    if (wiRenewBtn && !wiPending) {
        wiRenewBtn.addEventListener("click", () => {
            if (checkCanPay()) _openWiPurchaseModal(d, "renew", reload, modalHelpers);
        });
    }

    const wiManageBtn = body.querySelector("#wiManageBtn");
    if (wiManageBtn && !wiPending) {
        wiManageBtn.addEventListener("click", () => {
            if (checkCanPay()) _openWiManageModal(d, reload, modalHelpers);
        });
    }
}

function _buildDevicesSectionHtml(d) {
    const devices = [...(d.devices || [])].sort((a, b) => (a.id || 0) - (b.id || 0));
    const std = d.standard || {};
    const stdActive = Boolean(std && std.active);
    const wi = d.wi || {};
    const wiActive = _wiHasActivePeriod(wi);
    const wiLimit = Number(wi.device_limit || 1);
    const stdLimit = Number(std.device_limit || 2);
    const totalLimit = Math.max((stdActive ? stdLimit : 0) + (wiActive ? wiLimit : 0), 1);
    const isLimitReached = devices.length >= totalLimit;
    const stdAssignedCount = stdActive ? devices.filter((dev) => dev.is_std_assigned).length : 0;
    const biAssignedCount = wiActive ? devices.filter((dev) => dev.is_bi_assigned).length : 0;
    const isWiExhausted = Boolean(
        wiActive && (
            String(wi.status || "").toUpperCase() === "EXHAUSTED"
            || (wi.traffic_limit_bytes > 0 && wi.traffic_used_bytes >= wi.traffic_limit_bytes)
        )
    );
    const needStdToggles = Boolean(
        stdActive && (devices.length > stdLimit || stdAssignedCount < devices.length || (wiActive && devices.length > 1))
    );
    const needBiToggles = Boolean(
        wiActive && (devices.length > wiLimit || biAssignedCount < devices.length || (stdActive && devices.length > 1))
    );

    const rowsHtml = !devices.length
        ? `
            <div class="card card-center" style="padding:24px 16px;">
                <div style="color:var(--text-secondary);">${_ICONS.phone}</div>
                <div style="font-weight:600; font-size:14px;">Устройств пока нет</div>
                <p class="muted" style="font-size:12px;">
                    Нажмите «Подключить в INCY» или «Подключить в Happ» выше, чтобы привязать устройство
                </p>
            </div>
        `
        : `<div class="list-group">${devices.map((dev, idx) => {
            const label = dev.device_name || dev.platform || "Устройство";
            const rawHwid = String(dev.hwid || "");
            const shortHwid = rawHwid.length > 14 ? `${rawHwid.slice(0, 6)}…${rawHwid.slice(-4)}` : rawHwid;
            const osText = [dev.platform, dev.os_version].filter(Boolean).join(" ");
            const meta = [osText, dev.app_version].filter(Boolean).join(" · ");
            const isDesktop = /win|mac|linux|desktop|amd64|x86_64/i.test(`${label} ${meta}`);
            const devIcon = isDesktop ? _ICONS.desktop : _ICONS.phone;
            const isStd = Boolean(stdActive && dev.is_std_assigned);
            const isBi = Boolean(wiActive && dev.is_bi_assigned);

            const leftMeta = meta || shortHwid;
            const dateLabel = _safeFormatDate(dev.last_seen_at);
            const metaLine = [leftMeta, dateLabel].filter(Boolean).join(" · ");

            let stdRowHtml = "";
            if (needStdToggles) {
                const stdInputId = `devStdSwitch_${idx}`;
                stdRowHtml = `
                    <label class="device-bi-bar" for="${stdInputId}">
                        <span style="color:${isStd ? "var(--text-primary)" : "var(--text-secondary)"};">
                            Слот «Стандарт»
                        </span>
                        <span class="switch">
                            <input id="${stdInputId}" type="checkbox" data-std-toggle="${escapeHtml(dev.hwid)}" data-name="${escapeHtml(label)}" aria-label="Слот «Стандарт» для ${escapeHtml(label)}" ${isStd ? "checked" : ""}>
                            <span class="switch-track"></span>
                        </span>
                    </label>
                `;
            }

            let biRowHtml = "";
            if (needBiToggles) {
                const biInputId = `devBiSwitch_${idx}`;
                biRowHtml = `
                    <label class="device-bi-bar" for="${biInputId}">
                        <span style="color:${isBi ? "var(--text-primary)" : "var(--text-secondary)"};">
                            Слот «Белый интернет»
                        </span>
                        <span class="switch switch-wi">
                            <input id="${biInputId}" type="checkbox" data-bi-toggle="${escapeHtml(dev.hwid)}" data-name="${escapeHtml(label)}" aria-label="Слот «Белый интернет» для ${escapeHtml(label)}" ${isBi ? "checked" : ""}>
                            <span class="switch-track"></span>
                        </span>
                    </label>
                `;
            }

            return `
                <div class="device-item">
                    <div class="device-top">
                        <div class="device-info">
                            <div class="device-icon-box ${isBi && needBiToggles ? "is-bi" : ""}">${devIcon}</div>
                            <div style="min-width:0;">
                                <div class="device-name">${escapeHtml(label)}</div>
                                <div class="device-meta">${escapeHtml(metaLine)}</div>
                            </div>
                        </div>
                        <button type="button" class="btn btn-icon btn-danger" data-del="${escapeHtml(dev.hwid)}" data-name="${escapeHtml(label)}" aria-label="Удалить ${escapeHtml(label)}" title="Удалить">${_ICONS.trash}</button>
                    </div>
                    ${stdRowHtml}
                    ${biRowHtml}
                </div>
            `;
        }).join("")}</div>`;

    const headerChips = [];
    if (stdActive && wiActive) {
        headerChips.push(`<span class="chip chip-muted">Стандарт: ${stdAssignedCount} из ${stdLimit}</span>`);
    }
    if (wiActive) {
        if (isWiExhausted) {
            headerChips.push(`<span class="chip chip-warn">Белый интернет: 0 ГБ</span>`);
        } else if (stdActive || totalLimit !== wiLimit) {
            headerChips.push(`<span class="chip chip-wi">Белый интернет: ${biAssignedCount} из ${wiLimit}</span>`);
        }
    }
    const headerChipsHtml = headerChips.join("");

    return `
        <section class="section-block">
            <div class="section-head">
                <div class="section-title-wrap">
                    <h2 class="section-title">Устройства</h2>
                    <span class="mono-badge" style="white-space:nowrap;">${devices.length}&nbsp;/&nbsp;${totalLimit}</span>
                </div>
                <div style="display:flex; align-items:center; gap:8px; flex-shrink:0;">
                    <button type="button" class="btn btn-sm" id="refreshDevicesBtn" aria-label="Обновить список устройств">${_ICONS.refresh} Обновить</button>
                    ${devices.length > 1 ? `<button type="button" class="btn btn-sm btn-danger" id="resetAllDevicesBtn">Сбросить</button>` : ""}
                </div>
            </div>
            ${headerChipsHtml ? `<div class="devices-chips-row">${headerChipsHtml}</div>` : ""}
            ${(needStdToggles || needBiToggles) ? `
                <div class="notice-box" style="margin:0; font-size:12px;"><span>Выберите, какие тарифы активны на каждом устройстве, затем обновите список в приложении</span></div>
            ` : ""}
            ${isLimitReached ? `
                <div class="notice-box notice-warn" style="margin:0;">
                    <span>Все слоты (${devices.length} из ${totalLimit}) заняты: удалите устройство, чтобы подключить новое.</span>
                </div>
            ` : ""}
            ${rowsHtml}
        </section>
    `;
}

function _bindDevicesEvents(container, d, reload) {
    container.querySelectorAll("[data-del]").forEach((btn) => {
        btn.addEventListener("click", () => {
            const devName = btn.dataset.name || "это устройство";
            showConfirmModal({
                title: "Удалить устройство?",
                message: `«${devName}» потеряет доступ к подключению. Вы сможете добавить его снова позже.`,
                confirmText: "Удалить",
                cancelText: "Отмена",
                isDestructive: true,
                onConfirm: async () => {
                    btn.disabled = true;
                    try {
                        await api.deleteDevice(btn.dataset.del);
                        showToast("Устройство удалено");
                        await reload();
                    } catch (err) {
                        throw new Error((err && err.message) || "Не удалось удалить устройство");
                    } finally {
                        btn.disabled = false;
                    }
                },
            });
        });
    });

    const refreshBtn = container.querySelector("#refreshDevicesBtn");
    if (refreshBtn) {
        refreshBtn.addEventListener("click", async () => {
            refreshBtn.disabled = true;
            refreshBtn.style.opacity = "0.5";
            await reload();
        });
    }

    const resetBtn = container.querySelector("#resetAllDevicesBtn");
    if (resetBtn) {
        resetBtn.addEventListener("click", () => {
            const devCount = container.querySelectorAll("[data-del]").length;
            showConfirmModal({
                title: "Сбросить все устройства?",
                message: `Все привязанные устройства (${devCount} шт.) потеряют доступ к подключению. Слоты станут свободными.`,
                confirmText: "Сбросить все",
                cancelText: "Отмена",
                isDestructive: true,
                onConfirm: async () => {
                    resetBtn.disabled = true;
                    try {
                        await api.resetAllDevices();
                        showToast("Все устройства отвязаны");
                        await reload();
                    } catch (err) {
                        throw new Error((err && err.message) || "Не удалось сбросить устройства");
                    } finally {
                        resetBtn.disabled = false;
                    }
                },
            });
        });
    }

    const devices = [...(d?.devices || [])].sort((a, b) => (a.id || 0) - (b.id || 0));
    const stdLimit = Number((d?.standard && d.standard.device_limit) || 2);
    const wiLimit = Number((d?.wi && d.wi.device_limit) || 1);
    const activeStdDevices = devices.filter((dev) => dev.is_std_assigned);
    const activeBiDevices = devices.filter((dev) => dev.is_bi_assigned);

    const bindSlotToggle = ({
        selector,
        dataAttr,
        slotTitle,
        slotLimit,
        activeDevices,
        applyFn,
        chipClass,
    }) => {
        container.querySelectorAll(selector).forEach((chk) => {
            chk.addEventListener("change", async () => {
                const hwid = chk.dataset[dataAttr];
                const wantEnable = chk.checked;
                const targetDev = devices.find((dev) => dev.hwid === hwid) || { hwid, device_name: chk.dataset.name };

                if (wantEnable && activeDevices.length >= slotLimit) {
                    chk.checked = false;
                    if (activeDevices.length === 1) {
                        const fromDev = activeDevices[0];
                        const fromLabel = fromDev.device_name || fromDev.platform || "текущего устройства";
                        const toLabel = targetDev.device_name || targetDev.platform || "это устройство";
                        showConfirmModal({
                            title: `Перенести «${slotTitle}»?`,
                            message: `Слот занят устройством «${fromLabel}». Перенести «${slotTitle}» на «${toLabel}»?`,
                            confirmText: "Перенести слот",
                            cancelText: "Отмена",
                            onConfirm: async () => {
                                chk.disabled = true;
                                try {
                                    await applyFn(hwid, 10, fromDev.hwid);
                                    showToast("Слот перенесён. Обновите список в приложении");
                                    await reload();
                                } catch (err) {
                                    throw new Error((err && err.message) || "Не удалось перенести слот");
                                } finally {
                                    chk.disabled = false;
                                }
                            },
                        });
                        return;
                    }
                    _openSlotReplaceModal(slotTitle, targetDev, activeDevices, applyFn, reload, chipClass);
                    return;
                }

                chk.disabled = true;
                try {
                    await applyFn(hwid, wantEnable ? 10 : -1);
                    showToast(
                        wantEnable
                            ? `${slotTitle} включён. Обновите список в приложении`
                            : `${slotTitle} отключён на устройстве`
                    );
                    await reload();
                } catch (err) {
                    chk.checked = !wantEnable;
                    showToast(err.message || "Не удалось изменить статус слота", "error");
                } finally {
                    chk.disabled = false;
                }
            });
        });
    };

    bindSlotToggle({
        selector: "[data-std-toggle]",
        dataAttr: "stdToggle",
        slotTitle: "Стандарт",
        slotLimit: stdLimit,
        activeDevices: activeStdDevices,
        applyFn: (hwid, prio, rep) => api.setDeviceStdPriority(hwid, prio, rep),
        chipClass: "chip-muted",
    });
    bindSlotToggle({
        selector: "[data-bi-toggle]",
        dataAttr: "biToggle",
        slotTitle: "Белый интернет",
        slotLimit: wiLimit,
        activeDevices: activeBiDevices,
        applyFn: (hwid, prio, rep) => api.setDevicePriority(hwid, prio, rep),
        chipClass: "chip-wi",
    });
}

function _renderMoreTab(body, d, root) {
    const ref = d.referrals || {};
    const bal = d.balance || { real: 0, bonus: 0, total: 0 };
    const minActive = ref.active_min_topup_rub || 67;
    const shareText = `Держи приглашение: скидка ${ref.welcome_discount_pct ?? 25}% на первый заказ`;
    const shareUrl = `https://t.me/share/url?url=${encodeURIComponent(ref.link || "")}&text=${encodeURIComponent(shareText)}`;
    const sup = d.support || {};
    const invitedUsers = ref.invited_users || [];
    const leaderboard = ref.leaderboard || [];

    const _formatFriendsCount = (n) => {
        const rem10 = n % 10;
        const rem100 = n % 100;
        if (rem10 === 1 && rem100 !== 11) return `${n} активный друг`;
        if (rem10 >= 2 && rem10 <= 4 && (rem100 < 10 || rem100 >= 20)) return `${n} активных друга`;
        return `${n} активных друзей`;
    };

    body.innerHTML = `
        <section class="hero-title-group">
            <h1 class="hero-headline-lg">Ещё</h1>
            <p class="hero-sub">Помощь, инструкции и рефералы</p>
        </section>

        <section class="section-block">
            <div class="section-head">
                <h2 class="section-title">Помощь и инструкции</h2>
            </div>
            <div class="list-group">
                <button id="openGuidesFromMoreBtn" type="button" class="settings-row">
                    <span class="settings-left">${_ICONS.book}<span>Пошаговая инструкция и видео</span></span>
                    <span style="display:inline-flex; color:var(--text-muted);">${_ICONS.chevronRight}</span>
                </button>
                ${sup.support_url ? `
                    <button id="supportContactBtn" type="button" class="settings-row">
                        <span class="settings-left">${_ICONS.message}<span>Написать в поддержку</span></span>
                        <span style="display:inline-flex; color:var(--text-muted);">${_ICONS.chevronRight}</span>
                    </button>
                ` : ""}
                ${sup.tg_proxy_url ? `
                    <button id="tgProxyBtn" type="button" class="settings-row">
                        <span class="settings-left">${_ICONS.send}<span>Доступ к Telegram</span></span>
                        <span style="display:inline-flex; color:var(--text-muted);">${_ICONS.chevronRight}</span>
                    </button>
                ` : ""}
            </div>
        </section>

        <section class="stats-strip">
            <div class="stat-box"><div class="stat-val">${ref.invited_count ?? 0}</div><div class="stat-lbl">Приглашено</div></div>
            <div class="stat-box"><div class="stat-val">${ref.active_count ?? 0}</div><div class="stat-lbl">Активных</div></div>
            <div class="stat-box"><div class="stat-val">${ref.rate_pct ?? 15}%</div><div class="stat-lbl">Ваш %</div></div>
            <div class="stat-box stat-lead"><div class="stat-val">${bal.bonus ?? 0} ₽</div><div class="stat-lbl">Бонусы</div></div>
        </section>

        <section class="card">
            <div class="row">
                <h2 class="card-title">Приглашения и бонусы</h2>
                <span class="chip chip-ok">${escapeHtml(ref.tier_name || "Стандарт")}</span>
            </div>
            ${ref.invited_by ? `
                <div class="muted" style="font-size:12px;">Вас пригласил: <strong style="color:var(--text-primary);">${escapeHtml(ref.invited_by)}</strong></div>
            ` : ""}
            <p class="muted">
                Друг получает скидку <b>${ref.welcome_discount_pct ?? 25}%</b> на первый заказ.
                Реферал считается активным после оплаты или пополнения от <b>${minActive} ₽</b>.
                Бонусами можно оплатить до 100% любого заказа.
            </p>
            ${ref.needed_for_next ? `
                <div class="notice-box">
                    <span>До уровня <b>${escapeHtml(ref.next_tier_name)}</b> (${ref.next_rate_pct}%): ещё ${_formatFriendsCount(ref.needed_for_next)}</span>
                </div>
            ` : ""}
            <button id="refLinkBox" type="button" class="sub-url-box clickable" style="width:100%; text-align:left;" aria-label="Скопировать реферальную ссылку">${escapeHtml(ref.link || "")}</button>
            <div class="connect-pair">
                <button id="shareRefBtn" type="button" class="btn btn-primary">${_ICONS.share} Пригласить</button>
                <button id="copyRefBtn" type="button" class="btn">${_ICONS.copy} Копировать</button>
            </div>
            ${invitedUsers.length ? `
                <details class="guide-details" style="margin-top:4px;">
                    <summary class="guide-summary">
                        <span>Приглашённые пользователи (${invitedUsers.length})</span>
                        <span class="disclosure-pill"><span>Показать</span>${_ICONS.chevronDown}</span>
                    </summary>
                    <div class="guide-body" style="gap:8px;">
                        ${invitedUsers.map((u) => `
                            <div class="item-row">
                                <div class="row">
                                    <span style="font-size:13px; font-weight:500;">${escapeHtml(u.label)}</span>
                                    <span class="chip ${u.is_active ? "chip-ok" : "chip-muted"}">${u.is_active ? "Активен" : "Ожидает"}</span>
                                </div>
                                <div class="muted" style="font-size:12px;">${_safeFormatDate(u.created_at) || "—"}</div>
                            </div>
                        `).join("")}
                    </div>
                </details>
            ` : ""}
            ${leaderboard.length ? `
                <details class="guide-details" style="margin-top:4px;">
                    <summary class="guide-summary">
                        <span>Рейтинг лидеров ${ref.user_rank ? `(Ваше место: #${ref.user_rank})` : ""}</span>
                        <span class="disclosure-pill"><span>Показать</span>${_ICONS.chevronDown}</span>
                    </summary>
                    <div class="guide-body" style="gap:8px;">
                        ${leaderboard.map((item, idx) => {
                            const rankLabel = `#${item.rank || idx + 1}`;
                            return `
                                <div class="item-row ${item.is_you ? "is-current-user" : ""}">
                                    <div class="row">
                                        <span style="font-size:13px; font-weight:500;">
                                            <span class="mono muted">${rankLabel}</span> ${escapeHtml(item.label)}
                                            ${item.is_you ? `<span class="chip chip-standard" style="font-size:12px; padding:2px 8px; margin-left:4px;">Вы</span>` : ""}
                                        </span>
                                        <span class="muted" style="font-size:12px;">${item.count} активных</span>
                                    </div>
                                </div>
                            `;
                        }).join("")}
                        ${ref.user_rank && !leaderboard.some((l) => l.is_you) ? `
                            <div class="notice-box" style="margin-top:4px;">
                                <span>Ваше место в рейтинге: <b>#${ref.user_rank}</b> (${ref.active_count ?? 0} активных)</span>
                            </div>
                        ` : ""}
                    </div>
                </details>
            ` : ""}
        </section>

        ${(sup.tos_url || sup.privacy_url) ? `
            <section class="section-block">
                <div class="section-head">
                    <h2 class="section-title">Документы</h2>
                </div>
                <div class="list-group">
                    ${sup.tos_url ? `
                        <button id="tosLinkBtn" type="button" class="settings-row">
                            <span class="settings-left">${_ICONS.file}<span>Пользовательское соглашение</span></span>
                            <span style="display:inline-flex; color:var(--text-muted);">${_ICONS.chevronRight}</span>
                        </button>
                    ` : ""}
                    ${sup.privacy_url ? `
                        <button id="privacyLinkBtn" type="button" class="settings-row">
                            <span class="settings-left">${_ICONS.file}<span>Политика конфиденциальности</span></span>
                            <span style="display:inline-flex; color:var(--text-muted);">${_ICONS.chevronRight}</span>
                        </button>
                    ` : ""}
                </div>
            </section>
        ` : ""}
    `;

    body.querySelector("#openGuidesFromMoreBtn")?.addEventListener("click", () => {
        _state.guidesReturnTab = "more";
        _state.guidesReturnScrollY = window.scrollY;
        _state.tab = "guides";
        _render(root, { preserveScroll: false });
        window.scrollTo(0, 0);
    });
    body.querySelector("#shareRefBtn").addEventListener("click", () => openExternalLink(shareUrl));
    body.querySelector("#copyRefBtn").addEventListener("click", () => copyText(ref.link, "Ссылка скопирована"));
    body.querySelector("#refLinkBox")?.addEventListener("click", () => copyText(ref.link, "Ссылка скопирована"));
    const supBtn = body.querySelector("#supportContactBtn");
    if (supBtn && sup.support_url) supBtn.addEventListener("click", () => openExternalLink(sup.support_url));
    const tgProxyBtn = body.querySelector("#tgProxyBtn");
    if (tgProxyBtn && sup.tg_proxy_url) tgProxyBtn.addEventListener("click", () => openExternalLink(sup.tg_proxy_url));
    const tosBtn = body.querySelector("#tosLinkBtn");
    if (tosBtn && sup.tos_url) tosBtn.addEventListener("click", () => openExternalLink(sup.tos_url));
    const privBtn = body.querySelector("#privacyLinkBtn");
    if (privBtn && sup.privacy_url) privBtn.addEventListener("click", () => openExternalLink(sup.privacy_url));
}

function _renderProfileTab(body, d, reload, onBack) {
    const bal = d.balance || { real: 0, bonus: 0, total: 0 };
    const minTopup = (d.pricing && d.pricing.min_topup_rub) || 20;
    const items = d.history || [];
    const meta = _getProfileMeta();
    const subBadge = [meta.username, meta.userId ? `ID ${meta.userId}` : ""].filter(Boolean).join(" · ");

    body.innerHTML = `
        <div class="detail-header-nav">
            <button id="profileBackBtn" type="button" class="btn btn-sm btn-ghost">← Назад</button>
            <span class="detail-sticky-meta">Профиль и баланс</span>
        </div>

        <section class="hero-title-group">
            <div style="display:flex; align-items:center; gap:12px;">
                <span id="profileLargeAvatar" class="avatar-circle avatar-lg">${_renderAvatarInner(meta)}</span>
                <div>
                    <h1 class="admin-page-title">${escapeHtml(meta.displayName)}</h1>
                    ${subBadge ? `<span class="mono-badge">${escapeHtml(subBadge)}</span>` : ""}
                </div>
            </div>
        </section>

        ${d.financial_hold ? `
            <div class="notice-box notice-warn">
                <span style="display:flex; align-items:center; gap:8px;">${_ICONS.alert}<span>Оплата и изменение подписок временно недоступны: профиль находится на проверке. Свяжитесь с поддержкой.</span></span>
            </div>
        ` : (d.payments_maintenance ? `
            <div class="notice-box notice-warn">
                <span style="display:flex; align-items:center; gap:8px;">${_ICONS.wrench}<span>Приём платежей временно приостановлен для проведения технических работ. Действующие подписки работают в штатном режиме.</span></span>
            </div>
        ` : "")}

        <section class="balance-card">
            <div>
                <div class="muted" style="font-size:12px;">Баланс аккаунта</div>
                <div class="balance-value" style="white-space:nowrap;">${bal.total}&nbsp;₽</div>
                <div class="balance-sub">Основной: <span style="white-space:nowrap;">${bal.real}&nbsp;₽</span> · Бонусы: <span style="white-space:nowrap;">${bal.bonus}&nbsp;₽</span></div>
            </div>
            <button id="profileTopupBtn" type="button" class="btn btn-primary btn-sm">${_ICONS.plus} Пополнить</button>
        </section>

        <section class="section-block">
            <div class="section-head">
                <h2 class="section-title">История операций</h2>
            </div>
            <div id="historyList"></div>
        </section>
    `;

    _bindAvatarImg(body.querySelector("#profileLargeAvatar"), meta.initial);

    body.querySelector("#profileBackBtn")?.addEventListener("click", () => {
        if (typeof onBack === "function") onBack();
    });

    body.querySelector("#profileTopupBtn").addEventListener("click", () => {
        if (d.financial_hold) {
            showToast("Профиль на проверке, свяжитесь с поддержкой", "error");
            return;
        }
        if (d.payments_maintenance) {
            showToast("Приём платежей временно приостановлен (техработы)", "error");
            return;
        }
        _openTopupModal(minTopup, reload, (tab) => { _state.tab = tab; });
    });

    const list = body.querySelector("#historyList");
    if (!items.length) {
        list.innerHTML = `<div class="card card-center" style="padding:20px 16px;"><span class="muted">Операций пока нет</span></div>`;
        return;
    }

    const renderHistory = () => {
        list.innerHTML = "";
        const group = document.createElement("div");
        group.className = "list-group";

        const visibleLimit = _state.historyLimit || 5;
        const visibleItems = items.slice(0, visibleLimit);
        visibleItems.forEach((op) => {
            const row = document.createElement("div");
            row.className = "device-item";
            const amt = Number(op.amount_rub ?? op.amount ?? 0);
            const sign = amt > 0 ? "+" : "";
            const cls = amt > 0 ? "amount-pos" : "amount-neg";
            row.innerHTML = `
                <div class="row">
                    <span style="font-weight:500;">${escapeHtml(op.description)}</span>
                    <span class="${cls}">${sign}${amt} ₽</span>
                </div>
                <div class="device-meta">${_safeFormatDate(op.created_at) || "—"}</div>
            `;
            group.appendChild(row);
        });
        list.appendChild(group);

        if (items.length > visibleLimit) {
            const showMoreBtn = document.createElement("button");
            showMoreBtn.type = "button";
            showMoreBtn.className = "btn btn-secondary btn-sm";
            showMoreBtn.style.cssText = "margin-top:8px; width:100%;";
            showMoreBtn.textContent = `Показать ещё (осталось ${items.length - visibleLimit})`;
            showMoreBtn.addEventListener("click", () => {
                _state.historyLimit = visibleLimit + 10;
                renderHistory();
            });
            list.appendChild(showMoreBtn);
        } else if (items.length > 5 && visibleLimit > 5) {
            const collapseBtn = document.createElement("button");
            collapseBtn.type = "button";
            collapseBtn.className = "btn btn-sm";
            collapseBtn.style.cssText = "margin-top:8px; width:100%; color:var(--text-muted);";
            collapseBtn.textContent = "Свернуть список";
            collapseBtn.addEventListener("click", () => {
                _state.historyLimit = 5;
                renderHistory();
            });
            list.appendChild(collapseBtn);
        }
    };
    renderHistory();
}
