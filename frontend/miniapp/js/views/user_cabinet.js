import { api } from "../api.js";
import {
    copyText,
    escapeHtml,
    formatBytesGb,
    formatDate,
    openExternalLink,
    openModal,
    showToast,
} from "../ui.js";

const _state = {
    tab: "access",
    stdDevices: 2,
    stdDuration: 30,
    stdUseBalance: true,
    stdQuote: null,
    wiDevices: 1,
    wiUseBalance: true,
    platform: _detectPlatform(),
    data: null,
};

let _quoteTimer = null;

const _ICONS = {
    copy: `<svg class="icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`,
    bolt: `<svg class="icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`,
    share: `<svg class="icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>`,
    plus: `<svg class="icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`,
    phone: `<svg class="icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="2" width="14" height="20" rx="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>`,
};

const _CLIENT_LINKS = {
    ios: [
        { name: "INCY", sub: "App Store · Рекомендуем", url: "https://apps.apple.com/app/incy/id6756943388" },
        { name: "Happ", sub: "App Store", url: "https://apps.apple.com/app/happ-proxy-utility/id6504287215" },
    ],
    android: [
        { name: "INCY (Google Play)", sub: "Официальный магазин", url: "https://play.google.com/store/apps/details?id=llc.itdev.incy" },
        { name: "INCY (APK)", sub: "Прямая загрузка", url: "https://github.com/INCY-DEV/incy-platforms/releases/download/latest/Incy.apk" },
        { name: "Happ (APK)", sub: "Прямая загрузка", url: "https://github.com/Happ-proxy/happ-android/releases/latest/download/Happ.apk" },
    ],
    windows: [
        { name: "Happ Desktop", sub: "Windows x64 · Рекомендуем", url: "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe" },
        { name: "INCY Desktop", sub: "Windows", url: "https://github.com/INCY-DEV/incy-platforms/releases/download/latest/incy-windows-setup.exe" },
    ],
    macos: [
        { name: "Happ Desktop", sub: "macOS Universal (.dmg)", url: "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/Happ.macOS.universal.dmg" },
        { name: "INCY", sub: "App Store", url: "https://apps.apple.com/app/incy/id6756943388" },
    ],
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

function _handlePaymentResponse(res, successMsg, onReload) {
    if (res && res.confirmation_url) {
        showToast("Открываем страницу оплаты...");
        openExternalLink(res.confirmation_url);
        return;
    }
    showToast(successMsg);
    onReload();
}

function _openTopupModal(minTopup, onReload) {
    const form = document.createElement("form");
    form.className = "field";
    form.style.gap = "12px";
    form.innerHTML = `
        <div class="field">
            <label>Сумма пополнения (от ${minTopup} ₽)</label>
            <input name="amount" class="input tabular" type="number" min="${minTopup}" max="50000" step="1" value="250" required>
        </div>
        <div class="filter-pills">
            <button type="button" class="pill-btn" data-amt="100">100 ₽</button>
            <button type="button" class="pill-btn" data-amt="250">250 ₽</button>
            <button type="button" class="pill-btn" data-amt="500">500 ₽</button>
            <button type="button" class="pill-btn" data-amt="1000">1000 ₽</button>
        </div>
        <button type="submit" class="btn btn-primary btn-lg">Перейти к оплате</button>
    `;
    const close = openModal("Пополнение баланса", form);
    form.querySelectorAll("[data-amt]").forEach((b) => {
        b.addEventListener("click", () => {
            form.querySelector("input[name='amount']").value = b.dataset.amt;
        });
    });
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const amount = Number(form.querySelector("input[name='amount']").value);
        if (!Number.isInteger(amount) || amount < minTopup) {
            showToast(`Минимальная сумма — ${minTopup} ₽`);
            return;
        }
        const btn = form.querySelector("button[type='submit']");
        btn.disabled = true;
        try {
            const res = await api.topupBalance(amount);
            close();
            _handlePaymentResponse(res, "Баланс пополнен", onReload);
        } catch (err) {
            showToast(err.message || "Не удалось создать платёж");
        } finally {
            btn.disabled = false;
        }
    });
}

export async function loadAndRenderUserCabinet(root) {
    try {
        const data = await api.getUserDashboard();
        _state.data = data;
        if (data.standard && data.standard.device_limit >= 2 && !_state.stdQuote) {
            _state.stdDevices = Math.min(10, Math.max(2, data.standard.device_limit));
        }
        if (data.wi && data.wi.device_limit >= 1) {
            _state.wiDevices = Math.min(3, Math.max(1, data.wi.device_limit));
        }
        _render(root);
        _refreshQuote(root);
    } catch (err) {
        root.innerHTML = `
            <section class="card card-center">
                <div class="card-title">Не удалось загрузить кабинет</div>
                <p class="muted">${escapeHtml(err.message || "Попробуйте обновить экран")}</p>
                <button id="retryDashBtn" type="button" class="btn btn-primary btn-sm">Повторить</button>
            </section>
        `;
        const retry = root.querySelector("#retryDashBtn");
        if (retry) retry.addEventListener("click", () => loadAndRenderUserCabinet(root));
    }
}

