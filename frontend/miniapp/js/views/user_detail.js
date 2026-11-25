import { api } from "../api.js?v=2.8.0";
import {
    ORDER_KIND_MAP,
    ORDER_STATUS_MAP,
    formatAuditAction,
    formatLedgerEntryType,
    formatMetadataFlag,
    formatWiNodeSyncStatus,
    formatWiProvisioningStatus,
} from "../admin_labels.js?v=2.8.0";
import {
    copyText,
    escapeHtml,
    formatBytesGb,
    formatDate,
    formatRelativeTime,
    showConfirmModal,
    showToast,
    withButtonLoading,
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

function _formatShortHwid(hwid) {
    const raw = String(hwid || "");
    if (raw.length <= 20) return raw;
    return `${raw.slice(0, 10)}…${raw.slice(-8)}`;
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
        <div class="detail-grid" style="padding-top:8px; border-top:1px solid var(--border-subtle);">
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

function _renderHistoryTabContent(tab, details) {
    if (tab === "orders") {
        const orders = details.recent_orders || [];
        if (!orders.length) {
            return `<div class="muted" style="text-align:center; padding:16px;">Заказов нет</div>`;
        }
        return orders
            .map((o) => {
                const stInfo = ORDER_STATUS_MAP[o.status] || {
                    text: o.status || "—",
                    cls: "chip-muted",
                };
                const kindLabel = ORDER_KIND_MAP[o.kind] || o.kind || "";
                const flags = Array.isArray(o.metadata_flags) ? o.metadata_flags : [];
                const flagsHtml = flags
                    .map((flag) => {
                        const info = formatMetadataFlag(flag);
                        return `<span class="chip ${info.cls}">${escapeHtml(info.text)}</span>`;
                    })
                    .join("");
                return `
                    <div class="item-row">
                        <div class="row">
                            <span style="font-weight:600; font-size:13px;">${escapeHtml(kindLabel)}${o.description ? ` · ${escapeHtml(o.description)}` : ""}</span>
                            <span class="tabular" style="font-weight:600;">${o.amount_rub} ₽</span>
                        </div>
                        <div class="history-row-meta">
                            <span>${formatDate(o.created_at)}</span>
                            <span class="chip ${stInfo.cls}">${escapeHtml(stInfo.text)}</span>
                        </div>
                        <div class="filter-pills" style="margin-top:4px;">
                            <button type="button" class="mono-copy" data-copy-val="${escapeHtml(o.id)}" data-copy-msg="ID заказа скопирован">ID: ${escapeHtml(String(o.id).slice(0, 8))}…</button>
                            ${
                                o.external_id
                                    ? `<button type="button" class="mono-copy" data-copy-val="${escapeHtml(o.external_id)}" data-copy-msg="ID ЮKassa скопирован">ЮKassa: ${escapeHtml(String(o.external_id).slice(0, 12))}…</button>`
                                    : ""
                            }
                            ${flagsHtml}
                        </div>
                    </div>
                `;
            })
            .join("");
    }

    if (tab === "ledger") {
        const entries = details.recent_ledger || [];
        if (!entries.length) {
            return `<div class="muted" style="text-align:center; padding:16px;">Операций по балансу нет</div>`;
        }
        return entries
            .map((entry) => {
                const amt = Number(entry.amount || 0);
                const signStr = amt > 0 ? `+${amt} ₽` : `${amt} ₽`;
                const amtCls = amt > 0 ? "amount-pos" : amt < 0 ? "amount-err" : "tabular";
                const bucketLabel = entry.bucket === "bonus" ? "бонус" : "основной";
                const typeLabel = formatLedgerEntryType(entry.entry_type);
                return `
                    <div class="item-row">
                        <div class="row">
                            <span style="font-weight:600; font-size:13px;">${escapeHtml(typeLabel)} <span class="chip chip-muted">${bucketLabel}</span></span>
                            <span class="${amtCls}" style="font-weight:600;">${escapeHtml(signStr)}</span>
                        </div>
                        ${
                            entry.description
                                ? `<div class="meta-text">${escapeHtml(entry.description)}</div>`
                                : ""
                        }
                        <div class="history-row-meta">
                            <span>${formatDate(entry.created_at)}</span>
                        </div>
                    </div>
                `;
            })
            .join("");
    }

    if (tab === "devices") {
        const events = details.recent_device_events || [];
        if (!events.length) {
            return `<div class="muted" style="text-align:center; padding:16px;">Событий устройств нет</div>`;
        }
        return events
            .map((ev) => {
                const title = [ev.device_model, ev.platform].filter(Boolean).join(" · ") || "Устройство";
                return `
                    <div class="item-row">
                        <div class="row">
                            <span style="font-weight:600; font-size:13px;">${escapeHtml(title)}</span>
                            <span class="meta-text">${formatDate(ev.created_at)}</span>
                        </div>
                        ${
                            ev.hwid
                                ? `<div style="margin-top:4px;"><button type="button" class="mono-copy" data-copy-val="${escapeHtml(ev.hwid)}" data-copy-msg="HWID скопирован">${escapeHtml(_formatShortHwid(ev.hwid))}</button></div>`
                                : ""
                        }
                    </div>
                `;
            })
            .join("");
    }

    const audits = details.recent_audit || [];
    if (!audits.length) {
        return `<div class="muted" style="text-align:center; padding:16px;">Записей аудита нет</div>`;
    }
    return audits
        .map((a) => {
            const actionLabel = formatAuditAction(a.action);
            const detailsStr =
                typeof a.details === "string"
                    ? a.details
                    : a.details
                      ? JSON.stringify(a.details)
                      : "";
            return `
                <div class="item-row">
                    <div class="row">
                        <span class="chip chip-muted">${escapeHtml(actionLabel)}</span>
                        <span class="meta-text">${formatDate(a.created_at)}</span>
                    </div>
                    ${
                        detailsStr
                            ? `<div class="mono meta-text" style="white-space:pre-wrap; word-break:break-word;">${escapeHtml(detailsStr)}</div>`
                            : ""
                    }
                </div>
            `;
        })
        .join("");
}

export async function renderUserDetailView(root, userSummary, onBack, onReloadUser, state = null, options = {}) {
    const reqId = ++_detailReqId;
    const preserveScroll = Boolean(options && options.preserveScroll);
    const savedScrollY = options && Number.isFinite(options.scrollY) ? options.scrollY : null;
    const drafts = (options && options.drafts) || null;
    const fallbackName = _formatDisplayName(userSummary);

    if (!preserveScroll) {
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
    try {
        const res = await api.getUserDetails(userSummary.id);
        details = res.user;
    } catch (err) {
        if (reqId !== _detailReqId) return;
        if (state && state.selectedUserId !== userSummary.id) return;
        if (!root.isConnected || (sentinelBtn && !root.contains(sentinelBtn))) return;
        if (err && err.code === "2fa_required") return;
        if (preserveScroll) {
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

    const stdInfo = details.standard || null;
    const stdEndTs = details.standard_end ? new Date(details.standard_end).getTime() : 0;
    const stdExpired = Boolean(details.standard_end && stdEndTs <= Date.now());
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
    const wiIsExpiredByDate = Boolean(wi && wi.expires_at && new Date(wi.expires_at).getTime() <= Date.now());
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
                <div>
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
                <div style="text-align:right;">
                    <div class="tabular" style="font-size:18px; font-weight:600;">${totalBal} ₽</div>
                    <div class="muted" style="font-size:12px;">Осн. ${realBal} ₽ · Бонусы ${bonusBal} ₽</div>
                </div>
            </div>
            <div class="detail-grid" style="padding-top:8px; border-top:1px solid var(--border-subtle);">
                <div class="detail-item"><span class="detail-label">Регистрация</span><span>${formatDate(details.created_at || userSummary.created_at)}</span></div>
                <div class="detail-item"><span class="detail-label">Последняя оплата</span><span>${details.last_payment_at ? formatDate(details.last_payment_at) : "—"}</span></div>
                <div class="detail-item"><span class="detail-label">Пригласил</span><span>${escapeHtml(inviterStr)}</span></div>
                <div class="detail-item"><span class="detail-label">Трафик Стандарт</span><span class="tabular">${formatBytesGb(details.monthly_vless_bytes)} ГБ / всего ${formatBytesGb(details.total_traffic_bytes)} ГБ</span></div>
            </div>
            <div class="actions-row" style="padding-top:8px; border-top:1px solid var(--border-subtle);">
                <button id="detResyncUserBtn" type="button" class="btn btn-sm">Пересинхронизировать узлы</button>
                <button id="detOpenFinancesBtn" type="button" class="btn btn-sm">Заказы в Финансах</button>
                <button id="detOpenAuditBtn" type="button" class="btn btn-sm">Аудит</button>
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
                <label for="userBonusSendNote" class="toggle-row">
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
            <h2 class="card-title">Подписки</h2>

            <div class="user-sub-box">
                <div class="row">
                    <div>
                        <div style="font-weight:600; font-size:13px; display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                            <span>Стандарт</span>
                            ${stdExpired ? `<span class="chip chip-err">Истекла</span>` : stdInfo && !stdInfo.is_active ? `<span class="chip chip-warn">Отключена</span>` : ""}
                        </div>
                        <div class="muted" style="font-size:12px;">${escapeHtml(stdSummary)}</div>
                    </div>
                    <button id="toggleStdEditBtn" type="button" class="btn btn-sm">Изменить</button>
                </div>
                <form id="stdEditForm" class="field" style="gap:8px; padding-top:8px; border-top:1px solid var(--border-subtle);" hidden>
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
                    <div>
                        <div style="font-weight:600; font-size:13px;">Белый интернет</div>
                        <div class="muted" style="font-size:12px;">${escapeHtml(wiSummary)}</div>
                    </div>
                    <div class="btn-group">
                        <button id="toggleWiEditBtn" type="button" class="btn btn-sm">Срок</button>
                        <button id="toggleWiTrafficBtn" type="button" class="btn btn-sm">Трафик</button>
                    </div>
                </div>
                ${_renderWiDiagnosticsHtml(wi)}

                <form id="wiEditForm" class="field" style="gap:8px; padding-top:8px; border-top:1px solid var(--border-subtle);" hidden>
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

                <form id="wiTrafficForm" class="field" style="gap:8px; padding-top:8px; border-top:1px solid var(--border-subtle);" hidden>
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
                ${_renderHistoryTabContent(activeHistoryTab, details)}
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

    if (drafts) {
        if (drafts.dmText != null) {
            const dmInput = dmForm.querySelector("textarea[name='text']");
            if (dmInput) dmInput.value = drafts.dmText;
        }
        if (drafts.bonus) {
            const amtEl = bonusForm.querySelector("input[name='amount_rub']");
            const noteEl = bonusForm.querySelector("input[name='note']");
            const notifyEl = bonusForm.querySelector("input[name='notify_user']");
            const sendNoteEl = bonusForm.querySelector("input[name='send_note_to_user']");
            if (amtEl && drafts.bonus.amount != null) amtEl.value = drafts.bonus.amount;
            if (noteEl && drafts.bonus.note != null) noteEl.value = drafts.bonus.note;
            if (notifyEl && drafts.bonus.notify != null) notifyEl.checked = drafts.bonus.notify;
            if (sendNoteEl && drafts.bonus.sendNote != null) sendNoteEl.checked = drafts.bonus.sendNote;
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

    if (preserveScroll && savedScrollY !== null) {
        requestAnimationFrame(() => window.scrollTo(0, savedScrollY));
    }

    if (options && options.focusSelector) {
        const modalOpen = Boolean(document.querySelector(".modal-backdrop:not([hidden])"));
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
            if (document.querySelector(".modal-backdrop:not([hidden])")) return;
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

    const resyncBtn = root.querySelector("#detResyncUserBtn");
    resyncBtn.addEventListener("click", () => {
        withButtonLoading(resyncBtn, "Синхронизация...", async () => {
            try {
                const res = await api.resyncUser(details.id);
                showToast(res.message || "Синхронизация запущена");
                await refreshAll({ focusSelector: "#detResyncUserBtn" });
            } catch (err) {
                showToast(err.message || "Ошибка пересинхронизации", "error");
            }
        });
    });

    root.querySelector("#detOpenFinancesBtn").addEventListener("click", () => {
        state?.onOpenFinances?.(String(details.telegram_id));
    });

    root.querySelector("#detOpenAuditBtn").addEventListener("click", () => {
        state?.onOpenAudit?.(String(details.id), "user");
    });

    const forgiveDebtBtn = root.querySelector("#detForgiveDebtBtn");
    if (forgiveDebtBtn) {
        forgiveDebtBtn.addEventListener("click", () => {
            showConfirmModal({
                title: "Погасить задолженность?",
                message: `Списать долг ${debt} ₽ пользователю ${uname}?`,
                confirmText: "Погасить долг",
                cancelText: "Отмена",
                isDestructive: true,
                restoreFocusSelector: "#userDetailBackBtn",
                onConfirm: async () => {
                    const res = await api.adjustUser(details.id, { action: "forgive_debt" });
                    showToast(res.message || "Задолженность списана");
                    await refreshAll();
                    schedulePostModalFocus("#userDetailBackBtn");
                },
            });
        });
    }

    const bonusAmountInput = bonusForm.querySelector("input[name='amount_rub']");
    bonusAmountInput.addEventListener("input", () => bonusAmountInput.removeAttribute("aria-invalid"));
    bonusForm.querySelectorAll("[data-add]").forEach((b) => {
        b.addEventListener("click", () => {
            bonusAmountInput.value = b.dataset.add;
            bonusAmountInput.removeAttribute("aria-invalid");
        });
    });
    bonusForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const rawAmount = bonusAmountInput.value.trim();
        const amount = /^-?\d+$/.test(rawAmount) ? Number(rawAmount) : NaN;
        if (!Number.isInteger(amount) || amount === 0 || amount < -100000 || amount > 100000) {
            bonusAmountInput.setAttribute("aria-invalid", "true");
            bonusAmountInput.focus();
            showToast("Укажите ненулевое целое число от -100000 до 100000 ₽", "error");
            return;
        }
        const note = bonusForm.querySelector("input[name='note']").value.trim() || null;
        const notifyUser = Boolean(bonusForm.querySelector("input[name='notify_user']")?.checked);
        const sendNoteToUser = Boolean(bonusForm.querySelector("input[name='send_note_to_user']")?.checked);
        const isNegative = amount < 0;
        showConfirmModal({
            title: isNegative ? "Списать средства с баланса?" : "Начислить бонус на баланс?",
            message: `${isNegative ? "Списание" : "Начисление"}: ${amount > 0 ? `+${amount}` : amount} ₽ для ${uname}.${notifyUser ? " Пользователь получит уведомление в Telegram." : ""}`,
            confirmText: isNegative ? "Списать" : "Начислить",
            cancelText: "Отмена",
            isDestructive: isNegative,
            restoreFocusSelector: "#userBonusAmount",
            onConfirm: async () => {
                const res = await api.adjustUser(details.id, {
                    action: "balance",
                    amount_rub: amount,
                    note,
                    notify_user: notifyUser,
                    send_note_to_user: sendNoteToUser,
                });
                showToast(res.message || "Баланс обновлён");
                await refreshAll({ clearDraft: "bonus" });
                schedulePostModalFocus("#userBonusAmount");
            },
        });
    });

    const stdDaysInput = stdForm.querySelector("input[name='days']");
    const stdLimitInput = stdForm.querySelector("input[name='device_limit']");
    stdDaysInput.addEventListener("input", () => stdDaysInput.removeAttribute("aria-invalid"));
    stdLimitInput.addEventListener("input", () => stdLimitInput.removeAttribute("aria-invalid"));
    root.querySelector("#toggleStdEditBtn").addEventListener("click", () => {
        stdForm.hidden = !stdForm.hidden;
    });
    stdForm.querySelectorAll("[data-std-days]").forEach((b) => {
        b.addEventListener("click", () => {
            stdDaysInput.value = b.dataset.stdDays;
            stdDaysInput.removeAttribute("aria-invalid");
        });
    });
    stdForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const rawDays = stdDaysInput.value.trim();
        const rawLimit = stdLimitInput.value.trim();
        if (rawDays !== "" && !/^-?\d+$/.test(rawDays)) {
            stdDaysInput.setAttribute("aria-invalid", "true");
            stdDaysInput.focus();
            showToast("Укажите целое количество дней (от -3650 до 3650)", "error");
            return;
        }
        const days = rawDays === "" ? 0 : Number(rawDays);
        if (!Number.isInteger(days) || days < -3650 || days > 3650) {
            stdDaysInput.setAttribute("aria-invalid", "true");
            stdDaysInput.focus();
            showToast("Укажите корректное количество дней (от -3650 до 3650)", "error");
            return;
        }
        const deviceLimit = /^\d+$/.test(rawLimit) ? Number(rawLimit) : NaN;
        if (!Number.isInteger(deviceLimit) || deviceLimit < 2 || deviceLimit > 10) {
            stdLimitInput.setAttribute("aria-invalid", "true");
            stdLimitInput.focus();
            showToast("Лимит устройств должен быть целым числом от 2 до 10", "error");
            return;
        }
        const limitChanged = deviceLimit !== Number(stdLimit);
        if (days === 0 && !limitChanged) {
            stdDaysInput.setAttribute("aria-invalid", "true");
            stdDaysInput.focus();
            showToast("Укажите ненулевое количество дней или измените лимит устройств", "error");
            return;
        }
        const notifyUser = Boolean(stdForm.querySelector("input[name='notify_user']")?.checked);
        const payload = { action: "grant_standard", days, notify_user: notifyUser };
        if (days >= 0 || limitChanged) {
            payload.device_limit = deviceLimit;
        }
        const execute = async (fromModal = false) => {
            const btn = stdForm.querySelector("button[type='submit']");
            await withButtonLoading(btn, "Сохранение...", async () => {
                const res = await api.adjustUser(details.id, payload);
                showToast(res.message || "Подписка Стандарт обновлена");
                await refreshAll({
                    clearDraft: "std",
                    focusSelector: fromModal ? null : "#toggleStdEditBtn",
                });
                if (fromModal) schedulePostModalFocus("#toggleStdEditBtn");
            });
        };

        if (days < 0) {
            showConfirmModal({
                title: "Сократить подписку Стандарт?",
                message: `Срок подписки Стандарт пользователя ${uname} будет уменьшен на ${Math.abs(days)} дн. Если срок истечёт, доступ будет отключён.`,
                confirmText: "Сократить",
                cancelText: "Отмена",
                isDestructive: true,
                restoreFocusSelector: "#toggleStdEditBtn",
                onConfirm: () => execute(true),
            });
            return;
        }

        try {
            await execute(false);
        } catch (err) {
            showToast(err.message || "Ошибка обновления подписки", "error");
        }
    });

    const wiDaysInput = wiForm.querySelector("input[name='days']");
    const wiLimitInput = wiForm.querySelector("input[name='device_limit']");
    wiDaysInput.addEventListener("input", () => wiDaysInput.removeAttribute("aria-invalid"));
    wiLimitInput.addEventListener("input", () => wiLimitInput.removeAttribute("aria-invalid"));
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
            wiDaysInput.value = b.dataset.wiDays;
            wiDaysInput.removeAttribute("aria-invalid");
        });
    });
    wiForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const rawDays = wiDaysInput.value.trim();
        const rawLimit = wiLimitInput.value.trim();
        if (rawDays !== "" && !/^-?\d+$/.test(rawDays)) {
            wiDaysInput.setAttribute("aria-invalid", "true");
            wiDaysInput.focus();
            showToast("Укажите целое количество дней (от -3650 до 3650)", "error");
            return;
        }
        const days = rawDays === "" ? 0 : Number(rawDays);
        if (!Number.isInteger(days) || days < -3650 || days > 3650) {
            wiDaysInput.setAttribute("aria-invalid", "true");
            wiDaysInput.focus();
            showToast("Укажите корректное количество дней (от -3650 до 3650)", "error");
            return;
        }
        const deviceLimit = /^\d+$/.test(rawLimit) ? Number(rawLimit) : NaN;
        if (!Number.isInteger(deviceLimit) || deviceLimit < 1 || deviceLimit > 3) {
            wiLimitInput.setAttribute("aria-invalid", "true");
            wiLimitInput.focus();
            showToast("Количество слотов должно быть целым числом от 1 до 3", "error");
            return;
        }
        const limitChanged = deviceLimit !== Number(wiLimit);
        if (days === 0 && !limitChanged) {
            wiDaysInput.setAttribute("aria-invalid", "true");
            wiDaysInput.focus();
            showToast("Укажите ненулевое количество дней или измените количество слотов", "error");
            return;
        }
        const notifyUser = Boolean(wiForm.querySelector("input[name='notify_user']")?.checked);
        const payload = { action: "grant_wi", days, notify_user: notifyUser };
        if (days >= 0 || limitChanged) {
            payload.device_limit = deviceLimit;
        }
        const execute = async (fromModal = false) => {
            const btn = wiForm.querySelector("button[type='submit']");
            await withButtonLoading(btn, "Сохранение...", async () => {
                const res = await api.adjustUser(details.id, payload);
                showToast(res.message || "Белый интернет обновлён");
                await refreshAll({
                    clearDraft: "wi",
                    focusSelector: fromModal ? null : "#toggleWiEditBtn",
                });
                if (fromModal) schedulePostModalFocus("#toggleWiEditBtn");
            });
        };

        if (days < 0) {
            showConfirmModal({
                title: "Сократить Белый интернет?",
                message: `Срок подписки «Белый интернет» пользователя ${uname} будет уменьшен на ${Math.abs(days)} дн. Если срок истечёт, доступ будет отозван.`,
                confirmText: "Сократить",
                cancelText: "Отмена",
                isDestructive: true,
                restoreFocusSelector: "#toggleWiEditBtn",
                onConfirm: () => execute(true),
            });
            return;
        }

        try {
            await execute(false);
        } catch (err) {
            showToast(err.message || "Ошибка обновления подписки", "error");
        }
    });

    const wiGbInput = wiTrafficForm.querySelector("input[name='gb']");
    wiGbInput.addEventListener("input", () => wiGbInput.removeAttribute("aria-invalid"));
    trafficSelect.addEventListener("change", () => {
        wiGbField.hidden = trafficSelect.value === "reset_used";
    });
    wiTrafficForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const notifyUser = Boolean(wiTrafficForm.querySelector("input[name='notify_user']")?.checked);
        const payload = {
            action: "wi_traffic",
            traffic_action: trafficSelect.value,
            notify_user: notifyUser,
        };
        if (payload.traffic_action !== "reset_used") {
            const rawGb = wiGbInput.value.trim();
            const gb = /^\d+$/.test(rawGb) ? Number(rawGb) : NaN;
            if (!Number.isInteger(gb) || gb < 1 || gb > 5000) {
                wiGbInput.setAttribute("aria-invalid", "true");
                wiGbInput.focus();
                showToast("Укажите целое число ГБ от 1 до 5000", "error");
                return;
            }
            payload.gb = gb;
        }
        const execute = async (fromModal = false) => {
            const btn = wiTrafficForm.querySelector("button[type='submit']");
            await withButtonLoading(btn, "Применение...", async () => {
                const res = await api.adjustUser(details.id, payload);
                showToast(res.message || "Трафик обновлён");
                await refreshAll({
                    clearDraft: "wiTraffic",
                    focusSelector: fromModal ? null : "#toggleWiTrafficBtn",
                });
                if (fromModal) schedulePostModalFocus("#toggleWiTrafficBtn");
            });
        };

        if (payload.traffic_action === "reset_used") {
            showConfirmModal({
                title: "Сбросить использованный трафик?",
                message: `Счётчик использованного трафика «Белый интернет» пользователя ${uname} будет обнулён.`,
                confirmText: "Сбросить",
                cancelText: "Отмена",
                isDestructive: true,
                restoreFocusSelector: "#toggleWiTrafficBtn",
                onConfirm: () => execute(true),
            });
            return;
        }

        try {
            await execute(false);
        } catch (err) {
            showToast(err.message || "Ошибка обновления трафика", "error");
        }
    });

    dmForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const text = dmForm.querySelector("textarea[name='text']").value.trim();
        if (!text) {
            showToast("Введите текст сообщения", "error");
            return;
        }
        const btn = dmForm.querySelector("button[type='submit']");
        try {
            await withButtonLoading(btn, "Отправка...", async () => {
                const res = await api.sendUserMessage(details.id, text);
                dmForm.reset();
                showToast(res.message || "Сообщение отправлено");
            });
        } catch (err) {
            showToast(err.message || "Ошибка отправки сообщения", "error");
        }
    });

    const devicesContainer = root.querySelector("#detDevicesList");
    devicesContainer.querySelectorAll("[data-copy-hwid]").forEach((btn) => {
        btn.addEventListener("click", () => {
            copyText(btn.dataset.copyHwid, "HWID скопирован");
        });
    });
    devicesContainer.querySelectorAll("[data-del-hwid]").forEach((btn) => {
        btn.addEventListener("click", () => {
            const hwid = btn.dataset.delHwid;
            showConfirmModal({
                title: "Удалить устройство?",
                message: `Устройство ${_formatShortHwid(hwid)} будет отвязано от аккаунта ${uname}.`,
                confirmText: "Удалить",
                cancelText: "Отмена",
                isDestructive: true,
                restoreFocusSelector: "#detDevicesList",
                onConfirm: async () => {
                    const res = await api.deleteUserDevice(details.id, hwid);
                    showToast(res.message || "Устройство удалено");
                    await refreshAll();
                    schedulePostModalFocus("#detDevicesList button, #detResetHwidBtn");
                },
            });
        });
    });

    restrForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const financialHold = Boolean(restrForm.querySelector("input[name='financial_hold']")?.checked);
        const topupBlocked = Boolean(restrForm.querySelector("input[name='topup_blocked']")?.checked);
        const note = restrForm.querySelector("input[name='note']")?.value.trim() || null;
        const holdChanged = financialHold !== origFinancialHold;
        const topupChanged = topupBlocked !== origTopupBlocked;
        if (!holdChanged && !topupChanged) {
            showToast("Ограничения не изменились", "warn");
            return;
        }
        const payload = { action: "restrictions", note };
        if (holdChanged) payload.financial_hold = financialHold;
        if (topupChanged) payload.topup_blocked = topupBlocked;
        const enablingRestriction = (holdChanged && financialHold) || (topupChanged && topupBlocked);
        showConfirmModal({
            title: "Сохранить ограничения?",
            message: `Применить настройки ограничений для ${uname}?${holdChanged && financialHold ? " Финансовая блокировка ограничит доступ." : ""}`,
            confirmText: "Сохранить",
            cancelText: "Отмена",
            isDestructive: enablingRestriction,
            restoreFocusSelector: "#restrHoldInput",
            onConfirm: async () => {
                const res = await api.adjustUser(details.id, payload);
                showToast(res.message || "Ограничения сохранены");
                await refreshAll({ clearDraft: "restr" });
                schedulePostModalFocus("#restrHoldInput");
            },
        });
    });

    const historyTabsEl = root.querySelector("#userHistoryTabs");
    const historyListEl = root.querySelector("#userHistoryList");
    const wireHistoryCopyButtons = () => {
        historyListEl.querySelectorAll("[data-copy-val]").forEach((btn) => {
            btn.addEventListener("click", () => {
                copyText(btn.dataset.copyVal, btn.dataset.copyMsg || "Скопировано");
            });
        });
    };
    wireHistoryCopyButtons();

    const historyTabButtons = Array.from(historyTabsEl.querySelectorAll("[data-htab]"));
    const activateHistoryTab = (nextTab, focusTab = false) => {
        activeHistoryTab = nextTab;
        historyTabButtons.forEach((b) => {
            const isActive = b.dataset.htab === activeHistoryTab;
            b.classList.toggle("active", isActive);
            b.setAttribute("aria-selected", isActive ? "true" : "false");
            b.setAttribute("tabindex", isActive ? "0" : "-1");
            if (isActive && focusTab) {
                b.focus({ preventScroll: true });
            }
        });
        historyListEl.setAttribute("aria-labelledby", `htab-${activeHistoryTab}`);
        historyListEl.innerHTML = _renderHistoryTabContent(activeHistoryTab, details);
        wireHistoryCopyButtons();
    };

    historyTabButtons.forEach((btn, idx) => {
        btn.addEventListener("click", () => {
            activateHistoryTab(btn.dataset.htab, false);
        });
        btn.addEventListener("keydown", (e) => {
            let targetIdx = null;
            if (e.key === "ArrowRight") {
                targetIdx = (idx + 1) % historyTabButtons.length;
            } else if (e.key === "ArrowLeft") {
                targetIdx = (idx - 1 + historyTabButtons.length) % historyTabButtons.length;
            } else if (e.key === "Home") {
                targetIdx = 0;
            } else if (e.key === "End") {
                targetIdx = historyTabButtons.length - 1;
            }
            if (targetIdx !== null) {
                e.preventDefault();
                activateHistoryTab(historyTabButtons[targetIdx].dataset.htab, true);
            }
        });
    });

    const getDangerNotify = () => Boolean(root.querySelector("#dangerNotifyUser")?.checked);

    root.querySelector("#detResetHwidBtn").addEventListener("click", () => {
        const notifyUser = getDangerNotify();
        showConfirmModal({
            title: "Сбросить привязку устройств?",
            message: `Все привязанные устройства пользователя ${uname} будут отвязаны. Ему потребуется заново обновить подписку в приложении.`,
            confirmText: "Сбросить HWID",
            cancelText: "Отмена",
            isDestructive: true,
            restoreFocusSelector: "#detResetHwidBtn",
            onConfirm: async () => {
                const res = await api.resetUserDevices(details.id, { notify_user: notifyUser });
                showToast(res.message || "Устройства сброшены");
                await refreshAll();
                schedulePostModalFocus("#detResetHwidBtn");
            },
        });
    });

    root.querySelector("#detRotateTokenBtn").addEventListener("click", () => {
        const notifyUser = getDangerNotify();
        showConfirmModal({
            title: "Перевыпустить токен подписки?",
            message: "Старая ссылка подключения перестанет работать. Пользователю нужно будет заново добавить подписку.",
            confirmText: "Перевыпустить",
            cancelText: "Отмена",
            isDestructive: true,
            restoreFocusSelector: "#detRotateTokenBtn",
            onConfirm: async () => {
                const res = await api.rotateUserToken(details.id, { notify_user: notifyUser });
                showToast(res.message || "Токен перевыпущен");
                await refreshAll();
                schedulePostModalFocus("#detRotateTokenBtn");
            },
        });
    });
}
