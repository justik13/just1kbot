import { api } from "../api.js?v=2.8.0";
import {
    escapeHtml,
    formatDate,
    parseDate,
    renderPagination,
    showToast,
    withButtonLoading,
} from "../ui.js?v=2.8.0";

const USERS_PAGE_SIZE = 25;
let _usersReqId = 0;

const _SHORT_DATE_FMT = new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    timeZone: "Europe/Moscow",
});

function _formatShortDate(iso) {
    const d = parseDate(iso);
    if (!d) return "";
    return _SHORT_DATE_FMT.format(d);
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

function _renderStatsHtml(stats) {
    return `
        <div class="stat-box"><div class="stat-val">${stats.total_users ?? 0}</div><div class="stat-lbl">Всего</div></div>
        <div class="stat-box"><div class="stat-val">${stats.active_standard ?? 0}</div><div class="stat-lbl">Стандарт</div></div>
        <div class="stat-box"><div class="stat-val">${stats.active_wi ?? 0}</div><div class="stat-lbl">Белый интернет</div></div>
        <div class="stat-box"><div class="stat-val">${stats.total_balance_rub ?? 0} ₽</div><div class="stat-lbl">Балансы</div></div>
    `;
}

function _renderFilterPillsHtml(fc, stats, activeFilter) {
    const items = [
        { id: "all", label: `Все (${fc.all ?? stats.total_users ?? 0})` },
        { id: "active", label: `Активные (${fc.active ?? 0})` },
        { id: "active_standard", label: `Стандарт (${fc.active_standard ?? stats.active_standard ?? 0})` },
        { id: "active_wi", label: `Белый интернет (${fc.active_wi ?? stats.active_wi ?? 0})` },
        { id: "expiring_3d", label: `Истекают 3д (${fc.expiring_3d ?? 0})` },
        { id: "new_7d", label: `Новые 7д (${fc.new_7d ?? 0})` },
        { id: "expired", label: `Истекли (${fc.expired ?? 0})` },
        { id: "never", label: `Без подписки (${fc.never ?? 0})` },
        { id: "banned", label: `Бот заблокирован (${fc.banned ?? 0})` },
    ];
    return items
        .map((it) => {
            const active = activeFilter === it.id;
            return `<button type="button" class="pill-btn ${active ? "active" : ""}" data-ufilter="${it.id}" aria-pressed="${active ? "true" : "false"}">${escapeHtml(it.label)}</button>`;
        })
        .join("");
}

function _renderUsersListContent(listEl, users, onOpenUser) {
    listEl.innerHTML = "";
    if (!users.length) {
        listEl.innerHTML = `<div class="muted" style="text-align:center; padding:16px;">Пользователи не найдены</div>`;
        return;
    }

    users.forEach((u) => {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "card node-card-compact card-btn";

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
            <span class="row">
                <span>
                    <span style="font-weight:600;">${_formatUserTitle(u)}</span>
                    <span class="mono muted" style="margin-left:8px; word-break:normal;">${escapeHtml(u.telegram_id)}</span>
                </span>
                <span style="display:flex; align-items:center; gap:8px;">
                    <span class="tabular" style="font-weight:600;">${u.total_balance} ₽</span>
                    <span class="node-chevron" aria-hidden="true">›</span>
                </span>
            </span>
            <span class="row meta-text">
                <span>Осн.: ${u.real_balance} ₽ · Бонусы: ${u.bonus_balance} ₽</span>
                <span>Рег.: ${formatDate(u.created_at)}</span>
            </span>
            <span class="filter-pills">
                ${stdBadge}
                ${wiBadge}
                ${holdBadge}
            </span>
        `;

        card.addEventListener("click", () => {
            if (onOpenUser) onOpenUser(u);
        });
        listEl.appendChild(card);
    });
}

export function renderAdminUsersView(root, state, onOpenUser) {
    ++_usersReqId;
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
        <section id="usersStatsStrip" class="stats-strip">${_renderStatsHtml(stats)}</section>

        <section class="card">
            <div class="row">
                <h2 class="card-title">Пользователи и балансы</h2>
                <button id="reloadUsersBtn" type="button" class="btn btn-sm">Обновить</button>
            </div>
            <div id="usersFilterPills" class="filter-pills">${_renderFilterPillsHtml(fc, stats, activeFilter)}</div>
            <form id="userSearchForm" class="row-nowrap">
                <label for="userSearchInput" class="visually-hidden">Поиск пользователей</label>
                <input id="userSearchInput" class="input" type="search" value="${escapeHtml(query)}" placeholder="ID, @username, имя, HWID, UUID">
                <button type="submit" class="btn btn-primary btn-sm">Найти</button>
            </form>
            <div id="usersPagerTop"></div>
            <div id="usersListContainer" class="stack-sm"></div>
            <div id="usersPagerBottom"></div>
        </section>
    `;

    const searchInput = root.querySelector("#userSearchInput");
    const searchFormEl = root.querySelector("#userSearchForm");
    const statsEl = root.querySelector("#usersStatsStrip");
    const pillsEl = root.querySelector("#usersFilterPills");
    const pagerTopEl = root.querySelector("#usersPagerTop");
    const listEl = root.querySelector("#usersListContainer");
    const pagerBottomEl = root.querySelector("#usersPagerBottom");

    const getLiveQuery = () => {
        const raw = searchInput ? searchInput.value : state.usersQuery || "";
        return String(raw).trim().replace(/^@+/, "");
    };

    const isStillActive = (reqId) =>
        reqId === _usersReqId &&
        root.isConnected &&
        state.selectedUserId == null &&
        state.adminTab === "users" &&
        state.mode === "admin";

    const updateDynamicSections = (curData) => {
        const curStats = curData.stats || {};
        const curFc = curData.filter_counts || {};
        const curUsers = curData.users || [];
        const curFilter = state.usersFilter || "all";

        statsEl.innerHTML = _renderStatsHtml(curStats);

        const activeEl = document.activeElement;
        const focusedFilter =
            activeEl && pillsEl.contains(activeEl) ? activeEl.getAttribute("data-ufilter") : null;
        pillsEl.innerHTML = _renderFilterPillsHtml(curFc, curStats, curFilter);
        bindFilterPills();
        if (focusedFilter) {
            pillsEl.querySelector(`[data-ufilter="${CSS.escape(focusedFilter)}"]`)?.focus({ preventScroll: true });
        }

        const onUserPage = (newOffset) =>
            reload(getLiveQuery(), state.usersFilter || "all", newOffset);
        const total = Number(curData.total ?? curUsers.length);
        const offset = Number(curData.offset ?? state.usersOffset ?? 0);
        const limit = Number(curData.limit || USERS_PAGE_SIZE);

        renderPagination({
            container: pagerTopEl,
            total,
            offset,
            limit,
            onPage: onUserPage,
            scrollTarget: searchFormEl,
            entityName: "пользователей",
        });
        renderPagination({
            container: pagerBottomEl,
            total,
            offset,
            limit,
            onPage: onUserPage,
            scrollTarget: searchFormEl,
            entityName: "пользователей",
        });
        _renderUsersListContent(listEl, curUsers, onOpenUser);
    };

    const reload = async (qVal, filterVal = state.usersFilter || "all", offsetVal = 0) => {
        const reqId = ++_usersReqId;
        const cleanQ = String(qVal || "").trim().replace(/^@+/, "");
        try {
            const nextData = await api.listUsers(cleanQ, offsetVal, USERS_PAGE_SIZE, filterVal);
            if (!isStillActive(reqId)) return;
            state.usersQuery = cleanQ;
            state.usersFilter = filterVal;
            state.usersOffset = offsetVal;
            state.usersData = nextData;
            updateDynamicSections(nextData);
        } catch (err) {
            if (!isStillActive(reqId)) return;
            if (err && err.code === "init_data_expired") return;
            showToast(err.message || "Не удалось загрузить пользователей", "error");
            pagerTopEl.innerHTML = "";
            pagerBottomEl.innerHTML = "";
            listEl.innerHTML = `
                <div class="card card-center">
                    <div class="card-title">Не удалось загрузить пользователей</div>
                    <p class="muted">${escapeHtml(err.message || "Ошибка запроса")}</p>
                    <button id="usersRetryBtn" type="button" class="btn btn-primary btn-sm">Повторить</button>
                </div>
            `;
            listEl.querySelector("#usersRetryBtn")?.addEventListener("click", () => {
                reload(cleanQ, filterVal, offsetVal);
            });
        }
    };

    function bindFilterPills() {
        pillsEl.querySelectorAll("[data-ufilter]").forEach((btn) => {
            btn.addEventListener("click", () => {
                reload(getLiveQuery(), btn.dataset.ufilter, 0);
            });
        });
    }

    bindFilterPills();

    const reloadBtn = root.querySelector("#reloadUsersBtn");
    reloadBtn.addEventListener("click", () => {
        withButtonLoading(reloadBtn, "Обновление...", () =>
            reload(getLiveQuery(), state.usersFilter || "all", state.usersOffset || 0)
        );
    });

    searchFormEl.addEventListener("submit", (e) => {
        e.preventDefault();
        reload(getLiveQuery(), state.usersFilter || "all", 0);
    });

    searchInput.addEventListener("input", () => {
        state.usersQuery = getLiveQuery();
    });

    searchInput.addEventListener("search", () => {
        if (searchInput.value === "") {
            reload("", state.usersFilter || "all", 0);
        }
    });

    const onUserPage = (newOffset) =>
        reload(getLiveQuery(), state.usersFilter || "all", newOffset);
    const total = Number(data.total ?? users.length);
    const offset = Number(data.offset ?? state.usersOffset ?? 0);
    const limit = Number(data.limit || USERS_PAGE_SIZE);

    renderPagination({
        container: pagerTopEl,
        total,
        offset,
        limit,
        onPage: onUserPage,
        scrollTarget: searchFormEl,
        entityName: "пользователей",
    });
    renderPagination({
        container: pagerBottomEl,
        total,
        offset,
        limit,
        onPage: onUserPage,
        scrollTarget: searchFormEl,
        entityName: "пользователей",
    });
    _renderUsersListContent(listEl, users, onOpenUser);
}