function _scheduleQuoteRefresh(root) {
    if (_quoteTimer) clearTimeout(_quoteTimer);
    const quoteBox = root.querySelector("#stdQuoteSummary");
    if (quoteBox) {
        quoteBox.innerHTML = `<div class="quote-line"><span>Пересчёт стоимости...</span></div>`;
    }
    _quoteTimer = setTimeout(() => {
        _refreshQuote(root);
    }, 240);
}

async function _refreshQuote(root) {
    if (_state.tab !== "access") return;
    const reqDevices = _state.stdDevices;
    const reqDuration = _state.stdDuration;
    try {
        const quote = await api.quoteStandard(reqDevices, reqDuration);
        if (reqDevices !== _state.stdDevices || reqDuration !== _state.stdDuration) return;
        _state.stdQuote = quote;
        _updateQuoteUi(root);
    } catch {
        // Non-critical
    }
}

function _computeQuoteBreakdown(q, useBalance) {
    if (!q) return null;
    const price = Number(q.due_rub ?? q.price_rub ?? q.amount_rub ?? 0);
    const bonus = useBalance ? Number(q.bonus_used || 0) : 0;
    const real = useBalance ? Number(q.real_used || 0) : 0;
    const balUsed = Math.min(price, bonus + real);
    const card = useBalance ? Number(q.card_to_pay ?? Math.max(0, price - balUsed)) : price;
    return { price, bonus, real, balUsed, card, discount: Number(q.discount_rub || 0) };
}

function _buildQuoteSummaryHtml(q, useBalance) {
    if (!q) return `<div class="quote-line"><span>Расчёт стоимости...</span></div>`;
    if (q.blocked_reason) {
        return `<div class="quote-line" style="color:var(--accent-amber);">${escapeHtml(q.blocked_reason)}</div>`;
    }
    if (q.operation === "downgrade") {
        return `<div class="quote-line"><span>Выбрано меньше устройств (${_state.stdDevices}). Можно снизить лимит бесплатно.</span></div>`;
    }
    const b = _computeQuoteBreakdown(q, useBalance);
    const rows = [`<div class="quote-line"><span>Стоимость тарифа</span><strong>${b.price} ₽</strong></div>`];
    if (b.discount > 0) {
        rows.push(`<div class="quote-line"><span>Приветственная скидка</span><strong style="color:var(--accent-emerald);">-${b.discount} ₽</strong></div>`);
    }
    if (b.balUsed > 0) {
        rows.push(`<div class="quote-line"><span>Спишется с баланса</span><strong>-${b.balUsed} ₽</strong></div>`);
    }
    rows.push(
        b.card > 0
            ? `<div class="quote-line"><span>К оплате картой / СБП</span><strong>${b.card} ₽</strong></div>`
            : `<div class="quote-line"><span>К оплате картой / СБП</span><strong style="color:var(--accent-emerald);">0 ₽ (с баланса)</strong></div>`
    );
    return rows.join("");
}

function _getStdButtonLabel(std, q, useBalance) {
    const baseVerb = std.active ? "Продлить" : "Подключить";
    const b = _computeQuoteBreakdown(q, useBalance);
    if (!b || q.operation === "downgrade") return baseVerb;
    if (b.card === 0 && b.price > 0) return `Оплатить с баланса (${b.price} ₽)`;
    if (b.balUsed > 0 && b.card > 0) return `${baseVerb} · доплата ${b.card} ₽`;
    return `${baseVerb} за ${b.card} ₽`;
}

function _updateQuoteUi(root) {
    const std = (_state.data && _state.data.standard) || {};
    const quoteBox = root.querySelector("#stdQuoteSummary");
    if (quoteBox) {
        quoteBox.innerHTML = _buildQuoteSummaryHtml(_state.stdQuote, _state.stdUseBalance);
    }
    const btn = root.querySelector("#stdCheckoutBtn");
    if (btn) {
        btn.textContent = _getStdButtonLabel(std, _state.stdQuote, _state.stdUseBalance);
    }
}

