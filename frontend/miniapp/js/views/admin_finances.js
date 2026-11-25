import { api } from "../api.js?v=2.8.0";
import { formatAuditAction, formatAuditTarget, formatMetadataFlag } from "../admin_labels.js?v=2.8.0";
import {
    copyText,
    escapeHtml,
    formatDate,
    formatRelativeTime,
    renderPagination,
    showConfirmModal,
    showToast,
    withButtonLoading,
} from "../ui.js?v=2.8.0";

let _finViewReqId = 0;
let _maintInFlight = null;

function _financeStatusChip(status) {
    if (status === "paid") {
        return `<span class="chip chip-ok">Оплачен</span>`;
    }
    if (status === "pending") {
        return `<span class="chip chip-warn">Ожидает</span>`;
    }
    if (status === "refunded") {
        return `<span class="chip chip-warn">Возврат</span>`;
    }
    if (status === "partially_refunded") {
        return `<span class="chip chip-warn">Част. возврат</span>`;
    }
    if (status === "canceled") {
        return `<span class="chip chip-muted">Отменён</span>`;
    }
    return `<span class="chip chip-muted">${escapeHtml(status)}</span>`;
}

function _webhookStatusChip(status) {
    if (status === "pending") return `<span class="chip chip-warn">Ожидает</span>`;
    if (status === "processing") return `<span class="chip chip-warn">Обработка</span>`;
    if (status === "retry") return `<span class="chip chip-warn">Повтор</span>`;
    if (status === "succeeded") return `<span class="chip chip-ok">Обработан</span>`;
    if (status === "dead") return `<span class="chip chip-err">Сбой доставки</span>`;
    return `<span class="chip chip-muted">${escapeHtml(status || "—")}</span>`;
}

function _formatServiceType(serviceType) {
    if (serviceType === "standard") return "Стандарт";
    if (serviceType === "white_internet") return "Белый интернет";
    if (serviceType === "topup") return "Пополнение";
    return serviceType || "";
}

function _formatPaymentMethod(method) {
    if (method === "yookassa") return "ЮKassa";
    if (method === "wallet") return "Баланс";
    return method || "";
}

function _cleanQuery(val) {
    return String(val || "").trim().replace(/^@+/, "");
}

function _renderMaintenanceCardInner(state) {
    const isLoading = typeof state.paymentsMaintenance === "undefined" && !state.paymentsMaintenanceError;
    const isError = Boolean(state.paymentsMaintenanceError);
    const enabled = Boolean(state.paymentsMaintenance);

    let badgeCls = enabled ? "chip-warn" : "chip-ok";
    let badgeText = enabled ? "Включены (оплата заблокирована)" : "Выключены (оплата активна)";
    let btnCls = enabled ? "btn-secondary" : "btn-danger";
    let btnText = enabled ? "Включить приём платежей" : "Приостановить платежи";
    let btnDisabled = "";

    if (isLoading) {
        badgeCls = "chip-muted";
        badgeText = "Загрузка…";
        btnCls = "btn-secondary";
        btnText = "Загрузка…";
        btnDisabled = "disabled";
    } else if (isError) {
        badgeCls = "chip-err";
        badgeText = "Ошибка загрузки";
        btnCls = "btn-secondary";
        btnText = "Повторить";
    }

    return `
        <div class="row" style="align-items:center; justify-content:space-between; gap:12px;">
            <div>
                <h2 class="card-title" style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                    <span>Техработы платежей</span>
                    <span id="paymentsMaintBadge" class="chip ${badgeCls}">${escapeHtml(badgeText)}</span>
                </h2>
                <div class="meta-text" style="margin-top:4px;">
                    Блокирует создание новых заказов и пополнение баланса. Действующие подписки продолжают работать.
                </div>
            </div>
            <button type="button" id="togglePaymentsMaintBtn" class="btn ${btnCls}" style="white-space:nowrap;" ${btnDisabled}>
                ${escapeHtml(btnText)}
            </button>
        </div>
    `;
}

function _renderMaintenanceCard(state) {
    return `<section id="paymentsMaintCard" class="card">${_renderMaintenanceCardInner(state)}</section>`;
}

function _renderFinStatsInner(summary) {
    return `
        <div class="stat-box"><div class="stat-val tabular">${Math.round(summary.revenue_24h ?? 0)} ₽</div><div class="stat-lbl">Внеш. оплаты 24ч</div></div>
        <div class="stat-box"><div class="stat-val tabular">${Math.round(summary.revenue_7d ?? 0)} ₽</div><div class="stat-lbl">Внеш. оплаты 7д</div></div>
        <div class="stat-box"><div class="stat-val tabular">${Math.round(summary.revenue_30d ?? 0)} ₽</div><div class="stat-lbl">Внеш. оплаты 30д</div></div>
        <div class="stat-box"><div class="stat-val tabular">${summary.paid_orders_count ?? 0}</div><div class="stat-lbl">Всего оплачено</div></div>
    `;
}

