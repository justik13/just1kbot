import { api } from "../api.js";
import { escapeHtml, formatDate, showToast } from "../ui.js";

export function renderAdminUsersView(root, state) {
    const data = state.usersData || { stats: {}, users: [] };
    const stats = data.stats || {};
    const users = data.users || [];
    const query = state.usersQuery || "";

    root.innerHTML = `
        <section class="stats-strip">
            <div class="stat-box"><div class="stat-val">${stats.total_users ?? 0}</div><div class="stat-lbl">Всего</div></div>
            <div class="stat-box"><div class="stat-val">${stats.active_standard ?? 0}</div><div class="stat-lbl">Стандарт</div></div>
            <div class="stat-box"><div class="stat-val">${stats.active_wi ?? 0}</div><div class="stat-lbl">Доп. опция</div></div>
            <div class="stat-box"><div class="stat-val">${stats.total_balance_rub ?? 0} ₽</div><div class="stat-lbl">Балансы</div></div>
        </section>

        <section class="card">
            <div class="row">
                <div class="card-title">Пользователи и балансы</div>
                <button id="reloadUsersBtn" type="button" class="btn btn-sm">Обновить</button>
            </div>
            <form id="userSearchForm" class="row" style="flex-wrap:nowrap;">
                <input id="userSearchInput" class="input" type="search" value="${escapeHtml(query)}" placeholder="Telegram ID, @username или имя">
                <button type="submit" class="btn btn-primary btn-sm">Найти</button>
            </form>
            <div id="usersListContainer" class="nodes-list" style="display:flex; flex-direction:column; gap:8px;"></div>
        </section>
    `;

    const reload = async (qVal) => {
        try {
            state.usersQuery = qVal;
            state.usersData = await api.listUsers(qVal);
            renderAdminUsersView(root, state);
        } catch (err) {
            showToast(err.message || "Не удалось загрузить пользователей");
        }
    };

    root.querySelector("#reloadUsersBtn").addEventListener("click", () => reload(state.usersQuery || ""));
    root.querySelector("#userSearchForm").addEventListener("submit", (e) => {
        e.preventDefault();
        const val = root.querySelector("#userSearchInput").value.trim();
        reload(val);
    });

    const listEl = root.querySelector("#usersListContainer");
    if (!users.length) {
        listEl.innerHTML = `<div class="muted" style="text-align:center; padding:14px;">Пользователи не найдены</div>`;
        return;
    }

    users.forEach((u) => {
        const card = document.createElement("div");
        card.className = "item-row";
        const uname = u.username ? `@${u.username}` : (u.first_name || `ID ${u.telegram_id}`);
        const stdBadge = u.standard_active
            ? `<span class="chip chip-ok">Стандарт: ${u.standard_device_limit} устр.</span>`
            : `<span class="chip chip-muted">Стандарт: нет</span>`;
        const wiBadge = u.wi_active
            ? `<span class="chip chip-info">Доп. опция: ${u.wi_device_limit} устр.</span>`
            : `<span class="chip chip-muted">Доп. опция: нет</span>`;

        card.innerHTML = `
            <div class="row">
                <div>
                    <span style="font-weight:600;">${escapeHtml(uname)}</span>
                    <span class="mono muted" style="margin-left:6px;">${u.telegram_id}</span>
                </div>
                <span style="font-weight:600;">${u.total_balance} ₽</span>
            </div>
            <div class="row muted" style="font-size:11px;">
                <span>Осн.: ${u.real_balance} ₽ · Бонусы: ${u.bonus_balance} ₽</span>
                <span>Рег.: ${formatDate(u.created_at)}</span>
            </div>
            <div class="filter-pills">
                ${stdBadge}
                ${wiBadge}
            </div>
        `;
        listEl.appendChild(card);
    });
}
