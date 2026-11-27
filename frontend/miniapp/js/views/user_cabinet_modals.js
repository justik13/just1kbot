import { api } from "../api.js?v=2.8.0";
import {
    escapeHtml,
    formatBytesGb,
    openExternalLink,
    openModal,
    showConfirmModal,
    showToast,
} from "../ui.js?v=2.8.0";

const _CHEVRON_DOWN_SVG = `<svg class="icon-svg" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>`;

export function calcStdPrice(source, devices, days) {
    const key = String(days);
    const devKey = String(devices);
    const topPlans = source && source.standard_tariff_prices;
    const nestedPlans = source && (source.standard_plans || (source.pricing && source.pricing.standard_plans));
    const entry = (topPlans && topPlans[key]) || (nestedPlans && nestedPlans[key]);
    if (entry != null) {
        if (Array.isArray(entry) && entry.length >= 2) {
            return Number(entry[0]) + Math.max(0, devices - 2) * Number(entry[1]);
        }
        if (typeof entry === "object") {
            if (entry[devKey] != null) return Number(entry[devKey]);
            const base = entry.base_rub ?? entry.base;
            const step = entry.step_rub ?? entry.step;
            if (base != null && step != null) {
                return Number(base) + Math.max(0, devices - 2) * Number(step);
            }
        }
    }
    return null;
}