export function renderAdminFinancesView(root, state) {
    ++_finViewReqId;
    const subTab = state.finSubTab || "orders";
    const finData = state.financesData || {
        summary: { revenue_24h: 0, revenue_7d: 0, revenue_30d: 0, paid_orders_count: 0 },
        items: [],
        total: 0,
        offset: 0,
        limit: 25,
    };
    const auditData = state.auditLogsData || { items: [], actions: [], total: 0, offset: 0, limit: 40 };
    const webhooksData = state.webhooksData || [];
    const summary = finData.summary || {};

    const hasReturn = Array.isArray(state.returnStack) && state.returnStack.length > 0;

    root.innerHTML = `
        ${
            hasReturn
                ? `<div class="return-banner">
                    <button type="button" id="finReturnBtn" class="btn btn-sm">← Вернуться к пользователю</button>
                </div>`
                : ""
        }
        <section id="finStatsStrip" class="stats-strip">${_renderFinStatsInner(summary)}</section>

        ${_renderMaintenanceCard(state)}

        <section class="card">
            <div class="row" style="gap:8px; align-items:center; justify-content:space-between;">
                <h2 class="card-title">Операции и логи</h2>
                <button type="button" id="finRefreshBtn" class="btn btn-sm">Обновить</button>
            </div>
            <div class="seg-group" style="overflow-x:auto; flex-wrap:nowrap; scrollbar-width:none; -webkit-overflow-scrolling:touch; gap:6px;">
                <button type="button" class="seg-btn ${subTab === "orders" ? "active" : ""}" style="flex:1 0 auto; min-width:max-content; white-space:nowrap; padding:8px 10px;" data-fintab="orders" aria-pressed="${subTab === "orders" ? "true" : "false"}">Все</button>
                <button type="button" class="seg-btn ${subTab === "subscriptions" ? "active" : ""}" style="flex:1 0 auto; min-width:max-content; white-space:nowrap; padding:8px 10px;" data-fintab="subscriptions" aria-pressed="${subTab === "subscriptions" ? "true" : "false"}">Подписки</button>
                <button type="button" class="seg-btn ${subTab === "topups" ? "active" : ""}" style="flex:1 0 auto; min-width:max-content; white-space:nowrap; padding:8px 10px;" data-fintab="topups" aria-pressed="${subTab === "topups" ? "true" : "false"}">Пополнения</button>
                <button type="button" class="seg-btn ${subTab === "audit" ? "active" : ""}" style="flex:1 0 auto; min-width:max-content; white-space:nowrap; padding:8px 10px;" data-fintab="audit" aria-pressed="${subTab === "audit" ? "true" : "false"}">Аудит</button>
                <button type="button" class="seg-btn ${subTab === "webhooks" ? "active" : ""}" style="flex:1 0 auto; min-width:max-content; white-space:nowrap; padding:8px 10px;" data-fintab="webhooks" aria-pressed="${subTab === "webhooks" ? "true" : "false"}">Вебхуки</button>
            </div>

            ${
                subTab === "audit"
                    ? `
                <form id="auditFilterForm" class="field" style="gap:8px;">
                    <div class="grid-2">
                        <label for="auditActionSelect" class="visually-hidden">Действие</label>
                        <select id="auditActionSelect" class="select">
                            <option value="">Все действия</option>
                            ${(auditData.actions || [])
                                .map(
                                    (act) =>
                                        `<option value="${escapeHtml(act)}" ${
                                            state.auditAction === act ? "selected" : ""
                                        }>${escapeHtml(formatAuditAction(act))}</option>`
                                )
                                .join("")}
                        </select>
                        <label for="auditTargetTypeSelect" class="visually-hidden">Тип цели</label>
                        <select id="auditTargetTypeSelect" class="select">
                            <option value="" ${!state.auditTargetType ? "selected" : ""}>Все цели</option>
                            <option value="user" ${state.auditTargetType === "user" ? "selected" : ""}>Пользователь</option>
                            <option value="server" ${state.auditTargetType === "server" ? "selected" : ""}>Нода</option>
                            <option value="broadcast" ${state.auditTargetType === "broadcast" ? "selected" : ""}>Рассылка</option>
                            <option value="system_setting" ${state.auditTargetType === "system_setting" ? "selected" : ""}>Настройки</option>
                            <option value="system" ${state.auditTargetType === "system" ? "selected" : ""}>Система</option>
                            <option value="admin" ${state.auditTargetType === "admin" ? "selected" : ""}>Админ</option>
                            <option value="mass_bonus" ${state.auditTargetType === "mass_bonus" ? "selected" : ""}>Массовый бонус</option>
                            <option value="mass_extend" ${state.auditTargetType === "mass_extend" ? "selected" : ""}>Массовое продление</option>
                        </select>
                    </div>
                    <div class="row-nowrap">
                        <label for="auditQueryInput" class="visually-hidden">Поиск в аудите</label>
                        <input id="auditQueryInput" class="input" type="search" value="${escapeHtml(
                            state.auditQuery || ""
                        )}" placeholder="ID или детали">
                        <button type="submit" class="btn btn-primary btn-sm">Найти</button>
                    </div>
                </form>
                <div id="finPagerTop"></div>
                <div id="finListContainer" class="stack-sm"></div>
                <div id="finPagerBottom"></div>
            `
                    : subTab === "webhooks"
                    ? `
                <div class="meta-text">
                    Последние 30 входящих вебхуков ЮKassa со статусами <span class="mono" style="word-break:normal; white-space:nowrap;">pending / retry / dead</span>.
                </div>
                <div id="finListContainer" class="stack-sm"></div>
            `
                    : `
                <form id="finFilterForm" class="field" style="gap:8px;">
                    <div class="row">
                        <label for="finQueryInput" class="visually-hidden">Поиск заказа или пользователя</label>
                        <input id="finQueryInput" class="input" type="search" value="${escapeHtml(
                            state.finQuery || ""
                        )}" placeholder="Telegram ID, username, ID заказа">
                        <label for="finStatusSelect" class="visually-hidden">Статус заказа</label>
                        <select id="finStatusSelect" class="select" style="max-width:140px;">
                            <option value="">Все статусы</option>
                            <option value="paid" ${state.finStatus === "paid" ? "selected" : ""}>Оплачен</option>
                            <option value="pending" ${state.finStatus === "pending" ? "selected" : ""}>Ожидает</option>
                            <option value="canceled" ${state.finStatus === "canceled" ? "selected" : ""}>Отменён</option>
                            <option value="refunded" ${state.finStatus === "refunded" ? "selected" : ""}>Возврат</option>
                            <option value="partially_refunded" ${state.finStatus === "partially_refunded" ? "selected" : ""}>Част. возврат</option>
                        </select>
                        <button type="submit" class="btn btn-primary btn-sm">Найти</button>
                    </div>
                    <div class="row row-start">
                        <button type="button" id="finAttentionToggleBtn" class="btn btn-sm ${
                            state.finAttentionOnly ? "btn-primary" : ""
                        }" aria-pressed="${state.finAttentionOnly ? "true" : "false"}">
                            Требуют внимания
                        </button>
                    </div>
                </form>
                <div id="finPagerTop"></div>
                <div id="finListContainer" class="stack-sm"></div>
                <div id="finPagerBottom"></div>
            `
            }
        </section>
    `;

    const isStillActive = (reqId) =>
        reqId === _finViewReqId &&
        root.isConnected &&
        state.adminTab === "finances" &&
        state.mode === "admin";

    const listContainer = root.querySelector("#finListContainer");

    const renderRetryList = (message, onRetry) => {
        const pagerTop = root.querySelector("#finPagerTop");
        const pagerBottom = root.querySelector("#finPagerBottom");
        if (pagerTop) pagerTop.innerHTML = "";
        if (pagerBottom) pagerBottom.innerHTML = "";
        if (!listContainer) return;
        listContainer.innerHTML = `
            <div class="card card-center">
                <div class="muted">${escapeHtml(message)}</div>
                <button id="finListRetryBtn" type="button" class="btn btn-primary btn-sm">Повторить</button>
            </div>
        `;
        listContainer.querySelector("#finListRetryBtn")?.addEventListener("click", onRetry);
    };

    const syncDynamicFinances = () => {
        const curFin = state.financesData || { summary: {}, items: [], total: 0, offset: 0, limit: 25 };
        const statsStrip = root.querySelector("#finStatsStrip");
        if (statsStrip) {
            statsStrip.innerHTML = _renderFinStatsInner(curFin.summary || {});
        }
        const attBtn = root.querySelector("#finAttentionToggleBtn");
        if (attBtn) {
            attBtn.classList.toggle("btn-primary", Boolean(state.finAttentionOnly));
            attBtn.setAttribute("aria-pressed", state.finAttentionOnly ? "true" : "false");
        }
        listContainer.innerHTML = "";
        _renderFinanceRows(listContainer, curFin.items || [], state);
        _renderPager(root, curFin, loadFinances, "заказов");
    };

    const syncDynamicAudit = () => {
        const curAudit = state.auditLogsData || { items: [], actions: [], total: 0, offset: 0, limit: 40 };
        const actionSel = root.querySelector("#auditActionSelect");
        if (actionSel && Array.isArray(curAudit.actions)) {
            const curVal = state.auditAction || "";
            actionSel.innerHTML = `
                <option value="">Все действия</option>
                ${curAudit.actions
                    .map(
                        (act) =>
                            `<option value="${escapeHtml(act)}" ${
                                curVal === act ? "selected" : ""
                            }>${escapeHtml(formatAuditAction(act))}</option>`
                    )
                    .join("")}
            `;
        }
        listContainer.innerHTML = "";
        _renderAuditRows(listContainer, curAudit.items || [], state);
        _renderPager(root, curAudit, loadAudit, "событий");
    };

    const syncDynamicWebhooks = () => {
        listContainer.innerHTML = "";
        _renderWebhookRows(listContainer, state.webhooksData || [], loadWebhooks);
    };

    const loadFinances = async (offset = 0) => {
        const reqId = ++_finViewReqId;
        try {
            const kind = ["orders", "subscriptions", "topups"].includes(state.finSubTab)
                ? state.finSubTab
                : "orders";
            const data = await api.listFinances(
                kind,
                state.finStatus || "",
                state.finQuery || "",
                offset,
                25,
                Boolean(state.finAttentionOnly)
            );
            if (!isStillActive(reqId)) return;
            state.financesData = data;
            state.financesLoadAttempted = true;
            syncDynamicFinances();
        } catch (err) {
            if (!isStillActive(reqId)) return;
            if (err && err.code === "2fa_required") return;
            state.financesLoadAttempted = true;
            showToast(err.message || "Ошибка загрузки финансов", "error");
            renderRetryList(err.message || "Не удалось загрузить финансы", () => loadFinances(offset));
        }
    };

    const loadAudit = async (offset = 0) => {
        const reqId = ++_finViewReqId;
        try {
            const data = await api.listAuditLogs(
                state.auditAction || "",
                state.auditQuery || "",
                offset,
                40,
                state.auditTargetType || ""
            );
            if (!isStillActive(reqId)) return;
            state.auditLogsData = data;
            state.auditLoadAttempted = true;
            syncDynamicAudit();
        } catch (err) {
            if (!isStillActive(reqId)) return;
            if (err && err.code === "2fa_required") return;
            state.auditLoadAttempted = true;
            showToast(err.message || "Ошибка загрузки аудит-лога", "error");
            renderRetryList(err.message || "Не удалось загрузить аудит-лог", () => loadAudit(offset));
        }
    };

    const loadWebhooks = async () => {
        const reqId = ++_finViewReqId;
        try {
            const res = await api.getAdminWebhooks();
            if (!isStillActive(reqId)) return;
            state.webhooksData = res.webhooks || [];
            state.webhooksLoadAttempted = true;
            syncDynamicWebhooks();
        } catch (err) {
            if (!isStillActive(reqId)) return;
            if (err && err.code === "2fa_required") return;
            state.webhooksLoadAttempted = true;
            showToast(err.message || "Ошибка загрузки вебхуков", "error");
            renderRetryList(err.message || "Не удалось загрузить вебхуки", () => loadWebhooks());
        }
    };

    const syncMaintenanceCard = () => {
        if (!root.isConnected) return;
        const maintCard = root.querySelector("#paymentsMaintCard");
        if (!maintCard) return;
        maintCard.innerHTML = _renderMaintenanceCardInner(state);
        bindMaintenanceToggle(maintCard);
    };

    const loadPaymentsMaintenance = () => {
        if (_maintInFlight) return _maintInFlight;
        state.paymentsMaintenanceError = false;
        _maintInFlight = api
            .getPaymentsMaintenance()
            .then((res) => {
                state.paymentsMaintenance = Boolean(res.enabled);
                state.paymentsMaintenanceError = false;
            })
            .catch((err) => {
                if (err && err.code === "2fa_required") return;
                state.paymentsMaintenanceError = true;
                showToast(err.message || "Ошибка загрузки статуса техработ", "error");
            })
            .finally(() => {
                _maintInFlight = null;
                if (root.isConnected && state.adminTab === "finances" && state.mode === "admin") {
                    syncMaintenanceCard();
                }
            });
        return _maintInFlight;
    };

    function bindMaintenanceToggle(scopeEl = root) {
        const toggleMaintBtn = scopeEl.querySelector("#togglePaymentsMaintBtn");
        if (!toggleMaintBtn) return;
        toggleMaintBtn.addEventListener("click", () => {
            if (state.paymentsMaintenanceError) {
                state.paymentsMaintenance = undefined;
                state.paymentsMaintenanceError = false;
                syncMaintenanceCard();
                loadPaymentsMaintenance();
                return;
            }
            const nextVal = !state.paymentsMaintenance;
            showConfirmModal({
                title: nextVal ? "Приостановить приём платежей?" : "Возобновить приём платежей?",
                message: nextVal
                    ? "Создание новых заказов и пополнение баланса будет временно заблокировано для всех пользователей. Действующие подписки продолжают работать."
                    : "Пользователи снова смогут оформлять подписки и пополнять баланс.",
                confirmText: nextVal ? "Приостановить" : "Возобновить",
                cancelText: "Отмена",
                isDestructive: nextVal,
                restoreFocusSelector: "#togglePaymentsMaintBtn",
                onConfirm: async () => {
                    const res = await api.setPaymentsMaintenance(nextVal);
                    state.paymentsMaintenance = Boolean(res.enabled);
                    state.paymentsMaintenanceError = false;
                    showToast(res.enabled ? "Техработы платежей включены" : "Приём платежей возобновлён");
                    syncMaintenanceCard();
                },
            });
        });
    }

    root.querySelector("#finReturnBtn")?.addEventListener("click", () => {
        state.onReturnBack?.();
    });

    root.querySelectorAll("[data-fintab]").forEach((btn) => {
        btn.addEventListener("click", () => {
            const nextSubTab = btn.dataset.fintab;
            if (!nextSubTab || nextSubTab === state.finSubTab) return;
            state.finSubTab = nextSubTab;
            if (nextSubTab === "audit") {
                const hadData = state.auditLogsData != null;
                if (!hadData) {
                    state.auditLoadAttempted = false;
                }
                renderAdminFinancesView(root, state);
                if (hadData) {
                    loadAudit(0);
                }
            } else if (nextSubTab === "webhooks") {
                const hadData = state.webhooksData != null;
                if (!hadData) {
                    state.webhooksLoadAttempted = false;
                }
                renderAdminFinancesView(root, state);
                if (hadData) {
                    loadWebhooks();
                }
            } else {
                state.financesData = null;
                state.financesLoadAttempted = false;
                state.finStatus = "";
                renderAdminFinancesView(root, state);
            }
        });
    });

    const refreshBtn = root.querySelector("#finRefreshBtn");
    if (refreshBtn) {
        refreshBtn.addEventListener("click", async () => {
            await withButtonLoading(refreshBtn, "Обновление…", async () => {
                if (subTab === "audit") {
                    const qInput = root.querySelector("#auditQueryInput");
                    if (qInput) state.auditQuery = _cleanQuery(qInput.value);
                    await loadAudit((state.auditLogsData && state.auditLogsData.offset) || 0);
                } else if (subTab === "webhooks") {
                    await loadWebhooks();
                } else {
                    const qInput = root.querySelector("#finQueryInput");
                    if (qInput) state.finQuery = _cleanQuery(qInput.value);
                    await loadFinances((state.financesData && state.financesData.offset) || 0);
                }
            });
        });
    }

    bindMaintenanceToggle();

    if (typeof state.paymentsMaintenance === "undefined" && !state.paymentsMaintenanceError) {
        loadPaymentsMaintenance();
    }

    if (subTab === "audit") {
        const auditQueryInput = root.querySelector("#auditQueryInput");
        const syncAuditFilters = () => {
            state.auditAction = root.querySelector("#auditActionSelect").value;
            state.auditTargetType = root.querySelector("#auditTargetTypeSelect").value;
            state.auditQuery = _cleanQuery(auditQueryInput.value);
        };
        auditQueryInput.addEventListener("input", () => {
            state.auditQuery = _cleanQuery(auditQueryInput.value);
        });
        root.querySelector("#auditFilterForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            syncAuditFilters();
            await loadAudit(0);
        });
        auditQueryInput.addEventListener("search", async () => {
            if (auditQueryInput.value === "") {
                syncAuditFilters();
                await loadAudit(0);
            }
        });
        root.querySelector("#auditActionSelect").addEventListener("change", async () => {
            syncAuditFilters();
            await loadAudit(0);
        });
        root.querySelector("#auditTargetTypeSelect").addEventListener("change", async () => {
            syncAuditFilters();
            await loadAudit(0);
        });
        if (state.auditLogsData == null) {
            if (!state.auditLoadAttempted) {
                listContainer.innerHTML = `<div class="muted" style="text-align:center; padding:16px;">Загрузка...</div>`;
                loadAudit(0);
            } else {
                renderRetryList("Не удалось загрузить аудит-лог", () => loadAudit(0));
            }
        } else {
            _renderAuditRows(listContainer, auditData.items || [], state);
            _renderPager(root, auditData, loadAudit, "событий");
        }
    } else if (subTab === "webhooks") {
        if (state.webhooksData == null) {
            if (!state.webhooksLoadAttempted) {
                listContainer.innerHTML = `<div class="muted" style="text-align:center; padding:16px;">Загрузка...</div>`;
                loadWebhooks();
            } else {
                renderRetryList("Не удалось загрузить вебхуки", () => loadWebhooks());
            }
        } else {
            _renderWebhookRows(listContainer, webhooksData, loadWebhooks);
        }
    } else {
        const finQueryInput = root.querySelector("#finQueryInput");
        const syncFinFilters = () => {
            state.finQuery = _cleanQuery(finQueryInput.value);
            state.finStatus = root.querySelector("#finStatusSelect").value;
        };
        finQueryInput.addEventListener("input", () => {
            state.finQuery = _cleanQuery(finQueryInput.value);
        });
        root.querySelector("#finFilterForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            syncFinFilters();
            await loadFinances(0);
        });
        finQueryInput.addEventListener("search", async () => {
            if (finQueryInput.value === "") {
                syncFinFilters();
                await loadFinances(0);
            }
        });
        root.querySelector("#finStatusSelect").addEventListener("change", async () => {
            syncFinFilters();
            await loadFinances(0);
        });
        root.querySelector("#finAttentionToggleBtn").addEventListener("click", async () => {
            syncFinFilters();
            state.finAttentionOnly = !state.finAttentionOnly;
            await loadFinances(0);
        });
        if (state.financesData == null) {
            if (!state.financesLoadAttempted) {
                listContainer.innerHTML = `<div class="muted" style="text-align:center; padding:16px;">Загрузка...</div>`;
                loadFinances(0);
            } else {
                renderRetryList("Не удалось загрузить финансы", () => loadFinances(0));
            }
        } else {
            _renderFinanceRows(listContainer, finData.items || [], state);
            _renderPager(root, finData, loadFinances, "заказов");
        }
    }
}

