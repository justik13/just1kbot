import { api } from "../api.js";
import { copyText, escapeHtml, formatDate, showConfirmModal, showToast } from "../ui.js";

function _renderDevicesHtml(devices) {
    if (!devices.length) {
        return `<div class="muted">Устройства не привязаны</div>`;
    }
    return devices
        .map((d) => {
            const slotChips = [];
            if (d.is_std_assigned) {
                slotChips.push(`<span class="chip chip-ok">Стандарт</span>`);
            }
            if (d.is_bi_assigned) {
                slotChips.push(`<span class="chip chip-wi">Белый интернет</span>`);
            }
            if (!slotChips.length) {
                slotChips.push(`<span class="chip chip-muted">Без слота</span>`);
            }
            return `
                <div class="item-row" style="padding:8px 12px;">
                    <div class="row">
                        <span style="font-weight:600; font-size:13px;">${escapeHtml(d.device_model || d.platform || "Устройство")}</span>
                        <span class="muted" style="font-size:12px;">${d.last_seen_at ? formatDate(d.last_seen_at) : "—"}</span>
                    </div>
                    <div class="mono muted" style="font-size:12px; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(d.hwid)}</div>
                    <div class="row" style="gap:8px; justify-content:flex-start; margin-top:4px;">${slotChips.join("")}</div>
                </div>
            `;
        })
        .join("");
}

