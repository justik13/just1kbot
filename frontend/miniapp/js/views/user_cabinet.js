import { api } from "../api.js";
import {
    copyText,
    escapeHtml,
    formatBytesGb,
    formatDate,
    openExternalLink,
    openModal,
    showConfirmModal,
    showToast,
} from "../ui.js";

const _state = {
    tab: null,
    platform: _detectPlatform(),
    data: null,
    avatarFailed: false,
};

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

function _calcStdPrice(pricing, devices, days) {
    const plans = (pricing && pricing.standard_plans) || {};
    const entry = plans[String(days)];
    if (entry && entry.base_rub != null && entry.step_rub != null) {
        return Number(entry.base_rub) + Math.max(0, devices - 2) * Number(entry.step_rub);
    }
    const fallback = { 7: [39, 12], 30: [99, 30], 90: [255, 78] }[days] || [99, 30];
    return fallback[0] + Math.max(0, devices - 2) * fallback[1];
}

function _calcBalanceSplit(priceRub, bal, useBalance, minTopupRub = 20) {
    const price = Math.max(0, Number(priceRub || 0));
    if (!useBalance || price === 0) {
        return { balUsed: 0, cardDue: price };
    }
    const minCard = Math.max(1, Number(minTopupRub || 20));
    const avail = Math.max(0, Number((bal && bal.total) || 0));
    let balUsed = Math.min(price, avail);
    let cardDue = Math.max(0, price - balUsed);
    if (balUsed > 0 && cardDue > 0 && cardDue < minCard && price >= minCard) {
        balUsed = price - minCard;
        cardDue = minCard;
    }
    return { balUsed, cardDue };
}