function _render(root) {
    const d = _state.data;
    if (!d) return;
    const devCount = (d.devices || []).length;

    root.innerHTML = `
        <nav class="tabs-bar">
            <button type="button" class="tab-btn ${_state.tab === "access" ? "active" : ""}" data-utab="access">Доступ</button>
            <button type="button" class="tab-btn ${_state.tab === "devices" ? "active" : ""}" data-utab="devices">Устройства (${devCount})</button>
            <button type="button" class="tab-btn ${_state.tab === "referrals" ? "active" : ""}" data-utab="referrals">Бонусы</button>
            <button type="button" class="tab-btn ${_state.tab === "history" ? "active" : ""}" data-utab="history">История</button>
        </nav>
        <div id="userTabBody" class="app-main"></div>
    `;

    root.querySelectorAll("[data-utab]").forEach((b) => {
        b.addEventListener("click", () => {
            _state.tab = b.dataset.utab;
            _render(root);
            if (_state.tab === "access") _refreshQuote(root);
        });
    });

    const body = root.querySelector("#userTabBody");
    const reload = () => loadAndRenderUserCabinet(root);

    if (_state.tab === "access") _renderAccessTab(body, d, reload, root);
    else if (_state.tab === "devices") _renderDevicesTab(body, d, reload);
    else if (_state.tab === "referrals") _renderReferralsTab(body, d);
    else _renderHistoryTab(body, d);
}

function _buildGuideHtml(subUrl, isActive) {
    const platformLabels = { ios: "iOS", android: "Android", windows: "Windows", macos: "macOS" };
    const clients = _CLIENT_LINKS[_state.platform] || _CLIENT_LINKS.ios;

    return `
        <details class="guide-details" id="connectGuide">
            <summary class="guide-summary">
                <span>${isActive ? "Как подключить на этом устройстве" : "Установить приложение заранее"}</span>
                <span class="muted" style="font-size:11px;">${platformLabels[_state.platform] || "iOS"} ▾</span>
            </summary>
            <div class="guide-body">
                <div class="filter-pills">
                    ${Object.entries(platformLabels).map(([key, label]) => `
                        <button type="button" class="pill-btn ${_state.platform === key ? "active" : ""}" data-platform="${key}">${label}</button>
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
                        <label>Ключ подключения (для ручного ввода)</label>
                        <div class="sub-url-box">${escapeHtml(subUrl)}</div>
                    </div>
                ` : ""}
            </div>
        </details>
    `;
}

function _buildHeroHtml(d, std, wi) {
    const isAnyActive = Boolean(std.active || wi.active);
    const subUrl = d.subscription_url || "";
    const desktopFirst = _state.platform === "windows" || _state.platform === "macos";

    if (!isAnyActive) {
        return `
            <section class="hero-card">
                <div class="row">
                    <div style="display:flex; align-items:center; gap:9px;">
                        <span class="status-dot"></span>
                        <span class="hero-headline">Доступ не подключён</span>
                    </div>
                    <span class="chip chip-muted">Не активен</span>
                </div>
                <p class="muted">Выберите подходящий тариф ниже — ключ подключения активируется сразу после оплаты.</p>
                ${_buildGuideHtml(subUrl, false)}
            </section>
        `;
    }

    const pills = [];
    if (std.active) {
        pills.push(`<span class="chip chip-ok">Базовый · ${Number(std.device_limit || 2)} устр. · до ${formatDate(std.expires_at)}</span>`);
    }
    if (wi.active) {
        pills.push(`<span class="chip chip-info">Устойчивость · ${Number(wi.device_limit || 1)} устр. · до ${formatDate(wi.expires_at)}</span>`);
    }

    const incyHref = `incy://add/${subUrl}`;
    const happHref = `happ://add/${subUrl}`;

    return `
        <section class="hero-card is-active">
            <div class="row">
                <div style="display:flex; align-items:center; gap:9px;">
                    <span class="status-dot active"></span>
                    <span class="hero-headline">Доступ активен</span>
                </div>
                <span class="chip chip-ok">Готов к работе</span>
            </div>
            <div class="option-pills">${pills.join("")}</div>
            <div class="connect-actions">
                ${desktopFirst ? `
                    <a href="${escapeHtml(happHref)}" class="btn btn-primary btn-lg">${_ICONS.bolt} Подключить в Happ</a>
                ` : `
                    <a href="${escapeHtml(incyHref)}" class="btn btn-primary btn-lg">${_ICONS.bolt} Подключить в INCY</a>
                `}
                <div class="connect-pair">
                    ${desktopFirst ? `
                        <a href="${escapeHtml(incyHref)}" class="btn btn-sm">Открыть в INCY</a>
                    ` : `
                        <a href="${escapeHtml(happHref)}" class="btn btn-sm">Открыть в Happ</a>
                    `}
                    <button id="copySubBtn" type="button" class="btn btn-sm">${_ICONS.copy} Копировать ключ</button>
                </div>
            </div>
            ${_buildGuideHtml(subUrl, true)}
        </section>
    `;
}