export function calcBalanceSplit(priceRub, bal, useBalance, minTopupRub = 20) {
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

function _handlePaymentResponse(res, successMsg, onReload, onTabSwitch) {
    if (!res) {
        showToast("Не удалось оформить платёж. Попробуйте ещё раз.", "error");
        return;
    }
    if (res.fulfillment_error || res.fulfillment_failed) {
        showToast(res.fulfillment_error || "Не удалось активировать подписку. Средства сохранены на балансе.", "error");
        onReload();
        return;
    }
    if (!res.confirmation_url) {
        const st = String(res.status || "").toLowerCase();
        if (st === "canceled" || st === "pending") {
            showToast("Платёж не был завершён. Попробуйте оформить заново.", "error");
            return;
        }
        if (res.service_type && res.service_type !== "topup") {
            onTabSwitch("connect");
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
            Страница оплаты открыта. После завершения платежа статус обновится автоматически, или нажмите «Проверить оплату».
        </div>
        <button id="modalPayCheckBtn" type="button" class="btn btn-primary btn-lg">Проверить оплату</button>
        <button id="modalPayReopenBtn" type="button" class="btn btn-secondary">Открыть оплату снова</button>
    `;

    const hintEl = wrap.querySelector("#modalPayHint");
    const checkBtn = wrap.querySelector("#modalPayCheckBtn");
    const reopenBtn = wrap.querySelector("#modalPayReopenBtn");

    let stopped = false;
    let inFlight = false;
    let pollCount = 0;
    let timerId = null;

    const cleanup = () => {
        if (stopped) return;
        stopped = true;
        if (timerId !== null) {
            clearInterval(timerId);
            timerId = null;
        }
        document.removeEventListener("visibilitychange", onVisible);
        window.removeEventListener("focus", onFocus);
    };

    const close = openModal("Ожидание оплаты", wrap, { onClose: cleanup });

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
                    onTabSwitch("connect");
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
            if (st.status === "refunded") {
                cleanup();
                hintEl.textContent = "Платёж был возвращён. При необходимости оформите заказ заново.";
                checkBtn.disabled = true;
                checkBtn.textContent = "Платёж возвращён";
                onReload();
                return;
            }
            if (manual) {
                hintEl.textContent = "Платёж ещё обрабатывается банком. Попробуйте через пару секунд.";
            }
        } catch (err) {
            if (!wrap.isConnected || stopped) {
                cleanup();
                return;
            }
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
        if (!wrap.isConnected || stopped) {
            cleanup();
            return;
        }
        if (document.visibilityState === "visible") {
            checkNow(false);
        }
    };
    const onFocus = () => {
        if (!wrap.isConnected || stopped) {
            cleanup();
            return;
        }
        checkNow(false);
    };

    timerId = setInterval(() => {
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

export function openTopupModal(minTopup, onReload, onTabSwitch) {
    const form = document.createElement("form");
    form.className = "field";
    form.style.gap = "12px";
    form.innerHTML = `
        <div class="field">
            <label for="topupAmountInput">Сумма пополнения (от ${minTopup} до 50000 ₽)</label>
            <input id="topupAmountInput" name="amount" class="input tabular" type="number" min="${minTopup}" max="50000" step="1" value="250" required>
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
    const amountInput = form.querySelector("#topupAmountInput");
    form.querySelectorAll("[data-amt]").forEach((b) => {
        b.addEventListener("click", () => {
            if (amountInput) amountInput.value = b.dataset.amt;
        });
    });
    let idemKey = _newIdempotencyKey();
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const raw = String((amountInput && amountInput.value) || "").trim();
        const amount = Number(raw);
        if (!/^\d+$/.test(raw) || !Number.isInteger(amount) || amount < minTopup) {
            showToast(`Минимальная сумма: ${minTopup} ₽`, "error");
            return;
        }
        if (amount > 50000) {
            showToast("Максимальная сумма пополнения: 50000 ₽", "error");
            return;
        }
        const btn = form.querySelector("button[type='submit']");
        btn.disabled = true;
        try {
            const res = await api.topupBalance(amount, idemKey);
            close();
            _handlePaymentResponse(res, "Баланс пополнен", onReload, onTabSwitch);
        } catch (err) {
            idemKey = _newIdempotencyKey();
            btn.disabled = false;
            showToast(err.message || "Не удалось создать платёж", "error");
        }
    });
}

export function openStandardPlanModal(d, mode, onReload, helpers) {
    const { getStdGraceInfo, onTabSwitch } = helpers;
    const std = d.standard || {};
    const bal = d.balance || { real: 0, bonus: 0, total: 0 };
    const pricing = d.pricing || {};
    const tariffDurations = d.standard_tariff_prices
        ? Object.keys(d.standard_tariff_prices).map(Number).filter(Number.isFinite).sort((a, b) => a - b)
        : [];
    const durations = pricing.standard_durations || (tariffDurations.length ? tariffDurations : [7, 30, 90]);
    const isGraceRenew = mode === "renew" && getStdGraceInfo(d, std).inGrace;
    const allowDeviceChoice = mode === "buy" || isGraceRenew;

    let devices = mode === "renew" ? Math.max(2, Number(std.device_limit || 2)) : 2;
    let duration = 30;
    let useBalance = true;
    let quote = null;
    let idemKey = _newIdempotencyKey();

    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.style.gap = "12px";

    const close = openModal(mode === "renew" ? "Продление · Стандарт" : "Подключение · Стандарт", wrap);

    const getCheckoutText = (price, cardDue, balUsed) => {
        if (price == null) return "Расчёт цены...";
        const verb = mode === "renew" ? "Продлить" : "Подключить";
        if (cardDue === 0 && price > 0) return `Оплатить с баланса (${price} ₽)`;
        if (balUsed > 0 && cardDue > 0) return `${verb} · доплата ${cardDue} ₽`;
        return `${verb} за ${cardDue} ₽`;
    };

    const updateSummary = () => {
        const summaryEl = wrap.querySelector("#modalStdSummary");
        const submitBtn = wrap.querySelector("#modalStdPayBtn");
        if (!summaryEl || !submitBtn) return;

        const basePrice = calcStdPrice(d, devices, duration);
        const price = quote ? Number(quote.due_rub ?? basePrice ?? 0) : basePrice;
        const originalPrice = quote ? Number(quote.original_price_rub ?? price ?? 0) : basePrice;
        const discount = quote ? Number(quote.discount_rub || 0) : 0;
        const split = price != null
            ? calcBalanceSplit(price, bal, useBalance, pricing.min_topup_rub || 20)
            : { balUsed: 0, cardDue: 0 };

        const lines = [
            `<div class="quote-line"><span>Тариф (${devices} устр. · ${duration} дн.)</span><strong>${originalPrice != null ? `${originalPrice} ₽` : "—"}</strong></div>`,
        ];
        if (discount > 0) {
            lines.push(`<div class="quote-line"><span>Приветственная скидка</span><strong style="color:var(--accent-emerald);">-${discount} ₽</strong></div>`);
        }
        if (split.balUsed > 0) {
            lines.push(`<div class="quote-line"><span>С баланса</span><strong>-${split.balUsed} ₽</strong></div>`);
        }
        if (price != null) {
            lines.push(
                split.cardDue > 0
                    ? `<div class="quote-line"><span>К оплате картой / СБП</span><strong>${split.cardDue} ₽</strong></div>`
                    : `<div class="quote-line"><span>К оплате картой / СБП</span><strong style="color:var(--accent-emerald);">0 ₽</strong></div>`
            );
        }
        const blockedReason = quote && quote.blocked_reason ? quote.blocked_reason : null;
        if (blockedReason) {
            const tooManyDevices = Boolean(quote && quote.blocked_by_devices);
            lines.push(`
                <div class="notice-box notice-warn" style="margin-top:6px; flex-direction:column; align-items:flex-start; gap:8px;">
                    <span>${escapeHtml(blockedReason)}</span>
                    ${tooManyDevices ? `<button id="stdRenewGoDevicesBtn" type="button" class="btn btn-sm">Управлять устройствами</button>` : ""}
                </div>
            `);
        }
        summaryEl.innerHTML = lines.join("");
        const goDevBtn = summaryEl.querySelector("#stdRenewGoDevicesBtn");
        if (goDevBtn) {
            goDevBtn.addEventListener("click", () => {
                close();
                onTabSwitch("connect");
                onReload({ tab: "connect" });
            });
        }
        submitBtn.textContent = getCheckoutText(price, split.cardDue, split.balUsed);
        submitBtn.disabled = Boolean(blockedReason || price == null);
    };

    const updateControls = () => {
        wrap.querySelectorAll("[data-preset]").forEach((b) => {
            const isActive = devices === Number(b.dataset.preset);
            b.classList.toggle("active", isActive);
            b.setAttribute("aria-pressed", isActive ? "true" : "false");
        });
        const stepVal = wrap.querySelector("#stepDevVal");
        if (stepVal) stepVal.textContent = `${devices} устр.`;
        const minus = wrap.querySelector("#stepMinus");
        const plus = wrap.querySelector("#stepPlus");
        if (minus) minus.disabled = devices <= 2;
        if (plus) plus.disabled = devices >= 10;

        wrap.querySelectorAll("[data-dur]").forEach((b) => {
            const dDays = Number(b.dataset.dur);
            const isActive = duration === dDays;
            b.classList.toggle("active", isActive);
            b.setAttribute("aria-pressed", isActive ? "true" : "false");
            const priceEl = b.querySelector(".price-tile-price");
            const p = calcStdPrice(d, devices, dDays);
            if (priceEl) priceEl.textContent = p != null ? `${p} ₽` : "—";
        });
        updateSummary();
    };

    const fetchQuote = async () => {
        if (!wrap.isConnected) return;
        const reqDev = devices;
        const reqDur = duration;
        try {
            const q = await api.quoteStandard(reqDev, reqDur, isGraceRenew ? "renew" : null);
            if (!wrap.isConnected || reqDev !== devices || reqDur !== duration) return;
            quote = q;
            updateSummary();
        } catch {
            if (!wrap.isConnected || reqDev !== devices || reqDur !== duration) return;
            updateSummary();
            const sumEl = wrap.querySelector("#modalStdSummary");
            if (sumEl && !sumEl.querySelector(".std-quote-warn")) {
                const warn = document.createElement("div");
                warn.className = "meta-text std-quote-warn";
                warn.style.cssText = "color:var(--accent-amber); font-size:11px; margin-top:4px;";
                warn.textContent = "Не удалось обновить расчёт с сервера — показана базовая оценка";
                sumEl.appendChild(warn);
            }
        }
    };
    const debouncedQuote = _createDebouncedTask(fetchQuote, 150);

    wrap.innerHTML = `
        ${allowDeviceChoice ? `
            ${isGraceRenew ? `<div class="notice-box notice-warn"><span>Льготный период: при продлении можно сразу выбрать нужное число устройств.</span></div>` : ""}
            <div class="field">
                <span id="stdPresetsLabel" style="font-size:12px; color:var(--text-secondary);">Готовые конфигурации</span>
                <div class="filter-pills" role="group" aria-labelledby="stdPresetsLabel">
                    <button type="button" class="pill-btn ${devices === 2 ? "active" : ""}" aria-pressed="${devices === 2 ? "true" : "false"}" data-preset="2">Базовый · 2</button>
                    <button type="button" class="pill-btn ${devices === 5 ? "active" : ""}" aria-pressed="${devices === 5 ? "true" : "false"}" data-preset="5">Семейный · 5</button>
                    <button type="button" class="pill-btn ${devices === 10 ? "active" : ""}" aria-pressed="${devices === 10 ? "true" : "false"}" data-preset="10">Pro · 10</button>
                </div>
            </div>
            <div class="field">
                <span id="stdStepperLabel" style="font-size:12px; color:var(--text-secondary);">Точное число устройств (от 2 до 10)</span>
                <div class="stepper" role="group" aria-labelledby="stdStepperLabel">
                    <button type="button" class="stepper-btn" id="stepMinus" aria-label="Уменьшить количество устройств" ${devices <= 2 ? "disabled" : ""}>−</button>
                    <span class="stepper-val" id="stepDevVal">${devices} устр.</span>
                    <button type="button" class="stepper-btn" id="stepPlus" aria-label="Увеличить количество устройств" ${devices >= 10 ? "disabled" : ""}>+</button>
                </div>
            </div>
        ` : `
            <div class="notice-box">
                <span>Текущий лимит: <strong style="color:var(--text-primary);">${devices} устр.</strong></span>
                <span>+ дни к сроку</span>
            </div>
        `}
        <div class="field">
            <span id="stdDurationLabel" style="font-size:12px; color:var(--text-secondary);">Выберите срок</span>
            <div class="price-tile-grid" role="group" aria-labelledby="stdDurationLabel">
                ${durations.map((days) => {
                    const p = calcStdPrice(d, devices, days);
                    return `
                        <button type="button" class="price-tile ${duration === days ? "active" : ""}" aria-pressed="${duration === days ? "true" : "false"}" data-dur="${days}">
                            <span class="price-tile-top">${days} дн.</span>
                            <span class="price-tile-price">${p != null ? `${p} ₽` : "—"}</span>
                        </button>
                    `;
                }).join("")}
            </div>
        </div>
        <label class="toggle-row" for="modalStdBalChk">
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
            idemKey = _newIdempotencyKey();
            updateControls();
            debouncedQuote.schedule();
        });
    });

    const minus = wrap.querySelector("#stepMinus");
    const plus = wrap.querySelector("#stepPlus");
    if (minus) {
        minus.addEventListener("click", () => {
            if (devices > 2) {
                devices -= 1;
                quote = null;
                idemKey = _newIdempotencyKey();
                updateControls();
                debouncedQuote.schedule();
            }
        });
    }
    if (plus) {
        plus.addEventListener("click", () => {
            if (devices < 10) {
                devices += 1;
                quote = null;
                idemKey = _newIdempotencyKey();
                updateControls();
                debouncedQuote.schedule();
            }
        });
    }

    wrap.querySelectorAll("[data-dur]").forEach((b) => {
        b.addEventListener("click", () => {
            duration = Number(b.dataset.dur);
            quote = null;
            idemKey = _newIdempotencyKey();
            updateControls();
            debouncedQuote.schedule();
        });
    });

    const balChk = wrap.querySelector("#modalStdBalChk");
    balChk.addEventListener("change", () => {
        useBalance = balChk.checked;
        idemKey = _newIdempotencyKey();
        updateSummary();
    });

    const payBtn = wrap.querySelector("#modalStdPayBtn");
    payBtn.addEventListener("click", async () => {
        debouncedQuote.cancel();
        payBtn.disabled = true;
        try {
            const res = await api.checkoutStandard(devices, duration, useBalance, idemKey, isGraceRenew ? "renew" : null);
            close();
            _handlePaymentResponse(res, "Подписка «Стандарт» оформлена", onReload, onTabSwitch);
        } catch (err) {
            idemKey = _newIdempotencyKey();
            payBtn.disabled = false;
            showToast(err.message || "Ошибка оплаты", "error");
        }
    });

    updateSummary();
    fetchQuote();
}

/** Создаёт отложенный вызов с отменой предыдущего таймера при быстрых кликах. */
function _createDebouncedTask(fn, delayMs = 150) {
    let timer = null;
    return {
        schedule() {
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => {
                timer = null;
                fn();
            }, delayMs);
        },
        cancel() {
            if (timer) {
                clearTimeout(timer);
                timer = null;
            }
        },
    };
}

export function openStandardDevicesModal(d, onReload, helpers) {
    const { safeFormatDate, onTabSwitch } = helpers;
    const std = d.standard || {};
    const bal = d.balance || { real: 0, bonus: 0, total: 0 };
    const currentLimit = Math.max(2, Number(std.device_limit || 2));
    let targetLimit = currentLimit < 10 ? currentLimit + 1 : currentLimit - 1;
    let useBalance = true;
    let quote = null;
    let quoteError = null;
    let idemKey = _newIdempotencyKey();

    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.style.gap = "12px";

    const close = openModal("Устройства · Стандарт", wrap);
    const expStr = safeFormatDate(std.expires_at);

    wrap.innerHTML = `
        <div class="notice-box">
            <span>Сейчас активно: <strong style="color:var(--text-primary);">${currentLimit} устр.</strong></span>
            <span>${expStr ? `до ${expStr}` : ""}</span>
        </div>
        <div class="field">
            <span id="devStepperLabel" style="font-size:12px; color:var(--text-secondary);">Новый лимит устройств (от 2 до 10)</span>
            <div class="stepper" role="group" aria-labelledby="devStepperLabel">
                <button type="button" class="stepper-btn" id="devMinus" aria-label="Уменьшить лимит устройств">−</button>
                <span class="stepper-val" id="devTargetVal">${targetLimit} устр.</span>
                <button type="button" class="stepper-btn" id="devPlus" aria-label="Увеличить лимит устройств">+</button>
            </div>
        </div>
        <label class="toggle-row" id="devBalWrap" for="devBalChk" hidden>
            <span class="toggle-label">Списать с баланса (доступно ${bal.total} ₽)</span>
            <span class="switch">
                <input id="devBalChk" type="checkbox" ${useBalance ? "checked" : ""}>
                <span class="switch-track"></span>
            </span>
        </label>
        <div id="devInfoBox"></div>
        <button id="devApplyBtn" type="button" class="btn btn-primary btn-lg" disabled>Выберите другой лимит</button>
    `;

    const devMinus = wrap.querySelector("#devMinus");
    const devPlus = wrap.querySelector("#devPlus");
    const devTargetVal = wrap.querySelector("#devTargetVal");
    const devBalWrap = wrap.querySelector("#devBalWrap");
    const devBalChk = wrap.querySelector("#devBalChk");
    const devInfoBox = wrap.querySelector("#devInfoBox");
    const devApplyBtn = wrap.querySelector("#devApplyBtn");

    const updateView = () => {
        const isSame = targetLimit === currentLimit;
        const isUpgrade = targetLimit > currentLimit;
        const isDowngrade = targetLimit < currentLimit;
        const price = quote ? Number(quote.due_rub || 0) : 0;
        const split = calcBalanceSplit(price, bal, useBalance, (d.pricing && d.pricing.min_topup_rub) || 20);
        const blockedReason = quote && quote.blocked_reason ? quote.blocked_reason : null;

        devTargetVal.textContent = `${targetLimit} устр.`;
        devMinus.disabled = targetLimit <= 2;
        devPlus.disabled = targetLimit >= 10;
        devBalWrap.hidden = !isUpgrade;

        let infoHtml = `<div class="muted">Выберите новое число устройств, отличное от текущего (${currentLimit} устр.).</div>`;
        if (!isSame && quoteError) {
            infoHtml = `
                <div class="notice-box notice-warn" style="flex-direction:column; align-items:flex-start; gap:8px;">
                    <span>${escapeHtml(quoteError)}</span>
                    <button id="devRetryQuoteBtn" type="button" class="btn btn-sm">Повторить</button>
                </div>
            `;
        } else if (!isSame && !quote) {
            infoHtml = `<div class="muted">Расчёт условий...</div>`;
        } else if (isUpgrade && quote) {
            infoHtml = blockedReason
                ? `<div class="notice-box notice-warn">${escapeHtml(blockedReason)}</div>`
                : `
                <div class="quote-box">
                    <div class="quote-line"><span>Доплата до конца текущего срока (${quote.days_left || 1} дн.)</span><strong>${price} ₽</strong></div>
                    ${split.balUsed > 0 ? `<div class="quote-line"><span>С баланса</span><strong>-${split.balUsed} ₽</strong></div>` : ""}
                    <div class="quote-line"><span>К оплате картой / СБП</span><strong>${split.cardDue} ₽</strong></div>
                </div>
            `;
        } else if (isDowngrade && quote) {
            const tooManyDevices = Boolean(
                quote.blocked_by_devices
                || (blockedReason && blockedReason.includes("устройств") && Number(quote.active_hwid_count || 0) > targetLimit)
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
                       ? `<div class="muted" style="font-size:11px; margin-top:6px;">Бонусами можно оплатить до 100% следующих заказов.</div>`
                       : ""}`;
        }

        devInfoBox.innerHTML = infoHtml;

        const btnLabel = isSame
            ? "Выберите другой лимит"
            : (isUpgrade
                ? (split.cardDue === 0 ? `Увеличить за ${price} ₽ с баланса` : `Доплатить ${split.cardDue} ₽`)
                : `Снизить до ${targetLimit} устр.`);
        devApplyBtn.textContent = btnLabel;
        devApplyBtn.disabled = Boolean(isSame || !quote || blockedReason || quoteError);

        const retryBtn = devInfoBox.querySelector("#devRetryQuoteBtn");
        if (retryBtn) {
            retryBtn.addEventListener("click", () => fetchQuote());
        }
        const goDevBtn = devInfoBox.querySelector("#devGoDevicesBtn");
        if (goDevBtn) {
            goDevBtn.addEventListener("click", () => {
                close();
                onTabSwitch("connect");
                onReload({ tab: "connect" });
            });
        }
    };

    const fetchQuote = async () => {
        if (!wrap.isConnected) return;
        if (targetLimit === currentLimit) {
            quote = null;
            quoteError = null;
            updateView();
            return;
        }
        quote = null;
        quoteError = null;
        updateView();
        const reqTarget = targetLimit;
        try {
            const q = await api.quoteStandard(reqTarget, 30);
            if (!wrap.isConnected || reqTarget !== targetLimit) return;
            quote = q;
            quoteError = null;
            updateView();
        } catch (err) {
            if (!wrap.isConnected || reqTarget !== targetLimit) return;
            quote = null;
            quoteError = (err && err.message) || "Не удалось рассчитать изменение";
            showToast(quoteError, "error");
            updateView();
        }
    };
    const debouncedQuote = _createDebouncedTask(fetchQuote, 150);

    devMinus.addEventListener("click", () => {
        if (targetLimit > 2) {
            targetLimit -= 1;
            idemKey = _newIdempotencyKey();
            quote = null;
            quoteError = null;
            updateView();
            if (targetLimit === currentLimit) debouncedQuote.cancel();
            else debouncedQuote.schedule();
        }
    });
    devPlus.addEventListener("click", () => {
        if (targetLimit < 10) {
            targetLimit += 1;
            idemKey = _newIdempotencyKey();
            quote = null;
            quoteError = null;
            updateView();
            if (targetLimit === currentLimit) debouncedQuote.cancel();
            else debouncedQuote.schedule();
        }
    });
    devBalChk.addEventListener("change", () => {
        useBalance = devBalChk.checked;
        idemKey = _newIdempotencyKey();
        updateView();
    });

    devApplyBtn.addEventListener("click", async () => {
        const isUpgrade = targetLimit > currentLimit;
        if (isUpgrade) {
            devApplyBtn.disabled = true;
            try {
                const res = await api.checkoutStandard(targetLimit, 30, useBalance, idemKey);
                close();
                _handlePaymentResponse(res, `Лимит увеличен до ${targetLimit} устр.`, onReload, onTabSwitch);
            } catch (err) {
                idemKey = _newIdempotencyKey();
                devApplyBtn.disabled = false;
                showToast(err.message || "Не удалось изменить лимит", "error");
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
                devApplyBtn.disabled = true;
                try {
                    await api.downgradeStandard(targetLimit);
                    showToast(`Лимит снижен до ${targetLimit} устр.`);
                    queueMicrotask(() => {
                        close();
                        onReload();
                    });
                } catch (err) {
                    devApplyBtn.disabled = false;
                    throw new Error((err && err.message) || "Не удалось изменить лимит");
                }
            },
        });
    });

    updateView();
    fetchQuote();
}

