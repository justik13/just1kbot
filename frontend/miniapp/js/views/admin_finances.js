import { api } from "../api.js";
import { escapeHtml, formatDate, showToast } from "../ui.js";

function _financeStatusChip(status) {
    if (status === "paid") {
        return `<span class="chip chip-ok">Оплачен</span>`;
    }
    if (status === "pending") {
        return `<span class="chip chip-info">Ожидает</span>`;
    }
    if (status === "refunded") {
        return `<span class="chip chip-warn">Возврат</span>`;
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
                <div id="finListContainer" style="display:flex; flex-direction:column; gap:8px;"></div>
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
                    </select>
                    <button type="submit" class="btn btn-primary btn-sm">Найти</button>
                </form>
                <div id="finListContainer" style="display:flex; flex-direction:column; gap:8px;"></div>
            `
            }
            <div class="row" id="finPagerRow"></div>
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
        _renderPager(root.querySelector("#finPagerRow"), auditData, loadAudit);
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
        _renderPager(root.querySelector("#finPagerRow"), finData, loadFinances);
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
                <span class="chip chip-info">${escapeHtml(item.action)}</span>
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

function _renderPager(pagerEl, data, onPage) {
    const total = data.total || 0;
    const offset = data.offset || 0;
    const limit = data.limit || 25;
    if (total <= limit && offset === 0) {
        pagerEl.innerHTML = `<span class="muted" style="font-size:11px;">Всего: ${total}</span>`;
        return;
    }
    const hasPrev = offset > 0;
    const hasNext = offset + limit < total;
    pagerEl.innerHTML = `
        <span class="muted" style="font-size:11px;">${offset + 1}–${Math.min(offset + limit, total)} из ${total}</span>
        <div class="btn-group">
            <button type="button" class="btn btn-sm" id="pgPrev" ${hasPrev ? "" : "disabled"}>Назад</button>
            <button type="button" class="btn btn-sm" id="pgNext" ${hasNext ? "" : "disabled"}>Далее</button>
        </div>
    `;
    if (hasPrev) {
        pagerEl.querySelector("#pgPrev").addEventListener("click", () => onPage(Math.max(0, offset - limit)));
    }
    if (hasNext) {
        pagerEl.querySelector("#pgNext").addEventListener("click", () => onPage(offset + limit));
    }
}