function _renderAccessTab(body, d, reload, root) {
    const bal = d.balance || { real: 0, bonus: 0, total: 0 };
    const std = d.standard || {};
    const wi = d.wi || {};
    const pricing = d.pricing || {};
    const minTopup = pricing.min_topup_rub || 20;

    const stdLimit = Number(std.device_limit || 0);
    const isStdDowngrade = Boolean(std.active && _state.stdDevices < stdLimit);
    const durations = pricing.standard_durations || [7, 30, 90];

    body.innerHTML = `
        <section class="balance-card">
            <div>
                <div class="muted" style="font-size:12px;">Баланс аккаунта</div>
                <div class="balance-value">${bal.total} ₽</div>
                <div class="balance-sub">Основной: ${bal.real} ₽ · Бонусы: ${bal.bonus} ₽</div>
            </div>
            <button id="topupBtn" type="button" class="btn btn-primary btn-sm">${_ICONS.plus} Пополнить</button>
        </section>

        ${_buildHeroHtml(d, std, wi)}

        <section class="card">
            <div class="row">
                <div>
                    <div class="card-title">Базовый доступ</div>
                    <div class="muted" style="font-size:12px;">Высокая скорость и стабильное подключение на каждый день</div>
                </div>
                <span class="chip ${std.active ? "chip-ok" : "chip-muted"}">
                    ${std.active ? `${stdLimit} устр.` : "Не активен"}
                </span>
            </div>

            ${std.active ? `
                <label class="toggle-row">
                    <span class="toggle-label">Автопродление с баланса</span>
                    <span class="switch">
                        <input id="stdAutoRenewChk" type="checkbox" ${std.auto_renew_enabled ? "checked" : ""}>
                        <span class="switch-track"></span>
                    </span>
                </label>
            ` : ""}

            <div class="range-wrap">
                <div class="row">
                    <span class="muted" style="font-size:12px;">Количество устройств</span>
                    <span id="stdDevicesValue" class="tabular" style="font-weight:600;">${_state.stdDevices} устр.</span>
                </div>
                <input id="stdDevicesRange" class="range-input" type="range" min="2" max="10" step="1" value="${_state.stdDevices}">
            </div>

            ${isStdDowngrade ? "" : `
                <div class="field">
                    <label>Срок действия</label>
                    <div class="seg-group">
                        ${durations.map((days) => `
                            <button type="button" class="seg-btn ${_state.stdDuration === days ? "active" : ""}" data-std-days="${days}">
                                ${days} дн.
                            </button>
                        `).join("")}
                    </div>
                </div>

                <label class="toggle-row">
                    <span class="toggle-label">Оплатить с баланса (бонусы сначала)</span>
                    <span class="switch">
                        <input id="stdUseBalChk" type="checkbox" ${_state.stdUseBalance ? "checked" : ""}>
                        <span class="switch-track"></span>
                    </span>
                </label>
            `}

            <div id="stdQuoteSummary" class="quote-box">${_buildQuoteSummaryHtml(_state.stdQuote, _state.stdUseBalance)}</div>

            ${isStdDowngrade ? `
                <button id="stdDowngradeBtn" type="button" class="btn btn-primary btn-lg">
                    Понизить лимит до ${_state.stdDevices} устр.
                </button>
            ` : `
                <button id="stdCheckoutBtn" type="button" class="btn btn-primary btn-lg">
                    ${_getStdButtonLabel(std, _state.stdQuote, _state.stdUseBalance)}
                </button>
            `}

            ${std.downgrade_cooldown_until ? `
                <div class="notice-box notice-warn">
                    <span>Снижение лимита доступно после ${formatDate(std.downgrade_cooldown_until)}</span>
                </div>
            ` : ""}
        </section>

        <section class="card">
            <div class="row">
                <div>
                    <div class="card-title">Повышенная устойчивость</div>
                    <div class="muted" style="font-size:12px;">Резервный канал для сложных сетей и ограничений</div>
                </div>
                <span class="chip ${wi.active ? "chip-info" : "chip-muted"}">
                    ${wi.active ? `до ${formatDate(wi.expires_at)}` : "Доп. опция"}
                </span>
            </div>
            ${wi.active ? _wiActiveHtml(wi, pricing) : _wiInactiveHtml(d, pricing)}
        </section>
    `;

    body.querySelector("#topupBtn").addEventListener("click", () => _openTopupModal(minTopup, reload));

    const copyBtn = body.querySelector("#copySubBtn");
    if (copyBtn) {
        copyBtn.addEventListener("click", () => copyText(d.subscription_url, "Ключ скопирован"));
    }

    body.querySelectorAll("[data-platform]").forEach((btn) => {
        btn.addEventListener("click", () => {
            _state.platform = btn.dataset.platform;
            const guideWasOpen = Boolean(body.querySelector("#connectGuide")?.open);
            _render(root);
            if (guideWasOpen) {
                const nextGuide = root.querySelector("#connectGuide");
                if (nextGuide) nextGuide.open = true;
            }
        });
    });

    body.querySelectorAll("[data-download]").forEach((btn) => {
        btn.addEventListener("click", () => openExternalLink(btn.dataset.download));
    });

    const stdAuto = body.querySelector("#stdAutoRenewChk");
    if (stdAuto) {
        stdAuto.addEventListener("change", async () => {
            try {
                await api.setAutoRenew("standard", stdAuto.checked);
                showToast(stdAuto.checked ? "Автопродление включено" : "Автопродление выключено");
            } catch (err) {
                stdAuto.checked = !stdAuto.checked;
                showToast(err.message || "Ошибка настройки автопродления");
            }
        });
    }

    const range = body.querySelector("#stdDevicesRange");
    const devLabel = body.querySelector("#stdDevicesValue");
    range.addEventListener("input", () => {
        const nextVal = Number(range.value);
        const wasDowngrade = Boolean(std.active && _state.stdDevices < stdLimit);
        const willBeDowngrade = Boolean(std.active && nextVal < stdLimit);
        _state.stdDevices = nextVal;
        if (devLabel) devLabel.textContent = `${nextVal} устр.`;
        if (wasDowngrade !== willBeDowngrade) {
            _render(root);
            _refreshQuote(root);
            return;
        }
        _scheduleQuoteRefresh(root);
    });

    body.querySelectorAll("[data-std-days]").forEach((b) => {
        b.addEventListener("click", () => {
            _state.stdDuration = Number(b.dataset.stdDays);
            _render(root);
            _refreshQuote(root);
        });
    });

    const useBalChk = body.querySelector("#stdUseBalChk");
    if (useBalChk) {
        useBalChk.addEventListener("change", () => {
            _state.stdUseBalance = useBalChk.checked;
            _updateQuoteUi(root);
        });
    }

    const checkoutBtn = body.querySelector("#stdCheckoutBtn");
    if (checkoutBtn) {
        checkoutBtn.addEventListener("click", async () => {
            checkoutBtn.disabled = true;
            try {
                const res = await api.checkoutStandard(_state.stdDevices, _state.stdDuration, _state.stdUseBalance);
                _handlePaymentResponse(res, "Базовый доступ оформлен", reload);
            } catch (err) {
                showToast(err.message || "Ошибка оформления");
            } finally {
                checkoutBtn.disabled = false;
            }
        });
    }

    const dgBtn = body.querySelector("#stdDowngradeBtn");
    if (dgBtn) {
        dgBtn.addEventListener("click", async () => {
            dgBtn.disabled = true;
            try {
                await api.downgradeStandard(_state.stdDevices);
                showToast("Лимит устройств снижен");
                reload();
            } catch (err) {
                showToast(err.message || "Не удалось снизить лимит");
            } finally {
                dgBtn.disabled = false;
            }
        });
    }

    _bindWiEvents(body, d, reload, root);
}