function _newIdempotencyKey() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
        return crypto.randomUUID();
    }
    return `idem-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function _handlePaymentResponse(res, successMsg, onReload) {
    if (!res) {
        showToast("Не удалось оформить платёж. Попробуйте ещё раз.");
        return;
    }
    if (res.fulfillment_error || res.fulfillment_failed) {
        showToast(res.fulfillment_error || "Не удалось активировать подписку. Средства сохранены на балансе.");
        onReload();
        return;
    }
    if (!res.confirmation_url) {
        const st = String(res.status || "").toLowerCase();
        if (st === "canceled" || st === "pending") {
            showToast("Платёж не был завершён. Попробуйте оформить заново.");
            return;
        }
        showToast(successMsg);
        onReload();
        return;
    }

    openExternalLink(res.confirmation_url);
    if (!res.order_id) {
        showToast("Открываем страницу оплаты...");
        return;
    }

    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.style.gap = "12px";
    wrap.innerHTML = `
        <div class="hint" id="modalPayHint">
            Страница оплаты открыта. После завершения платежа статус обновится автоматически — или нажмите «Проверить оплату».
        </div>
        <button id="modalPayCheckBtn" type="button" class="btn btn-primary btn-lg">Проверить оплату</button>
        <button id="modalPayReopenBtn" type="button" class="btn btn-secondary">Открыть оплату снова</button>
    `;

    const close = openModal("Ожидание оплаты", wrap);
    const hintEl = wrap.querySelector("#modalPayHint");
    const checkBtn = wrap.querySelector("#modalPayCheckBtn");
    const reopenBtn = wrap.querySelector("#modalPayReopenBtn");

    let stopped = false;
    let inFlight = false;
    let pollCount = 0;

    const cleanup = () => {
        if (stopped) return;
        stopped = true;
        clearInterval(timerId);
        document.removeEventListener("visibilitychange", onVisible);
        window.removeEventListener("focus", onFocus);
    };

    const checkNow = async (manual = false) => {
        if (stopped) return;
        if (!wrap.isConnected) {
            cleanup();
            return;
        }
        if (inFlight) return;
        inFlight = true;
        checkBtn.disabled = true;
        if (manual) checkBtn.textContent = "Проверяем...";
        try {
            const st = await api.checkOrderStatus(res.order_id);
            if (!wrap.isConnected || stopped) {
                cleanup();
                return;
            }
            if (st.status === "paid") {
                cleanup();
                close();
                if (st.service_type && st.service_type !== "topup") {
                    _state.tab = "connect";
                }
                showToast(successMsg);
                onReload();
                return;
            }
            if (st.status === "canceled") {
                cleanup();
                hintEl.textContent = "Платёж отменён или истёк. Попробуйте оформить заказ заново.";
                checkBtn.disabled = true;
                checkBtn.textContent = "Платёж отменён";
                return;
            }
            if (manual) {
                hintEl.textContent = "Платёж ещё обрабатывается банком. Попробуйте через пару секунд.";
            }
        } catch (err) {
            hintEl.textContent = err.message || "Не удалось проверить оплату";
        } finally {
            inFlight = false;
            if (!stopped) {
                checkBtn.disabled = false;
                checkBtn.textContent = "Проверить оплату";
            }
        }
    };

    const onVisible = () => {
        if (document.visibilityState === "visible") {
            checkNow(false);
        }
    };
    const onFocus = () => checkNow(false);

    const timerId = setInterval(() => {
        pollCount += 1;
        if (pollCount > 60 || !wrap.isConnected) {
            cleanup();
            return;
        }
        checkNow(false);
    }, 5000);

    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onFocus);

    checkBtn.addEventListener("click", () => checkNow(true));
    reopenBtn.addEventListener("click", () => openExternalLink(res.confirmation_url));
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
    let idemKey = _newIdempotencyKey();
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
            const res = await api.topupBalance(amount, idemKey);
            close();
            _handlePaymentResponse(res, "Баланс пополнен", onReload);
        } catch (err) {
            idemKey = _newIdempotencyKey();
            btn.disabled = false;
            showToast(err.message || "Не удалось создать платёж");
        }
    });
}

function _openStandardPlanModal(d, mode, onReload) {
    const std = d.standard || {};
    const bal = d.balance || { real: 0, bonus: 0, total: 0 };
    const pricing = d.pricing || {};
    const durations = pricing.standard_durations || [7, 30, 90];

    let devices = mode === "renew" ? Math.max(2, Number(std.device_limit || 2)) : 2;
    let duration = 30;
    let useBalance = true;
    let quote = null;

    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.style.gap = "12px";

    const close = openModal(mode === "renew" ? "Продление · Стандарт" : "Подключение · Стандарт", wrap);

    const fetchQuote = async () => {
        const reqDev = devices;
        const reqDur = duration;
        try {
            const q = await api.quoteStandard(reqDev, reqDur);
            if (reqDev !== devices || reqDur !== duration) return;
            quote = q;
            updateSummary();
        } catch {
            // Instant local fallback stays shown
        }
    };

    const getCheckoutText = (price, cardDue, balUsed) => {
        const verb = mode === "renew" ? "Продлить" : "Подключить";
        if (cardDue === 0 && price > 0) return `Оплатить с баланса (${price} ₽)`;
        if (balUsed > 0 && cardDue > 0) return `${verb} · доплата ${cardDue} ₽`;
        return `${verb} за ${cardDue} ₽`;
    };

    const updateSummary = () => {
        const summaryEl = wrap.querySelector("#modalStdSummary");
        const submitBtn = wrap.querySelector("#modalStdPayBtn");
        if (!summaryEl || !submitBtn) return;

        const basePrice = _calcStdPrice(pricing, devices, duration);
        const price = quote ? Number(quote.due_rub ?? basePrice) : basePrice;
        const originalPrice = quote ? Number(quote.original_price_rub ?? price) : basePrice;
        const discount = quote ? Number(quote.discount_rub || 0) : 0;
        const split = _calcBalanceSplit(price, bal, useBalance, pricing.min_topup_rub || 20);

        const lines = [`<div class="quote-line"><span>Тариф (${devices} устр. · ${duration} дн.)</span><strong>${originalPrice} ₽</strong></div>`];
        if (discount > 0) {
            lines.push(`<div class="quote-line"><span>Приветственная скидка</span><strong style="color:var(--accent-emerald);">-${discount} ₽</strong></div>`);
        }
        if (split.balUsed > 0) {
            lines.push(`<div class="quote-line"><span>С баланса</span><strong>-${split.balUsed} ₽</strong></div>`);
        }
        lines.push(
            split.cardDue > 0
                ? `<div class="quote-line"><span>К оплате картой / СБП</span><strong>${split.cardDue} ₽</strong></div>`
                : `<div class="quote-line"><span>К оплате картой / СБП</span><strong style="color:var(--accent-emerald);">0 ₽</strong></div>`
        );
        const blockedReason = quote && quote.blocked_reason ? quote.blocked_reason : null;
        if (blockedReason) {
            lines.push(`<div class="notice-box notice-warn" style="margin-top:6px;"><span>${escapeHtml(blockedReason)}</span></div>`);
        }
        summaryEl.innerHTML = lines.join("");
        submitBtn.textContent = getCheckoutText(price, split.cardDue, split.balUsed);
        submitBtn.disabled = Boolean(blockedReason);
    };

    const renderModal = () => {
        wrap.innerHTML = `
            ${mode === "buy" ? `
                <div class="field">
                    <label>Готовые конфигурации</label>
                    <div class="filter-pills">
                        <button type="button" class="pill-btn ${devices === 2 ? "active" : ""}" data-preset="2">Базовый · 2</button>
                        <button type="button" class="pill-btn ${devices === 5 ? "active" : ""}" data-preset="5">Семейный · 5</button>
                        <button type="button" class="pill-btn ${devices === 10 ? "active" : ""}" data-preset="10">Pro · 10</button>
                    </div>
                </div>
                <div class="field">
                    <label>Точное число устройств</label>
                    <div class="stepper">
                        <button type="button" class="stepper-btn" id="stepMinus" ${devices <= 2 ? "disabled" : ""}>−</button>
                        <span class="stepper-val">${devices} устр.</span>
                        <button type="button" class="stepper-btn" id="stepPlus" ${devices >= 10 ? "disabled" : ""}>+</button>
                    </div>
                </div>
            ` : `
                <div class="notice-box">
                    <span>Текущий лимит: <strong style="color:var(--text-primary);">${devices} устр.</strong></span>
                    <span>+ дни к сроку</span>
                </div>
            `}
            <div class="field">
                <label>Выберите срок</label>
                <div class="price-tile-grid">
                    ${durations.map((days) => {
                        const p = _calcStdPrice(pricing, devices, days);
                        return `
                            <button type="button" class="price-tile ${duration === days ? "active" : ""}" data-dur="${days}">
                                <span class="price-tile-top">${days} дн.</span>
                                <span class="price-tile-price">${p} ₽</span>
                            </button>
                        `;
                    }).join("")}
                </div>
            </div>
            <label class="toggle-row">
                <span class="toggle-label">Списать с баланса (доступно ${bal.total} ₽)</span>
                <span class="switch">
                    <input id="modalStdBalChk" type="checkbox" ${useBalance ? "checked" : ""}>
                    <span class="switch-track"></span>
                </span>
            </label>
            <div id="modalStdSummary" class="quote-box"></div>
            <button id="modalStdPayBtn" type="button" class="btn btn-primary btn-lg"></button>
        `;

        wrap.querySelectorAll("[data-preset]").forEach((b) => {
            b.addEventListener("click", () => {
                devices = Number(b.dataset.preset);
                quote = null;
                renderModal();
                fetchQuote();
            });
        });

        const minus = wrap.querySelector("#stepMinus");
        const plus = wrap.querySelector("#stepPlus");
        if (minus) {
            minus.addEventListener("click", () => {
                if (devices > 2) {
                    devices -= 1;
                    quote = null;
                    renderModal();
                    fetchQuote();
                }
            });
        }
        if (plus) {
            plus.addEventListener("click", () => {
                if (devices < 10) {
                    devices += 1;
                    quote = null;
                    renderModal();
                    fetchQuote();
                }
            });
        }

        wrap.querySelectorAll("[data-dur]").forEach((b) => {
            b.addEventListener("click", () => {
                duration = Number(b.dataset.dur);
                quote = null;
                renderModal();
                fetchQuote();
            });
        });

        const balChk = wrap.querySelector("#modalStdBalChk");
        balChk.addEventListener("change", () => {
            useBalance = balChk.checked;
            updateSummary();
        });

        const payBtn = wrap.querySelector("#modalStdPayBtn");
        let idemKey = _newIdempotencyKey();
        payBtn.addEventListener("click", async () => {
            payBtn.disabled = true;
            try {
                const res = await api.checkoutStandard(devices, duration, useBalance, idemKey);
                close();
                _handlePaymentResponse(res, "Подписка «Стандарт» оформлена", onReload);
            } catch (err) {
                idemKey = _newIdempotencyKey();
                payBtn.disabled = false;
                showToast(err.message || "Ошибка оплаты");
            }
        });

        updateSummary();
    };

    renderModal();
    fetchQuote();
}

function _openStandardDevicesModal(d, onReload) {
    const std = d.standard || {};
    const bal = d.balance || { real: 0, bonus: 0, total: 0 };
    const currentLimit = Math.max(2, Number(std.device_limit || 2));
    let targetLimit = currentLimit < 10 ? currentLimit + 1 : currentLimit - 1;
    let useBalance = true;
    let quote = null;

    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.style.gap = "12px";

    const close = openModal("Устройства · Стандарт", wrap);

    const fetchQuote = async () => {
        if (targetLimit === currentLimit) {
            quote = null;
            renderContent();
            return;
        }
        const reqTarget = targetLimit;
        try {
            const q = await api.quoteStandard(reqTarget, 30);
            if (reqTarget !== targetLimit) return;
            quote = q;
            renderContent();
        } catch (err) {
            showToast(err.message || "Не удалось рассчитать изменение");
        }
    };

    const renderContent = () => {
        const isSame = targetLimit === currentLimit;
        const isUpgrade = targetLimit > currentLimit;
        const isDowngrade = targetLimit < currentLimit;
        const price = quote ? Number(quote.due_rub || 0) : 0;
        const split = _calcBalanceSplit(price, bal, useBalance, (d.pricing && d.pricing.min_topup_rub) || 20);
        const blockedReason = quote && quote.blocked_reason ? quote.blocked_reason : null;

        let infoHtml = `<div class="muted">Выберите новое число устройств, отличное от текущего (${currentLimit} устр.).</div>`;
        if (!isSame && !quote) {
            infoHtml = `<div class="muted">Расчёт условий...</div>`;
        } else if (isUpgrade && quote) {
            infoHtml = `
                <div class="quote-box">
                    <div class="quote-line"><span>Доплата до конца текущего срока (${quote.days_left || 1} дн.)</span><strong>${price} ₽</strong></div>
                    ${split.balUsed > 0 ? `<div class="quote-line"><span>С баланса</span><strong>-${split.balUsed} ₽</strong></div>` : ""}
                    <div class="quote-line"><span>К оплате картой / СБП</span><strong>${split.cardDue} ₽</strong></div>
                </div>
            `;
        } else if (isDowngrade && quote) {
            const tooManyDevices = Boolean(
                blockedReason && blockedReason.includes("устройств") && Number(quote.active_hwid_count || 0) > targetLimit
            );
            infoHtml = blockedReason
                ? `<div class="notice-box notice-warn" style="flex-direction:column; align-items:flex-start; gap:8px;">
                        <span>${escapeHtml(blockedReason)}</span>
                        ${tooManyDevices ? `<button id="devGoDevicesBtn" type="button" class="btn btn-sm">Управлять устройствами</button>` : ""}
                   </div>`
                : `<div class="quote-box">
                        ${Number(quote.bonus_credit_rub || 0) > 0
                            ? `<div class="quote-line"><span>Возврат бонусами (${quote.days_left || 0} полн. дн.)</span><strong style="color:var(--accent-emerald);">+${quote.bonus_credit_rub} ₽</strong></div>`
                            : `<div class="quote-line"><span>Возврат бонусами</span><strong>0 ₽</strong></div>`}
                        <div class="quote-line"><span>Следующее снижение</span><strong>через 7 дней</strong></div>
                   </div>
                   ${Number(quote.bonus_credit_rub || 0) > 0
                       ? `<div class="muted" style="font-size:11px;">Бонусами можно оплатить до 100% следующих заказов.</div>`
                       : ""}`;
        }

        const btnLabel = isSame
            ? "Выберите другой лимит"
            : (isUpgrade
                ? (split.cardDue === 0 ? `Увеличить за ${price} ₽ с баланса` : `Доплатить ${split.cardDue} ₽`)
                : `Снизить до ${targetLimit} устр.`);

        wrap.innerHTML = `
            <div class="notice-box">
                <span>Сейчас активно: <strong style="color:var(--text-primary);">${currentLimit} устр.</strong></span>
                <span>до ${formatDate(std.expires_at)}</span>
            </div>
            <div class="field">
                <label>Новый лимит устройств</label>
                <div class="stepper">
                    <button type="button" class="stepper-btn" id="devMinus" ${targetLimit <= 2 ? "disabled" : ""}>−</button>
                    <span class="stepper-val">${targetLimit} устр.</span>
                    <button type="button" class="stepper-btn" id="devPlus" ${targetLimit >= 10 ? "disabled" : ""}>+</button>
                </div>
            </div>
            ${isUpgrade ? `
                <label class="toggle-row">
                    <span class="toggle-label">Списать с баланса (доступно ${bal.total} ₽)</span>
                    <span class="switch">
                        <input id="devBalChk" type="checkbox" ${useBalance ? "checked" : ""}>
                        <span class="switch-track"></span>
                    </span>
                </label>
            ` : ""}
            ${infoHtml}
            <button id="devApplyBtn" type="button" class="btn btn-primary btn-lg" ${isSame || !quote || blockedReason ? "disabled" : ""}>
                ${btnLabel}
            </button>
        `;

        wrap.querySelector("#devMinus").addEventListener("click", () => {
            if (targetLimit > 2) {
                targetLimit -= 1;
                quote = null;
                renderContent();
                fetchQuote();
            }
        });
        wrap.querySelector("#devPlus").addEventListener("click", () => {
            if (targetLimit < 10) {
                targetLimit += 1;
                quote = null;
                renderContent();
                fetchQuote();
            }
        });
        const balChk = wrap.querySelector("#devBalChk");
        if (balChk) {
            balChk.addEventListener("change", () => {
                useBalance = balChk.checked;
                renderContent();
            });
        }
        const goDevBtn = wrap.querySelector("#devGoDevicesBtn");
        if (goDevBtn) {
            goDevBtn.addEventListener("click", () => {
                close();
                const connectTab = document.querySelector('[data-utab="connect"]');
                if (connectTab) connectTab.click();
            });
        }

        let idemKey = _newIdempotencyKey();
        wrap.querySelector("#devApplyBtn").addEventListener("click", async () => {
            const btn = wrap.querySelector("#devApplyBtn");
            if (isUpgrade) {
                btn.disabled = true;
                try {
                    const res = await api.checkoutStandard(targetLimit, 30, useBalance, idemKey);
                    close();
                    _handlePaymentResponse(res, `Лимит увеличен до ${targetLimit} устр.`, onReload);
                } catch (err) {
                    idemKey = _newIdempotencyKey();
                    btn.disabled = false;
                    showToast(err.message || "Не удалось изменить лимит");
                }
                return;
            }
            const bonusAmt = Number((quote && quote.bonus_credit_rub) || 0);
            const bonusMsg = bonusAmt > 0 ? ` На бонусный баланс вернётся ${bonusAmt} ₽.` : "";
            showConfirmModal({
                title: "Снизить лимит устройств?",
                message: `Лимит уменьшится с ${currentLimit} до ${targetLimit} устр.${bonusMsg} Следующее снижение будет доступно через 7 дней.`,
                confirmText: `Снизить до ${targetLimit} устр.`,
                cancelText: "Отмена",
                onConfirm: async () => {
                    btn.disabled = true;
                    try {
                        await api.downgradeStandard(targetLimit);
                        close();
                        showToast(`Лимит снижен до ${targetLimit} устр.`);
                        onReload();
                    } catch (err) {
                        btn.disabled = false;
                        showToast(err.message || "Не удалось изменить лимит");
                    }
                },
            });
        });
    };

    renderContent();
    fetchQuote();
}

function _openWiPurchaseModal(d, mode, onReload) {
    const isRenew = mode === "renew";
    const wi = d.wi || {};
    const bal = d.balance || { real: 0, bonus: 0, total: 0 };
    const pricing = d.pricing || {};
    const ref = d.referrals || {};
    const minTopup = Number(pricing.min_topup_rub || 20);
    const base = Number(pricing.wi_base_price_rub || 250);
    const extra = Number(pricing.wi_extra_device_price_rub || 200);
    const curLimit = Math.max(1, Number(wi.device_limit || 1));
    const isActivePeriod = Boolean(
        isRenew && wi.expires_at && new Date(wi.expires_at).getTime() > Date.now()
    );
    const defaultDevices = isRenew
        ? (isActivePeriod ? curLimit : Math.max(1, Number(wi.pending_device_limit || curLimit)))
        : 1;
    let devices = defaultDevices;
    let useBalance = true;

    const welcomeDiscountEligible = Boolean(!isRenew && ref.welcome_discount_eligible);
    const welcomeDiscountPct = Number(ref.welcome_discount_pct || 25);

    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.style.gap = "12px";
    const close = openModal(isRenew ? "Продление · Белый интернет" : "Подключение · Белый интернет", wrap);

    const renderModal = () => {
        const originalPrice = base + Math.max(0, devices - 1) * extra;
        const discount = welcomeDiscountEligible
            ? Math.floor((originalPrice * welcomeDiscountPct) / 100)
            : 0;
        const total = Math.max(1, originalPrice - discount);
        const split = _calcBalanceSplit(total, bal, useBalance, minTopup);
        const verb = isRenew ? "Продлить" : "Подключить";
        const ctaText = split.cardDue === 0
            ? `Оплатить с баланса (${total} ₽)`
            : (split.balUsed > 0 ? `${verb} · доплата ${split.cardDue} ₽` : `${verb} за ${total} ₽`);

        wrap.innerHTML = `
            ${isRenew ? `
                <div class="notice-box">
                    <span>Сейчас активно: <strong style="color:var(--text-primary);">${curLimit} устр.</strong></span>
                    <span>до ${formatDate(wi.expires_at)}</span>
                </div>
                ${wi.pending_device_limit ? `
                    <div class="notice-box notice-info" style="font-size:11px; padding:6px 10px;">
                        <span>${isActivePeriod
                            ? `Запланировано снижение до ${wi.pending_device_limit} устр. с ${formatDate(wi.pending_from || wi.expires_at)}. Переход произойдёт в эту дату (автопродление или продление после окончания срока). Продление сейчас на ${curLimit} устр. отменит запланированное снижение.`
                            : `Запланировано снижение до ${wi.pending_device_limit} устр. Вы можете продлить на ${wi.pending_device_limit} устр. или выбрать другое число слотов ниже.`
                        }</span>
                    </div>
                ` : ""}
            ` : `
                <div class="muted" style="font-size:12px;">
                    Независимая подписка на 30 дней для мобильной сети при жёстких ограничениях. Работает как отдельно, так и в дополнение к «Стандарту».
                </div>
                ${d.devices && d.devices.length > devices ? (() => {
                    const sortedDevs = [...d.devices].sort((a, b) => (a.id || 0) - (b.id || 0));
                    const firstDevName = sortedDevs[0]?.device_name || sortedDevs[0]?.platform || "Устройство";
                    return `
                    <div class="notice-box notice-info" style="font-size:11px; padding:6px 10px; margin-top:4px;">
                        <span>Слот будет привязан к основному устройству (${escapeHtml(firstDevName)}). Выбрать другое можно в любой момент на вкладке подключения.</span>
                    </div>
                    `;
                })() : ""}
            `}
            <div class="field">
                <label>${isRenew ? "Количество слотов на новый период (+30 дн.)" : "Количество устройств (по 50 ГБ на слот)"}</label>
                <div class="price-tile-grid">
                    ${[1, 2, 3].map((n) => {
                        const p = base + (n - 1) * extra;
                        const isDisabled = isActivePeriod && n < curLimit;
                        return `
                            <button type="button" class="price-tile ${devices === n ? "active" : ""}" data-wi-cnt="${n}" ${isDisabled ? "disabled" : ""}>
                                <span class="price-tile-top">${n} устр. · ${n * 50} ГБ</span>
                                <span class="price-tile-price">${p} ₽</span>
                            </button>
                        `;
                    }).join("")}
                </div>
                ${isActivePeriod && curLimit > 1 ? `
                    <div class="muted" style="font-size:11px;">Снизить число слотов со следующего периода можно в «Слоты и трафик».</div>
                ` : ""}
            </div>
            <label class="toggle-row">
                <span class="toggle-label">Списать с баланса (доступно ${bal.total} ₽)</span>
                <span class="switch switch-wi">
                    <input id="modalWiBalChk" type="checkbox" ${useBalance ? "checked" : ""}>
                    <span class="switch-track"></span>
                </span>
            </label>
            <div class="quote-box">
                <div class="quote-line"><span>${isRenew ? "Продление" : "Подписка"} (${devices} устр. · +30 дн.)</span><strong>${originalPrice} ₽</strong></div>
                ${discount > 0 ? `<div class="quote-line"><span>Приветственная скидка</span><strong style="color:var(--accent-emerald);">-${discount} ₽</strong></div>` : ""}
                ${split.balUsed > 0 ? `<div class="quote-line"><span>С баланса</span><strong>-${split.balUsed} ₽</strong></div>` : ""}
                <div class="quote-line"><span>К оплате картой / СБП</span><strong ${split.cardDue === 0 ? 'style="color:var(--accent-emerald);"' : ""}>${split.cardDue} ₽</strong></div>
            </div>
            <button id="modalWiPayBtn" type="button" class="btn btn-primary btn-lg">${ctaText}</button>
        `;

        wrap.querySelectorAll("[data-wi-cnt]").forEach((b) => {
            b.addEventListener("click", () => {
                devices = Number(b.dataset.wiCnt);
                renderModal();
            });
        });

        const balChk = wrap.querySelector("#modalWiBalChk");
        balChk.addEventListener("change", () => {
            useBalance = balChk.checked;
            renderModal();
        });

        const payBtn = wrap.querySelector("#modalWiPayBtn");
        let idemKey = _newIdempotencyKey();
        payBtn.addEventListener("click", async () => {
            payBtn.disabled = true;
            try {
                const res = await api.checkoutWi({
                    action: isRenew ? "renew" : "buy",
                    device_limit: devices,
                    use_balance: useBalance,
                    idempotency_key: idemKey,
                });
                close();
                _handlePaymentResponse(
                    res,
                    isRenew ? "Подписка продлена на 30 дней" : "Подписка «Белый интернет» подключена",
                    onReload
                );
            } catch (err) {
                idemKey = _newIdempotencyKey();
                payBtn.disabled = false;
                showToast(err.message || (isRenew ? "Ошибка продления" : "Ошибка подключения"));
            }
        });
    };

    renderModal();
}

function _openWiManageModal(d, onReload) {
    const wi = d.wi || {};
    const bal = d.balance || { real: 0, bonus: 0, total: 0 };
    const pricing = d.pricing || {};
    const minTopup = Number(pricing.min_topup_rub || 20);
    const wiCurLimit = Number(wi.device_limit || 1);
    const wiMaxDevices = pricing.wi_max_devices || 3;
    const wiPacks = pricing.wi_topup_packs || [];
    const wiUsedGb = formatBytesGb(wi.traffic_used_bytes);
    const wiTotalGb = formatBytesGb(wi.traffic_limit_bytes);
    const canAddSlot = Boolean(wiCurLimit < wiMaxDevices && wi.add_slot_price_rub != null);
    const addSlotPrice = Number(wi.add_slot_price_rub || 0);
    const addSlotGb = Number(wi.add_slot_gb || 50);

    // selectedItem: { kind: "slot" } | { kind: "pack", gb: number, price: number }
    let selectedItem = wiPacks.length
        ? { kind: "pack", gb: Number(wiPacks[0].gb), price: Number(wiPacks[0].price_rub) }
        : (canAddSlot ? { kind: "slot" } : null);
    let useBalance = true;

    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.style.gap = "12px";

    const close = openModal("Слоты и трафик · Белый интернет", wrap);

    const renderModal = () => {
        let itemLabel = "";
        let itemPrice = 0;
        if (selectedItem && selectedItem.kind === "slot") {
            itemLabel = `Доп. слот (+1 устр. · +${addSlotGb} ГБ)`;
            itemPrice = addSlotPrice;
        } else if (selectedItem && selectedItem.kind === "pack") {
            itemLabel = `Пакет трафика (+${selectedItem.gb} ГБ)`;
            itemPrice = selectedItem.price;
        }
        const split = _calcBalanceSplit(itemPrice, bal, useBalance, minTopup);
        const ctaText = split.cardDue === 0
            ? `Оплатить с баланса (${itemPrice} ₽)`
            : (split.balUsed > 0 ? `Оплатить · доплата ${split.cardDue} ₽` : `Оплатить ${itemPrice} ₽`);

        wrap.innerHTML = `
            <div class="quote-box">
                <div class="quote-line"><span>Трафик</span><strong>${wiUsedGb} / ${wiTotalGb} ГБ</strong></div>
                <div class="quote-line"><span>Слотов устройств</span><strong>${wiCurLimit}</strong></div>
                <div class="quote-line"><span>Баланс аккаунта</span><strong>${bal.total} ₽</strong></div>
            </div>
            ${canAddSlot ? `
                <div class="field">
                    <label>Дополнительный слот (до ${wiMaxDevices} слотов в тарифе)</label>
                    <button id="modalWiSelectSlotBtn" type="button" class="price-tile ${selectedItem && selectedItem.kind === "slot" ? "active" : ""}" style="width:100%; flex-direction:row; justify-content:space-between; padding:11px 14px;">
                        <span class="price-tile-top">+1 слот устройства (+${addSlotGb} ГБ)</span>
                        <span class="price-tile-price">${addSlotPrice} ₽</span>
                    </button>
                </div>
            ` : (wiCurLimit >= wiMaxDevices ? `
                <div class="notice-box notice-info" style="font-size:12px; margin-top:2px;">
                    <span>Достигнут максимум тарифа (${wiMaxDevices} слота). Больше слотов добавить нельзя.</span>
                </div>
            ` : "")}
            ${wiPacks.length ? `
                <div class="field">
                    <label>Дополнительный трафик</label>
                    <div class="price-tile-grid">
                        ${wiPacks.map((p) => {
                            const isSel = selectedItem && selectedItem.kind === "pack" && Number(selectedItem.gb) === Number(p.gb);
                            return `
                                <button type="button" class="price-tile ${isSel ? "active" : ""}" data-modal-wi-pack="${p.gb}" data-modal-wi-price="${p.price_rub}">
                                    <span class="price-tile-top">+${p.gb} ГБ</span>
                                    <span class="price-tile-price">${p.price_rub} ₽</span>
                                </button>
                            `;
                        }).join("")}
                    </div>
                </div>
            ` : ""}
            ${selectedItem ? `
                <label class="toggle-row">
                    <span class="toggle-label">Списать с баланса (доступно ${bal.total} ₽)</span>
                    <span class="switch switch-wi">
                        <input id="modalWiManageBalChk" type="checkbox" ${useBalance ? "checked" : ""}>
                        <span class="switch-track"></span>
                    </span>
                </label>
                <div class="quote-box">
                    <div class="quote-line"><span>${escapeHtml(itemLabel)}</span><strong>${itemPrice} ₽</strong></div>
                    ${split.balUsed > 0 ? `<div class="quote-line"><span>С баланса</span><strong>-${split.balUsed} ₽</strong></div>` : ""}
                    <div class="quote-line"><span>К оплате картой / СБП</span><strong ${split.cardDue === 0 ? 'style="color:var(--accent-emerald);"' : ""}>${split.cardDue} ₽</strong></div>
                </div>
                <button id="modalWiManagePayBtn" type="button" class="btn btn-primary btn-lg">${ctaText}</button>
            ` : ""}
            ${wi.pending_device_limit ? `
                <div class="notice-box notice-warn">
                    <span>Снижение до ${wi.pending_device_limit} устр. с ${formatDate(wi.pending_from)}</span>
                    <button id="modalWiCancelDgBtn" type="button" class="btn btn-sm btn-danger">Отменить</button>
                </div>
            ` : (wiCurLimit > 1 ? `
                <details class="guide-details">
                    <summary class="guide-summary">
                        <span>Снизить число слотов со следующего периода</span>
                        <span class="muted">▾</span>
                    </summary>
                    <div class="guide-body">
                        <div class="actions-row">
                            ${Array.from({ length: wiCurLimit - 1 }, (_, i) => i + 1).map((target) => `
                                <button type="button" class="btn btn-sm" data-modal-wi-dg="${target}">
                                    Снизить до ${target} устр.
                                </button>
                            `).join("")}
                        </div>
                    </div>
                </details>
            ` : "")}
        `;

        const selectSlotBtn = wrap.querySelector("#modalWiSelectSlotBtn");
        if (selectSlotBtn) {
            selectSlotBtn.addEventListener("click", () => {
                selectedItem = { kind: "slot" };
                renderModal();
            });
        }

        wrap.querySelectorAll("[data-modal-wi-pack]").forEach((b) => {
            b.addEventListener("click", () => {
                selectedItem = {
                    kind: "pack",
                    gb: Number(b.dataset.modalWiPack),
                    price: Number(b.dataset.modalWiPrice),
                };
                renderModal();
            });
        });

        const balChk = wrap.querySelector("#modalWiManageBalChk");
        if (balChk) {
            balChk.addEventListener("change", () => {
                useBalance = balChk.checked;
                renderModal();
            });
        }

        const payBtn = wrap.querySelector("#modalWiManagePayBtn");
        let idemKey = _newIdempotencyKey();
        if (payBtn && selectedItem) {
            payBtn.addEventListener("click", async () => {
                payBtn.disabled = true;
                try {
                    if (selectedItem.kind === "slot") {
                        const res = await api.checkoutWi({
                            action: "add_slot",
                            use_balance: useBalance,
                            idempotency_key: idemKey,
                        });
                        close();
                        _handlePaymentResponse(res, "Слот добавлен", onReload);
                    } else {
                        const res = await api.checkoutWi({
                            action: "topup",
                            gb: Number(selectedItem.gb),
                            use_balance: useBalance,
                            idempotency_key: idemKey,
                        });
                        close();
                        _handlePaymentResponse(res, "Пакет трафика добавлен", onReload);
                    }
                } catch (err) {
                    idemKey = _newIdempotencyKey();
                    payBtn.disabled = false;
                    showToast(err.message || "Ошибка оформления");
                }
            });
        }

        wrap.querySelectorAll("[data-modal-wi-dg]").forEach((b) => {
            b.addEventListener("click", () => {
                const target = Number(b.dataset.modalWiDg);
                showConfirmModal({
                    title: "Снизить число слотов?",
                    message: `Со следующего периода (${formatDate(wi.expires_at)}) лимит уменьшится до ${target} устр. (${target * 50} ГБ). До конца текущего срока сохранится ${wiCurLimit} устр.`,
                    confirmText: `Снизить до ${target} устр.`,
                    cancelText: "Отмена",
                    onConfirm: async () => {
                        b.disabled = true;
                        try {
                            await api.scheduleWiDowngrade(target);
                            close();
                            showToast("Снижение запланировано на следующий период");
                            onReload();
                        } catch (err) {
                            showToast(err.message || "Ошибка");
                        } finally {
                            b.disabled = false;
                        }
                    },
                });
            });
        });

        const cancelDgBtn = wrap.querySelector("#modalWiCancelDgBtn");
        if (cancelDgBtn) {
            cancelDgBtn.addEventListener("click", async () => {
                cancelDgBtn.disabled = true;
                try {
                    await api.cancelWiDowngrade();
                    close();
                    showToast("Отложенное снижение отменено");
                    onReload();
                } catch (err) {
                    showToast(err.message || "Ошибка отмены");
                } finally {
                    cancelDgBtn.disabled = false;
                }
            });
        }
    };

    renderModal();
}

function _wiHasActivePeriod(wi) {
    if (!wi) return false;
    if (wi.has_period || wi.active) return true;
    const status = String(wi.status || "").toUpperCase();
    return Boolean(
        ["ACTIVE", "PENDING", "EXHAUSTED"].includes(status)
        && wi.expires_at
        && new Date(wi.expires_at).getTime() > Date.now()
    );
}

export async function loadAndRenderUserCabinet(root) {
    try {
        const data = await api.getUserDashboard();
        _state.data = data;
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
        _render(root);
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

function _getProfileMeta(d) {
    const tgUser = (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initDataUnsafe && window.Telegram.WebApp.initDataUnsafe.user) || {};
    const displayName = (d.user && d.user.name) || [tgUser.first_name, tgUser.last_name].filter(Boolean).join(" ") || (tgUser.username ? `@${tgUser.username}` : "Профиль");
    const initial = String(displayName.replace(/^@/, "").trim().charAt(0) || "J").toUpperCase();
    const photoUrl = tgUser.photo_url || "";
    return {
        displayName,
        username: tgUser.username ? `@${tgUser.username}` : "",
        userId: (d.user && d.user.telegram_id) || tgUser.id || "",
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
    const meta = _getProfileMeta(d);
    const avatarKey = meta.photoUrl && !_state.avatarFailed ? meta.photoUrl : meta.initial;
    if (avatarEl && avatarEl.dataset.renderedKey !== avatarKey) {
        avatarEl.dataset.renderedKey = avatarKey;
        avatarEl.innerHTML = _renderAvatarInner(meta);
        _bindAvatarImg(avatarEl, meta.initial);
    }
    if (balEl) balEl.textContent = `${bal.total ?? 0} ₽`;
    pill.classList.toggle("active", _state.tab === "profile");

    pill.onclick = () => {
        if (_state.tab === "profile") return;
        _state.tab = "profile";
        _render(root);
        window.scrollTo({ top: 0, behavior: "smooth" });
    };
}

function _render(root) {
    const d = _state.data;
    if (!d) return;
    const prevGuideOpen = Boolean(root.querySelector("#connectGuide")?.open);
    const prevScrollY = window.scrollY;
    const stdActive = Boolean(d.standard && d.standard.active);
    const wiActive = _wiHasActivePeriod(d.wi);
    const hasAny = stdActive || wiActive;

    const navTabs = [
        { id: "connect", label: "Доступ", icon: _ICONS.bolt },
        { id: "tariffs", label: "Тарифы", icon: _ICONS.card },
        { id: "more", label: "Ещё", icon: _ICONS.more },
    ];
    const validTabs = ["connect", "tariffs", "more", "profile"];

    if (!validTabs.includes(_state.tab)) {
        _state.tab = hasAny ? "connect" : "tariffs";
    }

    _syncTopbarProfile(d, root);

    root.innerHTML = `
        <div id="userTabBody" class="app-main"></div>
        <nav class="bottom-nav">
            ${navTabs.map((t) => `
                <button type="button" class="nav-item ${_state.tab === t.id ? "active" : ""}" data-utab="${t.id}">
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
            _render(root);
            window.scrollTo({ top: 0, behavior: "smooth" });
        });
    });

    const body = root.querySelector("#userTabBody");
    const reload = () => loadAndRenderUserCabinet(root);

    if (_state.tab === "connect") _renderConnectTab(body, d, reload, root);
    else if (_state.tab === "tariffs") _renderTariffsTab(body, d, reload);
    else if (_state.tab === "more") _renderMoreTab(body, d, reload);
    else _renderProfileTab(body, d, reload);

    if (prevGuideOpen) {
        const nextGuide = root.querySelector("#connectGuide");
        if (nextGuide) nextGuide.open = true;
    }
    if (prevScrollY > 0 && _state.tab !== "profile") {
        window.scrollTo(0, prevScrollY);
    }
}

function _buildGuideHtml(subUrl, backupSubUrl, isActive) {
    const platformLabels = { ios: "iOS", android: "Android", windows: "Windows", macos: "macOS" };
    const clients = _CLIENT_LINKS[_state.platform] || _CLIENT_LINKS.ios;

    return `
        <details class="guide-details guide-details-prominent" id="connectGuide">
            <summary class="guide-summary">
                <span>Как подключить</span>
                <span class="chip" style="padding:2px 8px;">${platformLabels[_state.platform] || "iOS"} ▾</span>
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
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <label style="margin:0;">Основная ссылка подключения</label>
                            <button id="copyInlineSubBtn" type="button" class="btn btn-sm" style="padding:4px 10px; font-size:12px;">Скопировать</button>
                        </div>
                        <div class="sub-url-box">${escapeHtml(subUrl)}</div>
                    </div>
                ` : ""}
                ${isActive && backupSubUrl ? `
                    <div class="field" style="margin-top:8px;">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <label style="margin:0;">Резервная ссылка (прямая)</label>
                            <button id="copyBackupSubBtn" type="button" class="btn btn-sm" style="padding:4px 10px; font-size:12px;">Скопировать</button>
                        </div>
                        <div class="sub-url-box" style="font-size:11px; opacity:0.85;">${escapeHtml(backupSubUrl)}</div>
                        <span class="muted" style="font-size:10px; margin-top:2px;">Используйте прямое подключение, если по основной ссылке есть задержки</span>
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
                    <span class="hero-kicker err">Ограничение доступа</span>
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
                    <span class="hero-kicker muted">Не подключено</span>
                    <h1 class="hero-headline-lg">Нет активных подписок</h1>
                    <p class="hero-sub">Оформите «Стандарт» или «Белый интернет» — ссылка подключения активируется сразу после оплаты.</p>
                </div>
                <button id="goTariffsBtn" type="button" class="btn btn-primary btn-lg">Перейти к тарифам</button>
                ${_buildGuideHtml(subUrl, backupSubUrl, false)}
            </section>
            ${(d.devices || []).length ? _buildDevicesSectionHtml(d) : ""}
        `;
        body.querySelector("#goTariffsBtn").addEventListener("click", () => {
            _state.tab = "tariffs";
            _render(root);
        });
        _bindGuideEvents(body, root);
        _bindDevicesEvents(body, d, reload);
        return;
    }

    const stdExpMs = std.expires_at ? new Date(std.expires_at).getTime() : 0;
    const isStdGrace = Boolean(std.active && stdExpMs && stdExpMs < Date.now());
    const graceEndMs = stdExpMs + (4 * 3600 * 1000);
    const graceHoursLeft = Math.max(1, Math.ceil((graceEndMs - Date.now()) / (3600 * 1000)));

    const wiLimitBytes = Number(wi.traffic_limit_bytes || 0);
    const wiUsedBytes = Number(wi.traffic_used_bytes || 0);
    const wiRemainingBytes = Math.max(0, wiLimitBytes - wiUsedBytes);
    const wiRemainingGb = formatBytesGb(wiRemainingBytes);
    const wiRemainingPct = wiLimitBytes > 0 ? Math.min(100, Math.max(0, Math.round((wiRemainingBytes / wiLimitBytes) * 100))) : 0;
    const wiExhausted = Boolean(wiHasPeriod && (String(wi.status || "").toUpperCase() === "EXHAUSTED" || (wiLimitBytes > 0 && wiUsedBytes >= wiLimitBytes)));

    const _shortDate = (iso) => {
        const dStr = formatDate(iso);
        return dStr ? String(dStr).split(",")[0].trim() : "";
    };

    const stdTileHtml = std.active
        ? `
            <button type="button" class="channel-tile ${isStdGrace ? "is-warn" : "is-active"}" data-jump-tariffs="1">
                <div class="channel-tile-head">
                    <span class="channel-name">Стандарт</span>
                    <span class="channel-indicator"></span>
                </div>
                <div class="channel-metric">∞ <span class="channel-metric-unit">ГБ</span></div>
                <div class="channel-foot">
                    <div class="channel-meta">
                        <span style="white-space:nowrap;">${Number(std.device_limit || 2)} устр.</span>
                        <span style="white-space:nowrap;">${isStdGrace ? `~${graceHoursLeft} ч.` : `до ${_shortDate(std.expires_at)}`}</span>
                    </div>
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
                        <span>Не подключен</span>
                        <span>Подключить</span>
                    </div>
                </div>
            </button>
        `;

    const wiTileHtml = wiHasPeriod
        ? `
            <button type="button" class="channel-tile is-wi ${wiExhausted ? "is-warn" : "is-active"}" data-jump-tariffs="1">
                <div class="channel-tile-head">
                    <span class="channel-name">Белый интернет</span>
                    <span class="channel-indicator"></span>
                </div>
                <div class="channel-metric">${wiExhausted ? "0" : wiRemainingGb} <span class="channel-metric-unit">ГБ ост.</span></div>
                <div class="channel-foot">
                    <div class="progress-track">
                        <div class="progress-fill" style="width:${wiExhausted ? 0 : wiRemainingPct}%;"></div>
                    </div>
                    <div class="channel-meta">
                        <span style="white-space:nowrap;">${Number(wi.device_limit || 1)} слот</span>
                        <span style="white-space:nowrap;">до ${_shortDate(wi.expires_at)}</span>
                    </div>
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
                        <span>Не подключен</span>
                        <span>Подключить</span>
                    </div>
                </div>
            </button>
        `;

    const isWiOnlyExhausted = Boolean(!std.active && wiExhausted);

    body.innerHTML = `
        <section class="hero-section">
            <div class="hero-title-group">
                <span class="hero-kicker ${isStdGrace || isWiOnlyExhausted ? "warn" : ""}">${isStdGrace ? `Льготный период · ~${graceHoursLeft} ч.` : (isWiOnlyExhausted ? "Трафик исчерпан" : "Активен · Готов к работе")}</span>
                <h1 class="hero-headline-lg">${isStdGrace ? "Подписка закончилась" : (isWiOnlyExhausted ? "Трафик закончился" : "Доступ настроен")}</h1>
                <p class="hero-sub">${isStdGrace
                    ? `Основной срок подписки закончился, но доступ сохранён ещё на ~${graceHoursLeft} ч., чтобы вы успели продлить подписку без отключения.`
                    : (isWiOnlyExhausted ? "Пополните пакет трафика или продлите подписку на вкладке «Тарифы»." : "Обе подписки работают по одной ссылке")}</p>
            </div>

            ${isStdGrace ? `
                <button id="graceRenewBtn" type="button" class="btn btn-primary btn-lg">Продлить подписку</button>
            ` : ""}

            <div class="channels-grid">
                ${stdTileHtml}
                ${wiTileHtml}
            </div>

            <div class="connect-dock">
                ${desktopFirst ? `
                    <button type="button" data-open-client="happ" class="btn btn-primary btn-lg">${_ICONS.bolt} Подключить в Happ</button>
                    <button type="button" data-open-client="incy" class="btn btn-sm" style="width:100%;">Открыть в INCY</button>
                ` : `
                    <button type="button" data-open-client="incy" class="btn btn-primary btn-lg">${_ICONS.bolt} Подключить в INCY</button>
                    <button type="button" data-open-client="happ" class="btn btn-sm" style="width:100%;">Открыть в Happ</button>
                `}
                ${subUrl ? `
                    <button id="copyUrlStripBtn" type="button" class="sub-link-strip" title="Нажмите, чтобы скопировать">
                        <span class="sub-link-text">${escapeHtml(subUrl)}</span>
                        <span style="display:inline-flex; color:var(--text-muted);">${_ICONS.copy}</span>
                    </button>
                ` : ""}
            </div>

            ${_buildGuideHtml(subUrl, backupSubUrl, true)}

            <div class="troubleshoot-box" style="margin-top:0;">
                <div class="troubleshoot-title">
                    <span class="status-dot warn" style="width:6px; height:6px;"></span>
                    <span>Если нет подключения:</span>
                </div>
                <span>• Выберите другую локацию в списке приложения</span>
                <span>• Обновите подписку (потяните список серверов вниз или нажмите ⟳)</span>
                <span>• Напишите в поддержку, если ничего не изменилось</span>
            </div>
        </section>

        ${_buildDevicesSectionHtml(d)}
    `;

    body.querySelectorAll("[data-jump-tariffs]").forEach((btn) => {
        btn.addEventListener("click", () => {
            _state.tab = "tariffs";
            _render(root);
            window.scrollTo({ top: 0, behavior: "smooth" });
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
        stripBtn.addEventListener("click", () => copyText(subUrl, "Ссылка скопирована"));
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
            _render(root);
        });
    }
    _bindGuideEvents(body, root);
    _bindDevicesEvents(body, d, reload);
}

function _renderTariffsTab(body, d, reload) {
    const bal = d.balance || { real: 0, bonus: 0, total: 0 };
    const std = d.standard || {};
    const wi = d.wi || {};
    const pricing = d.pricing || {};
    const minTopup = pricing.min_topup_rub || 20;

    const stdLimit = Number(std.device_limit || 2);
    const stdMin7 = _calcStdPrice(pricing, 2, 7);
    const stdMin30 = _calcStdPrice(pricing, 2, 30);
    const stdExpMs = std.expires_at ? new Date(std.expires_at).getTime() : 0;
    const isStdGrace = Boolean(std.active && stdExpMs > 0 && stdExpMs < Date.now());
    const graceEndMs = stdExpMs + (4 * 3600 * 1000);
    const graceHoursLeft = Math.max(1, Math.ceil((graceEndMs - Date.now()) / (3600 * 1000)));

    const wiBase = Number(pricing.wi_base_price_rub || 250);
    const wiUsedGb = formatBytesGb(wi.traffic_used_bytes);
    const wiTotalGb = formatBytesGb(wi.traffic_limit_bytes);
    const wiPct = wi.traffic_limit_bytes > 0
        ? Math.min(100, Math.round((wi.traffic_used_bytes / wi.traffic_limit_bytes) * 100))
        : 0;
    const wiCurLimit = Number(wi.device_limit || 1);
    const wiMaxDevices = pricing.wi_max_devices || 3;

    body.innerHTML = `
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
                <div class="balance-value">${bal.total} ₽</div>
                <div class="balance-sub">Основной: ${bal.real} ₽ · Бонусы: ${bal.bonus} ₽</div>
            </div>
            <button id="topupBtn" type="button" class="btn btn-secondary btn-sm">${_ICONS.plus} Пополнить</button>
        </section>

        <div class="muted" style="font-size:12px; padding:0 2px;">
            Две независимые подписки на одну ссылку — выберите нужную или подключите обе.
        </div>

        <section class="plan-card ${std.active && !isStdGrace ? "plan-active" : ""}" ${isStdGrace ? 'style="border-color: rgba(245, 158, 11, 0.4);"' : ""}>
            <div class="row">
                <div>
                    <div class="card-title">Стандарт</div>
                    <div class="muted" style="font-size:12px;">Для дома, Wi-Fi и повседневного использования</div>
                </div>
                <span class="chip ${isStdGrace ? "chip-warn" : (std.active ? "chip-ok" : "chip-muted")}">
                    ${isStdGrace ? `Льготный период (~${graceHoursLeft} ч.)` : (std.active ? `до ${formatDate(std.expires_at)}` : "Не подключена")}
                </span>
            </div>
            <div class="plan-features">
                <span>• Без ограничений по трафику, от 2 до 10 устройств</span>
                <span>• Работает через приложение по единой ссылке подключения</span>
            </div>
            ${std.active ? `
                <label class="toggle-row">
                    <span class="toggle-label">Автопродление с баланса (${stdLimit} устр.)</span>
                    <span class="switch">
                        <input id="stdAutoRenewChk" type="checkbox" ${std.auto_renew_enabled ? "checked" : ""}>
                        <span class="switch-track"></span>
                    </span>
                </label>
                <div class="connect-pair">
                    <button id="stdRenewBtn" type="button" class="btn btn-primary">Продлить</button>
                    <button id="stdDevicesBtn" type="button" class="btn">Лимит: ${stdLimit} устр.</button>
                </div>
            ` : `
                <div class="plan-meta-row">
                    <span class="plan-price-tag">от ${stdMin7} ₽ / 7 дн. · от ${stdMin30} ₽ / 30 дн.</span>
                    <button id="stdBuyBtn" type="button" class="btn btn-primary">Выбрать</button>
                </div>
            `}
        </section>

        <section class="plan-card plan-wi ${wi.active ? "plan-active" : ""}">
            <div class="row">
                <div>
                    <div class="card-title">Белый интернет</div>
                    <div class="muted" style="font-size:12px;">Для мобильной сети при жёстких ограничениях доступа</div>
                </div>
                <span class="chip ${
                    wi.status === "EXHAUSTED"
                        ? "chip-warn"
                        : wi.status === "PENDING"
                            ? "chip-wi"
                            : wi.active
                                ? "chip-wi"
                                : "chip-muted"
                }">
                    ${
                        wi.status === "EXHAUSTED"
                            ? "Трафик исчерпан"
                            : wi.status === "PENDING"
                                ? "Настраивается"
                                : wi.active
                                    ? `до ${formatDate(wi.expires_at)}`
                                    : wi.status === "EXPIRED"
                                        ? "Истекла"
                                        : "Не подключена"
                    }
                </span>
            </div>
            <div class="plan-features">
                <span>• Работает отдельно от «Стандарта» или вместе с ним</span>
                <span>• 50 ГБ трафика на каждый слот устройства, срок 30 дней</span>
            </div>
            ${_wiHasActivePeriod(wi) ? `
                <div class="field">
                    <div class="row muted" style="font-size:12px;">
                        <span class="tabular">Трафик: <strong style="color:var(--text-primary);">${wiUsedGb} / ${wiTotalGb} ГБ</strong></span>
                        <span class="tabular">Слотов: <strong style="color:var(--text-primary);">${wiCurLimit}</strong></span>
                    </div>
                    <div class="progress-track"><div class="progress-fill" style="transform:scaleX(${(wiPct / 100).toFixed(3)});"></div></div>
                </div>
                <label class="toggle-row">
                    <span class="toggle-label">Автопродление с баланса</span>
                    <span class="switch switch-wi">
                        <input id="wiAutoRenewChk" type="checkbox" ${wi.auto_renew_enabled ? "checked" : ""}>
                        <span class="switch-track"></span>
                    </span>
                </label>
                <div class="connect-pair">
                    <button id="wiRenewBtn" type="button" class="btn btn-primary">Продлить +30 дн.</button>
                    <button id="wiManageBtn" type="button" class="btn">Слоты и трафик</button>
                </div>
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
            showToast("Профиль на проверке — свяжитесь с поддержкой");
            return false;
        }
        if (d.payments_maintenance) {
            showToast("Приём платежей временно приостановлен (техработы)");
            return false;
        }
        return true;
    };

    body.querySelector("#topupBtn").addEventListener("click", () => {
        if (checkCanPay()) _openTopupModal(minTopup, reload);
    });

    const stdBuyBtn = body.querySelector("#stdBuyBtn");
    if (stdBuyBtn) stdBuyBtn.addEventListener("click", () => {
        if (checkCanPay()) _openStandardPlanModal(d, "buy", reload);
    });

    const stdRenewBtn = body.querySelector("#stdRenewBtn");
    if (stdRenewBtn) stdRenewBtn.addEventListener("click", () => {
        if (checkCanPay()) _openStandardPlanModal(d, "renew", reload);
    });

    const stdDevBtn = body.querySelector("#stdDevicesBtn");
    if (stdDevBtn) stdDevBtn.addEventListener("click", () => {
        if (checkCanPay()) _openStandardDevicesModal(d, reload);
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

    const wiBuyBtn = body.querySelector("#wiBuyBtn");
    if (wiBuyBtn) wiBuyBtn.addEventListener("click", () => {
        if (checkCanPay()) _openWiPurchaseModal(d, "buy", reload);
    });

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

    const wiRenewBtn = body.querySelector("#wiRenewBtn");
    if (wiRenewBtn) {
        wiRenewBtn.addEventListener("click", () => {
            if (checkCanPay()) _openWiPurchaseModal(d, "renew", reload);
        });
    }

    const wiManageBtn = body.querySelector("#wiManageBtn");
    if (wiManageBtn) {
        wiManageBtn.addEventListener("click", () => {
            if (checkCanPay()) _openWiManageModal(d, reload);
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
                    Нажмите «Подключить в INCY» или «Открыть в Happ» выше — устройство привяжется автоматически.
                </p>
            </div>
        `
        : `<div class="list-group">${devices.map((dev) => {
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
            const dateLabel = formatDate(dev.last_seen_at);
            const metaLine = [leftMeta, dateLabel].filter(Boolean).join(" · ");

            let stdRowHtml = "";
            if (needStdToggles) {
                stdRowHtml = `
                    <label class="device-bi-bar">
                        <span style="color:${isStd ? "var(--text-primary)" : "var(--text-secondary)"};">
                            Слот «Стандарт»
                        </span>
                        <span class="switch">
                            <input type="checkbox" data-std-toggle="${escapeHtml(dev.hwid)}" data-name="${escapeHtml(label)}" ${isStd ? "checked" : ""}>
                            <span class="switch-track"></span>
                        </span>
                    </label>
                `;
            }

            let biRowHtml = "";
            if (needBiToggles) {
                biRowHtml = `
                    <label class="device-bi-bar">
                        <span style="color:${isBi ? "var(--text-primary)" : "var(--text-secondary)"};">
                            Слот «Белый интернет»
                        </span>
                        <span class="switch switch-wi">
                            <input type="checkbox" data-bi-toggle="${escapeHtml(dev.hwid)}" data-name="${escapeHtml(label)}" ${isBi ? "checked" : ""}>
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
                    <span class="mono-badge">${devices.length} / ${totalLimit}</span>
                </div>
                <div style="display:flex; align-items:center; gap:8px; flex-shrink:0;">
                    <button type="button" class="btn btn-icon" id="refreshDevicesBtn" aria-label="Обновить" title="Обновить">${_ICONS.refresh}</button>
                    ${devices.length > 1 ? `<button type="button" class="btn btn-sm btn-danger" id="resetAllDevicesBtn">Сбросить</button>` : ""}
                </div>
            </div>
            ${headerChipsHtml ? `<div class="devices-chips-row">${headerChipsHtml}</div>` : ""}
            ${isLimitReached ? `
                <div class="notice-box notice-warn" style="margin:0;">
                    <span>Все слоты (${devices.length} из ${totalLimit}) заняты — удалите устройство, чтобы подключить новое.</span>
                </div>
            ` : ""}
            ${rowsHtml}
        </section>
    `;
}

function _openSlotReplaceModal(slotTitle, targetDev, activeDevices, applyFn, onReplaced, chipClass = "chip-wi") {
    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.style.gap = "10px";

    const targetLabel = targetDev.device_name || targetDev.platform || "Устройство";
    const close = openModal(`Замена слота · ${slotTitle}`, wrap);

    wrap.innerHTML = `
        <div class="muted" style="font-size:12px; line-height:1.45;">
            Все слоты (${activeDevices.length} из ${activeDevices.length}) уже заняты. Выберите, с какого устройства перенести «${escapeHtml(slotTitle)}» на <strong style="color:var(--text-primary);">${escapeHtml(targetLabel)}</strong>:
        </div>
        <div style="display:flex; flex-direction:column; gap:8px;">
            ${activeDevices.map((dev) => {
                const label = dev.device_name || dev.platform || "Устройство";
                const osText = [dev.platform, dev.os_version].filter(Boolean).join(" ");
                const meta = [osText, dev.app_version].filter(Boolean).join(" · ");
                return `
                    <button type="button" class="item-row" data-replace-from="${escapeHtml(dev.hwid)}" style="text-align:left; cursor:pointer; align-items:center; flex-direction:row; justify-content:space-between; gap:10px; padding:10px 12px; border-color:rgba(255,255,255,0.25);">
                        <div style="min-width:0; flex:1;">
                            <div style="font-weight:600; font-size:13px; color:var(--text-primary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(label)}</div>
                            ${meta ? `<div class="muted" style="font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(meta)}</div>` : ""}
                        </div>
                        <span class="chip ${chipClass}" style="flex-shrink:0;">Забрать слот →</span>
                    </button>
                `;
            }).join("")}
        </div>
    `;

    wrap.querySelectorAll("[data-replace-from]").forEach((btn) => {
        btn.addEventListener("click", async () => {
            const fromHwid = btn.dataset.replaceFrom;
            wrap.querySelectorAll("[data-replace-from]").forEach((b) => { b.disabled = true; });
            try {
                await applyFn(targetDev.hwid, 10, fromHwid);
                close();
                showToast("Слот перенесён — нажмите ⟳ в приложении");
                onReplaced();
            } catch (err) {
                showToast(err.message || "Не удалось перенести слот");
                wrap.querySelectorAll("[data-replace-from]").forEach((b) => { b.disabled = false; });
            }
        });
    });
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
                        reload();
                    } catch (err) {
                        showToast(err.message || "Не удалось удалить устройство", "error");
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
                        reload();
                    } catch (err) {
                        showToast(err.message || "Не удалось сбросить устройства", "error");
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
                                    showToast("Слот перенесён — нажмите ⟳ в приложении");
                                    reload();
                                } catch (err) {
                                    showToast(err.message || "Не удалось перенести слот");
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
                            ? `${slotTitle} включён — нажмите ⟳ в приложении`
                            : `${slotTitle} отключён на устройстве`
                    );
                    reload();
                } catch (err) {
                    chk.checked = !wantEnable;
                    showToast(err.message || "Не удалось изменить статус слота");
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

function _renderMoreTab(body, d) {
    const ref = d.referrals || {};
    const bal = d.balance || { real: 0, bonus: 0, total: 0 };
    const minActive = ref.active_min_topup_rub || 67;
    const shareText = `Держи приглашение — скидка ${ref.welcome_discount_pct ?? 25}% на первый заказ`;
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

    const hasSupportRows = Boolean(sup.tg_proxy_url || sup.support_url || sup.tos_url || sup.privacy_url);

    body.innerHTML = `
        <section class="hero-title-group">
            <span class="hero-kicker muted">Рефералы, помощь и документы</span>
            <h1 class="hero-headline-lg">Ещё</h1>
        </section>

        <section class="stats-strip">
            <div class="stat-box"><div class="stat-val">${ref.invited_count ?? 0}</div><div class="stat-lbl">Приглашено</div></div>
            <div class="stat-box"><div class="stat-val">${ref.active_count ?? 0}</div><div class="stat-lbl">Активных</div></div>
            <div class="stat-box"><div class="stat-val">${ref.rate_pct ?? 15}%</div><div class="stat-lbl">Ваш %</div></div>
            <div class="stat-box stat-lead"><div class="stat-val">${bal.bonus ?? 0} ₽</div><div class="stat-lbl">Бонусы</div></div>
        </section>

        <section class="card">
            <div class="row">
                <div class="card-title">Приглашения и бонусы</div>
                <span class="chip chip-ok">${escapeHtml(ref.tier_name || "Standard")}</span>
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
            <div id="refLinkBox" class="sub-url-box clickable" title="Нажмите, чтобы скопировать">${escapeHtml(ref.link || "")}</div>
            <div class="connect-pair">
                <button id="shareRefBtn" type="button" class="btn btn-primary">${_ICONS.share} Пригласить</button>
                <button id="copyRefBtn" type="button" class="btn">${_ICONS.copy} Копировать</button>
            </div>
            ${invitedUsers.length ? `
                <details class="guide-details" style="margin-top:4px;">
                    <summary class="guide-summary">
                        <span>Приглашённые пользователи (${invitedUsers.length})</span>
                        <span class="muted" style="font-size:12px;">▾</span>
                    </summary>
                    <div class="guide-body" style="gap:8px;">
                        ${invitedUsers.map((u) => `
                            <div class="item-row">
                                <div class="row">
                                    <span style="font-size:13px; font-weight:500;">${escapeHtml(u.label)}</span>
                                    <span class="chip ${u.is_active ? "chip-ok" : "chip-muted"}">${u.is_active ? "Активен" : "Ожидает"}</span>
                                </div>
                                <div class="muted" style="font-size:12px;">${formatDate(u.created_at)}</div>
                            </div>
                        `).join("")}
                    </div>
                </details>
            ` : ""}
            ${leaderboard.length ? `
                <details class="guide-details" style="margin-top:4px;">
                    <summary class="guide-summary">
                        <span>Рейтинг лидеров ${ref.user_rank ? `(Ваше место: #${ref.user_rank})` : ""}</span>
                        <span class="muted" style="font-size:12px;">▾</span>
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

        ${hasSupportRows ? `
            <section class="section-block">
                <div class="section-head">
                    <h2 class="section-title">Помощь и документы</h2>
                </div>
                <div class="list-group">
                    ${sup.tg_proxy_url ? `
                        <button id="tgProxyBtn" type="button" class="settings-row">
                            <span class="settings-left">${_ICONS.send}<span>Прокси для Telegram</span></span>
                            <span class="mono-badge">→</span>
                        </button>
                    ` : ""}
                    ${sup.support_url ? `
                        <button id="supportContactBtn" type="button" class="settings-row">
                            <span class="settings-left">${_ICONS.message}<span>Написать в поддержку</span></span>
                            <span class="mono-badge">→</span>
                        </button>
                    ` : ""}
                    ${sup.tos_url ? `
                        <button id="tosLinkBtn" type="button" class="settings-row">
                            <span class="settings-left">${_ICONS.file}<span>Пользовательское соглашение</span></span>
                            <span class="mono-badge">→</span>
                        </button>
                    ` : ""}
                    ${sup.privacy_url ? `
                        <button id="privacyLinkBtn" type="button" class="settings-row">
                            <span class="settings-left">${_ICONS.file}<span>Политика конфиденциальности</span></span>
                            <span class="mono-badge">→</span>
                        </button>
                    ` : ""}
                </div>
            </section>
        ` : ""}
    `;

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

function _renderProfileTab(body, d, reload) {
    const bal = d.balance || { real: 0, bonus: 0, total: 0 };
    const minTopup = (d.pricing && d.pricing.min_topup_rub) || 20;
    const items = d.history || [];
    const meta = _getProfileMeta(d);

    body.innerHTML = `
        <section class="hero-title-group">
            <div style="display:flex; align-items:center; gap:12px;">
                <span id="profileLargeAvatar" class="avatar-circle avatar-lg">${_renderAvatarInner(meta)}</span>
                <div>
                    <h1 class="hero-headline-lg" style="font-size:20px;">${escapeHtml(meta.displayName)}</h1>
                    <span class="mono-badge">${escapeHtml([meta.username, meta.userId ? `ID ${meta.userId}` : ""].filter(Boolean).join(" · ") || "Профиль")}</span>
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
                <div class="balance-value">${bal.total} ₽</div>
                <div class="balance-sub">Основной: ${bal.real} ₽ · Бонусы: ${bal.bonus} ₽</div>
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

    body.querySelector("#profileTopupBtn").addEventListener("click", () => {
        if (d.financial_hold) {
            showToast("Профиль на проверке — свяжитесь с поддержкой");
            return;
        }
        if (d.payments_maintenance) {
            showToast("Приём платежей временно приостановлен (техработы)");
            return;
        }
        _openTopupModal(minTopup, reload);
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
            const sign = op.amount_rub > 0 ? "+" : "";
            const cls = op.amount_rub > 0 ? "amount-pos" : "amount-neg";
            row.innerHTML = `
                <div class="row">
                    <span style="font-weight:500;">${escapeHtml(op.description)}</span>
                    <span class="${cls}">${sign}${op.amount_rub} ₽</span>
                </div>
                <div class="device-meta">${formatDate(op.created_at)}</div>
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
