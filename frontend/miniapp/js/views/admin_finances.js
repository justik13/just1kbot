import { api } from "../api.js";
import { escapeHtml, formatDate, renderPagination, showConfirmModal, showToast } from "../ui.js";

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

export function renderAdminFinancesView(root, state) {
    const subTab = state.finSubTab || "orders";
    const finData = state.financesData || {
        summary: { revenue_24h: 0, revenue_7d: 0, revenue_30d: 0, paid_orders_count: 0 },
        items: [],
        total: 0,
        offset: 0,
        limit: 25,
    };
    const auditData = state.auditLogsData || { items: [], actions: [], total: 0, offset: 0, limit: 40 };
    const summary = finData.summary || {};

    root.innerHTML = `
        <section class="stats-strip">
            <div class="stat-box"><div class="stat-val tabular">${Math.round(summary.revenue_24h ?? 0)} ₽</div><div class="stat-lbl">За 24 ч</div></div>
            <div class="stat-box"><div class="stat-val tabular">${Math.round(summary.revenue_7d ?? 0)} ₽</div><div class="stat-lbl">За 7 дн.</div></div>
            <div class="stat-box"><div class="stat-val tabular">${Math.round(summary.revenue_30d ?? 0)} ₽</div><div class="stat-lbl">За 30 дн.</div></div>
            <div class="stat-box"><div class="stat-val tabular">${summary.paid_orders_count ?? 0}</div><div class="stat-lbl">Заказов</div></div>
        </section>

        <section class="card" style="margin-bottom:12px;">
            <div class="row" style="align-items:center; justify-content:space-between; gap:12px;">
                <div>
                    <div style="font-weight:600; display:flex; align-items:center; gap:8px;">
                        <span>Техработы платежей</span>
                        <span id="paymentsMaintBadge" class="chip ${state.paymentsMaintenance ? "chip-warn" : "chip-ok"}">
                            ${state.paymentsMaintenance ? "ВКЛЮЧЕНЫ (оплата заблокирована)" : "ВЫКЛЮЧЕНЫ (оплата активна)"}
                        </span>
                    </div>
                    <div class="muted" style="font-size:12px; margin-top:4px;">
                        Блокирует создание новых заказов и пополнение баланса. Действующие подписки продолжают работать.
                    </div>
                </div>
                <button type="button" id="togglePaymentsMaintBtn" class="btn ${state.paymentsMaintenance ? "btn-secondary" : "btn-danger"}" style="white-space:nowrap;">
                    ${state.paymentsMaintenance ? "Включить приём платежей" : "Приостановить платежи"}
                </button>
            </div>
        </section>

        <section class="card">
            <div class="row">
                <div class="seg-group">
                    <button type="button" class="seg-btn ${subTab === "orders" ? "active" : ""}" data-fintab="orders">Все</button>
                    <button type="button" class="seg-btn ${subTab === "subscriptions" ? "active" : ""}" data-fintab="subscriptions">Подписки</button>
                    <button type="button" class="seg-btn ${subTab === "topups" ? "active" : ""}" data-fintab="topups">Пополнения</button>
                    <button type="button" class="seg-btn ${subTab === "audit" ? "active" : ""}" data-fintab="audit">Аудит</button>
                </div>
            </div>

            ${
                subTab === "audit"
                    ? `
                <form id="auditFilterForm" class="field" style="gap:8px;">
                    <div class="grid-2">
                        <select id="auditActionSelect" class="select">
                            <option value="">Все действия</option>
                            ${(auditData.actions || [])
                                .map(
                                    (act) =>
                                        `<option value="${escapeHtml(act)}" ${
                                            state.auditAction === act ? "selected" : ""
                                        }>${escapeHtml(act)}</option>`
                                )
                                .join("")}
                        </select>
                        <div class="row" style="flex-wrap:nowrap;">
                            <input id="auditQueryInput" class="input" type="search" value="${escapeHtml(
                                state.auditQuery || ""
                            )}" placeholder="ID, детали...">
                            <button type="submit" class="btn btn-primary btn-sm">Найти</button>
                        </div>
                    </div>
                </form>
                <div id="finPagerTop"></div>
                <div id="finListContainer" style="display:flex; flex-direction:column; gap:8px;"></div>
                <div id="finPagerBottom"></div>
            `
                    : `
                <form id="finFilterForm" class="row" style="flex-wrap:nowrap;">
                    <input id="finQueryInput" class="input" type="search" value="${escapeHtml(
                        state.finQuery || ""
                    )}" placeholder="Telegram ID, @username, ID заказа">
                    <select id="finStatusSelect" class="select" style="max-width:130px;">
                        <option value="">Все статусы</option>
                        <option value="paid" ${state.finStatus === "paid" ? "selected" : ""}>Оплачен</option>
                        <option value="pending" ${state.finStatus === "pending" ? "selected" : ""}>Ожидает</option>
                        <option value="canceled" ${state.finStatus === "canceled" ? "selected" : ""}>Отменён</option>
                        <option value="refunded" ${state.finStatus === "refunded" ? "selected" : ""}>Возврат</option>
                        <option value="partially_refunded" ${state.finStatus === "partially_refunded" ? "selected" : ""}>Част. возврат</option>
                    </select>
                    <button type="submit" class="btn btn-primary btn-sm">Найти</button>
                </form>
                <div id="finPagerTop"></div>
                <div id="finListContainer" style="display:flex; flex-direction:column; gap:8px;"></div>
                <div id="finPagerBottom"></div>
            `
            }
        </section>
    `;

    const loadFinances = async (offset = 0) => {
        try {
            const kind = ["orders", "subscriptions", "topups"].includes(state.finSubTab)
                ? state.finSubTab
                : "orders";
            state.financesData = await api.listFinances(
                kind,
                state.finStatus || "",
                state.finQuery || "",
                offset,
                25
            );
            renderAdminFinancesView(root, state);
        } catch (err) {
            showToast(err.message || "Ошибка загрузки финансов");
        }
    };

    const loadAudit = async (offset = 0) => {
        try {
            state.auditLogsData = await api.listAuditLogs(
                state.auditAction || "",
                state.auditQuery || "",
                offset,
                40
            );
            renderAdminFinancesView(root, state);
        } catch (err) {
            showToast(err.message || "Ошибка загрузки аудит-лога");
        }
    };

    root.querySelectorAll("[data-fintab]").forEach((btn) => {
        btn.addEventListener("click", async () => {
            state.finSubTab = btn.dataset.fintab;
            if (state.finSubTab === "audit") {
                await loadAudit(0);
            } else {
                state.finStatus = "";
                await loadFinances(0);
            }
        });
    });

    const toggleMaintBtn = root.querySelector("#togglePaymentsMaintBtn");
    if (toggleMaintBtn) {
        toggleMaintBtn.addEventListener("click", () => {
            const nextVal = !state.paymentsMaintenance;
            showConfirmModal({
                title: nextVal ? "Приостановить приём платежей?" : "Возобновить приём платежей?",
                message: nextVal
                    ? "Создание новых заказов и пополнение баланса будет временно заблокировано для всех пользователей. Действующие подписки продолжат работать."
                    : "Пользователи снова смогут оформлять подписки и пополнять баланс.",
                confirmText: nextVal ? "Приостановить" : "Возобновить",
                cancelText: "Отмена",
                isDestructive: nextVal,
                onConfirm: async () => {
                    toggleMaintBtn.disabled = true;
                    try {
                        const res = await api.setPaymentsMaintenance(nextVal);
                        state.paymentsMaintenance = !!res.enabled;
                        showToast(res.enabled ? "Техработы платежей включены" : "Приём платежей возобновлён");
                        renderAdminFinancesView(root, state);
                    } catch (err) {
                        showToast(err.message || "Ошибка переключения техработ");
                        toggleMaintBtn.disabled = false;
                    }
                },
            });
        });
    }

    if (typeof state.paymentsMaintenance === "undefined") {
        const initBtn = root.querySelector("#togglePaymentsMaintBtn");
        if (initBtn) initBtn.disabled = true;
        api.getPaymentsMaintenance().then((res) => {
            state.paymentsMaintenance = !!res.enabled;
            const badge = root.querySelector("#paymentsMaintBadge");
            const btn = root.querySelector("#togglePaymentsMaintBtn");
            if (badge && btn) {
                badge.className = `chip ${state.paymentsMaintenance ? "chip-warn" : "chip-ok"}`;
                badge.textContent = state.paymentsMaintenance ? "ВКЛЮЧЕНЫ (оплата заблокирована)" : "ВЫКЛЮЧЕНЫ (оплата активна)";
                btn.className = `btn ${state.paymentsMaintenance ? "btn-secondary" : "btn-danger"}`;
                btn.textContent = state.paymentsMaintenance ? "Включить приём платежей" : "Приостановить платежи";
                btn.disabled = false;
            }
        }).catch(() => {
            const badge = root.querySelector("#paymentsMaintBadge");
            const btn = root.querySelector("#togglePaymentsMaintBtn");
            if (badge) {
                badge.className = "chip chip-warn";
                badge.textContent = "Ошибка загрузки";
            }
            if (btn) {
                btn.disabled = true;
                btn.textContent = "Недоступно";
            }
        });
    }

    if (subTab === "audit") {
        root.querySelector("#auditFilterForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            state.auditAction = root.querySelector("#auditActionSelect").value;
            state.auditQuery = root.querySelector("#auditQueryInput").value.trim();
            await loadAudit(0);
        });
        root.querySelector("#auditActionSelect").addEventListener("change", async () => {
            state.auditAction = root.querySelector("#auditActionSelect").value;
            await loadAudit(0);
        });
        _renderAuditRows(root.querySelector("#finListContainer"), auditData.items || []);
        _renderPager(root, auditData, loadAudit, "событий");
    } else {
        root.querySelector("#finFilterForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            state.finQuery = root.querySelector("#finQueryInput").value.trim();
            state.finStatus = root.querySelector("#finStatusSelect").value;
            await loadFinances(0);
        });
        root.querySelector("#finStatusSelect").addEventListener("change", async () => {
            state.finStatus = root.querySelector("#finStatusSelect").value;
            await loadFinances(0);
        });
        _renderFinanceRows(root.querySelector("#finListContainer"), finData.items || []);
        _renderPager(root, finData, loadFinances, "заказов");
    }
}