function _wiActiveHtml(wi, pricing) {
    const usedGb = formatBytesGb(wi.traffic_used_bytes);
    const totalGb = formatBytesGb(wi.traffic_limit_bytes);
    const pct = wi.traffic_limit_bytes > 0
        ? Math.min(100, Math.round((wi.traffic_used_bytes / wi.traffic_limit_bytes) * 100))
        : 0;
    const packs = pricing.wi_topup_packs || [];
    const curLimit = Number(wi.device_limit || 1);
    const maxDevices = pricing.wi_max_devices || 3;

    return `
        <div class="field">
            <div class="row muted" style="font-size:12px;">
                <span class="tabular">Трафик: <strong style="color:var(--text-primary);">${usedGb} / ${totalGb} ГБ</strong></span>
                <span class="tabular">Слотов: <strong style="color:var(--text-primary);">${curLimit} из ${maxDevices}</strong></span>
            </div>
            <div class="progress-track"><div class="progress-fill" style="width:${pct}%;"></div></div>
        </div>
        <label class="toggle-row">
            <span class="toggle-label">Автопродление с баланса</span>
            <span class="switch">
                <input id="wiAutoRenewChk" type="checkbox" ${wi.auto_renew_enabled ? "checked" : ""}>
                <span class="switch-track"></span>
            </span>
        </label>
        <label class="toggle-row">
            <span class="toggle-label">Оплатить с баланса (бонусы сначала)</span>
            <span class="switch">
                <input id="wiUseBalChk" type="checkbox" ${_state.wiUseBalance ? "checked" : ""}>
                <span class="switch-track"></span>
            </span>
        </label>
        <div class="actions-row">
            <button id="wiRenewBtn" type="button" class="btn btn-primary" style="flex:1;">Продлить +30 дн.</button>
            ${curLimit < maxDevices && wi.add_slot_price_rub != null ? `
                <button id="wiAddSlotBtn" type="button" class="btn">+1 слот · ${wi.add_slot_price_rub} ₽</button>
            ` : ""}
        </div>
        ${packs.length ? `
            <div class="field">
                <label>Дополнительный трафик</label>
                <div class="filter-pills">
                    ${packs.map((p) => `
                        <button type="button" class="pill-btn" data-wi-pack="${p.gb}">+${p.gb} ГБ · ${p.price_rub} ₽</button>
                    `).join("")}
                </div>
            </div>
        ` : ""}
        ${wi.pending_device_limit ? `
            <div class="notice-box notice-warn">
                <span>Снижение до ${wi.pending_device_limit} устр. с ${formatDate(wi.pending_from)}</span>
                <button id="wiCancelDgBtn" type="button" class="btn btn-sm btn-danger">Отменить</button>
            </div>
        ` : (curLimit > 1 ? `
            <details class="guide-details">
                <summary class="guide-summary">
                    <span>Изменить число слотов со следующего периода</span>
                    <span class="muted">▾</span>
                </summary>
                <div class="guide-body">
                    <div class="actions-row">
                        ${Array.from({ length: curLimit - 1 }, (_, i) => i + 1).map((target) => `
                            <button type="button" class="btn btn-sm" data-wi-dg="${target}">
                                Снизить до ${target} устр.
                            </button>
                        `).join("")}
                    </div>
                </div>
            </details>
        ` : "")}
    `;
}