function _renderFlagChips(flags) {
    if (!Array.isArray(flags) || !flags.length) return "";
    return flags
        .map((flag) => {
            const info = formatMetadataFlag(flag);
            return `<span class="chip ${info.cls}">${escapeHtml(info.text)}</span>`;
        })
        .join("");
}

function _renderFinanceRows(container, items, state) {
    if (!items.length) {
        container.innerHTML = `<div class="muted" style="text-align:center; padding:12px;">Записи не найдены</div>`;
        return;
    }
    items.forEach((item) => {
        const row = document.createElement("div");
        row.className = "item-row";
        const uname = item.username ? `@${item.username}` : `TG ${item.telegram_id}`;
        const shortId = String(item.id || "").slice(0, 8);
        const metaParts = [_formatServiceType(item.service_type), _formatPaymentMethod(item.payment_method)];
        if (item.duration_days) metaParts.push(`${item.duration_days} дн.`);
        if (item.device_limit) metaParts.push(`${item.device_limit} устр.`);
        const flagsHtml = _renderFlagChips(item.metadata_flags);

        const timeDetails = [];
        if (item.paid_at) timeDetails.push(`Оплачен: ${formatDate(item.paid_at)}`);
        if (item.refunded_at) timeDetails.push(`Возврат: ${formatDate(item.refunded_at)}`);

        row.innerHTML = `
            <div class="row" style="align-items:flex-start; flex-wrap:nowrap; gap:8px;">
                <div style="display:flex; align-items:center; flex-wrap:wrap; gap:6px; min-width:0; flex:1;">
                    <span class="mono" style="font-weight:600; word-break:normal; white-space:nowrap;">#${escapeHtml(shortId)}</span>
                    <button type="button" class="btn btn-sm btn-ghost-inline" style="min-width:0; max-width:100%; text-align:left;" data-open-user="${item.user_id}">
                        <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escapeHtml(uname)}</span> · <span class="mono" style="word-break:normal; white-space:nowrap;">${escapeHtml(item.telegram_id)}</span>
                    </button>
                </div>
                <span class="tabular" style="font-weight:600; flex-shrink:0; white-space:nowrap;">${item.amount_rub} ₽</span>
            </div>
            <div class="row meta-text">
                <span>${escapeHtml(metaParts.filter(Boolean).join(" · "))}</span>
                <span>${formatDate(item.created_at)}</span>
            </div>
            ${
                timeDetails.length
                    ? `<div class="meta-text">${escapeHtml(timeDetails.join(" · "))}</div>`
                    : ""
            }
            ${
                flagsHtml
                    ? `<div style="display:flex; flex-wrap:wrap; gap:4px;">${flagsHtml}</div>`
                    : ""
            }
            <div class="row" style="gap:8px; flex-wrap:wrap;">
                <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; min-width:0;">
                    <button type="button" class="mono-copy" data-copy-id="${escapeHtml(item.id)}">
                        Копировать ID
                    </button>
                    ${
                        item.external_id
                            ? `<button type="button" class="mono-copy" data-copy-ext="${escapeHtml(item.external_id)}">
                                Копировать внешний ID
                            </button>`
                            : ""
                    }
                    ${
                        item.reference
                            ? `<span class="mono meta-text" style="word-break:break-all;">${escapeHtml(item.reference)}</span>`
                            : ""
                    }
                </div>
                ${_financeStatusChip(item.status)}
            </div>
        `;

        const userBtn = row.querySelector("[data-open-user]");
        if (userBtn) {
            userBtn.addEventListener("click", () => state.onOpenUser?.(item.user_id));
        }
        const copyIdBtn = row.querySelector("[data-copy-id]");
        if (copyIdBtn) {
            copyIdBtn.addEventListener("click", () => copyText(item.id, "ID заказа скопирован"));
        }
        const copyExtBtn = row.querySelector("[data-copy-ext]");
        if (copyExtBtn) {
            copyExtBtn.addEventListener("click", () => copyText(item.external_id, "Внешний ID скопирован"));
        }
        container.appendChild(row);
    });
}

