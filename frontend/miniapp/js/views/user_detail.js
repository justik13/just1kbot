import { api } from "../api.js?v=2.8.0";
import {
    formatWiNodeSyncStatus,
    formatWiProvisioningStatus,
} from "../admin_labels.js?v=2.8.0";
import { bindUserDetailActions } from "./user_detail_actions.js?v=2.8.0";
import { renderHistoryTabContent } from "./user_detail_history.js?v=2.8.0";
import {
    copyText,
    escapeHtml,
    formatBytesGb,
    formatDate,
    formatRelativeTime,
} from "../ui.js?v=2.8.0";

let _detailReqId = 0;

function _formatDisplayName(u) {
    if (u.first_name && u.username) {
        return `${u.first_name} (@${u.username})`;
    }
    if (u.username) return `@${u.username}`;
    if (u.first_name) return u.first_name;
    if (u.telegram_id) return `ID ${u.telegram_id}`;
    return `Пользователь #${u.id}`;
}

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
            if (d.is_std_priority) {
                slotChips.push(`<span class="chip chip-ok">Приоритет Стандарт</span>`);
            }
            if (d.is_bi_priority) {
                slotChips.push(`<span class="chip chip-wi">Приоритет БИ</span>`);
            }
            if (!slotChips.length) {
                slotChips.push(`<span class="chip chip-muted">Без слота</span>`);
            }
            const metaParts = [d.platform, d.app_version ? `v${d.app_version}` : ""].filter(Boolean);
            const metaText = metaParts.join(" · ");
            return `
                <div class="item-row" style="padding:12px;">
                    <div class="row">
                        <span style="font-weight:600; font-size:13px;">${escapeHtml(d.device_model || d.platform || "Устройство")}</span>
                        <span class="meta-text">${d.last_seen_at ? formatRelativeTime(d.last_seen_at) : "—"}</span>
                    </div>
                    ${metaText ? `<div class="meta-text">${escapeHtml(metaText)}</div>` : ""}
                    <div class="row" style="gap:8px; margin-top:4px; flex-wrap:nowrap;">
                        <button type="button" class="mono-copy" style="min-width:0; flex:1 1 auto; text-align:left;" data-copy-hwid="${escapeHtml(d.hwid)}" title="Копировать HWID">${escapeHtml(d.hwid)}</button>
                        <button type="button" class="btn btn-sm btn-danger" data-del-hwid="${escapeHtml(d.hwid)}" style="flex-shrink:0;">Удалить</button>
                    </div>
                    <div class="filter-pills" style="margin-top:4px;">${slotChips.join("")}</div>
                </div>
            `;
        })
        .join("");
}

function _renderWiDiagnosticsHtml(wi) {
    if (!wi) return "";
    const provStatus = formatWiProvisioningStatus(wi.provisioning_status);
    const syncStateEntries =
        wi.node_sync_state && typeof wi.node_sync_state === "object"
            ? Object.entries(wi.node_sync_state)
            : [];
    const syncNodesHtml = syncStateEntries.length
        ? syncStateEntries
              .map(([nodeId, st]) => {
                  let stText = "";
                  let chipCls = "chip-muted";
                  if (typeof st === "object" && st !== null) {
                      if (st.status) {
                          const rawStatus = String(st.status).toLowerCase();
                          stText = formatWiNodeSyncStatus(st.status);
                          if (rawStatus === "active" || rawStatus === "synced" || rawStatus === "synced_active") {
                              chipCls = "chip-ok";
                          } else if (rawStatus === "error" || rawStatus === "failed") {
                              chipCls = "chip-err";
                          }
                      } else if ("active" in st) {
                          stText = st.active ? "активен" : "отключён";
                          if (st.version != null) stText += ` · v${st.version}`;
                          chipCls = st.active ? "chip-ok" : "chip-muted";
                      } else {
                          stText = JSON.stringify(st);
                      }
                  } else {
                      stText = formatWiNodeSyncStatus(st);
                  }
                  return `<span class="chip ${chipCls}">Нода #${escapeHtml(nodeId)}: ${escapeHtml(stText)}</span>`;
              })
              .join("")
        : `<span class="muted">Нет данных по нодам</span>`;

    return `
        <div class="detail-grid" style="margin-top:4px;">
            <div class="detail-item"><span class="detail-label">Провижининг</span><span>${escapeHtml(provStatus)}</span></div>
            <div class="detail-item"><span class="detail-label">Автопродление</span><span>${wi.auto_renew_enabled ? "Включено" : "Выключено"}</span></div>
            <div class="detail-item"><span class="detail-label">Версия конфига</span><span class="mono tabular">${wi.actual_version ?? 0} / ${wi.desired_version ?? 1}</span></div>
            <div class="detail-item"><span class="detail-label">Синхронизация</span><span>${wi.last_synced_at ? formatRelativeTime(wi.last_synced_at) : "—"}</span></div>
            ${
                wi.status_reason
                    ? `<div class="detail-item" style="grid-column:1 / -1;"><span class="detail-label">Причина статуса</span><span>${escapeHtml(wi.status_reason)}</span></div>`
                    : ""
            }
        </div>
        <div class="filter-pills" style="margin-top:4px;">${syncNodesHtml}</div>
        ${
            wi.last_sync_error
                ? `<div class="text-err" style="margin-top:8px; padding:8px 12px; border-radius:8px; background:var(--accent-red-soft); border:1px solid rgba(248,113,113,0.32); font-size:12px; white-space:pre-wrap;">${escapeHtml(wi.last_sync_error)}</div>`
                : ""
        }
    `;
}