function _wiInactiveHtml(d, pricing) {
    const base = Number(pricing.wi_base_price_rub || 250);
    const extra = Number(pricing.wi_extra_device_price_rub || 200);
    const total = base + Math.max(0, _state.wiDevices - 1) * extra;
    const balTotal = Number((d.balance && d.balance.total) || 0);
    const balUsed = _state.wiUseBalance ? Math.min(total, balTotal) : 0;
    const cardDue = Math.max(0, total - balUsed);

    const ctaText = cardDue === 0
        ? `Оплатить с баланса (${total} ₽)`
        : (balUsed > 0 ? `Подключить · доплата ${cardDue} ₽` : `Подключить за ${total} ₽`);

    return `
        <div class="quote-box">
            <div class="quote-line"><span>Трафик на слот</span><strong>50 ГБ / 30 дней</strong></div>
            <div class="quote-line"><span>Стоимость опции</span><strong>${total} ₽</strong></div>
        </div>
        <div class="field">
            <label>Выберите количество устройств</label>
            <div class="seg-group">
                ${[1, 2, 3].map((n) => `
                    <button type="button" class="seg-btn ${_state.wiDevices === n ? "active" : ""}" data-wi-dev="${n}">
                        ${n} устр. · ${base + (n - 1) * extra} ₽
                    </button>
                `).join("")}
            </div>
        </div>
        <label class="toggle-row">
            <span class="toggle-label">Оплатить с баланса (бонусы сначала)</span>
            <span class="switch">
                <input id="wiUseBalChk" type="checkbox" ${_state.wiUseBalance ? "checked" : ""}>
                <span class="switch-track"></span>
            </span>
        </label>
        <button id="wiBuyBtn" type="button" class="btn btn-primary btn-lg">${ctaText}</button>
    `;
}

