import { api } from "../api.js?v=2.7.7";
import { escapeHtml, formatDate, renderPagination, showToast } from "../ui.js?v=2.7.7";

export function renderAdminUsersView(root, state, onOpenUser) {
    const data = state.usersData || { stats: {}, filter_counts: {}, users: [], total: 0, offset: 0, limit: 10 };
    const stats = data.stats || {};
    const fc = data.filter_counts || {};
    const users = data.users || [];
    const query = state.usersQuery || "";
    const activeFilter = state.usersFilter || "all";

    root.innerHTML = `
        <section class="stats-strip">
            <div class="stat-box"><div class="stat-val">${stats.total_users ?? 0}</div><div class="stat-lbl">Всего</div></div>
            <div class="stat-box"><div class="stat-val">${stats.active_standard ?? 0}</div><div class="stat-lbl">Стандарт</div></div>
            <div class="stat-box"><div class="stat-val">${stats.active_wi ?? 0}</div><div class="stat-lbl">Белый интернет</div></div>
            <div class="stat-box"><div class="stat-val">${stats.total_balance_rub ?? 0} ₽</div><div class="stat-lbl">Балансы</div></div>
        </section>

        <section class="card">
            <div class="row">
                <div class="card-title">Пользователи и балансы</div>
                <button id="reloadUsersBtn" type="button" class="btn btn-sm">Обновить</button>
            </div>
            <div class="filter-pills">
                <button type="button" class="pill-btn ${activeFilter === "all" ? "active" : ""}" data-ufilter="all">Все (${fc.all ?? stats.total_users ?? 0})</button>
                <button type="button" class="pill-btn ${activeFilter === "active_standard" ? "active" : ""}" data-ufilter="active_standard">Стандарт (${fc.active_standard ?? stats.active_standard ?? 0})</button>
                <button type="button" class="pill-btn ${activeFilter === "active_wi" ? "active" : ""}" data-ufilter="active_wi">Белый интернет (${fc.active_wi ?? stats.active_wi ?? 0})</button>
                <button type="button" class="pill-btn ${activeFilter === "expired" ? "active" : ""}" data-ufilter="expired">Истекли (${fc.expired ?? 0})</button>
                <button type="button" class="pill-btn ${activeFilter === "never" ? "active" : ""}" data-ufilter="never">Без подписки (${fc.never ?? 0})</button>
            </div>
            <form id="userSearchForm" class="row" style="flex-wrap:nowrap;">
                <input id="userSearchInput" class="input" type="search" value="${escapeHtml(query)}" placeholder="Telegram ID, @username или имя">
                <button type="submit" class="btn btn-primary btn-sm">Найти</button>
            </form>
            <div id="usersPagerTop"></div>
            <div id="usersListContainer" class="nodes-list" style="display:flex; flex-direction:column; gap:8px;"></div>
            <div id="usersPagerBottom"></div>
        </section>
    `;

    const reload = async (qVal, filterVal = state.usersFilter || "all", offsetVal = 0) => {
        try {
            state.usersQuery = qVal;
            state.usersFilter = filterVal;
            state.usersOffset = offsetVal;
            state.usersData = await api.listUsers(qVal, offsetVal, 10, filterVal);
            renderAdminUsersView(root, state, onOpenUser);
        } catch (err) {
            showToast(err.message || "Не удалось загрузить пользователей");
        }
    };

    root.querySelectorAll("[data-ufilter]").forEach((btn) => {
        btn.addEventListener("click", () => {
            reload(state.usersQuery || "", btn.dataset.ufilter, 0);
        });
    });

    root.querySelector("#reloadUsersBtn").addEventListener("click", () => {
        reload(state.usersQuery || "", state.usersFilter || "all", state.usersOffset || 0);
    });
    root.querySelector("#userSearchForm").addEventListener("submit", (e) => {
        e.preventDefault();
        const val = root.querySelector("#userSearchInput").value.trim();
        reload(val, state.usersFilter || "all", 0);
    });

    const searchFormEl = root.querySelector("#userSearchForm");
    const onUserPage = (newOffset) => reload(state.usersQuery || "", state.usersFilter || "all", newOffset);

    renderPagination({
        container: root.querySelector("#usersPagerTop"),
        total: Number(data.total ?? users.length),
        offset: Number(data.offset ?? state.usersOffset ?? 0),
        limit: Number(data.limit || 10),
        onPage: onUserPage,
        scrollTarget: searchFormEl,
        entityName: "пользователей",
    });

    renderPagination({
        container: root.querySelector("#usersPagerBottom"),
        total: Number(data.total ?? users.length),
        offset: Number(data.offset ?? state.usersOffset ?? 0),
        limit: Number(data.limit || 10),
        onPage: onUserPage,
        scrollTarget: searchFormEl,
        entityName: "пользователей",
    });

    const listEl = root.querySelector("#usersListContainer");
    if (!users.length) {
        listEl.innerHTML = `<div class="muted" style="text-align:center; padding:14px;">Пользователи не найдены</div>`;
        return;
    }

    users.forEach((u) => {
        const card = document.createElement("div");
        card.className = "card node-card-compact";
        card.setAttribute("role", "button");
        card.setAttribute("tabindex", "0");

        const uname = u.username ? `@${u.username}` : (u.first_name || `ID ${u.telegram_id}`);
        const stdBadge = u.standard_active
            ? `<span class="chip chip-ok">Стандарт: ${u.standard_device_limit} устр.</span>`
            : `<span class="chip chip-muted">Стандарт: нет</span>`;
        const wiBadge = u.wi_active
            ? `<span class="chip chip-wi">Белый интернет: ${u.wi_device_limit} сл.</span>`
            : `<span class="chip chip-muted">Белый интернет: нет</span>`;
        const holdBadge = u.financial_hold
            ? `<span class="chip chip-err">Заморожен</span>`
            : (u.topup_blocked ? `<span class="chip chip-warn">Блок оплат</span>` : "");

        card.innerHTML = `
            <div class="row">
                <div>
                    <span style="font-weight:600;">${escapeHtml(uname)}</span>
                    <span class="mono muted" style="margin-left:6px;">${u.telegram_id}</span>
                </div>
                <div style="display:flex; align-items:center; gap:8px;">
                    <span class="tabular" style="font-weight:600;">${u.total_balance} ₽</span>
                    <span class="node-chevron">›</span>
                </div>
            </div>
            <div class="row muted" style="font-size:12px;">
                <span>Осн.: ${u.real_balance} ₽ · Бонусы: ${u.bonus_balance} ₽</span>
                <span>Рег.: ${formatDate(u.created_at)}</span>
            </div>
            <div class="filter-pills">
                ${stdBadge}
                ${wiBadge}
                ${holdBadge}
            </div>
        `;

        const open = () => {
            if (onOpenUser) onOpenUser(u);
        };
        card.addEventListener("click", open);
        card.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                open();
            }
        });
        listEl.appendChild(card);
    });
}
