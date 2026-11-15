import { api } from "../api.js";
import { escapeHtml, formatDate, openModal, showToast } from "../ui.js";

function _openDirectMessageModal(user) {
    const uname = user.username ? `@${user.username}` : (user.first_name || `ID ${user.telegram_id}`);
    const form = document.createElement("form");
    form.className = "field";
    form.style.gap = "12px";

    form.innerHTML = `
        <div class="muted" style="font-size:12px;">
            Получатель: <b>${escapeHtml(uname)}</b> (${user.telegram_id})
        </div>
        <div class="field">
            <label for="dmTextArea">Текст сообщения (поддерживается HTML)</label>
            <textarea id="dmTextArea" class="textarea" rows="5" maxlength="4096" required
                placeholder="Введите сообщение пользователю..."></textarea>
        </div>
        <button type="submit" class="btn btn-primary btn-lg">Отправить в бот</button>
    `;

    const close = openModal(`Сообщение · ${uname}`, form);
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const text = form.querySelector("#dmTextArea").value.trim();
        if (!text) {
            showToast("Введите текст сообщения");
            return;
        }
        const btn = form.querySelector("button[type='submit']");
        btn.disabled = true;
        try {
            const res = await api.sendUserMessage(user.id, text);
            close();
            showToast(res.message || "Сообщение отправлено");
        } catch (err) {
            showToast(err.message || "Ошибка отправки сообщения");
        } finally {
            btn.disabled = false;
        }
    });
}

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
            <button type="button" class="seg-btn" data-act="wi_traffic">Трафик</button>
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
                    <label>Срок (дней, 0 = только лимит, &lt;0 = сократить)</label>
                    <input name="days" class="input tabular" type="number" min="-3650" max="3650" step="1" value="30">
                </div>
                <div class="field">
                    <label id="adjDevLbl">Устройств (2..10)</label>
                    <input name="device_limit" class="input tabular" type="number" min="1" max="10" step="1" value="${Math.max(2, user.standard_device_limit || 2)}">
                </div>
            </div>
            <div class="filter-pills">
                <button type="button" class="pill-btn" data-days="0">0 (лимит)</button>
                <button type="button" class="pill-btn" data-days="7">+7 дн.</button>
                <button type="button" class="pill-btn" data-days="30">+30 дн.</button>
                <button type="button" class="pill-btn" data-days="90">+90 дн.</button>
                <button type="button" class="pill-btn" data-days="-7">-7 дн.</button>
                <button type="button" class="pill-btn" data-days="-30">-30 дн.</button>
            </div>
        </div>

        <div id="adjTrafficFields" class="field" style="gap:8px;" hidden>
            <div class="field">
                <label>Операция с трафиком</label>
                <select name="traffic_action" class="select">
                    <option value="add_gb">Добавить дополнительный трафик (+ГБ)</option>
                    <option value="reset_used">Сбросить использованный трафик</option>
                    <option value="set_base_gb">Установить базовую квоту (ГБ)</option>
                </select>
            </div>
            <div class="field" id="adjGbField">
                <label>Объём (ГБ)</label>
                <input name="gb" class="input tabular" type="number" min="1" max="5000" step="1" value="50">
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
    const trafficFields = form.querySelector("#adjTrafficFields");
    const trafficSelect = form.querySelector("select[name='traffic_action']");
    const gbField = form.querySelector("#adjGbField");
    const devInput = form.querySelector("input[name='device_limit']");
    const devLbl = form.querySelector("#adjDevLbl");

    trafficSelect.addEventListener("change", () => {
        gbField.hidden = trafficSelect.value === "reset_used";
    });

    form.querySelectorAll("[data-act]").forEach((btn) => {
        btn.addEventListener("click", () => {
            action = btn.dataset.act;
            form.querySelectorAll("[data-act]").forEach((b) => b.classList.toggle("active", b === btn));
            balFields.hidden = action !== "balance";
            subFields.hidden = action !== "grant_standard" && action !== "grant_wi";
            trafficFields.hidden = action !== "wi_traffic";
            if (action === "grant_standard") {
                devInput.min = "2";
                devInput.max = "10";
                devInput.value = String(Math.min(10, Math.max(2, user.standard_device_limit || 2)));
                devLbl.textContent = "Устройств (2..10)";
            } else if (action === "grant_wi") {
                devInput.min = "1";
                devInput.max = "3";
                devInput.value = String(Math.min(3, Math.max(1, user.wi_device_limit || 1)));
                devLbl.textContent = "Слотов (1..3)";
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
        } else if (action === "wi_traffic") {
            payload.traffic_action = trafficSelect.value;
            if (payload.traffic_action !== "reset_used") {
                const gb = Number(form.querySelector("input[name='gb']").value);
                if (!Number.isInteger(gb) || gb < 1) {
                    showToast("Укажите объём в ГБ (>= 1)");
                    return;
                }
                payload.gb = gb;
            }
        } else {
            const days = Number(form.querySelector("input[name='days']").value);
            const deviceLimit = Number(devInput.value);
            if (!Number.isInteger(days) || days < -3650 || days > 3650) {
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

async function _openUserDetailsModal(user, onUpdated) {
    let details;
    try {
        const res = await api.getUserDetails(user.id);
        details = res.user;
    } catch (err) {
        showToast(err.message || "Не удалось загрузить детали");
        return;
    }

    const box = document.createElement("div");
    box.className = "field";
    box.style.gap = "10px";
    const uname = details.username ? `@${details.username}` : (details.first_name || `ID ${details.telegram_id}`);
    const inviterStr = details.inviter
        ? (details.inviter.username ? `@${details.inviter.username}` : `ID ${details.inviter.telegram_id}`)
        : "—";
    const wi = details.wi;
    const wiTotalGb = wi ? (((wi.base_traffic_bytes || 0) + (wi.extra_traffic_bytes || 0)) / (1024 ** 3)).toFixed(1) : "0";
    const wiUsedGb = wi ? ((wi.traffic_used_bytes || 0) / (1024 ** 3)).toFixed(2) : "0";
    const devicesHtml = (details.devices || []).length
        ? details.devices
              .map(
                  (d) => `
            <div class="item-row" style="padding:8px;">
                <div class="row">
                    <span style="font-weight:600; font-size:12px;">${escapeHtml(d.device_model || d.platform || "Устройство")}</span>
                    <span class="muted" style="font-size:11px;">${d.last_seen_at ? formatDate(d.last_seen_at) : "—"}</span>
                </div>
                <div class="mono muted" style="font-size:11px; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(d.hwid)}</div>
            </div>`
              )
              .join("")
        : `<div class="muted" style="font-size:12px;">Устройства не привязаны</div>`;

    box.innerHTML = `
        <div class="detail-list">
            <div class="detail-item"><span class="detail-label">Telegram ID</span><span class="mono">${details.telegram_id}</span></div>
            <div class="detail-item"><span class="detail-label">Пригласил</span><span>${escapeHtml(inviterStr)}</span></div>
            <div class="detail-item"><span class="detail-label">Стандарт до</span><span>${details.standard_end ? formatDate(details.standard_end) : "—"}</span></div>
            <div class="detail-item"><span class="detail-label">Доп. опция до</span><span>${wi && wi.expires_at ? `${formatDate(wi.expires_at)} (${wiUsedGb} / ${wiTotalGb} ГБ)` : "—"}</span></div>
        </div>
        ${details.subscription_url ? `
        <div class="field">
            <label>Ссылка подписки</label>
            <div class="row" style="gap:6px; flex-wrap:nowrap;">
                <input id="detSubUrl" class="input mono" readonly value="${escapeHtml(details.subscription_url)}">
                <button id="detCopySubBtn" type="button" class="btn btn-sm">Копировать</button>
            </div>
        </div>` : ""}
        <div class="field">
            <label>Устройства (${(details.devices || []).length})</label>
            ${devicesHtml}
        </div>
        <div class="actions-row">
            <button id="detResetHwidBtn" type="button" class="btn btn-sm">Сбросить HWID</button>
            <button id="detRotateTokenBtn" type="button" class="btn btn-sm btn-danger">Перевыпустить токен</button>
        </div>
    `;

    const close = openModal(`Детали · ${uname}`, box);

    const copyBtn = box.querySelector("#detCopySubBtn");
    if (copyBtn) {
        copyBtn.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(details.subscription_url);
                showToast("Ссылка скопирована");
            } catch {
                showToast("Не удалось скопировать");
            }
        });
    }

    box.querySelector("#detResetHwidBtn").addEventListener("click", async (e) => {
        const b = e.currentTarget;
        b.disabled = true;
        try {
            const res = await api.resetUserDevices(user.id);
            showToast(res.message || "Устройства сброшены");
            close();
            onUpdated();
        } catch (err) {
            showToast(err.message || "Ошибка сброса");
        } finally {
            b.disabled = false;
        }
    });

    box.querySelector("#detRotateTokenBtn").addEventListener("click", async (e) => {
        if (!confirm("Перевыпустить токен подписки? Старая ссылка перестанет работать.")) return;
        const b = e.currentTarget;
        b.disabled = true;
        try {
            const res = await api.rotateUserToken(user.id);
            showToast(res.message || "Токен перевыпущен");
            close();
            onUpdated();
        } catch (err) {
            showToast(err.message || "Ошибка перевыпуска");
        } finally {
            b.disabled = false;
        }
    });
}

export function renderAdminUsersView(root, state) {
    const data = state.usersData || { stats: {}, filter_counts: {}, users: [] };
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
                <button type="button" class="pill-btn ${activeFilter === "all" ? "active" : ""}" data-ufilter="all">Все (${fc.total ?? stats.total_users ?? 0})</button>
                <button type="button" class="pill-btn ${activeFilter === "active_standard" ? "active" : ""}" data-ufilter="active_standard">Стандарт (${fc.active_standard ?? stats.active_standard ?? 0})</button>
                <button type="button" class="pill-btn ${activeFilter === "active_wi" ? "active" : ""}" data-ufilter="active_wi">Доп. опция (${fc.active_wi ?? stats.active_wi ?? 0})</button>
                <button type="button" class="pill-btn ${activeFilter === "expired" ? "active" : ""}" data-ufilter="expired">Истекли (${fc.expired ?? 0})</button>
                <button type="button" class="pill-btn ${activeFilter === "never" ? "active" : ""}" data-ufilter="never">Без подписки (${fc.never ?? 0})</button>
            </div>
            <form id="userSearchForm" class="row" style="flex-wrap:nowrap;">
                <input id="userSearchInput" class="input" type="search" value="${escapeHtml(query)}" placeholder="Telegram ID, @username или имя">
                <button type="submit" class="btn btn-primary btn-sm">Найти</button>
            </form>
            <div id="usersListContainer" class="nodes-list" style="display:flex; flex-direction:column; gap:8px;"></div>
        </section>
    `;

    const reload = async (qVal, filterVal = state.usersFilter || "all") => {
        try {
            state.usersQuery = qVal;
            state.usersFilter = filterVal;
            state.usersData = await api.listUsers(qVal, 0, 25, filterVal);
            renderAdminUsersView(root, state);
        } catch (err) {
            showToast(err.message || "Не удалось загрузить пользователей");
        }
    };

    root.querySelectorAll("[data-ufilter]").forEach((btn) => {
        btn.addEventListener("click", () => {
            reload(state.usersQuery || "", btn.dataset.ufilter);
        });
    });

    root.querySelector("#reloadUsersBtn").addEventListener("click", () => reload(state.usersQuery || "", state.usersFilter || "all"));
    root.querySelector("#userSearchForm").addEventListener("submit", (e) => {
        e.preventDefault();
        const val = root.querySelector("#userSearchInput").value.trim();
        reload(val, state.usersFilter || "all");
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
                <div class="btn-group">
                    <button type="button" class="btn btn-sm" data-details-uid="${u.id}">Детали</button>
                    <button type="button" class="btn btn-sm" data-msg-uid="${u.id}">Написать</button>
                    <button type="button" class="btn btn-sm" data-manage-uid="${u.id}">Управление</button>
                </div>
            </div>
        `;
        card.querySelector("[data-details-uid]").addEventListener("click", () => {
            _openUserDetailsModal(u, () => reload(state.usersQuery || "", state.usersFilter || "all"));
        });
        card.querySelector("[data-msg-uid]").addEventListener("click", () => {
            _openDirectMessageModal(u);
        });
        card.querySelector("[data-manage-uid]").addEventListener("click", () => {
            _openAdjustUserModal(u, () => reload(state.usersQuery || "", state.usersFilter || "all"));
        });
        listEl.appendChild(card);
    });
}
