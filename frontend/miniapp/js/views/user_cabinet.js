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
    data: null,
};

function _handlePaymentResponse(res, successMsg, onReload) {
    if (res && res.confirmation_url) {
        showToast("Переход к оплате...");
        openExternalLink(res.confirmation_url);
        return;
    }
    showToast(successMsg);
    onReload();
}

function _openTopupModal(minTopup, onReload) {
    const form = document.createElement("form");
    form.className = "field";
    form.style.gap = "10px";
    form.innerHTML = `
        <div class="field">
            <label>Сумма пополнения (от ${minTopup} ₽)</label>
            <input name="amount" class="input" type="number" min="${minTopup}" max="50000" step="1" value="250" required>
        </div>
        <div class="filter-pills">
            <button type="button" class="pill-btn" data-amt="100">100 ₽</button>
            <button type="button" class="pill-btn" data-amt="250">250 ₽</button>
            <button type="button" class="pill-btn" data-amt="500">500 ₽</button>
            <button type="button" class="pill-btn" data-amt="1000">1000 ₽</button>
        </div>
        <button type="submit" class="btn btn-primary">Перейти к оплате</button>
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
                <p class="muted">${escapeHtml(err.message || "Попробуйте позже")}</p>
                <button id="retryDashBtn" type="button" class="btn btn-primary btn-sm">Повторить</button>
            </section>
        `;
        const retry = root.querySelector("#retryDashBtn");
        if (retry) retry.addEventListener("click", () => loadAndRenderUserCabinet(root));
    }
}

async function _refreshQuote(root) {
    if (_state.tab !== "access") return;
    try {
        _state.stdQuote = await api.quoteStandard(_state.stdDevices, _state.stdDuration);
        const quoteBox = root.querySelector("#stdQuoteSummary");
        if (quoteBox) {
            quoteBox.innerHTML = _buildQuoteSummaryHtml(_state.stdQuote, _state.stdUseBalance);
        }
    } catch {
        // Non-critical
    }
}

function _buildQuoteSummaryHtml(q, useBalance) {
    if (!q) return `<span class="muted">Расчёт стоимости...</span>`;
    if (q.blocked_reason) {
        return `<span style="color:var(--accent-amber);">${escapeHtml(q.blocked_reason)}</span>`;
    }
    const price = Number(q.due_rub ?? q.price_rub ?? q.amount_rub ?? 0);
    const bonus = useBalance ? Number(q.bonus_used || 0) : 0;
    const real = useBalance ? Number(q.real_used || 0) : 0;
    const card = useBalance ? Number(q.card_to_pay ?? Math.max(0, price - bonus - real)) : price;
    const parts = [`Стоимость: <b>${price} ₽</b>`];
    if (q.discount_rub > 0) {
        parts.push(`Скидка: <b>-${q.discount_rub} ₽</b>`);
    }
    if (useBalance && (bonus > 0 || real > 0)) {
        parts.push(`Спишется с баланса: <b>${bonus + real} ₽</b> (бонусы: ${bonus} ₽, осн.: ${real} ₽)`);
    }
    if (card > 0) {
        parts.push(`К доплате картой/СБП: <b>${card} ₽</b>`);
    } else if (price > 0) {
        parts.push(`К доплате картой/СБП: <b>0 ₽</b> (полностью с баланса)`);
    }
    return parts.join("<br>");
}