export function openWiPurchaseModal(d, mode, onReload, helpers) {
    const { serverNowMs, safeFormatDate, onTabSwitch } = helpers;
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
        isRenew && wi.expires_at && new Date(wi.expires_at).getTime() > serverNowMs()
    );
    const defaultDevices = isRenew
        ? (isActivePeriod ? curLimit : Math.max(1, Number(wi.pending_device_limit || curLimit)))
        : 1;
    let devices = defaultDevices;
    let useBalance = true;
    let idemKey = _newIdempotencyKey();

    const wiStatus = String(wi.status || "").toUpperCase();
    const isExpiredRebuy = !isRenew && wiStatus === "EXPIRED";
    const welcomeDiscountEligible = Boolean(!isRenew && wiStatus !== "EXPIRED" && ref.welcome_discount_eligible);
    const welcomeDiscountPct = Number(ref.welcome_discount_pct || 25);

    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.style.gap = "12px";
    const modalTitle = isRenew
        ? "Продление · Белый интернет"
        : isExpiredRebuy
            ? "Возобновление · Белый интернет"
            : "Подключение · Белый интернет";
    const close = openModal(modalTitle, wrap);

    const wiExpStr = safeFormatDate(wi.expires_at);
    const wiPendStr = safeFormatDate(wi.pending_from || wi.expires_at);

    wrap.innerHTML = `
        ${isRenew ? `
            <div class="notice-box">
                <span>Сейчас активно: <strong style="color:var(--text-primary);">${curLimit} устр.</strong></span>
                <span>${wiExpStr ? `до ${wiExpStr}` : ""}</span>
            </div>
            ${wi.pending_device_limit ? `
                <div class="notice-box notice-info" style="font-size:11px; padding:6px 10px;">
                    <span>${isActivePeriod
                        ? `Запланировано снижение до ${wi.pending_device_limit} устр.${wiPendStr ? ` с ${wiPendStr}` : ""}. Переход произойдёт в эту дату (автопродление или продление после окончания срока). Продление сейчас на ${curLimit} устр. отменит запланированное снижение.`
                        : `Запланировано снижение до ${wi.pending_device_limit} устр. Вы можете продлить на ${wi.pending_device_limit} устр. или выбрать другое число слотов ниже.`
                    }</span>
                </div>
            ` : ""}
        ` : `
            <div class="muted" style="font-size:12px;">
                ${isExpiredRebuy
                    ? "Срок предыдущего периода завершился. Выберите количество слотов на новый период (+30 дн.) для возобновления доступа."
                    : "Независимая подписка на 30 дней для мобильной сети при жёстких ограничениях. Работает как отдельно, так и в дополнение к «Стандарту»."}
            </div>
            ${d.devices && d.devices.length > 1 ? (() => {
                const sortedDevs = [...d.devices].sort((a, b) => (a.id || 0) - (b.id || 0));
                const firstDevName = sortedDevs[0]?.device_name || sortedDevs[0]?.platform || "Устройство";
                return `
                <div id="modalWiPrimaryDevNotice" class="notice-box notice-info" style="font-size:11px; padding:6px 10px; margin-top:4px;" ${d.devices.length > devices ? "" : "hidden"}>
                    <span>Слот будет привязан к основному устройству (${escapeHtml(firstDevName)}). Выбрать другое можно в любой момент на вкладке подключения.</span>
                </div>
                `;
            })() : ""}
        `}
        <div class="field">
            <span id="wiPurchaseDevicesLabel" style="font-size:12px; color:var(--text-secondary);">${isRenew ? "Количество слотов на новый период (+30 дн.)" : "Количество устройств (по 50 ГБ на слот)"}</span>
            <div class="price-tile-grid" role="group" aria-labelledby="wiPurchaseDevicesLabel">
                ${[1, 2, 3].map((n) => {
                    const p = base + (n - 1) * extra;
                    const isDisabled = isActivePeriod && n < curLimit;
                    return `
                        <button type="button" class="price-tile ${devices === n ? "active" : ""}" aria-pressed="${devices === n ? "true" : "false"}" data-wi-cnt="${n}" ${isDisabled ? "disabled" : ""}>
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
        <label class="toggle-row" for="modalWiBalChk">
            <span class="toggle-label">Списать с баланса (доступно ${bal.total} ₽)</span>
            <span class="switch switch-wi">
                <input id="modalWiBalChk" type="checkbox" ${useBalance ? "checked" : ""}>
                <span class="switch-track"></span>
            </span>
        </label>
        <div id="modalWiQuote" class="quote-box"></div>
        <button id="modalWiPayBtn" type="button" class="btn btn-primary btn-lg"></button>
    `;

    const quoteEl = wrap.querySelector("#modalWiQuote");
    const payBtn = wrap.querySelector("#modalWiPayBtn");
    const primaryDevNotice = wrap.querySelector("#modalWiPrimaryDevNotice");

    const updateView = () => {
        if (primaryDevNotice) {
            primaryDevNotice.hidden = !(d.devices && d.devices.length > devices);
        }
        const originalPrice = base + Math.max(0, devices - 1) * extra;
        const discount = welcomeDiscountEligible
            ? Math.floor((originalPrice * welcomeDiscountPct) / 100)
            : 0;
        const total = Math.max(1, originalPrice - discount);
        const split = calcBalanceSplit(total, bal, useBalance, minTopup);
        const verb = isRenew ? "Продлить" : "Подключить";
        const ctaText = split.cardDue === 0
            ? `Оплатить с баланса (${total} ₽)`
            : (split.balUsed > 0 ? `${verb} · доплата ${split.cardDue} ₽` : `${verb} за ${total} ₽`);

        wrap.querySelectorAll("[data-wi-cnt]").forEach((b) => {
            const isSel = Number(b.dataset.wiCnt) === devices;
            b.classList.toggle("active", isSel);
            b.setAttribute("aria-pressed", isSel ? "true" : "false");
        });
        quoteEl.innerHTML = `
            <div class="quote-line"><span>${isRenew ? "Продление" : "Подписка"} (${devices} устр. · +30 дн.)</span><strong>${originalPrice} ₽</strong></div>
            ${discount > 0 ? `<div class="quote-line"><span>Приветственная скидка</span><strong style="color:var(--accent-emerald);">-${discount} ₽</strong></div>` : ""}
            ${split.balUsed > 0 ? `<div class="quote-line"><span>С баланса</span><strong>-${split.balUsed} ₽</strong></div>` : ""}
            <div class="quote-line"><span>К оплате картой / СБП</span><strong ${split.cardDue === 0 ? 'style="color:var(--accent-emerald);"' : ""}>${split.cardDue} ₽</strong></div>
        `;
        payBtn.textContent = ctaText;
    };

    wrap.querySelectorAll("[data-wi-cnt]").forEach((b) => {
        b.addEventListener("click", () => {
            devices = Number(b.dataset.wiCnt);
            idemKey = _newIdempotencyKey();
            updateView();
        });
    });

    const balChk = wrap.querySelector("#modalWiBalChk");
    balChk.addEventListener("change", () => {
        useBalance = balChk.checked;
        idemKey = _newIdempotencyKey();
        updateView();
    });

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
                onReload,
                onTabSwitch
            );
        } catch (err) {
            idemKey = _newIdempotencyKey();
            payBtn.disabled = false;
            showToast(err.message || (isRenew ? "Ошибка продления" : "Ошибка подключения"), "error");
        }
    });

    updateView();
}

export function openWiManageModal(d, onReload, helpers) {
    const { safeFormatDate, onTabSwitch } = helpers;
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

    let selectedItem = wiPacks.length
        ? { kind: "pack", gb: Number(wiPacks[0].gb), price: Number(wiPacks[0].price_rub) }
        : (canAddSlot ? { kind: "slot" } : null);
    let useBalance = true;
    let idemKey = _newIdempotencyKey();

    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.style.gap = "12px";

    const close = openModal("Слоты и трафик · Белый интернет", wrap);
    const wiPendStr = safeFormatDate(wi.pending_from);
    const wiExpStr = safeFormatDate(wi.expires_at);

    wrap.innerHTML = `
        <div class="quote-box">
            <div class="quote-line"><span>Трафик</span><strong>${wiUsedGb} / ${wiTotalGb} ГБ</strong></div>
            <div class="quote-line"><span>Слотов устройств</span><strong>${wiCurLimit}</strong></div>
            <div class="quote-line"><span>Баланс аккаунта</span><strong>${bal.total} ₽</strong></div>
        </div>
        ${canAddSlot ? `
            <div class="field">
                <span id="wiManageSlotLabel" style="font-size:12px; color:var(--text-secondary);">Дополнительный слот (до ${wiMaxDevices} слотов в тарифе)</span>
                <button id="modalWiSelectSlotBtn" type="button" class="price-tile ${selectedItem && selectedItem.kind === "slot" ? "active" : ""}" aria-pressed="${selectedItem && selectedItem.kind === "slot" ? "true" : "false"}" aria-labelledby="wiManageSlotLabel" style="width:100%; flex-direction:row; justify-content:space-between; padding:12px 16px;">
                    <span class="price-tile-top">+1 слот устройства (+${addSlotGb} ГБ)</span>
                    <span class="price-tile-price">${addSlotPrice} ₽</span>
                </button>
            </div>
        ` : (wiCurLimit >= wiMaxDevices ? `
            <div class="notice-box notice-info" style="font-size:12px; margin-top:4px;">
                <span>Достигнут максимум тарифа (${wiMaxDevices} слота). Больше слотов добавить нельзя.</span>
            </div>
        ` : "")}
        ${wiPacks.length ? `
            <div class="field">
                <span id="wiManagePacksLabel" style="font-size:12px; color:var(--text-secondary);">Дополнительный трафик</span>
                <div class="price-tile-grid" role="group" aria-labelledby="wiManagePacksLabel">
                    ${wiPacks.map((p) => {
                        const isSel = selectedItem && selectedItem.kind === "pack" && Number(selectedItem.gb) === Number(p.gb);
                        return `
                            <button type="button" class="price-tile ${isSel ? "active" : ""}" aria-pressed="${isSel ? "true" : "false"}" data-modal-wi-pack="${p.gb}" data-modal-wi-price="${p.price_rub}">
                                <span class="price-tile-top">+${p.gb} ГБ</span>
                                <span class="price-tile-price">${p.price_rub} ₽</span>
                            </button>
                        `;
                    }).join("")}
                </div>
            </div>
        ` : ""}
        ${selectedItem ? `
            <label class="toggle-row" for="modalWiManageBalChk">
                <span class="toggle-label">Списать с баланса (доступно ${bal.total} ₽)</span>
                <span class="switch switch-wi">
                    <input id="modalWiManageBalChk" type="checkbox" ${useBalance ? "checked" : ""}>
                    <span class="switch-track"></span>
                </span>
            </label>
            <div id="modalWiManageQuote" class="quote-box"></div>
            <button id="modalWiManagePayBtn" type="button" class="btn btn-primary btn-lg"></button>
        ` : ""}
        ${wi.pending_device_limit ? `
            <div class="notice-box notice-warn">
                <span>Снижение до ${wi.pending_device_limit} устр.${wiPendStr ? ` с ${wiPendStr}` : ""}</span>
                <button id="modalWiCancelDgBtn" type="button" class="btn btn-sm btn-danger">Отменить</button>
            </div>
        ` : (wiCurLimit > 1 ? `
            <details class="guide-details">
                <summary class="guide-summary">
                    <span>Снизить число слотов со следующего периода</span>
                    <span class="disclosure-pill"><span>Изменить</span>${_CHEVRON_DOWN_SVG}</span>
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
    const quoteEl = wrap.querySelector("#modalWiManageQuote");
    const payBtn = wrap.querySelector("#modalWiManagePayBtn");

    const updateView = () => {
        if (selectSlotBtn) {
            const isSlot = Boolean(selectedItem && selectedItem.kind === "slot");
            selectSlotBtn.classList.toggle("active", isSlot);
            selectSlotBtn.setAttribute("aria-pressed", isSlot ? "true" : "false");
        }
        wrap.querySelectorAll("[data-modal-wi-pack]").forEach((b) => {
            const isSel = Boolean(
                selectedItem && selectedItem.kind === "pack" && Number(selectedItem.gb) === Number(b.dataset.modalWiPack)
            );
            b.classList.toggle("active", isSel);
            b.setAttribute("aria-pressed", isSel ? "true" : "false");
        });
        if (!selectedItem || !quoteEl || !payBtn) return;

        let itemLabel = "";
        let itemPrice = 0;
        if (selectedItem.kind === "slot") {
            itemLabel = `Доп. слот (+1 устр. · +${addSlotGb} ГБ)`;
            itemPrice = addSlotPrice;
        } else {
            itemLabel = `Пакет трафика (+${selectedItem.gb} ГБ)`;
            itemPrice = selectedItem.price;
        }
        const split = calcBalanceSplit(itemPrice, bal, useBalance, minTopup);
        const ctaText = split.cardDue === 0
            ? `Оплатить с баланса (${itemPrice} ₽)`
            : (split.balUsed > 0 ? `Оплатить · доплата ${split.cardDue} ₽` : `Оплатить ${itemPrice} ₽`);

        quoteEl.innerHTML = `
            <div class="quote-line"><span>${escapeHtml(itemLabel)}</span><strong>${itemPrice} ₽</strong></div>
            ${split.balUsed > 0 ? `<div class="quote-line"><span>С баланса</span><strong>-${split.balUsed} ₽</strong></div>` : ""}
            <div class="quote-line"><span>К оплате картой / СБП</span><strong ${split.cardDue === 0 ? 'style="color:var(--accent-emerald);"' : ""}>${split.cardDue} ₽</strong></div>
        `;
        payBtn.textContent = ctaText;
    };

    if (selectSlotBtn) {
        selectSlotBtn.addEventListener("click", () => {
            selectedItem = { kind: "slot" };
            idemKey = _newIdempotencyKey();
            updateView();
        });
    }

    wrap.querySelectorAll("[data-modal-wi-pack]").forEach((b) => {
        b.addEventListener("click", () => {
            selectedItem = {
                kind: "pack",
                gb: Number(b.dataset.modalWiPack),
                price: Number(b.dataset.modalWiPrice),
            };
            idemKey = _newIdempotencyKey();
            updateView();
        });
    });

    const balChk = wrap.querySelector("#modalWiManageBalChk");
    if (balChk) {
        balChk.addEventListener("change", () => {
            useBalance = balChk.checked;
            idemKey = _newIdempotencyKey();
            updateView();
        });
    }

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
                    _handlePaymentResponse(res, "Слот добавлен", onReload, onTabSwitch);
                } else {
                    const res = await api.checkoutWi({
                        action: "topup",
                        gb: Number(selectedItem.gb),
                        use_balance: useBalance,
                        idempotency_key: idemKey,
                    });
                    close();
                    _handlePaymentResponse(res, "Пакет трафика добавлен", onReload, onTabSwitch);
                }
            } catch (err) {
                idemKey = _newIdempotencyKey();
                payBtn.disabled = false;
                showToast(err.message || "Ошибка оформления", "error");
            }
        });
    }

    wrap.querySelectorAll("[data-modal-wi-dg]").forEach((b) => {
        b.addEventListener("click", () => {
            const target = Number(b.dataset.modalWiDg);
            showConfirmModal({
                title: "Снизить число слотов?",
                message: `Со следующего периода${wiExpStr ? ` (${wiExpStr})` : ""} лимит уменьшится до ${target} устр. (${target * 50} ГБ). До конца текущего срока сохранится ${wiCurLimit} устр.`,
                confirmText: `Снизить до ${target} устр.`,
                cancelText: "Отмена",
                onConfirm: async () => {
                    b.disabled = true;
                    try {
                        await api.scheduleWiDowngrade(target);
                        showToast("Снижение запланировано на следующий период");
                        queueMicrotask(() => {
                            close();
                            onReload();
                        });
                    } catch (err) {
                        throw new Error((err && err.message) || "Ошибка");
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
                showToast(err.message || "Ошибка отмены", "error");
            } finally {
                cancelDgBtn.disabled = false;
            }
        });
    }

    updateView();
}

export function openSlotReplaceModal(slotTitle, targetDev, activeDevices, applyFn, onReplaced, chipClass = "chip-wi") {
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
                        <span class="chip ${chipClass}" style="flex-shrink:0;">Перенести</span>
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
                showToast("Слот перенесён. Обновите список в приложении");
                onReplaced();
            } catch (err) {
                showToast(err.message || "Не удалось перенести слот", "error");
                wrap.querySelectorAll("[data-replace-from]").forEach((b) => { b.disabled = false; });
            }
        });
    });
}