function _bindWiEvents(body, d, reload, root) {
    const wiUseBal = body.querySelector("#wiUseBalChk");
    if (wiUseBal) {
        wiUseBal.addEventListener("change", () => {
            _state.wiUseBalance = wiUseBal.checked;
            if (!d.wi || !d.wi.active) _render(root);
        });
    }

    const wiAuto = body.querySelector("#wiAutoRenewChk");
    if (wiAuto) {
        wiAuto.addEventListener("change", async () => {
            try {
                await api.setAutoRenew("wi", wiAuto.checked);
                showToast(wiAuto.checked ? "Автопродление включено" : "Автопродление выключено");
            } catch (err) {
                wiAuto.checked = !wiAuto.checked;
                showToast(err.message || "Ошибка настройки автопродления");
            }
        });
    }

    body.querySelectorAll("[data-wi-dev]").forEach((b) => {
        b.addEventListener("click", () => {
            _state.wiDevices = Number(b.dataset.wiDev);
            _render(root);
        });
    });

    const buyBtn = body.querySelector("#wiBuyBtn");
    if (buyBtn) {
        buyBtn.addEventListener("click", async () => {
            buyBtn.disabled = true;
            try {
                const res = await api.checkoutWi({
                    action: "buy",
                    device_limit: _state.wiDevices,
                    use_balance: _state.wiUseBalance,
                });
                _handlePaymentResponse(res, "Опция подключена", reload);
            } catch (err) {
                showToast(err.message || "Ошибка подключения");
            } finally {
                buyBtn.disabled = false;
            }
        });
    }

    const renewBtn = body.querySelector("#wiRenewBtn");
    if (renewBtn) {
        renewBtn.addEventListener("click", async () => {
            renewBtn.disabled = true;
            try {
                const res = await api.checkoutWi({
                    action: "renew",
                    device_limit: (d.wi && d.wi.device_limit) || 1,
                    use_balance: _state.wiUseBalance,
                });
                _handlePaymentResponse(res, "Подписка продлена на 30 дней", reload);
            } catch (err) {
                showToast(err.message || "Ошибка продления");
            } finally {
                renewBtn.disabled = false;
            }
        });
    }

    const addSlotBtn = body.querySelector("#wiAddSlotBtn");
    if (addSlotBtn) {
        addSlotBtn.addEventListener("click", async () => {
            addSlotBtn.disabled = true;
            try {
                const res = await api.checkoutWi({
                    action: "add_slot",
                    use_balance: _state.wiUseBalance,
                });
                _handlePaymentResponse(res, "Слот добавлен", reload);
            } catch (err) {
                showToast(err.message || "Ошибка добавления слота");
            } finally {
                addSlotBtn.disabled = false;
            }
        });
    }

    body.querySelectorAll("[data-wi-dg]").forEach((b) => {
        b.addEventListener("click", async () => {
            b.disabled = true;
            try {
                await api.scheduleWiDowngrade(Number(b.dataset.wiDg));
                showToast("Снижение запланировано на следующий период");
                reload();
            } catch (err) {
                showToast(err.message || "Ошибка");
            } finally {
                b.disabled = false;
            }
        });
    });

    const cancelDgBtn = body.querySelector("#wiCancelDgBtn");
    if (cancelDgBtn) {
        cancelDgBtn.addEventListener("click", async () => {
            cancelDgBtn.disabled = true;
            try {
                await api.cancelWiDowngrade();
                showToast("Отложенное снижение отменено");
                reload();
            } catch (err) {
                showToast(err.message || "Ошибка отмены");
            } finally {
                cancelDgBtn.disabled = false;
            }
        });
    }

    body.querySelectorAll("[data-wi-pack]").forEach((b) => {
        b.addEventListener("click", async () => {
            b.disabled = true;
            try {
                const res = await api.checkoutWi({
                    action: "topup",
                    gb: Number(b.dataset.wiPack),
                    use_balance: _state.wiUseBalance,
                });
                _handlePaymentResponse(res, "Пакет трафика добавлен", reload);
            } catch (err) {
                showToast(err.message || "Ошибка покупки пакета");
            } finally {
                b.disabled = false;
            }
        });
    });
}

function _renderDevicesTab(body, d, reload) {
    const devices = d.devices || [];
    const wiActive = Boolean(d.wi && d.wi.active);

    body.innerHTML = `
        <section class="card">
            <div class="row">
                <div class="card-title">Привязанные устройства</div>
                <span class="chip chip-muted">${devices.length} шт.</span>
            </div>
            <p class="muted">
                ${wiActive
                    ? "Слоты повышенной устойчивости выдаются по приоритету (или в порядке первого подключения)."
                    : "Устройства добавляются автоматически после импорта ключа в Happ или INCY."}
            </p>
            <div id="devicesList" style="display:flex; flex-direction:column; gap:8px;"></div>
        </section>
    `;

    const list = body.querySelector("#devicesList");
    if (!devices.length) {
        list.innerHTML = `
            <div class="card card-center" style="background:var(--bg-elevated); padding:22px 16px;">
                <div style="color:var(--text-secondary);">${_ICONS.phone}</div>
                <div style="font-weight:600; font-size:14px;">Устройств пока нет</div>
                <p class="muted" style="font-size:12px;">
                    Откройте вкладку «Доступ» и нажмите «Подключить в INCY» или «Открыть в Happ» — телефон или компьютер появятся здесь автоматически.
                </p>
            </div>
        `;
        return;
    }

    devices.forEach((dev) => {
        const row = document.createElement("div");
        row.className = "item-row";
        const label = dev.device_name || dev.platform || "Устройство";
        const meta = [dev.platform, dev.os_version, dev.app_version].filter(Boolean).join(" · ");
        row.innerHTML = `
            <div class="row">
                <div>
                    <div style="font-weight:600;">${escapeHtml(label)}</div>
                    <div class="muted" style="font-size:11px;">${escapeHtml(meta || dev.hwid)}</div>
                </div>
                ${dev.is_bi_assigned ? `<span class="chip chip-info">Слот устойчивости</span>` : ""}
            </div>
            <div class="row muted" style="font-size:11px;">
                <span>Активность: ${formatDate(dev.last_seen_at)}</span>
                <span class="tabular">Приоритет: ${dev.bi_priority || 0}</span>
            </div>
            <div class="actions-row">
                ${wiActive ? `
                    <button type="button" class="btn btn-sm" data-prio="${escapeHtml(dev.hwid)}" data-cur="${dev.bi_priority || 0}">
                        ${(dev.bi_priority || 0) > 0 ? "Сбросить приоритет" : "Повысить приоритет"}
                    </button>
                ` : ""}
                <button type="button" class="btn btn-sm btn-danger" data-del="${escapeHtml(dev.hwid)}">Удалить</button>
            </div>
        `;
        list.appendChild(row);
    });

    list.querySelectorAll("[data-del]").forEach((btn) => {
        btn.addEventListener("click", async () => {
            btn.disabled = true;
            try {
                await api.deleteDevice(btn.dataset.del);
                showToast("Устройство удалено");
                reload();
            } catch (err) {
                showToast(err.message || "Не удалось удалить устройство");
            } finally {
                btn.disabled = false;
            }
        });
    });

    list.querySelectorAll("[data-prio]").forEach((btn) => {
        btn.addEventListener("click", async () => {
            btn.disabled = true;
            const nextPrio = Number(btn.dataset.cur) > 0 ? 0 : 10;
            try {
                await api.setDevicePriority(btn.dataset.prio, nextPrio);
                showToast(nextPrio > 0 ? "Приоритет повышен" : "Приоритет сброшен");
                reload();
            } catch (err) {
                showToast(err.message || "Не удалось изменить приоритет");
            } finally {
                btn.disabled = false;
            }
        });
    });
}

