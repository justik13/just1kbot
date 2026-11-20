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
};

const _ICONS = {
    copy: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`,
    bolt: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`,
    share: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>`,
    plus: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`,
    phone: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="2" width="14" height="20" rx="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>`,
    desktop: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>`,
    refresh: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>`,
    trash: `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>`,
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
    if (!res || !res.confirmation_url) {
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
    const idemKey = _newIdempotencyKey();
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
        summaryEl.innerHTML = lines.join("");
        submitBtn.textContent = getCheckoutText(price, split.cardDue, split.balUsed);
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
        const idemKey = _newIdempotencyKey();
        payBtn.addEventListener("click", async () => {
            payBtn.disabled = true;
            try {
                const res = await api.checkoutStandard(devices, duration, useBalance, idemKey);
                close();
                _handlePaymentResponse(res, "Подписка «Стандарт» оформлена", onReload);
            } catch (err) {
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
            infoHtml = blockedReason
                ? `<div class="notice-box notice-warn"><span>${escapeHtml(blockedReason)}</span></div>`
                : `<div class="quote-box">
                        <div class="quote-line"><span>Снижение лимита</span><strong>Бесплатно</strong></div>
                        ${Number(quote.bonus_credit_rub || 0) > 0
                            ? `<div class="quote-line"><span>Возврат бонусами на баланс</span><strong style="color:var(--accent-emerald);">+${quote.bonus_credit_rub} ₽</strong></div>`
                            : ""}
                        <div class="quote-line"><span>Следующее снижение</span><strong>через 7 дней</strong></div>
                   </div>`;
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
        const idemKey = _newIdempotencyKey();
        wrap.querySelector("#devApplyBtn").addEventListener("click", async () => {
            const btn = wrap.querySelector("#devApplyBtn");
            btn.disabled = true;
            try {
                if (isUpgrade) {
                    const res = await api.checkoutStandard(targetLimit, 30, useBalance, idemKey);
                    close();
                    _handlePaymentResponse(res, `Лимит увеличен до ${targetLimit} устр.`, onReload);
                } else {
                    await api.downgradeStandard(targetLimit);
                    close();
                    showToast(`Лимит снижен до ${targetLimit} устр.`);
                    onReload();
                }
            } catch (err) {
                btn.disabled = false;
                showToast(err.message || "Не удалось изменить лимит");
            }
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
                <span class="switch">
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
        const idemKey = _newIdempotencyKey();
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
                    <label>Дополнительный слот до конца срока</label>
                    <button id="modalWiSelectSlotBtn" type="button" class="price-tile ${selectedItem && selectedItem.kind === "slot" ? "active" : ""}" style="width:100%; flex-direction:row; justify-content:space-between; padding:11px 14px;">
                        <span class="price-tile-top">+1 слот устройства (+${addSlotGb} ГБ)</span>
                        <span class="price-tile-price">${addSlotPrice} ₽</span>
                    </button>
                </div>
            ` : ""}
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
                    <span class="switch">
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
        const idemKey = _newIdempotencyKey();
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

export async function loadAndRenderUserCabinet(root) {
    try {
        const data = await api.getUserDashboard();
        _state.data = data;
        const stdActive = Boolean(data.standard && data.standard.active);
        const wiActive = Boolean(data.wi && data.wi.active);
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

function _render(root) {
    const d = _state.data;
    if (!d) return;
    const prevGuideOpen = Boolean(root.querySelector("#connectGuide")?.open);
    const prevScrollY = window.scrollY;
    const stdActive = Boolean(d.standard && d.standard.active);
    const wiActive = Boolean(d.wi && d.wi.active);
    const hasAny = stdActive || wiActive;

    const tabs = hasAny
        ? [
            { id: "connect", label: "Подключение" },
            { id: "tariffs", label: "Тарифы" },
            { id: "profile", label: "Профиль" },
        ]
        : [
            { id: "tariffs", label: "Тарифы" },
            { id: "connect", label: "Подключение" },
            { id: "profile", label: "Профиль" },
        ];

    if (!tabs.some((t) => t.id === _state.tab)) {
        _state.tab = hasAny ? "connect" : "tariffs";
    }

    root.innerHTML = `
        <nav class="tabs-bar">
            ${tabs.map((t) => `
                <button type="button" class="tab-btn ${_state.tab === t.id ? "active" : ""}" data-utab="${t.id}">${escapeHtml(t.label)}</button>
            `).join("")}
        </nav>
        <div id="userTabBody" class="app-main"></div>
    `;

    root.querySelectorAll("[data-utab]").forEach((b) => {
        b.addEventListener("click", () => {
            if (_state.tab === b.dataset.utab) return;
            _state.tab = b.dataset.utab;
            _render(root);
        });
    });

    const body = root.querySelector("#userTabBody");
    const reload = () => loadAndRenderUserCabinet(root);

    if (_state.tab === "connect") _renderConnectTab(body, d, reload, root);
    else if (_state.tab === "tariffs") _renderTariffsTab(body, d, reload);
    else _renderProfileTab(body, d, reload);

    if (prevGuideOpen) {
        const nextGuide = root.querySelector("#connectGuide");
        if (nextGuide) nextGuide.open = true;
    }
    if (prevScrollY > 0) {
        window.scrollTo(0, prevScrollY);
    }
}

function _buildGuideHtml(subUrl, backupSubUrl, isActive) {
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
                <div class="notice-box" style="flex-direction:column; align-items:flex-start; gap:6px; font-size:12px;">
                    <strong>Раздельное туннелирование и частые вопросы</strong>
                    <span>• Российские банки, Госуслуги и локальные сервисы работают напрямую — выключать приложение не нужно.</span>
                    <span>• После добавления подписки используйте кнопку «Подключить» на главном экране или включите автообновление в приложении.</span>
                    <span>• При смене устройства старое можно удалить в списке устройств ниже — ссылка подключения при этом останется прежней.</span>
                </div>
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
            <section class="hero-card" style="border-color: rgba(239, 68, 68, 0.4);">
                <div class="row">
                    <div style="display:flex; align-items:center; gap:9px;">
                        <span class="status-dot" style="background:var(--accent-red); box-shadow:0 0 10px rgba(239, 68, 68, 0.5);"></span>
                        <span class="hero-headline" style="color:var(--accent-red);">Обслуживание приостановлено</span>
                    </div>
                    <span class="chip chip-warn">Ограничение</span>
                </div>
                <p class="muted">Доступ к сервису временно ограничен администратором. Если у вас возникли вопросы по оплате или настройке, напишите нам в поддержку.</p>
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
    const isAnyActive = Boolean(std.active || wi.active);
    const subUrl = d.subscription_url || "";
    const backupSubUrl = d.subscription_backup_url || "";
    const desktopFirst = _state.platform === "windows" || _state.platform === "macos";

    if (!isAnyActive) {
        body.innerHTML = `
            <section class="hero-card">
                <div class="row">
                    <div style="display:flex; align-items:center; gap:9px;">
                        <span class="status-dot"></span>
                        <span class="hero-headline">Нет активных подписок</span>
                    </div>
                    <span class="chip chip-muted">Не подключено</span>
                </div>
                <p class="muted">Оформите «Стандарт» или «Белый интернет» — ссылка подключения активируется сразу после оплаты.</p>
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

    const pills = [];
    if (std.active) {
        if (isStdGrace) {
            pills.push(`<span class="chip chip-warn">Стандарт · Льготный период (~${graceHoursLeft} ч.)</span>`);
        } else {
            pills.push(`<span class="chip chip-ok">Стандарт · ${Number(std.device_limit || 2)} устр. · до ${formatDate(std.expires_at)}</span>`);
        }
    }
    if (wi.active) {
        const usedGb = formatBytesGb(wi.traffic_used_bytes);
        const totalGb = formatBytesGb(wi.traffic_limit_bytes);
        pills.push(`<span class="chip chip-info">Белый интернет · ${Number(wi.device_limit || 1)} устр. · ${usedGb}/${totalGb} ГБ · до ${formatDate(wi.expires_at)}</span>`);
    }

    body.innerHTML = `
        <section class="hero-card is-active" ${isStdGrace ? 'style="border-color: rgba(245, 158, 11, 0.4);"' : ""}>
            <div class="row">
                <div style="display:flex; align-items:center; gap:9px;">
                    <span class="status-dot ${isStdGrace ? "" : "active"}" ${isStdGrace ? 'style="background:var(--accent-warn); box-shadow:0 0 10px rgba(245, 158, 11, 0.5);"' : ""}></span>
                    <span class="hero-headline" ${isStdGrace ? 'style="color:var(--accent-warn);"' : ""}>${isStdGrace ? "Подписка закончилась" : "Доступ активен"}</span>
                </div>
                <span class="chip ${isStdGrace ? "chip-warn" : "chip-ok"}">${isStdGrace ? "Льготный период" : "Готов к работе"}</span>
            </div>
            ${isStdGrace ? `
                <p class="muted" style="margin:4px 0 10px 0;">Основной срок подписки закончился, но доступ сохранён ещё на ~${graceHoursLeft} ч., чтобы вы успели продлить подписку без отключения.</p>
                <button id="graceRenewBtn" type="button" class="btn btn-primary btn-lg" style="margin-bottom:12px;">Продлить подписку</button>
            ` : ""}
            <div class="option-pills">${pills.join("")}</div>
            <div class="connect-actions">
                ${desktopFirst ? `
                    <button type="button" data-open-client="happ" class="btn btn-primary btn-lg">${_ICONS.bolt} Подключить в Happ</button>
                ` : `
                    <button type="button" data-open-client="incy" class="btn btn-primary btn-lg">${_ICONS.bolt} Подключить в INCY</button>
                `}
                <div class="connect-pair">
                    ${desktopFirst ? `
                        <button type="button" data-open-client="incy" class="btn btn-sm">Открыть в INCY</button>
                    ` : `
                        <button type="button" data-open-client="happ" class="btn btn-sm">Открыть в Happ</button>
                    `}
                    <button id="copySubBtn" type="button" class="btn btn-sm">${_ICONS.copy} Копировать ссылку</button>
                </div>
            </div>
            ${_buildGuideHtml(subUrl, backupSubUrl, true)}
        </section>

        ${_buildDevicesSectionHtml(d)}
    `;

    body.querySelectorAll("[data-open-client]").forEach((btn) => {
        btn.addEventListener("click", () => {
            const client = btn.dataset.openClient;
            if (!subUrl) return;
            const targetUrl = `${subUrl}${subUrl.includes("?") ? "&" : "?"}open=${encodeURIComponent(client)}`;
            openExternalLink(targetUrl);
        });
    });
    body.querySelector("#copySubBtn").addEventListener("click", () => copyText(subUrl, "Ссылка скопирована"));
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
                <span>⚠️ Оплата и изменение подписок временно недоступны: профиль находится на проверке. Свяжитесь с поддержкой.</span>
            </div>
        ` : (d.payments_maintenance ? `
            <div class="notice-box notice-warn" style="margin-bottom:8px;">
                <span>🛠 Приём платежей временно приостановлен для проведения технических работ. Действующие подписки работают в штатном режиме.</span>
            </div>
        ` : "")}

        <section class="balance-card">
            <div>
                <div class="muted" style="font-size:12px;">Баланс аккаунта</div>
                <div class="balance-value">${bal.total} ₽</div>
                <div class="balance-sub">Основной: ${bal.real} ₽ · Бонусы: ${bal.bonus} ₽</div>
            </div>
            <button id="topupBtn" type="button" class="btn btn-primary btn-sm">${_ICONS.plus} Пополнить</button>
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
                <span>• Высокая скорость для стриминга, игр и работы</span>
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

        <section class="plan-card ${wi.active ? "plan-active" : ""}">
            <div class="row">
                <div>
                    <div class="card-title">Белый интернет</div>
                    <div class="muted" style="font-size:12px;">Для мобильной сети при жёстких ограничениях доступа</div>
                </div>
                <span class="chip ${
                    wi.status === "EXHAUSTED"
                        ? "chip-warn"
                        : wi.status === "PENDING"
                            ? "chip-info"
                            : wi.active
                                ? "chip-info"
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
            ${wi.active ? `
                <div class="field">
                    <div class="row muted" style="font-size:12px;">
                        <span class="tabular">Трафик: <strong style="color:var(--text-primary);">${wiUsedGb} / ${wiTotalGb} ГБ</strong></span>
                        <span class="tabular">Слотов: <strong style="color:var(--text-primary);">${wiCurLimit}</strong></span>
                    </div>
                    <div class="progress-track"><div class="progress-fill" style="transform:scaleX(${(wiPct / 100).toFixed(3)});"></div></div>
                </div>
                <label class="toggle-row">
                    <span class="toggle-label">Автопродление с баланса</span>
                    <span class="switch">
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
    const wiActive = Boolean(wi && wi.active);
    const wiLimit = Number(wi.device_limit || 1);
    const stdLimit = Number(std.device_limit || 2);
    const totalLimit = Math.max(stdActive ? stdLimit : 0, wiActive ? wiLimit : 0, 1);
    const isLimitReached = devices.length >= totalLimit;
    const biAssignedCount = wiActive ? devices.filter((dev) => dev.is_bi_assigned).length : 0;
    const isWiExhausted = Boolean(
        wiActive && (
            String(wi.status || "").toUpperCase() === "EXHAUSTED"
            || (wi.traffic_limit_bytes > 0 && wi.traffic_used_bytes >= wi.traffic_limit_bytes)
        )
    );
    const needBiToggles = Boolean(wiActive && (devices.length > wiLimit || biAssignedCount < devices.length));

    const rowsHtml = !devices.length
        ? `
            <div class="card card-center" style="background:var(--bg-elevated); padding:20px 14px;">
                <div style="color:var(--text-secondary);">${_ICONS.phone}</div>
                <div style="font-weight:600; font-size:14px;">Устройств пока нет</div>
                <p class="muted" style="font-size:12px;">
                    Нажмите «Подключить в INCY» или «Открыть в Happ» выше — устройство привяжется автоматически.
                </p>
            </div>
        `
        : devices.map((dev) => {
            const label = dev.device_name || dev.platform || "Устройство";
            const rawHwid = String(dev.hwid || "");
            const shortHwid = rawHwid.length > 14 ? `${rawHwid.slice(0, 6)}…${rawHwid.slice(-4)}` : rawHwid;
            const osText = [dev.platform, dev.os_version].filter(Boolean).join(" ");
            const meta = [osText, dev.app_version].filter(Boolean).join(" · ");
            const isDesktop = /win|mac|linux|desktop|amd64|x86_64/i.test(`${label} ${meta}`);
            const devIcon = isDesktop ? _ICONS.desktop : _ICONS.phone;
            const isBi = Boolean(wiActive && dev.is_bi_assigned);

            const leftMeta = meta || shortHwid;
            const dateLabel = formatDate(dev.last_seen_at);

            let biRowHtml = "";
            if (needBiToggles) {
                biRowHtml = `
                    <label style="display:flex; align-items:center; justify-content:space-between; gap:10px; padding-top:7px; margin-top:2px; border-top:1px solid var(--border-subtle); cursor:pointer;">
                        <span style="font-size:12px; font-weight:500; color:${isBi ? "var(--text-primary)" : "var(--text-secondary)"};">
                            Белый интернет
                        </span>
                        <span class="switch" style="transform:scale(0.86); transform-origin:right center;">
                            <input type="checkbox" data-bi-toggle="${escapeHtml(dev.hwid)}" data-name="${escapeHtml(label)}" ${isBi ? "checked" : ""}>
                            <span class="switch-track"></span>
                        </span>
                    </label>
                `;
            }

            return `
                <div class="item-row" style="gap:5px; padding:10px 12px; ${isBi && needBiToggles ? "border-color:rgba(52,211,153,0.22);" : ""}">
                    <div style="display:flex; align-items:center; justify-content:space-between; gap:8px;">
                        <div style="display:flex; align-items:center; gap:7px; flex:1; min-width:0;">
                            <span style="display:inline-flex; flex-shrink:0; color:${isBi ? "var(--accent-emerald)" : "var(--text-secondary)"};">${devIcon}</span>
                            <span style="font-weight:600; font-size:14px; line-height:1.25; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(label)}</span>
                        </div>
                        <button type="button" class="btn btn-sm btn-danger" data-del="${escapeHtml(dev.hwid)}" data-name="${escapeHtml(label)}" aria-label="Удалить ${escapeHtml(label)}" title="Удалить" style="flex-shrink:0; padding:4px 8px; min-height:28px;">${_ICONS.trash}</button>
                    </div>
                    <div style="display:flex; align-items:center; justify-content:space-between; gap:8px; font-size:11px; line-height:1.3;">
                        <span class="muted" style="min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(leftMeta)}</span>
                        <span class="muted" style="flex-shrink:0; font-variant-numeric:tabular-nums;">${escapeHtml(dateLabel)}</span>
                    </div>
                    ${biRowHtml}
                </div>
            `;
        }).join("");

    let wiHeaderChipHtml = "";
    if (wiActive) {
        if (isWiExhausted) {
            wiHeaderChipHtml = `<span class="chip chip-warn" style="font-size:11px;">Белый интернет: 0 ГБ</span>`;
        } else if (stdActive || totalLimit !== wiLimit) {
            wiHeaderChipHtml = `<span class="chip chip-ok" style="font-size:11px;">Белый интернет: ${biAssignedCount} из ${wiLimit}</span>`;
        }
    }

    return `
        <section class="card" style="gap:10px;">
            <div class="row" style="align-items:center; gap:8px;">
                <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap; min-width:0;">
                    <div class="card-title" style="margin:0;">Устройства</div>
                    <span class="chip ${isLimitReached ? "chip-warn" : "chip-muted"}">${devices.length} из ${totalLimit}</span>
                    ${wiHeaderChipHtml}
                </div>
                <div style="display:flex; align-items:center; gap:6px; flex-shrink:0;">
                    <button type="button" class="btn btn-sm" id="refreshDevicesBtn" aria-label="Обновить" title="Обновить" style="padding:6px 10px; min-height:32px;">${_ICONS.refresh}</button>
                    ${devices.length > 1 ? `<button type="button" class="btn btn-sm btn-danger" id="resetAllDevicesBtn" style="font-size:12px; padding:6px 10px; min-height:32px;">Сбросить</button>` : ""}
                </div>
            </div>
            ${isLimitReached ? `
                <div class="notice-box notice-warn" style="margin:0;">
                    <span>Все слоты (${devices.length} из ${totalLimit}) заняты — удалите устройство, чтобы подключить новое.</span>
                </div>
            ` : ""}
            <div style="display:flex; flex-direction:column; gap:8px;">${rowsHtml}</div>
        </section>
    `;
}

function _openBiReplaceModal(targetDev, activeBiDevices, onReplaced) {
    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.style.gap = "10px";

    const targetLabel = targetDev.device_name || targetDev.platform || "Устройство";
    const close = openModal("Замена слота · Белый интернет", wrap);

    wrap.innerHTML = `
        <div class="muted" style="font-size:12px; line-height:1.45;">
            Все слоты (${activeBiDevices.length} из ${activeBiDevices.length}) уже заняты. Выберите, с какого устройства перенести «Белый интернет» на <strong style="color:var(--text-primary);">${escapeHtml(targetLabel)}</strong>:
        </div>
        <div style="display:flex; flex-direction:column; gap:8px;">
            ${activeBiDevices.map((dev) => {
                const label = dev.device_name || dev.platform || "Устройство";
                const osText = [dev.platform, dev.os_version].filter(Boolean).join(" ");
                const meta = [osText, dev.app_version].filter(Boolean).join(" · ");
                return `
                    <button type="button" class="item-row" data-replace-from="${escapeHtml(dev.hwid)}" style="text-align:left; cursor:pointer; align-items:center; flex-direction:row; justify-content:space-between; gap:10px; padding:10px 12px; border-color:rgba(52,211,153,0.28);">
                        <div style="min-width:0; flex:1;">
                            <div style="font-weight:600; font-size:13px; color:var(--text-primary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(label)}</div>
                            ${meta ? `<div class="muted" style="font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(meta)}</div>` : ""}
                        </div>
                        <span class="chip chip-ok" style="flex-shrink:0;">Забрать слот →</span>
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
                await api.setDevicePriority(targetDev.hwid, 10, fromHwid);
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
    const wiLimit = Number((d?.wi && d.wi.device_limit) || 1);
    const activeBiDevices = devices.filter((dev) => dev.is_bi_assigned);

    container.querySelectorAll("[data-bi-toggle]").forEach((chk) => {
        chk.addEventListener("change", async () => {
            const hwid = chk.dataset.biToggle;
            const wantEnable = chk.checked;
            const targetDev = devices.find((dev) => dev.hwid === hwid) || { hwid, device_name: chk.dataset.name };

            if (wantEnable && activeBiDevices.length >= wiLimit) {
                chk.checked = false;
                if (activeBiDevices.length === 1) {
                    const fromDev = activeBiDevices[0];
                    const fromLabel = fromDev.device_name || fromDev.platform || "текущего устройства";
                    const toLabel = targetDev.device_name || targetDev.platform || "это устройство";
                    showConfirmModal({
                        title: "Перенести «Белый интернет»?",
                        message: `Слот занят устройством «${fromLabel}». Перенести «Белый интернет» на «${toLabel}»?`,
                        confirmText: "Перенести слот",
                        cancelText: "Отмена",
                        onConfirm: async () => {
                            chk.disabled = true;
                            try {
                                await api.setDevicePriority(hwid, 10, fromDev.hwid);
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
                _openBiReplaceModal(targetDev, activeBiDevices, reload);
                return;
            }

            chk.disabled = true;
            try {
                await api.setDevicePriority(hwid, wantEnable ? 10 : -1);
                showToast(
                    wantEnable
                        ? "Белый интернет включён — нажмите ⟳ в приложении"
                        : "Белый интернет отключён на устройстве"
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
}

function _renderProfileTab(body, d, reload) {
    const ref = d.referrals || {};
    const bal = d.balance || { real: 0, bonus: 0, total: 0 };
    const minTopup = (d.pricing && d.pricing.min_topup_rub) || 20;
    const minActive = ref.active_min_topup_rub || 67;
    const shareText = `Держи приглашение — скидка ${ref.welcome_discount_pct ?? 25}% на первый заказ`;
    const shareUrl = `https://t.me/share/url?url=${encodeURIComponent(ref.link || "")}&text=${encodeURIComponent(shareText)}`;
    const items = d.history || [];
    const sup = d.support || {};
    const invitedUsers = ref.invited_users || [];

    body.innerHTML = `
        ${d.financial_hold ? `
            <div class="notice-box notice-warn" style="margin-bottom:8px;">
                <span>⚠️ Оплата и изменение подписок временно недоступны: профиль находится на проверке. Свяжитесь с поддержкой.</span>
            </div>
        ` : (d.payments_maintenance ? `
            <div class="notice-box notice-warn" style="margin-bottom:8px;">
                <span>🛠 Приём платежей временно приостановлен для проведения технических работ. Действующие подписки работают в штатном режиме.</span>
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
                    <span>До уровня <b>${escapeHtml(ref.next_tier_name)}</b> (${ref.next_rate_pct}%): осталось ${ref.needed_for_next} активных</span>
                </div>
            ` : ""}
            <div class="sub-url-box">${escapeHtml(ref.link || "")}</div>
            <div class="connect-pair">
                <button id="shareRefBtn" type="button" class="btn btn-primary">${_ICONS.share} Пригласить</button>
                <button id="copyRefBtn" type="button" class="btn">${_ICONS.copy} Копировать</button>
            </div>
            ${invitedUsers.length ? `
                <details class="guide-details" style="margin-top:4px;">
                    <summary class="guide-summary">
                        <span>Приглашённые пользователи (${invitedUsers.length})</span>
                        <span class="muted" style="font-size:11px;">▾</span>
                    </summary>
                    <div class="guide-body" style="gap:6px;">
                        ${invitedUsers.map((u) => `
                            <div class="item-row">
                                <div class="row">
                                    <span style="font-size:13px; font-weight:500;">${escapeHtml(u.label)}</span>
                                    <span class="chip ${u.is_active ? "chip-ok" : "chip-muted"}">${u.is_active ? "Активен" : "Ожидает"}</span>
                                </div>
                                <div class="muted" style="font-size:11px;">${formatDate(u.created_at)}</div>
                            </div>
                        `).join("")}
                    </div>
                </details>
            ` : ""}
        </section>

        <section class="card">
            <div class="card-title">Поддержка и документы</div>
            <div style="display:flex; flex-direction:column; gap:8px;">
                ${sup.support_url ? `<button id="supportContactBtn" type="button" class="btn btn-primary">Написать в поддержку</button>` : ""}
                <div class="connect-pair">
                    ${sup.tos_url ? `<button id="tosLinkBtn" type="button" class="btn btn-sm">Соглашение</button>` : ""}
                    ${sup.privacy_url ? `<button id="privacyLinkBtn" type="button" class="btn btn-sm">Конфиденциальность</button>` : ""}
                </div>
            </div>
        </section>

        <section class="card">
            <div class="card-title">История операций</div>
            <div id="historyList" style="display:flex; flex-direction:column; gap:8px;"></div>
        </section>
    `;

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
    body.querySelector("#shareRefBtn").addEventListener("click", () => openExternalLink(shareUrl));
    body.querySelector("#copyRefBtn").addEventListener("click", () => copyText(ref.link, "Ссылка скопирована"));
    const supBtn = body.querySelector("#supportContactBtn");
    if (supBtn && sup.support_url) supBtn.addEventListener("click", () => openExternalLink(sup.support_url));
    const tosBtn = body.querySelector("#tosLinkBtn");
    if (tosBtn && sup.tos_url) tosBtn.addEventListener("click", () => openExternalLink(sup.tos_url));
    const privBtn = body.querySelector("#privacyLinkBtn");
    if (privBtn && sup.privacy_url) privBtn.addEventListener("click", () => openExternalLink(sup.privacy_url));

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
