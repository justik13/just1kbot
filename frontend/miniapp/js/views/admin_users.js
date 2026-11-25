import { api } from "../api.js?v=2.8.0";
import {
    escapeHtml,
    formatDate,
    renderPagination,
    showToast,
    withButtonLoading,
} from "../ui.js?v=2.8.0";

const USERS_PAGE_SIZE = 25;
let _usersReqId = 0;

function _formatShortDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (!Number.isFinite(d.getTime())) return "";
    const dd = String(d.getDate()).padStart(2, "0");
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    return `${dd}.${mm}`;
}

function _formatUserTitle(u) {
    if (u.first_name && u.username) {
        return `${escapeHtml(u.first_name)} <span class="muted" style="font-weight:400;">@${escapeHtml(u.username)}</span>`;
    }
    if (u.username) {
        return `@${escapeHtml(u.username)}`;
    }
    if (u.first_name) {
        return escapeHtml(u.first_name);
    }
    return `ID ${escapeHtml(u.telegram_id)}`;
}

export function renderAdminUsersView(root, state, onOpenUser) {
    const data = state.usersData || {
        stats: {},
        filter_counts: {},
        users: [],
        total: 0,
        offset: 0,
        limit: USERS_PAGE_SIZE,
    };
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
                <button type="button" class="pill-btn ${activeFilter === "expiring_3d" ? "active" : ""}" data-ufilter="expiring_3d">Истекают 3д (${fc.expiring_3d ?? 0})</button>
                <button type="button" class="pill-btn ${activeFilter === "expired" ? "active" : ""}" data-ufilter="expired">Истекли (${fc.expired ?? 0})</button>
                <button type="button" class="pill-btn ${activeFilter === "never" ? "active" : ""}" data-ufilter="never">Без подписки (${fc.never ?? 0})</button>
                <button type="button" class="pill-btn ${activeFilter === "banned" ? "active" : ""}" data-ufilter="banned">Бот забл. (${fc.banned ?? 0})</button>
            </div>
            <form id="userSearchForm" class="row" style="flex-wrap:nowrap;">
                <input id="userSearchInput" class="input" type="search" value="${escapeHtml(query)}" placeholder="ID, @username, имя, HWID, UUID">
                <button type="submit" class="btn btn-primary btn-sm">Найти</button>
            </form>
            <div id="usersPagerTop"></div>
            <div id="usersListContainer" class="nodes-list" style="display:flex; flex-direction:column; gap:8px;"></div>
            <div id="usersPagerBottom"></div>
        </section>
    `;

    const searchInput = root.querySelector("#userSearchInput");
    const getLiveQuery = () => (searchInput ? searchInput.value.trim() : state.usersQuery || "");

    const reload = async (qVal, filterVal = state.usersFilter || "all", offsetVal = 0) => {
        const reqId = ++_usersReqId;
        try {
            const nextData = await api.listUsers(qVal, offsetVal, USERS_PAGE_SIZE, filterVal);
            if (reqId !== _usersReqId) return;
            state.usersQuery = qVal;
            state.usersFilter = filterVal;
            state.usersOffset = offsetVal;
            state.usersData = nextData;
            renderAdminUsersView(root, state, onOpenUser);
        } catch (err) {
            if (reqId !== _usersReqId) return;
            showToast(err.message || "Не удалось загрузить пользователей", "error");
        }
    };

    root.querySelectorAll("[data-ufilter]").forEach((btn) => {
        btn.addEventListener("click", () => {
            reload(getLiveQuery(), btn.dataset.ufilter, 0);
        });
    });

    const reloadBtn = root.querySelector("#reloadUsersBtn");
    reloadBtn.addEventListener("click", () => {
        withButtonLoading(reloadBtn, "Обновление...", () =>
            reload(getLiveQuery(), state.usersFilter || "all", state.usersOffset || 0)
        );
    });

    root.querySelector("#userSearchForm").addEventListener("submit", (e) => {
        e.preventDefault();
        reload(getLiveQuery(), state.usersFilter || "all", 0);
    });

    searchInput.addEventListener("search", () => {
        if (searchInput.value === "") {
            reload("", state.usersFilter || "all", 0);
        }
    });

    const searchFormEl = root.querySelector("#userSearchForm");
    const onUserPage = (newOffset) =>
        reload(getLiveQuery(), state.usersFilter || "all", newOffset);

    renderPagination({
        container: root.querySelector("#usersPagerTop"),
        total: Number(data.total ?? users.length),
        offset: Number(data.offset ?? state.usersOffset ?? 0),
        limit: Number(data.limit || USERS_PAGE_SIZE),
        onPage: onUserPage,
        scrollTarget: searchFormEl,
        entityName: "пользователей",
    });

    renderPagination({
        container: root.querySelector("#usersPagerBottom"),
        total: Number(data.total ?? users.length),
        offset: Number(data.offset ?? state.usersOffset ?? 0),
        limit: Number(data.limit || USERS_PAGE_SIZE),
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

        const stdShort = _formatShortDate(u.standard_end);
        const wiShort = _formatShortDate(u.wi_end);
        const stdBadge = u.standard_active
            ? `<span class="chip chip-ok">Стандарт: ${u.standard_device_limit} устр.${stdShort ? ` · до ${stdShort}` : ""}</span>`
            : `<span class="chip chip-muted">Стандарт: нет</span>`;
        const wiBadge = u.wi_active
            ? `<span class="chip chip-wi">Белый интернет: ${u.wi_device_limit} сл.${wiShort ? ` · до ${wiShort}` : ""}</span>`
            : `<span class="chip chip-muted">Белый интернет: нет</span>`;
        const holdBadge = u.financial_hold
            ? `<span class="chip chip-err">Заморожен</span>`
            : u.topup_blocked
              ? `<span class="chip chip-warn">Блок оплат</span>`
              : "";

        card.innerHTML = `
            <div class="row">
                <div>
                    <span style="font-weight:600;">${_formatUserTitle(u)}</span>
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