function _render(root) {
    const d = _state.data;
    if (!d) return;

    root.innerHTML = `
        <nav class="tabs-bar">
            <button type="button" class="tab-btn ${_state.tab === "access" ? "active" : ""}" data-utab="access">Доступ</button>
            <button type="button" class="tab-btn ${_state.tab === "devices" ? "active" : ""}" data-utab="devices">Устройства (${(d.devices || []).length})</button>
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

function _renderAccessTab(body, d, reload, root) {
    const bal = d.balance || { real: 0, bonus: 0, total: 0 };
    const std = d.standard || {};
    const wi = d.wi || {};
    const pricing = d.pricing || {};
    const minTopup = pricing.min_topup_rub || 20;

    const stdLimit = Number(std.device_limit || 0);
    const isStdDowngrade = std.active && _state.stdDevices < stdLimit;

    body.innerHTML = `
        <section class="card">
            <div class="row">
                <div>
                    <div class="muted">Доступный баланс</div>
                    <div style="font-size:22px; font-weight:700;">${bal.total} ₽</div>
                    <div class="muted" style="font-size:12px;">Основной: ${bal.real} ₽ · Бонусы: ${bal.bonus} ₽</div>
                </div>
                <button id="topupBtn" type="button" class="btn btn-primary btn-sm">Пополнить от ${minTopup} ₽</button>
            </div>
        </section>

        <section class="card">
            <div class="row">
                <div class="card-title">Ссылка подключения</div>
                <span class="chip ${std.active || wi.active ? "chip-ok" : "chip-muted"}">
                    ${std.active || wi.active ? "Единый ключ" : "Не активна"}
                </span>
            </div>
            <div class="sub-url-box">${escapeHtml(d.subscription_url || "")}</div>
            <div class="actions-row">
                <button id="copySubBtn" type="button" class="btn btn-primary btn-sm">Копировать ссылку</button>
                <button id="openSubBtn" type="button" class="btn btn-sm">Открыть</button>
            </div>
        </section>

        <section class="card">
            <div class="row">
                <div class="card-title">Базовый доступ</div>
                <span class="chip ${std.active ? "chip-ok" : "chip-muted"}">
                    ${std.active ? `Активен до ${formatDate(std.expires_at)}` : "Не активен"}
                </span>
            </div>
            ${std.active ? `
                <div class="row muted" style="font-size:12px;">
                    <span>Текущий лимит: <b>${stdLimit}</b> устр.</span>
                    <label class="checkbox-row">
                        <input id="stdAutoRenewChk" type="checkbox" ${std.auto_renew_enabled ? "checked" : ""}>
                        <span>Автопродление с баланса</span>
                    </label>
                </div>
            ` : ""}
            <div class="field">
                <div class="row">
                    <label>Количество устройств</label>
                    <span style="font-weight:600;">${_state.stdDevices} устр.</span>
                </div>
                <input id="stdDevicesRange" class="range-input" type="range" min="2" max="10" step="1" value="${_state.stdDevices}">
            </div>
            <div class="field">
                <label>Срок действия</label>
                <div class="filter-pills">
                    ${(pricing.standard_durations || [7, 30, 90]).map((days) => `
                        <button type="button" class="pill-btn ${_state.stdDuration === days ? "active" : ""}" data-std-days="${days}">
                            ${days} дн.
                        </button>
                    `).join("")}
                </div>
            </div>
            <label class="checkbox-row">
                <input id="stdUseBalChk" type="checkbox" ${_state.stdUseBalance ? "checked" : ""}>
                <span>Списать с баланса (сначала бонусы до 100%)</span>
            </label>
            <div id="stdQuoteSummary" class="meta-list">${_buildQuoteSummaryHtml(_state.stdQuote, _state.stdUseBalance)}</div>
            <div class="actions-row">
                <button id="stdCheckoutBtn" type="button" class="btn btn-primary">
                    ${std.active ? "Продлить / Изменить" : "Подключить"}
                </button>
                ${isStdDowngrade ? `
                    <button id="stdDowngradeBtn" type="button" class="btn btn-sm">
                        Понизить до ${_state.stdDevices} устр.
                    </button>
                ` : ""}
            </div>
            ${std.downgrade_cooldown_until ? `
                <div class="muted" style="font-size:11px;">Снижение лимита доступно после ${formatDate(std.downgrade_cooldown_until)}</div>
            ` : ""}
        </section>

        <section class="card">
            <div class="row">
                <div class="card-title">Высокая устойчивость (доп. опция)</div>
                <span class="chip ${wi.active ? "chip-info" : "chip-muted"}">
                    ${wi.active ? `Активна до ${formatDate(wi.expires_at)}` : "Не подключена"}
                </span>
            </div>
            ${wi.active ? _wiActiveHtml(wi, pricing) : _wiInactiveHtml(pricing)}
        </section>
    `;

    body.querySelector("#topupBtn").addEventListener("click", () => _openTopupModal(minTopup, reload));
    body.querySelector("#copySubBtn").addEventListener("click", () => copyText(d.subscription_url, "Ссылка скопирована"));
    body.querySelector("#openSubBtn").addEventListener("click", () => openExternalLink(d.subscription_url));

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
    range.addEventListener("input", () => {
        _state.stdDevices = Number(range.value);
        _render(root);
        _refreshQuote(root);
    });

    body.querySelectorAll("[data-std-days]").forEach((b) => {
        b.addEventListener("click", () => {
            _state.stdDuration = Number(b.dataset.stdDays);
            _render(root);
            _refreshQuote(root);
        });
    });

    const useBalChk = body.querySelector("#stdUseBalChk");
    useBalChk.addEventListener("change", () => {
        _state.stdUseBalance = useBalChk.checked;
        const quoteBox = body.querySelector("#stdQuoteSummary");
        if (quoteBox) quoteBox.innerHTML = _buildQuoteSummaryHtml(_state.stdQuote, _state.stdUseBalance);
    });

    body.querySelector("#stdCheckoutBtn").addEventListener("click", async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        try {
            const res = await api.checkoutStandard(_state.stdDevices, _state.stdDuration, _state.stdUseBalance);
            _handlePaymentResponse(res, "Базовый доступ оформлен", reload);
        } catch (err) {
            showToast(err.message || "Ошибка оформления");
        } finally {
            btn.disabled = false;
        }
    });

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

    return `
        <div class="field">
            <div class="row muted" style="font-size:12px;">
                <span>Трафик: <b>${usedGb} / ${totalGb} ГБ</b></span>
                <span>Слотов: <b>${curLimit} из ${pricing.wi_max_devices || 3}</b></span>
            </div>
            <div class="progress-track"><div class="progress-fill" style="width:${pct}%;"></div></div>
        </div>
        <label class="checkbox-row">
            <input id="wiAutoRenewChk" type="checkbox" ${wi.auto_renew_enabled ? "checked" : ""}>
            <span>Автопродление с баланса</span>
        </label>
        <label class="checkbox-row">
            <input id="wiUseBalChk" type="checkbox" ${_state.wiUseBalance ? "checked" : ""}>
            <span>Использовать баланс (бонусы в приоритете)</span>
        </label>
        <div class="actions-row">
            <button id="wiRenewBtn" type="button" class="btn btn-primary btn-sm">Продлить (+30 дн.)</button>
            ${curLimit < (pricing.wi_max_devices || 3) && wi.add_slot_price_rub != null ? `
                <button id="wiAddSlotBtn" type="button" class="btn btn-sm">+1 слот (${wi.add_slot_price_rub} ₽)</button>
            ` : ""}
        </div>
        ${wi.pending_device_limit ? `
            <div class="item-row">
                <div class="row">
                    <span class="muted" style="font-size:12px;">Запланировано снижение до ${wi.pending_device_limit} устр. с ${formatDate(wi.pending_from)}</span>
                    <button id="wiCancelDgBtn" type="button" class="btn btn-sm btn-danger">Отменить</button>
                </div>
            </div>
        ` : (curLimit > 1 ? `
            <div class="actions-row">
                ${Array.from({ length: curLimit - 1 }, (_, i) => i + 1).map((target) => `
                    <button type="button" class="btn btn-sm" data-wi-dg="${target}">
                        Снизить до ${target} устр. со след. периода
                    </button>
                `).join("")}
            </div>
        ` : "")}
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
    `;
}

function _wiInactiveHtml(pricing) {
    const base = Number(pricing.wi_base_price_rub || 250);
    const extra = Number(pricing.wi_extra_device_price_rub || 200);
    const total = base + Math.max(0, _state.wiDevices - 1) * extra;
    return `
        <p class="muted">Независимая опция высокой устойчивости (50 ГБ за каждый слот, 30 дней).</p>
        <div class="field">
            <label>Выберите количество устройств</label>
            <div class="filter-pills">
                ${[1, 2, 3].map((n) => `
                    <button type="button" class="pill-btn ${_state.wiDevices === n ? "active" : ""}" data-wi-dev="${n}">
                        ${n} устр. (${base + (n - 1) * extra} ₽)
                    </button>
                `).join("")}
            </div>
        </div>
        <label class="checkbox-row">
            <input id="wiUseBalChk" type="checkbox" ${_state.wiUseBalance ? "checked" : ""}>
            <span>Списать с баланса (бонусы в приоритете)</span>
        </label>
        <button id="wiBuyBtn" type="button" class="btn btn-primary">Подключить за ${total} ₽</button>
    `;
}

function _bindWiEvents(body, d, reload, root) {
    const wiUseBal = body.querySelector("#wiUseBalChk");
    if (wiUseBal) {
        wiUseBal.addEventListener("change", () => {
            _state.wiUseBalance = wiUseBal.checked;
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
                showToast(err.message || "Ошибка");
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
                    ? "Слоты высокой устойчивости выдаются по приоритету (или в порядке первого подключения)."
                    : "Устройства привязываются автоматически при обновлении ссылки в приложении."}
            </p>
            <div id="devicesList" style="display:flex; flex-direction:column; gap:8px;"></div>
        </section>
    `;

    const list = body.querySelector("#devicesList");
    if (!devices.length) {
        list.innerHTML = `<div class="muted" style="text-align:center; padding:14px;">Нет привязанных устройств</div>`;
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
                <span>Приоритет: ${dev.bi_priority || 0}</span>
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
                showToast(nextPrio > 0 ? "Приоритет повышен" : "Приоритет сброшен (FIFO)");
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
                Друг получает приветственную скидку <b>${ref.welcome_discount_pct ?? 25}%</b> на первый заказ.
                Реферал засчитывается активным после пополнения или заказа от <b>67 ₽</b>.
                Бонусами можно оплатить до 100% любого заказа.
            </p>
            ${ref.needed_for_next ? `
                <div class="muted" style="font-size:12px;">
                    До уровня <b>${escapeHtml(ref.next_tier_name)}</b> (${ref.next_rate_pct}%): ещё ${ref.needed_for_next} активных.
                </div>
            ` : ""}
            <div class="sub-url-box">${escapeHtml(ref.link || "")}</div>
            <button id="copyRefBtn" type="button" class="btn btn-primary btn-sm">Копировать ссылку</button>
        </section>
    `;

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
        list.innerHTML = `<div class="muted" style="text-align:center; padding:14px;">Операций пока нет</div>`;
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