function _formatAuditDetails(details) {
    if (!details) return "";
    let formatted = String(details);
    const trimmed = formatted.trim();
    if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
        try {
            formatted = JSON.stringify(JSON.parse(trimmed), null, 2);
        } catch {
            // Keep raw text if not valid JSON
        }
    }
    const isLong = formatted.length > 120 || formatted.includes("\n");
    if (isLong) {
        return `
            <details style="margin-top:4px;">
                <summary class="meta-text" style="cursor:pointer;">Детали (${formatted.length} симв.)</summary>
                <pre class="mono meta-text" style="white-space:pre-wrap; word-break:break-word; margin:4px 0 0;">${escapeHtml(formatted)}</pre>
            </details>
        `;
    }
    return `<div class="mono meta-text" style="white-space:pre-wrap; word-break:break-word;">${escapeHtml(formatted)}</div>`;
}

function _renderAuditRows(container, items, state) {
    if (!items.length) {
        container.innerHTML = `<div class="muted" style="text-align:center; padding:16px;">События не найдены</div>`;
        return;
    }
    items.forEach((item) => {
        const row = document.createElement("div");
        row.className = "item-row";
        const targetType = String(item.target_type || "").toLowerCase();
        const hasTargetId = item.target_id !== null && item.target_id !== undefined && item.target_id !== "";
        const targetText = formatAuditTarget(item.target_type, item.target_id);
        const isClickableTarget = hasTargetId && (targetType === "user" || targetType === "server");

        const targetHtml = isClickableTarget
            ? `<button type="button" class="btn btn-sm btn-ghost-inline" data-open-target>${escapeHtml(targetText)}</button>`
            : `<span>${escapeHtml(targetText)}</span>`;

        row.innerHTML = `
            <div class="row">
                <span class="chip chip-muted" title="${escapeHtml(item.action)}">${escapeHtml(formatAuditAction(item.action))}</span>
                <span class="meta-text">${formatDate(item.created_at)}</span>
            </div>
            <div class="row meta-text" style="align-items:center;">
                <span>Админ: <span class="mono" style="word-break:normal; white-space:nowrap;">${escapeHtml(item.admin_id)}</span></span>
                <span>Цель: ${targetHtml}</span>
            </div>
            ${_formatAuditDetails(item.details)}
        `;

        if (isClickableTarget) {
            const targetBtn = row.querySelector("[data-open-target]");
            if (targetBtn) {
                targetBtn.addEventListener("click", () => {
                    if (targetType === "user") {
                        state.onOpenUser?.(item.target_id);
                    } else if (targetType === "server") {
                        state.onOpenNode?.(item.target_id);
                    }
                });
            }
        }
        container.appendChild(row);
    });
}