export async function renderUserDetailView(root, userSummary, onBack, onReloadUser) {
    const fallbackName = userSummary.username
        ? `@${userSummary.username}`
        : (userSummary.first_name || `ID ${userSummary.telegram_id}`);

    root.innerHTML = `
        <div class="detail-header-nav">
            <button id="userDetailBackBtn" type="button" class="btn btn-sm btn-ghost">← Все пользователи</button>
            <span class="detail-sticky-meta">${escapeHtml(fallbackName)}</span>
        </div>
        <section class="card card-center">
            <div class="muted">Загрузка профиля...</div>
        </section>
    `;
    root.querySelector("#userDetailBackBtn").addEventListener("click", onBack);

    let details;
    try {
        const res = await api.getUserDetails(userSummary.id);
        details = res.user;
    } catch (err) {
        root.innerHTML = `
            <div class="detail-header-nav">
                <button id="userDetailBackBtn" type="button" class="btn btn-sm btn-ghost">← Все пользователи</button>
                <span class="detail-sticky-meta">${escapeHtml(fallbackName)}</span>
            </div>
            <section class="card card-center">
                <div class="card-title">Не удалось загрузить профиль</div>
                <div class="muted">${escapeHtml(err.message || "Ошибка запроса")}</div>
                <button id="userRetryBtn" type="button" class="btn btn-sm">Повторить</button>
            </section>
        `;
        root.querySelector("#userDetailBackBtn").addEventListener("click", onBack);
        root.querySelector("#userRetryBtn").addEventListener("click", () => {
            renderUserDetailView(root, userSummary, onBack, onReloadUser);
        });
        return;
    }

    const uname = details.username ? `@${details.username}` : (details.first_name || `ID ${details.telegram_id}`);
    const inviterStr = details.inviter
        ? (details.inviter.username ? `@${details.inviter.username}` : `ID ${details.inviter.telegram_id}`)
        : "—";
    const wi = details.wi;
    const wiTotalGb = wi ? (((wi.base_traffic_bytes || 0) + (wi.extra_traffic_bytes || 0)) / (1024 ** 3)).toFixed(1) : "0";
    const wiUsedGb = wi ? ((wi.traffic_used_bytes || 0) / (1024 ** 3)).toFixed(2) : "0";
    const devices = details.devices || [];
    const totalBal = details.total_balance ?? userSummary.total_balance ?? 0;
    const realBal = details.real_balance ?? userSummary.real_balance ?? 0;
    const bonusBal = details.bonus_balance ?? userSummary.bonus_balance ?? 0;
    const stdLimit = details.standard_device_limit ?? userSummary.standard_device_limit ?? 2;
    const wiLimit = details.wi_device_limit ?? userSummary.wi_device_limit ?? 1;

    const stdSummary = details.standard_end
        ? `До ${formatDate(details.standard_end)} · ${stdLimit} устр.`
        : "Не подключён";
    const wiSummary = wi && wi.expires_at
        ? `До ${formatDate(wi.expires_at)} · ${wiUsedGb} / ${wiTotalGb} ГБ · ${wiLimit} сл.`
        : "Не подключён";

    root.innerHTML = `
        <div class="detail-header-nav">
            <button id="userDetailBackBtn" type="button" class="btn btn-sm btn-ghost">← Все пользователи</button>
            <span class="detail-sticky-meta">${escapeHtml(uname)}</span>
        </div>

        <section class="card">
            <div class="row" style="align-items:flex-start;">
                <div>
                    <div style="font-size:16px; font-weight:600;">${escapeHtml(uname)}</div>
                    <button id="copyTgIdBtn" type="button" class="btn-ghost" style="border:none; padding:0; margin-top:4px; font-size:12px; color:var(--text-secondary); cursor:pointer; font-family:var(--font-mono);">
                        ID ${details.telegram_id} · Копировать
                    </button>
                </div>
                <div style="text-align:right;">
                    <div class="tabular" style="font-size:18px; font-weight:600;">${totalBal} ₽</div>
                    <div class="muted" style="font-size:12px;">Осн. ${realBal} ₽ · Бонусы ${bonusBal} ₽</div>
                </div>
            </div>
            <div class="detail-grid" style="padding-top:8px; border-top:1px solid var(--border-subtle);">
                <div class="detail-item"><span class="detail-label">Регистрация</span><span>${formatDate(details.created_at || userSummary.created_at)}</span></div>
                <div class="detail-item"><span class="detail-label">Пригласил</span><span>${escapeHtml(inviterStr)}</span></div>
            </div>
        </section>

        <section class="card">
            <div class="row">
                <div class="card-title">Бонусный баланс</div>
                <span class="muted">Коррекция</span>
            </div>
            <form id="userBonusForm" class="field" style="gap:8px;">
                <div class="filter-pills">
                    <button type="button" class="pill-btn" data-add="100">+100 ₽</button>
                    <button type="button" class="pill-btn" data-add="500">+500 ₽</button>
                    <button type="button" class="pill-btn" data-add="1000">+1000 ₽</button>
                    <button type="button" class="pill-btn" data-add="-100">-100 ₽</button>
                </div>
                <div class="row" style="flex-wrap:nowrap;">
                    <input name="amount_rub" class="input tabular" type="number" min="-100000" max="100000" step="1" value="500" aria-label="Сумма коррекции в рублях">
                    <button type="submit" class="btn btn-primary btn-sm" style="flex-shrink:0;">Применить</button>
                </div>
                <input name="note" class="input" type="text" maxlength="120" placeholder="Комментарий (необязательно)">
            </form>
        </section>

        <section class="card">
            <div class="card-title">Подписки</div>

            <div class="user-sub-box">
                <div class="row">
                    <div>
                        <div style="font-weight:600; font-size:13px;">Стандарт</div>
                        <div class="muted" style="font-size:12px;">${escapeHtml(stdSummary)}</div>
                    </div>
                    <button id="toggleStdEditBtn" type="button" class="btn btn-sm">Изменить</button>
                </div>
                <form id="stdEditForm" class="field" style="gap:8px; padding-top:8px; border-top:1px solid var(--border-subtle);" hidden>
                    <div class="grid-2">
                        <div class="field">
                            <label>Срок (дней, 0 = лимит)</label>
                            <input name="days" class="input tabular" type="number" min="-3650" max="3650" step="1" value="30">
                        </div>
                        <div class="field">
                            <label>Устройств (2..10)</label>
                            <input name="device_limit" class="input tabular" type="number" min="2" max="10" step="1" value="${Math.min(10, Math.max(2, stdLimit))}">
                        </div>
                    </div>
                    <div class="filter-pills">
                        <button type="button" class="pill-btn" data-std-days="0">0</button>
                        <button type="button" class="pill-btn" data-std-days="7">+7 дн.</button>
                        <button type="button" class="pill-btn" data-std-days="30">+30 дн.</button>
                        <button type="button" class="pill-btn" data-std-days="90">+90 дн.</button>
                        <button type="button" class="pill-btn" data-std-days="-7">-7 дн.</button>
                    </div>
                    <button type="submit" class="btn btn-primary btn-sm">Сохранить Стандарт</button>
                </form>
            </div>

            <div class="user-sub-box">
                <div class="row">
                    <div>
                        <div style="font-weight:600; font-size:13px;">Белый интернет</div>
                        <div class="muted" style="font-size:12px;">${escapeHtml(wiSummary)}</div>
                    </div>
                    <div class="btn-group">
                        <button id="toggleWiEditBtn" type="button" class="btn btn-sm">Срок</button>
                        <button id="toggleWiTrafficBtn" type="button" class="btn btn-sm">Трафик</button>
                    </div>
                </div>

                <form id="wiEditForm" class="field" style="gap:8px; padding-top:8px; border-top:1px solid var(--border-subtle);" hidden>
                    <div class="grid-2">
                        <div class="field">
                            <label>Срок (дней, 0 = слоты)</label>
                            <input name="days" class="input tabular" type="number" min="-3650" max="3650" step="1" value="30">
                        </div>
                        <div class="field">
                            <label>Слотов (1..3)</label>
                            <input name="device_limit" class="input tabular" type="number" min="1" max="3" step="1" value="${Math.min(3, Math.max(1, wiLimit))}">
                        </div>
                    </div>
                    <div class="filter-pills">
                        <button type="button" class="pill-btn" data-wi-days="0">0</button>
                        <button type="button" class="pill-btn" data-wi-days="7">+7 дн.</button>
                        <button type="button" class="pill-btn" data-wi-days="30">+30 дн.</button>
                        <button type="button" class="pill-btn" data-wi-days="-7">-7 дн.</button>
                    </div>
                    <button type="submit" class="btn btn-primary btn-sm">Сохранить Белый интернет</button>
                </form>

                <form id="wiTrafficForm" class="field" style="gap:8px; padding-top:8px; border-top:1px solid var(--border-subtle);" hidden>
                    <div class="field">
                        <label>Операция с трафиком</label>
                        <select name="traffic_action" class="select">
                            <option value="add_gb">Добавить дополнительный трафик (+ГБ)</option>
                            <option value="reset_used">Сбросить использованный трафик</option>
                            <option value="set_base_gb">Установить базовую квоту (ГБ)</option>
                        </select>
                    </div>
                    <div class="field" id="wiGbField">
                        <label>Объём (ГБ)</label>
                        <input name="gb" class="input tabular" type="number" min="1" max="5000" step="1" value="50">
                    </div>
                    <button type="submit" class="btn btn-primary btn-sm">Применить трафик</button>
                </form>
            </div>
        </section>

        <section class="card">
            <div class="card-title">Написать в бот</div>
            <form id="userDmForm" class="field" style="gap:8px;">
                <textarea name="text" class="textarea" rows="3" maxlength="4096" required style="min-height:84px; font-family:inherit; font-size:13px;" placeholder="Сообщение пользователю (поддерживается HTML)"></textarea>
                <button type="submit" class="btn btn-primary btn-sm">Отправить в бот</button>
            </form>
        </section>

        ${details.subscription_url || details.subscription_backup_url ? `
        <section class="card">
            <div class="card-title">Ссылки подключения</div>
            ${details.subscription_url ? `
            <div class="field">
                <label>Основная (CDN)</label>
                <div class="row" style="gap:8px; flex-wrap:nowrap;">
                    <input class="input mono" readonly value="${escapeHtml(details.subscription_url)}">
                    <button id="detCopySubBtn" type="button" class="btn btn-sm" style="flex-shrink:0;">Копировать</button>
                </div>
            </div>` : ""}
            ${details.subscription_backup_url ? `
            <div class="field">
                <label>Резервная (Прямая)</label>
                <div class="row" style="gap:8px; flex-wrap:nowrap;">
                    <input class="input mono" readonly value="${escapeHtml(details.subscription_backup_url)}">
                    <button id="detCopyBackupSubBtn" type="button" class="btn btn-sm" style="flex-shrink:0;">Копировать</button>
                </div>
            </div>` : ""}
        </section>` : ""}

        <section class="card">
            <div class="row">
                <div class="card-title">Устройства (${devices.length})</div>
            </div>
            <div class="field" style="gap:8px;">
                ${_renderDevicesHtml(devices)}
            </div>

            <form id="userRestrictionsForm" class="field" style="gap:8px; padding-top:8px; border-top:1px solid var(--border-subtle);">
                <label class="toggle-row">
                    <span class="toggle-label">Заморозка доступа и оплат (financial_hold)</span>
                    <span class="switch">
                        <input name="financial_hold" type="checkbox" ${details.financial_hold ? "checked" : ""}>
                        <span class="switch-track"></span>
                    </span>
                </label>
                <label class="toggle-row">
                    <span class="toggle-label">Блокировка внешних пополнений (topup_blocked)</span>
                    <span class="switch">
                        <input name="topup_blocked" type="checkbox" ${details.topup_blocked ? "checked" : ""}>
                        <span class="switch-track"></span>
                    </span>
                </label>
                <button type="submit" class="btn btn-sm">Сохранить ограничения</button>
            </form>

            <div class="actions-row" style="padding-top:8px; border-top:1px solid var(--border-subtle);">
                <button id="detResetHwidBtn" type="button" class="btn btn-sm">Сбросить HWID</button>
                <button id="detRotateTokenBtn" type="button" class="btn btn-sm btn-danger">Перевыпустить токен</button>
            </div>
        </section>
    `;

    root.querySelector("#userDetailBackBtn").addEventListener("click", onBack);
    root.querySelector("#copyTgIdBtn").addEventListener("click", () => {
        copyText(String(details.telegram_id), "Telegram ID скопирован");
    });

    const copyBtn = root.querySelector("#detCopySubBtn");
    if (copyBtn) {
        copyBtn.addEventListener("click", () => copyText(details.subscription_url, "Основная ссылка скопирована"));
    }
    const copyBackupBtn = root.querySelector("#detCopyBackupSubBtn");
    if (copyBackupBtn) {
        copyBackupBtn.addEventListener("click", () => copyText(details.subscription_backup_url, "Резервная ссылка скопирована"));
    }

    const refreshAll = async () => {
        await onReloadUser();
        await renderUserDetailView(root, userSummary, onBack, onReloadUser);
    };

    const bonusForm = root.querySelector("#userBonusForm");
    bonusForm.querySelectorAll("[data-add]").forEach((b) => {
        b.addEventListener("click", () => {
            bonusForm.querySelector("input[name='amount_rub']").value = b.dataset.add;
        });
    });
    bonusForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const amount = Number(bonusForm.querySelector("input[name='amount_rub']").value);
        if (!Number.isInteger(amount) || amount === 0) {
            showToast("Укажите ненулевую целую сумму в рублях");
            return;
        }
        const note = bonusForm.querySelector("input[name='note']").value.trim() || null;
        const btn = bonusForm.querySelector("button[type='submit']");
        btn.disabled = true;
        try {
            const res = await api.adjustUser(details.id, { action: "balance", amount_rub: amount, note });
            showToast(res.message || "Баланс обновлён");
            await refreshAll();
        } catch (err) {
            showToast(err.message || "Ошибка обновления баланса");
        } finally {
            btn.disabled = false;
        }
    });

    const stdForm = root.querySelector("#stdEditForm");
    root.querySelector("#toggleStdEditBtn").addEventListener("click", () => {
        stdForm.hidden = !stdForm.hidden;
    });
    stdForm.querySelectorAll("[data-std-days]").forEach((b) => {
        b.addEventListener("click", () => {
            stdForm.querySelector("input[name='days']").value = b.dataset.stdDays;
        });
    });
    stdForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const days = Number(stdForm.querySelector("input[name='days']").value);
        const deviceLimit = Number(stdForm.querySelector("input[name='device_limit']").value);
        if (!Number.isInteger(days) || days < -3650 || days > 3650) {
            showToast("Укажите корректное количество дней");
            return;
        }
        const payload = { action: "grant_standard", days };
        if (days >= 0 || deviceLimit !== Number(stdLimit)) {
            payload.device_limit = deviceLimit;
        }
        const btn = stdForm.querySelector("button[type='submit']");
        btn.disabled = true;
        try {
            const res = await api.adjustUser(details.id, payload);
            showToast(res.message || "Подписка Стандарт обновлена");
            await refreshAll();
        } catch (err) {
            showToast(err.message || "Ошибка обновления подписки");
        } finally {
            btn.disabled = false;
        }
    });

    const wiForm = root.querySelector("#wiEditForm");
    const wiTrafficForm = root.querySelector("#wiTrafficForm");
    root.querySelector("#toggleWiEditBtn").addEventListener("click", () => {
        wiForm.hidden = !wiForm.hidden;
        if (!wiForm.hidden) wiTrafficForm.hidden = true;
    });
    root.querySelector("#toggleWiTrafficBtn").addEventListener("click", () => {
        wiTrafficForm.hidden = !wiTrafficForm.hidden;
        if (!wiTrafficForm.hidden) wiForm.hidden = true;
    });
    wiForm.querySelectorAll("[data-wi-days]").forEach((b) => {
        b.addEventListener("click", () => {
            wiForm.querySelector("input[name='days']").value = b.dataset.wiDays;
        });
    });
    wiForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const days = Number(wiForm.querySelector("input[name='days']").value);
        const deviceLimit = Number(wiForm.querySelector("input[name='device_limit']").value);
        if (!Number.isInteger(days) || days < -3650 || days > 3650) {
            showToast("Укажите корректное количество дней");
            return;
        }
        const payload = { action: "grant_wi", days };
        if (days >= 0 || deviceLimit !== Number(wiLimit)) {
            payload.device_limit = deviceLimit;
        }
        const btn = wiForm.querySelector("button[type='submit']");
        btn.disabled = true;
        try {
            const res = await api.adjustUser(details.id, payload);
            showToast(res.message || "Белый интернет обновлён");
            await refreshAll();
        } catch (err) {
            showToast(err.message || "Ошибка обновления подписки");
        } finally {
            btn.disabled = false;
        }
    });

    const trafficSelect = wiTrafficForm.querySelector("select[name='traffic_action']");
    const wiGbField = wiTrafficForm.querySelector("#wiGbField");
    trafficSelect.addEventListener("change", () => {
        wiGbField.hidden = trafficSelect.value === "reset_used";
    });
    wiTrafficForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const payload = { action: "wi_traffic", traffic_action: trafficSelect.value };
        if (payload.traffic_action !== "reset_used") {
            const gb = Number(wiTrafficForm.querySelector("input[name='gb']").value);
            if (!Number.isInteger(gb) || gb < 1) {
                showToast("Укажите объём в ГБ (>= 1)");
                return;
            }
            payload.gb = gb;
        }
        const btn = wiTrafficForm.querySelector("button[type='submit']");
        btn.disabled = true;
        try {
            const res = await api.adjustUser(details.id, payload);
            showToast(res.message || "Трафик обновлён");
            await refreshAll();
        } catch (err) {
            showToast(err.message || "Ошибка обновления трафика");
        } finally {
            btn.disabled = false;
        }
    });

    const dmForm = root.querySelector("#userDmForm");
    dmForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const text = dmForm.querySelector("textarea[name='text']").value.trim();
        if (!text) {
            showToast("Введите текст сообщения");
            return;
        }
        const btn = dmForm.querySelector("button[type='submit']");
        btn.disabled = true;
        try {
            const res = await api.sendUserMessage(details.id, text);
            dmForm.reset();
            showToast(res.message || "Сообщение отправлено");
        } catch (err) {
            showToast(err.message || "Ошибка отправки сообщения");
        } finally {
            btn.disabled = false;
        }
    });

    const restrForm = root.querySelector("#userRestrictionsForm");
    restrForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const btn = restrForm.querySelector("button[type='submit']");
        btn.disabled = true;
        try {
            const res = await api.adjustUser(details.id, {
                action: "restrictions",
                financial_hold: Boolean(restrForm.querySelector("input[name='financial_hold']")?.checked),
                topup_blocked: Boolean(restrForm.querySelector("input[name='topup_blocked']")?.checked),
            });
            showToast(res.message || "Ограничения сохранены");
            await refreshAll();
        } catch (err) {
            showToast(err.message || "Ошибка сохранения ограничений");
        } finally {
            btn.disabled = false;
        }
    });

    root.querySelector("#detResetHwidBtn").addEventListener("click", (e) => {
        const b = e.currentTarget;
        showConfirmModal({
            title: "Сбросить привязку устройств?",
            message: `Все привязанные устройства пользователя ${uname} будут отвязаны. Ему потребуется заново обновить подписку в приложении.`,
            confirmText: "Сбросить HWID",
            cancelText: "Отмена",
            isDestructive: true,
            onConfirm: async () => {
                b.disabled = true;
                try {
                    const res = await api.resetUserDevices(details.id);
                    showToast(res.message || "Устройства сброшены");
                    await refreshAll();
                } catch (err) {
                    showToast(err.message || "Ошибка сброса");
                } finally {
                    b.disabled = false;
                }
            },
        });
    });

    root.querySelector("#detRotateTokenBtn").addEventListener("click", (e) => {
        const b = e.currentTarget;
        showConfirmModal({
            title: "Перевыпустить токен подписки?",
            message: "Старая ссылка подключения перестанет работать. Пользователю нужно будет заново добавить подписку.",
            confirmText: "Перевыпустить",
            cancelText: "Отмена",
            isDestructive: true,
            onConfirm: async () => {
                b.disabled = true;
                try {
                    const res = await api.rotateUserToken(details.id);
                    showToast(res.message || "Токен перевыпущен");
                    await refreshAll();
                } catch (err) {
                    showToast(err.message || "Ошибка перевыпуска");
                } finally {
                    b.disabled = false;
                }
            },
        });
    });
}