function _renderFinanceRows(container, items) {
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
        row.innerHTML = `
            <div class="row">
                <div>
                    <span class="mono" style="font-weight:600;">#${escapeHtml(shortId)}</span>
                    <span style="font-weight:600;"> · ${escapeHtml(uname)}</span>
                    <span class="mono muted" style="margin-left:6px;">${item.telegram_id}</span>
                </div>
                <span class="tabular" style="font-weight:600;">${item.amount_rub} ₽</span>
            </div>
            <div class="row muted" style="font-size:11px;">
                <span>${escapeHtml(metaParts.filter(Boolean).join(" · "))}</span>
                <span>${formatDate(item.created_at)}</span>
            </div>
            <div class="row">
                <span class="mono muted" style="font-size:11px;">${escapeHtml(item.reference || "")}</span>
                ${_financeStatusChip(item.status)}
            </div>
        `;
        container.appendChild(row);
    });
}

function _renderAuditRows(container, items) {
    if (!items.length) {
        container.innerHTML = `<div class="muted" style="text-align:center; padding:12px;">События не найдены</div>`;
        return;
    }
    items.forEach((item) => {
        const row = document.createElement("div");
        row.className = "item-row";
        const targetText = item.target_type
            ? `${item.target_type}${item.target_id != null ? `#${item.target_id}` : ""}`
            : "—";
        row.innerHTML = `
            <div class="row">
                <span class="chip chip-muted">${escapeHtml(item.action)}</span>
                <span class="muted" style="font-size:11px;">${formatDate(item.created_at)}</span>
            </div>
            <div class="row muted" style="font-size:11px;">
                <span>Админ: <span class="mono">${item.admin_id}</span></span>
                <span>Цель: <span class="mono">${escapeHtml(targetText)}</span></span>
            </div>
            ${
                item.details
                    ? `<div class="mono muted" style="font-size:11px; white-space:pre-wrap;">${escapeHtml(
                          item.details
                      )}</div>`
                    : ""
            }
        `;
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
