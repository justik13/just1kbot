import { api } from "../api.js";
import { escapeHtml, formatDate, openModal, showToast } from "../ui.js";

function _openAdjustUserModal(user, onUpdated) {
    const uname = user.username ? `@${user.username}` : (user.first_name || `ID ${user.telegram_id}`);
    const form = document.createElement("form");
    form.className = "field";
    form.style.gap = "12px";

    let action = "balance";

    form.innerHTML = `
        <div class="muted" style="font-size:12px;">
            Пользователь: <b>${escapeHtml(uname)}</b> (${user.telegram_id})<br>
            Баланс сейчас: <b>${user.total_balance} ₽</b> (осн. ${user.real_balance} ₽ · бонусы ${user.bonus_balance} ₽)
        </div>
        <div class="seg-group">
            <button type="button" class="seg-btn active" data-act="balance">Баланс</button>
            <button type="button" class="seg-btn" data-act="grant_standard">Стандарт</button>
            <button type="button" class="seg-btn" data-act="grant_wi">Белый интернет</button>
        </div>

        <div id="adjBalanceFields" class="field" style="gap:8px;">
            <label>Сумма коррекции (бонусный счёт, можно отрицательную)</label>
            <input name="amount_rub" class="input tabular" type="number" min="-100000" max="100000" step="1" value="500">
            <div class="filter-pills">
                <button type="button" class="pill-btn" data-add="100">+100 ₽</button>
                <button type="button" class="pill-btn" data-add="500">+500 ₽</button>
                <button type="button" class="pill-btn" data-add="1000">+1000 ₽</button>
                <button type="button" class="pill-btn" data-add="-100">-100 ₽</button>
            </div>
        </div>

        <div id="adjSubFields" class="field" style="gap:8px;" hidden>
            <div class="grid-2">
                <div class="field">
                    <label>Срок (дней)</label>
                    <input name="days" class="input tabular" type="number" min="1" max="3650" step="1" value="30">
                </div>
                <div class="field">
                    <label id="adjDevLbl">Устройств (2..10)</label>
                    <input name="device_limit" class="input tabular" type="number" min="1" max="10" step="1" value="${Math.max(2, user.standard_device_limit || 2)}">
                </div>
            </div>
            <div class="filter-pills">
                <button type="button" class="pill-btn" data-days="7">7 дн.</button>
                <button type="button" class="pill-btn" data-days="30">30 дн.</button>
                <button type="button" class="pill-btn" data-days="90">90 дн.</button>
            </div>
        </div>

        <div class="field">
            <label>Комментарий (необязательно)</label>
            <input name="note" class="input" type="text" maxlength="120" placeholder="Например: тестовое начисление">
        </div>

        <button type="submit" class="btn btn-primary btn-lg">Применить</button>
    `;

    const close = openModal(`Управление · ${uname}`, form);

    const balFields = form.querySelector("#adjBalanceFields");
    const subFields = form.querySelector("#adjSubFields");
    const devInput = form.querySelector("input[name='device_limit']");
    const devLbl = form.querySelector("#adjDevLbl");

    form.querySelectorAll("[data-act]").forEach((btn) => {
        btn.addEventListener("click", () => {
            action = btn.dataset.act;
            form.querySelectorAll("[data-act]").forEach((b) => b.classList.toggle("active", b === btn));
            balFields.hidden = action !== "balance";
            subFields.hidden = action === "balance";
            if (action === "grant_standard") {
                devInput.min = "2";
                devInput.max = "10";
                devInput.value = String(Math.min(10, Math.max(2, user.standard_device_limit || 2)));
                devLbl.textContent = "Устройств (2..10)";
            } else if (action === "grant_wi") {
                devInput.min = "1";
                devInput.max = "5";
                devInput.value = String(Math.min(5, Math.max(1, user.wi_device_limit || 1)));
                devLbl.textContent = "Слотов (1..5)";
            }
        });
    });

    form.querySelectorAll("[data-add]").forEach((b) => {
        b.addEventListener("click", () => {
            form.querySelector("input[name='amount_rub']").value = b.dataset.add;
        });
    });

    form.querySelectorAll("[data-days]").forEach((b) => {
        b.addEventListener("click", () => {
            form.querySelector("input[name='days']").value = b.dataset.days;
        });
    });

    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const note = form.querySelector("input[name='note']").value.trim() || null;
        const payload = { action, note };

        if (action === "balance") {
            const amount = Number(form.querySelector("input[name='amount_rub']").value);
            if (!Number.isInteger(amount) || amount === 0) {
                showToast("Укажите ненулевую целую сумму в рублях");
                return;
            }
            payload.amount_rub = amount;
        } else {
            const days = Number(form.querySelector("input[name='days']").value);
            const deviceLimit = Number(devInput.value);
            if (!Number.isInteger(days) || days < 1) {
                showToast("Укажите корректное количество дней");
                return;
            }
            payload.days = days;
            payload.device_limit = deviceLimit;
        }

        const btn = form.querySelector("button[type='submit']");
        btn.disabled = true;
        try {
            const res = await api.adjustUser(user.id, payload);
            close();
            showToast(res.message || "Изменения сохранены");
            onUpdated();
        } catch (err) {
            showToast(err.message || "Ошибка обновления пользователя");
        } finally {
            btn.disabled = false;
        }
    });
}

export function renderAdminUsersView(root, state) {
    const data = state.usersData || { stats: {}, users: [] };
    const stats = data.stats || {};
    const users = data.users || [];
    const query = state.usersQuery || "";

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
            ? `<span class="chip chip-info">Белый интернет: ${u.wi_device_limit} устр.</span>`
            : `<span class="chip chip-muted">Белый интернет: нет</span>`;

        card.innerHTML = `
            <div class="row">
                <div>
                    <span style="font-weight:600;">${escapeHtml(uname)}</span>
                    <span class="mono muted" style="margin-left:6px;">${u.telegram_id}</span>
                </div>
                <span class="tabular" style="font-weight:600;">${u.total_balance} ₽</span>
            </div>
            <div class="row muted" style="font-size:11px;">
                <span>Осн.: ${u.real_balance} ₽ · Бонусы: ${u.bonus_balance} ₽</span>
                <span>Рег.: ${formatDate(u.created_at)}</span>
            </div>
            <div class="row">
                <div class="filter-pills">
                    ${stdBadge}
                    ${wiBadge}
                </div>
                <button type="button" class="btn btn-sm" data-manage-uid="${u.id}">Управление</button>
            </div>
        `;
        card.querySelector("[data-manage-uid]").addEventListener("click", () => {
            _openAdjustUserModal(u, () => reload(state.usersQuery || ""));
        });
        listEl.appendChild(card);
    });
}
