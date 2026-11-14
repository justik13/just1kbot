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
    tab: null,
    platform: _detectPlatform(),
    data: null,
};

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

function _calcStdPrice(pricing, devices, days) {
    const plans = (pricing && pricing.standard_plans) || {};
    const entry = plans[String(days)];
    if (entry && entry.base_rub != null && entry.step_rub != null) {
        return Number(entry.base_rub) + Math.max(0, devices - 2) * Number(entry.step_rub);
    }
    const fallback = { 7: [39, 12], 30: [99, 30], 90: [255, 78] }[days] || [99, 30];
    return fallback[0] + Math.max(0, devices - 2) * fallback[1];
}

function _calcBalanceSplit(priceRub, bal, useBalance) {
    const price = Math.max(0, Number(priceRub || 0));
    if (!useBalance || price === 0) {
        return { balUsed: 0, cardDue: price };
    }
    const avail = Math.max(0, Number((bal && bal.total) || 0));
    const balUsed = Math.min(price, avail);
    return { balUsed, cardDue: Math.max(0, price - balUsed) };
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
        const discount = quote ? Number(quote.discount_rub || 0) : 0;
        const split = _calcBalanceSplit(price, bal, useBalance);

        const lines = [`<div class="quote-line"><span>Тариф (${devices} устр. · ${duration} дн.)</span><strong>${price} ₽</strong></div>`];
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
        payBtn.addEventListener("click", async () => {
            payBtn.disabled = true;
            try {
                const res = await api.checkoutStandard(devices, duration, useBalance);
                close();
                _handlePaymentResponse(res, "Подписка «Стандарт» оформлена", onReload);
            } catch (err) {
                showToast(err.message || "Ошибка оплаты");
            } finally {
                payBtn.disabled = false;
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
        const split = _calcBalanceSplit(price, bal, useBalance);
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
        wrap.querySelector("#devApplyBtn").addEventListener("click", async () => {
            const btn = wrap.querySelector("#devApplyBtn");
            btn.disabled = true;
            try {
                if (isUpgrade) {
                    const res = await api.checkoutStandard(targetLimit, 30, useBalance);
                    close();
                    _handlePaymentResponse(res, `Лимит увеличен до ${targetLimit} устр.`, onReload);
                } else {
                    await api.downgradeStandard(targetLimit);
                    close();
                    showToast(`Лимит снижен до ${targetLimit} устр.`);
                    onReload();
                }
            } catch (err) {
                showToast(err.message || "Не удалось изменить лимит");
            } finally {
                btn.disabled = false;
            }
        });
    };

    renderContent();
    fetchQuote();
}

function _openWiPurchaseModal(d, onReload) {
    const bal = d.balance || { real: 0, bonus: 0, total: 0 };
    const pricing = d.pricing || {};
    const base = Number(pricing.wi_base_price_rub || 250);
    const extra = Number(pricing.wi_extra_device_price_rub || 200);
    let devices = 1;
    let useBalance = true;

    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.style.gap = "12px";
    const close = openModal("Подключение · Белый интернет", wrap);

    const renderModal = () => {
        const total = base + Math.max(0, devices - 1) * extra;
        const split = _calcBalanceSplit(total, bal, useBalance);
        const ctaText = split.cardDue === 0
            ? `Оплатить с баланса (${total} ₽)`
            : (split.balUsed > 0 ? `Подключить · доплата ${split.cardDue} ₽` : `Подключить за ${total} ₽`);

        wrap.innerHTML = `
            <div class="muted" style="font-size:12px;">
                Независимая подписка на 30 дней для мобильной сети при жёстких ограничениях. Работает как отдельно, так и в дополнение к «Стандарту».
            </div>
            <div class="field">
                <label>Количество устройств (по 50 ГБ на слот)</label>
                <div class="price-tile-grid">
                    ${[1, 2, 3].map((n) => {
                        const p = base + (n - 1) * extra;
                        return `
                            <button type="button" class="price-tile ${devices === n ? "active" : ""}" data-wi-cnt="${n}">
                                <span class="price-tile-top">${n} устр. · ${n * 50} ГБ</span>
                                <span class="price-tile-price">${p} ₽</span>
                            </button>
                        `;
                    }).join("")}
                </div>
            </div>
            <label class="toggle-row">
                <span class="toggle-label">Списать с баланса (доступно ${bal.total} ₽)</span>
                <span class="switch">
                    <input id="modalWiBalChk" type="checkbox" ${useBalance ? "checked" : ""}>
                    <span class="switch-track"></span>
                </span>
            </label>
            <div class="quote-box">
                <div class="quote-line"><span>Подписка (${devices} устр. · 30 дн.)</span><strong>${total} ₽</strong></div>
                ${split.balUsed > 0 ? `<div class="quote-line"><span>С баланса</span><strong>-${split.balUsed} ₽</strong></div>` : ""}
                <div class="quote-line"><span>К оплате картой / СБП</span><strong>${split.cardDue} ₽</strong></div>
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
        payBtn.addEventListener("click", async () => {
            payBtn.disabled = true;
            try {
                const res = await api.checkoutWi({
                    action: "buy",
                    device_limit: devices,
                    use_balance: useBalance,
                });
                close();
                _handlePaymentResponse(res, "Подписка «Белый интернет» подключена", onReload);
            } catch (err) {
                showToast(err.message || "Ошибка подключения");
            } finally {
                payBtn.disabled = false;
            }
        });
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
            _state.tab = (stdActive || wiActive) ? "connect" : "tariffs";
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
    const stdActive = Boolean(d.standard && d.standard.active);
    const wiActive = Boolean(d.wi && d.wi.active);
    const hasAny = stdActive || wiActive;
    const devCount = (d.devices || []).length;

    const tabs = hasAny
        ? [
            { id: "connect", label: "Подключение" },
            { id: "tariffs", label: "Тарифы" },
            { id: "devices", label: `Устройства (${devCount})` },
            { id: "referrals", label: "Бонусы" },
            { id: "history", label: "История" },
        ]
        : [
            { id: "tariffs", label: "Тарифы" },
            { id: "connect", label: "Подключение" },
            { id: "devices", label: `Устройства (${devCount})` },
            { id: "referrals", label: "Бонусы" },
            { id: "history", label: "История" },
        ];

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
            _state.tab = b.dataset.utab;
            _render(root);
        });
    });

    const body = root.querySelector("#userTabBody");
    const reload = () => loadAndRenderUserCabinet(root);

    if (_state.tab === "connect") _renderConnectTab(body, d, reload, root);
    else if (_state.tab === "tariffs") _renderTariffsTab(body, d, reload);
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
    const std = d.standard || {};
    const wi = d.wi || {};
    const bal = d.balance || { real: 0, bonus: 0, total: 0 };
    const minTopup = (d.pricing && d.pricing.min_topup_rub) || 20;
    const isAnyActive = Boolean(std.active || wi.active);
    const subUrl = d.subscription_url || "";
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
                <p class="muted">Оформите «Стандарт» или «Белый интернет» — ключ подключения активируется сразу после оплаты.</p>
                <button id="goTariffsBtn" type="button" class="btn btn-primary btn-lg">Перейти к тарифам</button>
                ${_buildGuideHtml(subUrl, false)}
            </section>
        `;
        body.querySelector("#goTariffsBtn").addEventListener("click", () => {
            _state.tab = "tariffs";
            _render(root);
        });
        _bindGuideEvents(body, root);
        return;
    }

    const pills = [];
    if (std.active) {
        pills.push(`<span class="chip chip-ok">Стандарт · ${Number(std.device_limit || 2)} устр. · до ${formatDate(std.expires_at)}</span>`);
    }
    if (wi.active) {
        const usedGb = formatBytesGb(wi.traffic_used_bytes);
        const totalGb = formatBytesGb(wi.traffic_limit_bytes);
        pills.push(`<span class="chip chip-info">Белый интернет · ${Number(wi.device_limit || 1)} устр. · ${usedGb}/${totalGb} ГБ · до ${formatDate(wi.expires_at)}</span>`);
    }

    const incyHref = `incy://add/${subUrl}`;
    const happHref = `happ://add/${subUrl}`;

    body.innerHTML = `
        <section class="balance-card">
            <div>
                <div class="muted" style="font-size:12px;">Баланс аккаунта</div>
                <div class="balance-value">${bal.total} ₽</div>
                <div class="balance-sub">Основной: ${bal.real} ₽ · Бонусы: ${bal.bonus} ₽</div>
            </div>
            <button id="topupBtn" type="button" class="btn btn-primary btn-sm">${_ICONS.plus} Пополнить</button>
        </section>

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

        <section class="card">
            <div class="row">
                <div>
                    <div style="font-weight:600;">Продление и опции</div>
                    <div class="muted" style="font-size:12px;">Изменить число устройств, докупить ГБ или вторую подписку</div>
                </div>
                <button id="openTariffsFromConnect" type="button" class="btn btn-sm">Тарифы</button>
            </div>
        </section>
    `;

    body.querySelector("#topupBtn").addEventListener("click", () => _openTopupModal(minTopup, reload));
    body.querySelector("#copySubBtn").addEventListener("click", () => copyText(subUrl, "Ключ скопирован"));
    body.querySelector("#openTariffsFromConnect").addEventListener("click", () => {
        _state.tab = "tariffs";
        _render(root);
    });
    _bindGuideEvents(body, root);
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

    const wiBase = Number(pricing.wi_base_price_rub || 250);
    const wiUsedGb = formatBytesGb(wi.traffic_used_bytes);
    const wiTotalGb = formatBytesGb(wi.traffic_limit_bytes);
    const wiPct = wi.traffic_limit_bytes > 0
        ? Math.min(100, Math.round((wi.traffic_used_bytes / wi.traffic_limit_bytes) * 100))
        : 0;
    const wiCurLimit = Number(wi.device_limit || 1);
    const wiMaxDevices = pricing.wi_max_devices || 3;
    const wiPacks = pricing.wi_topup_packs || [];

    body.innerHTML = `
        <section class="balance-card">
            <div>
                <div class="muted" style="font-size:12px;">Баланс аккаунта</div>
                <div class="balance-value">${bal.total} ₽</div>
                <div class="balance-sub">Основной: ${bal.real} ₽ · Бонусы: ${bal.bonus} ₽</div>
            </div>
            <button id="topupBtn" type="button" class="btn btn-primary btn-sm">${_ICONS.plus} Пополнить</button>
        </section>

        <div class="muted" style="font-size:12px; padding:0 2px;">
            Две независимые подписки на один ключ — выберите нужную или подключите обе.
        </div>

        <section class="plan-card ${std.active ? "plan-active" : ""}">
            <div class="row">
                <div>
                    <div class="card-title">Стандарт</div>
                    <div class="muted" style="font-size:12px;">Для дома, Wi-Fi и повседневного использования</div>
                </div>
                <span class="chip ${std.active ? "chip-ok" : "chip-muted"}">
                    ${std.active ? `до ${formatDate(std.expires_at)}` : "Не подключена"}
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
                    <button id="stdDevicesBtn" type="button" class="btn">Устройства (${stdLimit})</button>
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
                <span class="chip ${wi.active ? "chip-info" : "chip-muted"}">
                    ${wi.active ? `до ${formatDate(wi.expires_at)}` : "Не подключена"}
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
                        <span class="tabular">Слотов: <strong style="color:var(--text-primary);">${wiCurLimit} из ${wiMaxDevices}</strong></span>
                    </div>
                    <div class="progress-track"><div class="progress-fill" style="width:${wiPct}%;"></div></div>
                </div>
                <label class="toggle-row">
                    <span class="toggle-label">Автопродление с баланса</span>
                    <span class="switch">
                        <input id="wiAutoRenewChk" type="checkbox" ${wi.auto_renew_enabled ? "checked" : ""}>
                        <span class="switch-track"></span>
                    </span>
                </label>
                <div class="actions-row">
                    <button id="wiRenewBtn" type="button" class="btn btn-primary" style="flex:1;">Продлить +30 дн.</button>
                    ${wiCurLimit < wiMaxDevices && wi.add_slot_price_rub != null ? `
                        <button id="wiAddSlotBtn" type="button" class="btn">+1 слот · ${wi.add_slot_price_rub} ₽</button>
                    ` : ""}
                </div>
                ${wiPacks.length ? `
                    <div class="field">
                        <label>Докупить трафик</label>
                        <div class="filter-pills">
                            ${wiPacks.map((p) => `
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
                ` : (wiCurLimit > 1 ? `
                    <details class="guide-details">
                        <summary class="guide-summary">
                            <span>Снизить число слотов со следующего периода</span>
                            <span class="muted">▾</span>
                        </summary>
                        <div class="guide-body">
                            <div class="actions-row">
                                ${Array.from({ length: wiCurLimit - 1 }, (_, i) => i + 1).map((target) => `
                                    <button type="button" class="btn btn-sm" data-wi-dg="${target}">
                                        Снизить до ${target} устр.
                                    </button>
                                `).join("")}
                            </div>
                        </div>
                    </details>
                ` : "")}
            ` : `
                <div class="plan-meta-row">
                    <span class="plan-price-tag">от ${wiBase} ₽ / 30 дн.</span>
                    <button id="wiBuyBtn" type="button" class="btn btn-primary">Выбрать</button>
                </div>
            `}
        </section>
    `;

    body.querySelector("#topupBtn").addEventListener("click", () => _openTopupModal(minTopup, reload));

    const stdBuyBtn = body.querySelector("#stdBuyBtn");
    if (stdBuyBtn) stdBuyBtn.addEventListener("click", () => _openStandardPlanModal(d, "buy", reload));

    const stdRenewBtn = body.querySelector("#stdRenewBtn");
    if (stdRenewBtn) stdRenewBtn.addEventListener("click", () => _openStandardPlanModal(d, "renew", reload));

    const stdDevBtn = body.querySelector("#stdDevicesBtn");
    if (stdDevBtn) stdDevBtn.addEventListener("click", () => _openStandardDevicesModal(d, reload));

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
    if (wiBuyBtn) wiBuyBtn.addEventListener("click", () => _openWiPurchaseModal(d, reload));

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
        wiRenewBtn.addEventListener("click", async () => {
            wiRenewBtn.disabled = true;
            try {
                const res = await api.checkoutWi({
                    action: "renew",
                    device_limit: wiCurLimit,
                    use_balance: true,
                });
                _handlePaymentResponse(res, "Подписка продлена на 30 дней", reload);
            } catch (err) {
                showToast(err.message || "Ошибка продления");
            } finally {
                wiRenewBtn.disabled = false;
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
                    use_balance: true,
                });
                _handlePaymentResponse(res, "Слот добавлен", reload);
            } catch (err) {
                showToast(err.message || "Ошибка добавления слота");
            } finally {
                addSlotBtn.disabled = false;
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
                    use_balance: true,
                });
                _handlePaymentResponse(res, "Пакет трафика добавлен", reload);
            } catch (err) {
                showToast(err.message || "Ошибка покупки пакета");
            } finally {
                b.disabled = false;
            }
        });
    });

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
                    ? "Слоты «Белого интернета» выдаются по приоритету (или в порядке первого подключения)."
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
                    Откройте вкладку «Подключение» и нажмите «Подключить в INCY» или «Открыть в Happ» — устройство появится здесь автоматически.
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
                ${dev.is_bi_assigned ? `<span class="chip chip-info">Слот БИ</span>` : ""}
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