export async function renderUserDetailView(root, userSummary, onBack, onReloadUser, state = null, options = {}) {
    const reqId = ++_detailReqId;
    const preserveScroll = Boolean(options && options.preserveScroll);
    const savedScrollY = options && Number.isFinite(options.scrollY) ? options.scrollY : null;
    const drafts = (options && options.drafts) || null;
    const fallbackName = _formatDisplayName(userSummary);
    const hadExistingProfile = Boolean(root.querySelector("#userDetailBackBtn"));

    if (!preserveScroll || !hadExistingProfile) {
        root.innerHTML = `
            <div class="detail-header-nav">
                <button id="userDetailBackBtn" type="button" class="btn btn-sm btn-ghost" style="white-space:nowrap; flex-shrink:0;">← Все пользователи</button>
                <span class="detail-sticky-meta">${escapeHtml(fallbackName)}</span>
            </div>
            <section class="card card-center">
                <div class="muted">Загрузка профиля...</div>
            </section>
        `;
        root.querySelector("#userDetailBackBtn").addEventListener("click", onBack);
    }

    const sentinelBtn = root.querySelector("#userDetailBackBtn");

    let details;
    let serverNow = null;
    try {
        const res = await api.getUserDetails(userSummary.id);
        details = res.user;
        serverNow = res.server_now || details.server_now || null;
    } catch (err) {
        if (reqId !== _detailReqId) return;
        if (state && state.selectedUserId !== userSummary.id) return;
        if (!root.isConnected || (sentinelBtn && !root.contains(sentinelBtn))) return;
        if (err && err.code === "2fa_required") return;
        if (preserveScroll && hadExistingProfile) {
            showToast(err.message || "Не удалось обновить профиль", "error");
            return;
        }
        root.innerHTML = `
            <div class="detail-header-nav">
                <button id="userDetailBackBtn" type="button" class="btn btn-sm btn-ghost" style="white-space:nowrap; flex-shrink:0;">← Все пользователи</button>
                <span class="detail-sticky-meta">${escapeHtml(fallbackName)}</span>
            </div>
            <section class="card card-center">
                <h1 class="card-title">Не удалось загрузить профиль</h1>
                <div class="muted">${escapeHtml(err.message || "Ошибка запроса")}</div>
                <button id="userRetryBtn" type="button" class="btn btn-primary btn-sm">Повторить</button>
            </section>
        `;
        root.querySelector("#userDetailBackBtn").addEventListener("click", onBack);
        root.querySelector("#userRetryBtn").addEventListener("click", () => {
            renderUserDetailView(root, userSummary, onBack, onReloadUser, state);
        });
        return;
    }

    if (reqId !== _detailReqId) return;
    if (state && state.selectedUserId !== userSummary.id) return;
    if (!root.isConnected || (sentinelBtn && !root.contains(sentinelBtn))) return;

    const uname = _formatDisplayName(details);
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
    const debt = Number(details.debt || 0);
    const stdLimit = details.standard_device_limit ?? userSummary.standard_device_limit ?? 2;
    const wiLimit = (wi && wi.device_limit) ?? userSummary.wi_device_limit ?? 1;

    const parsedServerNow = serverNow ? new Date(serverNow).getTime() : NaN;
    const nowMs = Number.isFinite(parsedServerNow) ? parsedServerNow : Date.now();

    const stdInfo = details.standard || null;
    const stdEndTs = details.standard_end ? new Date(details.standard_end).getTime() : 0;
    const stdExpired = Boolean(details.standard_end && stdEndTs <= nowMs);
    const stdSummary = details.standard_end
        ? `До ${formatDate(details.standard_end)} · ${stdLimit} устр.${stdInfo ? ` · автопродл.: ${stdInfo.auto_renew_enabled ? "вкл" : "выкл"}` : ""}`
        : "Не подключён";

    const wiStatusMap = {
        pending: "Настраивается",
        active: "Активен",
        expired: "Истёк",
        exhausted: "Трафик исчерпан",
        disabled: "Отключён",
        suspended: "Приостановлен",
        revoked: "Отозван",
    };
    const wiStatusKey = wi && wi.status ? String(wi.status).toLowerCase() : "";
    const wiIsExpiredByDate = Boolean(wi && wi.expires_at && new Date(wi.expires_at).getTime() <= nowMs);
    const wiStatusLabel = wi
        ? (wiStatusMap[wiStatusKey] || (wiIsExpiredByDate ? "Истёк" : (wi.status ? String(wi.status) : "")))
        : "";
    const wiEffectiveStatus = wiStatusKey === "active" && wiIsExpiredByDate ? "Истёк" : wiStatusLabel;
    const wiSummary = wi && wi.expires_at
        ? `${wiEffectiveStatus ? `${wiEffectiveStatus} · ` : ""}До ${formatDate(wi.expires_at)} · ${wiUsedGb} / ${wiTotalGb} ГБ · ${wiLimit} сл.`
        : (wi && wiEffectiveStatus ? `${wiEffectiveStatus} · ${wiUsedGb} / ${wiTotalGb} ГБ · ${wiLimit} сл.` : "Не подключён");

    let activeHistoryTab = (drafts && drafts.activeHistoryTab) || "orders";
    if (!["orders", "ledger", "devices", "audit"].includes(activeHistoryTab)) {
        activeHistoryTab = "orders";
    }

    root.innerHTML = `
        <div class="detail-header-nav">
            <button id="userDetailBackBtn" type="button" class="btn btn-sm btn-ghost" style="white-space:nowrap; flex-shrink:0;">← Все пользователи</button>
            <span class="detail-sticky-meta">${escapeHtml(uname)}</span>
        </div>

        <section class="card">
            <div class="row" style="align-items:flex-start;">
                <div style="min-width:0; flex:1;">
                    <h1 class="detail-hero-title">${escapeHtml(uname)}</h1>
                    <button id="copyTgIdBtn" type="button" class="mono-copy" style="margin-top:8px;">
                        ID ${details.telegram_id} · Копировать
                    </button>
                    <div class="filter-pills" style="margin-top:8px;">
                        ${details.is_bot_blocked ? `<span class="chip chip-err">Бот заблокирован</span>` : ""}
                        ${Number(details.churn_events_24h || 0) > 0 ? `<span class="chip chip-warn">Смены устр. 24ч: ${details.churn_events_24h}</span>` : ""}
                        ${details.financial_hold ? `<span class="chip chip-err">Финансовая блокировка</span>` : ""}
                        ${details.topup_blocked ? `<span class="chip chip-warn">Блок пополнений</span>` : ""}
                    </div>
                </div>
                <div class="user-balance-box">
                    <div class="tabular" style="font-size:18px; font-weight:600;">${totalBal} ₽</div>
                    <div class="muted" style="font-size:12px;">Осн. ${realBal} ₽ · Бонусы ${bonusBal} ₽</div>
                </div>
            </div>
            <div class="detail-grid" style="margin-top:4px;">
                <div class="detail-item"><span class="detail-label">Регистрация</span><span>${formatDate(details.created_at || userSummary.created_at)}</span></div>
                <div class="detail-item"><span class="detail-label">Последняя оплата</span><span>${details.last_payment_at ? formatDate(details.last_payment_at) : "—"}</span></div>
                <div class="detail-item"><span class="detail-label">Пригласил</span><span>${escapeHtml(inviterStr)}</span></div>
            </div>
        </section>

        ${
            debt > 0
                ? `
        <section class="danger-card">
            <div class="row">
                <div>
                    <h2 class="danger-card-title">Долг: ${debt} ₽</h2>
                    ${
                        details.financial_block_reason
                            ? `<div class="meta-text" style="margin-top:4px;">${escapeHtml(details.financial_block_reason)}</div>`
                            : ""
                    }
                </div>
                <button id="detForgiveDebtBtn" type="button" class="btn btn-sm btn-danger-filled" style="flex-shrink:0;">Погасить долг</button>
            </div>
        </section>`
                : ""
        }

        <section class="card">
            <div class="row">
                <h2 class="card-title">Подписки</h2>
                <button id="detResyncUserBtn" type="button" class="btn btn-sm">Пересинхронизировать узлы</button>
            </div>

            <div class="user-sub-box">
                <div class="row">
                    <div style="min-width:0; flex:1;">
                        <div style="font-weight:600; font-size:13px; display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                            <span>Стандарт</span>
                            ${stdExpired ? `<span class="chip chip-err">Истекла</span>` : stdInfo && !stdInfo.is_active ? `<span class="chip chip-warn">Отключена</span>` : ""}
                        </div>
                        <div class="muted" style="font-size:12px;">${escapeHtml(stdSummary)}</div>
                        <div class="meta-text tabular" style="margin-top:2px;">Трафик: ${formatBytesGb(details.monthly_vless_bytes)} ГБ за мес · ${formatBytesGb(details.total_traffic_bytes)} ГБ всего</div>
                    </div>
                    <button id="toggleStdEditBtn" type="button" class="btn btn-sm" aria-expanded="false" aria-controls="stdEditForm">Изменить</button>
                </div>
                <form id="stdEditForm" class="field" style="gap:8px; margin-top:4px;" hidden>
                    <div class="grid-2">
                        <div class="field">
                            <label for="stdDaysInput">Срок (дней, 0 = лимит)</label>
                            <input id="stdDaysInput" name="days" class="input tabular" type="number" min="-3650" max="3650" step="1" value="30">
                        </div>
                        <div class="field">
                            <label for="stdLimitInput">Устройств (2..10)</label>
                            <input id="stdLimitInput" name="device_limit" class="input tabular" type="number" min="2" max="10" step="1" value="${Math.min(10, Math.max(2, stdLimit))}">
                        </div>
                    </div>
                    <div class="filter-pills">
                        <button type="button" class="pill-btn" data-std-days="0">0</button>
                        <button type="button" class="pill-btn" data-std-days="7">+7 дн.</button>
                        <button type="button" class="pill-btn" data-std-days="30">+30 дн.</button>
                        <button type="button" class="pill-btn" data-std-days="90">+90 дн.</button>
                        <button type="button" class="pill-btn" data-std-days="-7">-7 дн.</button>
                    </div>
                    <label for="stdNotifyInput" class="toggle-row">
                        <span class="toggle-label">Уведомить пользователя</span>
                        <span class="switch">
                            <input id="stdNotifyInput" name="notify_user" type="checkbox">
                            <span class="switch-track"></span>
                        </span>
                    </label>
                    <button type="submit" class="btn btn-primary btn-sm">Сохранить Стандарт</button>
                </form>
            </div>

            <div class="user-sub-box">
                <div class="row">
                    <div style="min-width:0; flex:1;">
                        <div style="font-weight:600; font-size:13px;">Белый интернет</div>
                        <div class="muted" style="font-size:12px;">${escapeHtml(wiSummary)}</div>
                    </div>
                    <div class="btn-group">
                        <button id="toggleWiEditBtn" type="button" class="btn btn-sm" aria-expanded="false" aria-controls="wiEditForm">Срок</button>
                        <button id="toggleWiTrafficBtn" type="button" class="btn btn-sm" aria-expanded="false" aria-controls="wiTrafficForm">Трафик</button>
                    </div>
                </div>
                ${_renderWiDiagnosticsHtml(wi)}

                <form id="wiEditForm" class="field" style="gap:8px; margin-top:4px;" hidden>
                    <div class="grid-2">
                        <div class="field">
                            <label for="wiDaysInput">Срок (дней, 0 = слоты)</label>
                            <input id="wiDaysInput" name="days" class="input tabular" type="number" min="-3650" max="3650" step="1" value="30">
                        </div>
                        <div class="field">
                            <label for="wiLimitInput">Слотов (1..3)</label>
                            <input id="wiLimitInput" name="device_limit" class="input tabular" type="number" min="1" max="3" step="1" value="${Math.min(3, Math.max(1, wiLimit))}">
                        </div>
                    </div>
                    <div class="filter-pills">
                        <button type="button" class="pill-btn" data-wi-days="0">0</button>
                        <button type="button" class="pill-btn" data-wi-days="7">+7 дн.</button>
                        <button type="button" class="pill-btn" data-wi-days="30">+30 дн.</button>
                        <button type="button" class="pill-btn" data-wi-days="-7">-7 дн.</button>
                    </div>
                    <label for="wiNotifyInput" class="toggle-row">
                        <span class="toggle-label">Уведомить пользователя</span>
                        <span class="switch">
                            <input id="wiNotifyInput" name="notify_user" type="checkbox">
                            <span class="switch-track"></span>
                        </span>
                    </label>
                    <button type="submit" class="btn btn-primary btn-sm">Сохранить Белый интернет</button>
                </form>

                <form id="wiTrafficForm" class="field" style="gap:8px; margin-top:4px;" hidden>
                    <div class="field">
                        <label for="wiTrafficActionSelect">Операция с трафиком</label>
                        <select id="wiTrafficActionSelect" name="traffic_action" class="select">
                            <option value="add_gb">Добавить дополнительный трафик (+ГБ)</option>
                            <option value="reset_used">Сбросить использованный трафик</option>
                            <option value="set_base_gb">Установить базовую квоту (ГБ)</option>
                        </select>
                    </div>
                    <div class="field" id="wiGbField">
                        <label for="wiGbInput">Объём (ГБ)</label>
                        <input id="wiGbInput" name="gb" class="input tabular" type="number" min="1" max="5000" step="1" value="50">
                    </div>
                    <label for="wiTrafficNotifyInput" class="toggle-row">
                        <span class="toggle-label">Уведомить пользователя</span>
                        <span class="switch">
                            <input id="wiTrafficNotifyInput" name="notify_user" type="checkbox">
                            <span class="switch-track"></span>
                        </span>
                    </label>
                    <button type="submit" class="btn btn-primary btn-sm">Применить трафик</button>
                </form>
            </div>
        </section>

        <section class="card">
            <div class="row">
                <h2 class="card-title">Бонусный баланс</h2>
                <span class="muted">Коррекция</span>
            </div>
            <form id="userBonusForm" class="field" style="gap:8px;">
                <div class="filter-pills">
                    <button type="button" class="pill-btn" data-add="100">+100 ₽</button>
                    <button type="button" class="pill-btn" data-add="500">+500 ₽</button>
                    <button type="button" class="pill-btn" data-add="1000">+1000 ₽</button>
                    <button type="button" class="pill-btn" data-add="-100">-100 ₽</button>
                </div>
                <div class="row-nowrap">
                    <label for="userBonusAmount" class="visually-hidden">Сумма коррекции в рублях</label>
                    <input id="userBonusAmount" name="amount_rub" class="input tabular" type="number" min="-100000" max="100000" step="1" required value="" placeholder="Сумма ₽ (напр. 100 или -100)">
                    <button type="submit" class="btn btn-primary btn-sm" style="flex-shrink:0;">Применить</button>
                </div>
                <label for="userBonusNote" class="visually-hidden">Комментарий к коррекции</label>
                <input id="userBonusNote" name="note" class="input" type="text" maxlength="120" placeholder="Комментарий (необязательно)">
                <label for="userBonusNotify" class="toggle-row">
                    <span class="toggle-label">Уведомить в Telegram</span>
                    <span class="switch">
                        <input id="userBonusNotify" name="notify_user" type="checkbox" checked>
                        <span class="switch-track"></span>
                    </span>
                </label>
                <label id="userBonusSendNoteRow" for="userBonusSendNote" class="toggle-row">
                    <span class="toggle-label">Отправить комментарий пользователю</span>
                    <span class="switch">
                        <input id="userBonusSendNote" name="send_note_to_user" type="checkbox">
                        <span class="switch-track"></span>
                    </span>
                </label>
                <div class="meta-text">Если переключатель выключен, комментарий сохранится только в аудит-логе.</div>
            </form>
        </section>

        <section class="card">
            <h2 class="card-title">Написать в бот</h2>
            <form id="userDmForm" class="field" style="gap:8px;">
                <label for="userDmTextInput" class="visually-hidden">Сообщение пользователю</label>
                <textarea id="userDmTextInput" name="text" class="textarea" rows="3" maxlength="4096" required style="min-height:84px; font-family:inherit; font-size:13px;" placeholder="Сообщение пользователю (поддерживается HTML)"></textarea>
                <button type="submit" class="btn btn-primary btn-sm">Отправить в бот</button>
            </form>
        </section>

        ${details.subscription_url || details.subscription_backup_url ? `
        <section class="card">
            <h2 class="card-title">Ссылки подключения</h2>
            ${details.subscription_url ? `
            <div class="field" style="min-width:0;">
                <label for="detMainSubInput">Основная (CDN)</label>
                <div class="row-nowrap" style="min-width:0;">
                    <input id="detMainSubInput" class="input mono" style="min-width:0; text-overflow:ellipsis;" readonly value="${escapeHtml(details.subscription_url)}">
                    <button id="detCopySubBtn" type="button" class="btn btn-sm" style="flex-shrink:0;">Копировать</button>
                </div>
            </div>` : ""}
            ${details.subscription_backup_url ? `
            <div class="field" style="min-width:0;">
                <label for="detBackupSubInput">Резервная (Прямая)</label>
                <div class="row-nowrap" style="min-width:0;">
                    <input id="detBackupSubInput" class="input mono" style="min-width:0; text-overflow:ellipsis;" readonly value="${escapeHtml(details.subscription_backup_url)}">
                    <button id="detCopyBackupSubBtn" type="button" class="btn btn-sm" style="flex-shrink:0;">Копировать</button>
                </div>
            </div>` : ""}
        </section>` : ""}

        <section class="card">
            <div class="row">
                <h2 class="card-title">Устройства (${devices.length})</h2>
            </div>
            <div id="detDevicesList" class="field" style="gap:8px;">
                ${_renderDevicesHtml(devices)}
            </div>
        </section>

        <section class="card">
            <h2 class="card-title">Ограничения</h2>
            ${
                details.financial_block_reason
                    ? `<div class="text-err" style="font-size:12px; padding:8px 12px; border-radius:8px; background:var(--accent-red-soft); border:1px solid rgba(248,113,113,0.28);">Причина блокировки: ${escapeHtml(details.financial_block_reason)}</div>`
                    : ""
            }
            <form id="userRestrictionsForm" class="field" style="gap:8px;">
                <label for="restrHoldInput" class="toggle-row">
                    <span class="toggle-label">Финансовая блокировка</span>
                    <span class="switch">
                        <input id="restrHoldInput" name="financial_hold" type="checkbox" ${details.financial_hold ? "checked" : ""}>
                        <span class="switch-track"></span>
                    </span>
                </label>
                <label for="restrTopupInput" class="toggle-row">
                    <span class="toggle-label">Блокировка пополнений</span>
                    <span class="switch">
                        <input id="restrTopupInput" name="topup_blocked" type="checkbox" ${details.topup_blocked ? "checked" : ""}>
                        <span class="switch-track"></span>
                    </span>
                </label>
                <label for="restrNoteInput" class="visually-hidden">Причина или комментарий к ограничениям</label>
                <input id="restrNoteInput" name="note" class="input" type="text" maxlength="120" placeholder="Причина / комментарий (необязательно)">
                <button type="submit" class="btn btn-sm">Сохранить ограничения</button>
            </form>
        </section>

        <section class="card">
            <div class="row">
                <h2 class="card-title">История</h2>
            </div>
            <div class="seg-group" id="userHistoryTabs" role="tablist" aria-label="История операций">
                <button type="button" id="htab-orders" role="tab" aria-selected="${activeHistoryTab === "orders" ? "true" : "false"}" aria-controls="userHistoryList" tabindex="${activeHistoryTab === "orders" ? "0" : "-1"}" class="seg-btn ${activeHistoryTab === "orders" ? "active" : ""}" data-htab="orders">Заказы</button>
                <button type="button" id="htab-ledger" role="tab" aria-selected="${activeHistoryTab === "ledger" ? "true" : "false"}" aria-controls="userHistoryList" tabindex="${activeHistoryTab === "ledger" ? "0" : "-1"}" class="seg-btn ${activeHistoryTab === "ledger" ? "active" : ""}" data-htab="ledger">Баланс</button>
                <button type="button" id="htab-devices" role="tab" aria-selected="${activeHistoryTab === "devices" ? "true" : "false"}" aria-controls="userHistoryList" tabindex="${activeHistoryTab === "devices" ? "0" : "-1"}" class="seg-btn ${activeHistoryTab === "devices" ? "active" : ""}" data-htab="devices">Устройства</button>
                <button type="button" id="htab-audit" role="tab" aria-selected="${activeHistoryTab === "audit" ? "true" : "false"}" aria-controls="userHistoryList" tabindex="${activeHistoryTab === "audit" ? "0" : "-1"}" class="seg-btn ${activeHistoryTab === "audit" ? "active" : ""}" data-htab="audit">Админ-лог</button>
            </div>
            <div id="userHistoryList" role="tabpanel" aria-labelledby="htab-${activeHistoryTab}" class="field" style="gap:8px;">
                ${renderHistoryTabContent(activeHistoryTab, details)}
            </div>
        </section>

        <section class="danger-card">
            <h2 class="danger-card-title">Опасная зона</h2>
            <label for="dangerNotifyUser" class="toggle-row">
                <span class="toggle-label">Уведомить пользователя</span>
                <span class="switch">
                    <input id="dangerNotifyUser" type="checkbox">
                    <span class="switch-track"></span>
                </span>
            </label>
            <div class="actions-row">
                <button id="detResetHwidBtn" type="button" class="btn btn-sm" ${devices.length === 0 ? "disabled" : ""}>Сбросить все HWID</button>
                <button id="detRotateTokenBtn" type="button" class="btn btn-sm btn-danger">Перевыпустить токен</button>
            </div>
        </section>
    `;

    const bonusForm = root.querySelector("#userBonusForm");
    const stdForm = root.querySelector("#stdEditForm");
    const wiForm = root.querySelector("#wiEditForm");
    const wiTrafficForm = root.querySelector("#wiTrafficForm");
    const trafficSelect = wiTrafficForm.querySelector("select[name='traffic_action']");
    const wiGbField = wiTrafficForm.querySelector("#wiGbField");
    const dmForm = root.querySelector("#userDmForm");
    const restrForm = root.querySelector("#userRestrictionsForm");
    const origFinancialHold = Boolean(details.financial_hold);
    const origTopupBlocked = Boolean(details.topup_blocked);

    const bonusNotifyInput = bonusForm.querySelector("input[name='notify_user']");
    const bonusSendNoteInput = bonusForm.querySelector("input[name='send_note_to_user']");
    const bonusSendNoteRow = bonusForm.querySelector("#userBonusSendNoteRow");
    const syncBonusSendNoteState = () => {
        if (!bonusNotifyInput || !bonusSendNoteInput) return;
        const notifyOn = Boolean(bonusNotifyInput.checked);
        bonusSendNoteInput.disabled = !notifyOn;
        if (!notifyOn) {
            bonusSendNoteInput.checked = false;
        }
        if (bonusSendNoteRow) {
            bonusSendNoteRow.style.opacity = notifyOn ? "" : "0.5";
            bonusSendNoteRow.style.pointerEvents = notifyOn ? "" : "none";
        }
    };

    if (drafts) {
        if (drafts.dmText != null) {
            const dmInput = dmForm.querySelector("textarea[name='text']");
            if (dmInput) dmInput.value = drafts.dmText;
        }
        if (drafts.bonus) {
            const amtEl = bonusForm.querySelector("input[name='amount_rub']");
            const noteEl = bonusForm.querySelector("input[name='note']");
            if (amtEl && drafts.bonus.amount != null) amtEl.value = drafts.bonus.amount;
            if (noteEl && drafts.bonus.note != null) noteEl.value = drafts.bonus.note;
            if (bonusNotifyInput && drafts.bonus.notify != null) bonusNotifyInput.checked = drafts.bonus.notify;
            if (bonusSendNoteInput && drafts.bonus.sendNote != null) bonusSendNoteInput.checked = drafts.bonus.sendNote;
        }
        if (drafts.std) {
            stdForm.hidden = !drafts.std.open;
            const daysEl = stdForm.querySelector("input[name='days']");
            const limitEl = stdForm.querySelector("input[name='device_limit']");
            const notifyEl = stdForm.querySelector("input[name='notify_user']");
            if (daysEl && drafts.std.days != null) daysEl.value = drafts.std.days;
            if (limitEl && drafts.std.deviceLimit != null) limitEl.value = drafts.std.deviceLimit;
            if (notifyEl && drafts.std.notify != null) notifyEl.checked = drafts.std.notify;
        }
        if (drafts.wi) {
            wiForm.hidden = !drafts.wi.open;
            const daysEl = wiForm.querySelector("input[name='days']");
            const limitEl = wiForm.querySelector("input[name='device_limit']");
            const notifyEl = wiForm.querySelector("input[name='notify_user']");
            if (daysEl && drafts.wi.days != null) daysEl.value = drafts.wi.days;
            if (limitEl && drafts.wi.deviceLimit != null) limitEl.value = drafts.wi.deviceLimit;
            if (notifyEl && drafts.wi.notify != null) notifyEl.checked = drafts.wi.notify;
        }
        if (drafts.wiTraffic) {
            wiTrafficForm.hidden = !drafts.wiTraffic.open;
            const gbEl = wiTrafficForm.querySelector("input[name='gb']");
            const notifyEl = wiTrafficForm.querySelector("input[name='notify_user']");
            if (trafficSelect && drafts.wiTraffic.action) {
                trafficSelect.value = drafts.wiTraffic.action;
                wiGbField.hidden = trafficSelect.value === "reset_used";
            }
            if (gbEl && drafts.wiTraffic.gb != null) gbEl.value = drafts.wiTraffic.gb;
            if (notifyEl && drafts.wiTraffic.notify != null) notifyEl.checked = drafts.wiTraffic.notify;
        }
        if (drafts.restr) {
            const holdEl = restrForm.querySelector("input[name='financial_hold']");
            const topupEl = restrForm.querySelector("input[name='topup_blocked']");
            const noteEl = restrForm.querySelector("input[name='note']");
            if (holdEl && drafts.restr.holdDirty) holdEl.checked = drafts.restr.hold;
            if (topupEl && drafts.restr.topupDirty) topupEl.checked = drafts.restr.topup;
            if (noteEl && drafts.restr.note != null) noteEl.value = drafts.restr.note;
        }
        if (drafts.dangerNotify != null) {
            const dangerEl = root.querySelector("#dangerNotifyUser");
            if (dangerEl) dangerEl.checked = drafts.dangerNotify;
        }
    }
    syncBonusSendNoteState();
    bonusNotifyInput?.addEventListener("change", syncBonusSendNoteState);

    root.querySelector("#toggleStdEditBtn")?.setAttribute("aria-expanded", String(!stdForm.hidden));
    root.querySelector("#toggleWiEditBtn")?.setAttribute("aria-expanded", String(!wiForm.hidden));
    root.querySelector("#toggleWiTrafficBtn")?.setAttribute("aria-expanded", String(!wiTrafficForm.hidden));

    if (preserveScroll && savedScrollY !== null) {
        requestAnimationFrame(() => window.scrollTo(0, savedScrollY));
    }

    if (options && options.focusSelector) {
        const modalOpen = Boolean(document.querySelector("dialog.modal-dialog[open], .modal-backdrop:not([hidden])"));
        if (!modalOpen) {
            const target = root.querySelector(options.focusSelector) || root.querySelector("#userDetailBackBtn");
            target?.focus({ preventScroll: true });
        }
    }

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

    const collectDrafts = (clearDraft = null) => {
        const holdChecked = Boolean(restrForm.querySelector("input[name='financial_hold']")?.checked);
        const topupChecked = Boolean(restrForm.querySelector("input[name='topup_blocked']")?.checked);
        return {
            activeHistoryTab,
            dmText: dmForm.querySelector("textarea[name='text']")?.value ?? "",
            bonus:
                clearDraft === "bonus"
                    ? null
                    : {
                          amount: bonusForm.querySelector("input[name='amount_rub']")?.value ?? "",
                          note: bonusForm.querySelector("input[name='note']")?.value ?? "",
                          notify: Boolean(bonusForm.querySelector("input[name='notify_user']")?.checked),
                          sendNote: Boolean(bonusForm.querySelector("input[name='send_note_to_user']")?.checked),
                      },
            std:
                clearDraft === "std"
                    ? null
                    : {
                          open: !stdForm.hidden,
                          days: stdForm.querySelector("input[name='days']")?.value ?? "30",
                          deviceLimit: stdForm.querySelector("input[name='device_limit']")?.value ?? String(stdLimit),
                          notify: Boolean(stdForm.querySelector("input[name='notify_user']")?.checked),
                      },
            wi:
                clearDraft === "wi"
                    ? null
                    : {
                          open: !wiForm.hidden,
                          days: wiForm.querySelector("input[name='days']")?.value ?? "30",
                          deviceLimit: wiForm.querySelector("input[name='device_limit']")?.value ?? String(wiLimit),
                          notify: Boolean(wiForm.querySelector("input[name='notify_user']")?.checked),
                      },
            wiTraffic:
                clearDraft === "wiTraffic"
                    ? null
                    : {
                          open: !wiTrafficForm.hidden,
                          action: trafficSelect?.value ?? "add_gb",
                          gb: wiTrafficForm.querySelector("input[name='gb']")?.value ?? "50",
                          notify: Boolean(wiTrafficForm.querySelector("input[name='notify_user']")?.checked),
                      },
            restr:
                clearDraft === "restr"
                    ? null
                    : {
                          holdDirty: holdChecked !== origFinancialHold,
                          hold: holdChecked,
                          topupDirty: topupChecked !== origTopupBlocked,
                          topup: topupChecked,
                          note: restrForm.querySelector("input[name='note']")?.value ?? "",
                      },
            dangerNotify: Boolean(root.querySelector("#dangerNotifyUser")?.checked),
        };
    };

    const schedulePostModalFocus = (selector) => {
        setTimeout(() => {
            if (document.querySelector("dialog.modal-dialog[open], .modal-backdrop:not([hidden])")) return;
            if (!document.activeElement || document.activeElement === document.body || !root.contains(document.activeElement)) {
                const target = (selector && root.querySelector(selector)) || root.querySelector("#userDetailBackBtn");
                target?.focus({ preventScroll: true });
            }
        }, 0);
    };

    const refreshAll = async ({ clearDraft = null, focusSelector = null } = {}) => {
        const currentScrollY = window.scrollY;
        const capturedDrafts = collectDrafts(clearDraft);
        void onReloadUser?.();
        await renderUserDetailView(root, userSummary, onBack, onReloadUser, state, {
            preserveScroll: true,
            scrollY: currentScrollY,
            drafts: capturedDrafts,
            focusSelector,
        });
    };

    bindUserDetailActions({
        root,
        details,
        state,
        uname,
        debt,
        stdLimit,
        wiLimit,
        origFinancialHold,
        origTopupBlocked,
        bonusForm,
        stdForm,
        wiForm,
        wiTrafficForm,
        trafficSelect,
        wiGbField,
        dmForm,
        restrForm,
        getActiveHistoryTab: () => activeHistoryTab,
        setActiveHistoryTab: (nextTab) => {
            activeHistoryTab = nextTab;
        },
        refreshAll,
        schedulePostModalFocus,
    });
}