function _renderReferralsTab(body, d) {
    const ref = d.referrals || {};
    const bal = d.balance || {};
    const minActive = ref.active_min_topup_rub || 67;
    const shareText = `Держи приглашение — скидка ${ref.welcome_discount_pct ?? 25}% на первый заказ`;
    const shareUrl = `https://t.me/share/url?url=${encodeURIComponent(ref.link || "")}&text=${encodeURIComponent(shareText)}`;

    body.innerHTML = `
        <section class="stats-strip">
            <div class="stat-box"><div class="stat-val">${ref.invited_count ?? 0}</div><div class="stat-lbl">Приглашено</div></div>
            <div class="stat-box"><div class="stat-val">${ref.active_count ?? 0}</div><div class="stat-lbl">Активных</div></div>
            <div class="stat-box"><div class="stat-val">${ref.rate_pct ?? 15}%</div><div class="stat-lbl">Ставка</div></div>
            <div class="stat-box"><div class="stat-val">${bal.bonus ?? 0} ₽</div><div class="stat-lbl">Бонусы</div></div>
        </section>

        <section class="card">
            <div class="row">
                <div class="card-title">Приглашения и бонусы</div>
                <span class="chip chip-ok">${escapeHtml(ref.tier_name || "Standard")}</span>
            </div>
            <p class="muted">
                Друг получает скидку <b>${ref.welcome_discount_pct ?? 25}%</b> на первый заказ.
                Реферал считается активным после оплаты или пополнения от <b>${minActive} ₽</b>.
                Бонусами можно оплатить до 100% любого заказа.
            </p>
            ${ref.needed_for_next ? `
                <div class="notice-box">
                    <span>До уровня <b>${escapeHtml(ref.next_tier_name)}</b> (${ref.next_rate_pct}%): осталось ${ref.needed_for_next} активных</span>
                </div>
            ` : ""}
            <div class="sub-url-box">${escapeHtml(ref.link || "")}</div>
            <div class="connect-pair">
                <button id="shareRefBtn" type="button" class="btn btn-primary">${_ICONS.share} Пригласить</button>
                <button id="copyRefBtn" type="button" class="btn">${_ICONS.copy} Копировать</button>
            </div>
        </section>
    `;

    body.querySelector("#shareRefBtn").addEventListener("click", () => openExternalLink(shareUrl));
    body.querySelector("#copyRefBtn").addEventListener("click", () => copyText(ref.link, "Ссылка скопирована"));
}

function _renderHistoryTab(body, d) {
    const items = d.history || [];
    body.innerHTML = `
        <section class="card">
            <div class="card-title">История операций</div>
            <div id="historyList" style="display:flex; flex-direction:column; gap:8px;"></div>
        </section>
    `;
    const list = body.querySelector("#historyList");
    if (!items.length) {
        list.innerHTML = `<div class="muted" style="text-align:center; padding:18px;">Операций пока нет</div>`;
        return;
    }
    items.forEach((op) => {
        const row = document.createElement("div");
        row.className = "item-row";
        const sign = op.amount_rub > 0 ? "+" : "";
        const cls = op.amount_rub > 0 ? "amount-pos" : "amount-neg";
        row.innerHTML = `
            <div class="row">
                <span style="font-weight:500;">${escapeHtml(op.description)}</span>
                <span class="${cls}">${sign}${op.amount_rub} ₽</span>
            </div>
            <div class="muted" style="font-size:11px;">${formatDate(op.created_at)}</div>
        `;
        list.appendChild(row);
    });
}