function _renderWebhookRows(container, items, reloadWebhooks = null) {
    if (!items.length) {
        container.innerHTML = `<div class="muted" style="text-align:center; padding:16px;">Проблемных или ожидающих вебхуков нет</div>`;
        return;
    }
    items.forEach((wh) => {
        const row = document.createElement("div");
        row.className = "item-row";
        const eventKey = wh.event_key || wh.event_id || "";
        const receivedTs = wh.received_at || wh.created_at;
        const receivedStr = receivedTs ? `${formatRelativeTime(receivedTs)} (${formatDate(receivedTs)})` : "—";
        const statusLower = String(wh.status || "").toLowerCase();
        const canAct = wh.id != null && (statusLower === "dead" || statusLower === "retry");
        row.innerHTML = `
            <div class="row">
                <span class="mono" style="font-weight:600; font-size:12px;">${escapeHtml(wh.event_type || "—")}</span>
                ${_webhookStatusChip(wh.status)}
            </div>
            <div class="row meta-text">
                <span>Получен: ${escapeHtml(receivedStr)}</span>
            </div>
            ${
                eventKey
                    ? `<div class="meta-text" style="word-break:break-all;">Ключ: <span class="mono">${escapeHtml(eventKey)}</span></div>`
                    : ""
            }
            <div class="row meta-text">
                <span>Заказ: <span class="mono">${escapeHtml(wh.public_order_id || "—")}</span></span>
                ${
                    wh.public_order_id
                        ? `<button type="button" class="mono-copy" data-copy-wh-order="${escapeHtml(wh.public_order_id)}">Копировать заказ</button>`
                        : ""
                }
            </div>
            ${
                wh.payment_external_id
                    ? `<div class="row meta-text">
                        <span>Платёж: <span class="mono">${escapeHtml(wh.payment_external_id)}</span></span>
                        <button type="button" class="mono-copy" data-copy-wh-payment="${escapeHtml(wh.payment_external_id)}">Копировать платёж</button>
                    </div>`
                    : ""
            }
            ${
                canAct
                    ? `<div class="actions-row" style="margin-top:4px;">
                        <button type="button" class="btn btn-sm" data-retry-wh="${escapeHtml(wh.id)}">Повторить</button>
                        <button type="button" class="btn btn-sm btn-danger" data-dismiss-wh="${escapeHtml(wh.id)}">Скрыть</button>
                    </div>`
                    : ""
            }
        `;
        const copyOrderBtn = row.querySelector("[data-copy-wh-order]");
        if (copyOrderBtn) {
            copyOrderBtn.addEventListener("click", () => copyText(wh.public_order_id, "ID заказа скопирован"));
        }
        const copyPaymentBtn = row.querySelector("[data-copy-wh-payment]");
        if (copyPaymentBtn) {
            copyPaymentBtn.addEventListener("click", () => copyText(wh.payment_external_id, "ID платежа скопирован"));
        }
        const retryBtn = row.querySelector("[data-retry-wh]");
        if (retryBtn) {
            retryBtn.addEventListener("click", () => {
                showConfirmModal({
                    title: "Повторить обработку вебхука?",
                    message: `Вебхук #${wh.id} (${wh.event_type || "событие"}) будет поставлен обратно в очередь обработки.`,
                    confirmText: "Повторить",
                    cancelText: "Отмена",
                    onConfirm: async () => {
                        const res = await api.retryWebhook(wh.id);
                        showToast(res.message || "Вебхук поставлен в очередь");
                        if (typeof reloadWebhooks === "function") await reloadWebhooks();
                    },
                });
            });
        }
        const dismissBtn = row.querySelector("[data-dismiss-wh]");
        if (dismissBtn) {
            dismissBtn.addEventListener("click", () => {
                showConfirmModal({
                    title: "Скрыть проблемный вебхук?",
                    message: `Вебхук #${wh.id} будет отмечен как обработанный и убран из списка проблемных. Действие запишется в аудит-лог.`,
                    confirmText: "Скрыть",
                    cancelText: "Отмена",
                    isDestructive: true,
                    onConfirm: async () => {
                        const res = await api.dismissWebhook(wh.id);
                        showToast(res.message || "Вебхук скрыт");
                        if (typeof reloadWebhooks === "function") await reloadWebhooks();
                    },
                });
            });
        }
        container.appendChild(row);
    });
}

function _renderPager(root, data, onPage, entityName = "записей") {
    const listEl = root.querySelector("#finListContainer");
    renderPagination({
        container: root.querySelector("#finPagerTop"),
        total: data.total || 0,
        offset: data.offset || 0,
        limit: data.limit || 25,
        onPage,
        scrollTarget: listEl,
        entityName,
    });
    renderPagination({
        container: root.querySelector("#finPagerBottom"),
        total: data.total || 0,
        offset: data.offset || 0,
        limit: data.limit || 25,
        onPage,
        scrollTarget: listEl,
        entityName,
    });
}
