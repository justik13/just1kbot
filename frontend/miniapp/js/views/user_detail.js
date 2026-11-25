import { api } from "../api.js?v=2.8.0";
import {
    ORDER_KIND_MAP,
    ORDER_STATUS_MAP,
    formatAuditAction,
    formatLedgerEntryType,
    formatMetadataFlag,
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
                <div class="item-row" style="padding:8px 12px;">
                    <div class="row">
                        <span style="font-weight:600; font-size:13px;">${escapeHtml(d.device_model || d.platform || "Устройство")}</span>
                        <span class="muted" style="font-size:12px;">${d.last_seen_at ? formatRelativeTime(d.last_seen_at) : "—"}</span>
                    </div>
                    ${metaText ? `<div class="muted" style="font-size:11px;">${escapeHtml(metaText)}</div>` : ""}
                    <div class="row" style="gap:8px; margin-top:4px;">
                        <button type="button" class="mono-copy" data-copy-hwid="${escapeHtml(d.hwid)}" title="Копировать HWID">${escapeHtml(d.hwid)}</button>
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
    const provStatus = wi.provisioning_status ? String(wi.provisioning_status) : "—";
    const syncStateEntries =
        wi.node_sync_state && typeof wi.node_sync_state === "object"
            ? Object.entries(wi.node_sync_state)
            : [];
    const syncNodesHtml = syncStateEntries.length
        ? syncStateEntries
              .map(([nodeId, st]) => {
                  const stText = typeof st === "object" && st !== null ? st.status || JSON.stringify(st) : String(st);
                  return `<span class="chip chip-muted">${escapeHtml(nodeId)}: ${escapeHtml(stText)}</span>`;
              })
              .join("")
        : `<span class="muted">Нет данных по нодам</span>`;

    return `
        <div class="detail-grid" style="padding-top:8px; border-top:1px solid var(--border-subtle);">
            <div class="detail-item"><span class="detail-label">Провижининг</span><span class="mono">${escapeHtml(provStatus)}</span></div>
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
                ? `<div style="margin-top:6px; padding:8px 10px; border-radius:8px; background:var(--accent-red-soft); border:1px solid rgba(248,113,113,0.32); color:var(--accent-red); font-size:12px; white-space:pre-wrap;">${escapeHtml(wi.last_sync_error)}</div>`
                : ""
        }
    `;
}

function _renderHistoryTabContent(tab, details) {
    if (tab === "orders") {
        const orders = details.recent_orders || [];
        if (!orders.length) {
            return `<div class="muted" style="text-align:center; padding:12px;">Заказов нет</div>`;
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
                        <div class="filter-pills" style="margin-top:2px;">
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
            return `<div class="muted" style="text-align:center; padding:12px;">Операций по балансу нет</div>`;
        }
        return entries
            .map((entry) => {
                const amt = Number(entry.amount || 0);
                const signStr = amt > 0 ? `+${amt} ₽` : `${amt} ₽`;
                const amtColor =
                    amt > 0 ? "var(--accent-emerald, #34d399)" : amt < 0 ? "var(--accent-red, #f87171)" : "inherit";
                const bucketLabel = entry.bucket === "bonus" ? "бонус" : "реал";
                const typeLabel = formatLedgerEntryType(entry.entry_type);
                return `
                    <div class="item-row">
                        <div class="row">
                            <span style="font-weight:600; font-size:13px;">${escapeHtml(typeLabel)} <span class="chip chip-muted">${bucketLabel}</span></span>
                            <span class="tabular" style="font-weight:600; color:${amtColor};">${escapeHtml(signStr)}</span>
                        </div>
                        ${
                            entry.description
                                ? `<div class="muted" style="font-size:12px;">${escapeHtml(entry.description)}</div>`
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
            return `<div class="muted" style="text-align:center; padding:12px;">Событий устройств нет</div>`;
        }
        return events
            .map((ev) => {
                const title = [ev.device_model, ev.platform].filter(Boolean).join(" · ") || "Устройство";
                return `
                    <div class="item-row">
                        <div class="row">
                            <span style="font-weight:600; font-size:13px;">${escapeHtml(title)}</span>
                            <span class="muted" style="font-size:11px;">${formatDate(ev.created_at)}</span>
                        </div>
                        ${
                            ev.hwid
                                ? `<div style="margin-top:2px;"><button type="button" class="mono-copy" data-copy-val="${escapeHtml(ev.hwid)}" data-copy-msg="HWID скопирован">${escapeHtml(_formatShortHwid(ev.hwid))}</button></div>`
                                : ""
                        }
                    </div>
                `;
            })
            .join("");
    }

    const audits = details.recent_audit || [];
    if (!audits.length) {
        return `<div class="muted" style="text-align:center; padding:12px;">Записей аудита нет</div>`;
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
                        <span class="muted" style="font-size:11px;">${formatDate(a.created_at)}</span>
                    </div>
                    ${
                        detailsStr
                            ? `<div class="mono muted" style="font-size:11px; white-space:pre-wrap; word-break:break-word;">${escapeHtml(detailsStr)}</div>`
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
    const fallbackName = _formatDisplayName(userSummary);

    if (!preserveScroll) {
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
    }

    const sentinelBtn = root.querySelector("#userDetailBackBtn");

    let details;
    try {
        const res = await api.getUserDetails(userSummary.id);
        details = res.user;
    } catch (err) {
        if (reqId !== _detailReqId) return;
        if (state && state.selectedUserId !== userSummary.id) return;
        if (sentinelBtn && !root.contains(sentinelBtn)) return;
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
            renderUserDetailView(root, userSummary, onBack, onReloadUser, state);
        });
        return;
    }

    if (reqId !== _detailReqId) return;
    if (state && state.selectedUserId !== userSummary.id) return;
    if (sentinelBtn && !root.contains(sentinelBtn)) return;

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

    let activeHistoryTab = "orders";

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
                    <div class="filter-pills" style="margin-top:6px;">
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
                    <div class="danger-card-title">Долг: ${debt} ₽</div>
                    ${
                        details.financial_block_reason
                            ? `<div class="muted" style="font-size:12px; margin-top:4px;">${escapeHtml(details.financial_block_reason)}</div>`
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
                    <input name="amount_rub" class="input tabular" type="number" min="-100000" max="100000" step="1" value="" placeholder="Сумма ₽ (напр. 100 или -100)" aria-label="Сумма коррекции в рублях">
                    <button type="submit" class="btn btn-primary btn-sm" style="flex-shrink:0;">Применить</button>
                </div>
                <input name="note" class="input" type="text" maxlength="120" placeholder="Комментарий (необязательно)">
                <label class="toggle-row">
                    <span class="toggle-label">Уведомить в Telegram</span>
                    <span class="switch">
                        <input name="notify_user" type="checkbox" checked>
                        <span class="switch-track"></span>
                    </span>
                </label>
                <label class="toggle-row">
                    <span class="toggle-label">Отправить комментарий пользователю</span>
                    <span class="switch">
                        <input name="send_note_to_user" type="checkbox">
                        <span class="switch-track"></span>
                    </span>
                </label>
                <div class="muted" style="font-size:11px;">Если переключатель выключен, комментарий сохранится только в аудит-логе.</div>
            </form>
        </section>

        <section class="card">
            <div class="card-title">Подписки</div>

            <div class="user-sub-box">
                <div class="row">
                    <div>
                        <div style="font-weight:600; font-size:13px; display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
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
                    <label class="toggle-row">
                        <span class="toggle-label">Уведомить пользователя</span>
                        <span class="switch">
                            <input name="notify_user" type="checkbox">
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
                    <label class="toggle-row">
                        <span class="toggle-label">Уведомить пользователя</span>
                        <span class="switch">
                            <input name="notify_user" type="checkbox">
                            <span class="switch-track"></span>
                        </span>
                    </label>
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
                    <label class="toggle-row">
                        <span class="toggle-label">Уведомить пользователя</span>
                        <span class="switch">
                            <input name="notify_user" type="checkbox">
                            <span class="switch-track"></span>
                        </span>
                    </label>
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
            <div id="detDevicesList" class="field" style="gap:8px;">
                ${_renderDevicesHtml(devices)}
            </div>
        </section>

        <section class="card">
            <div class="card-title">Ограничения</div>
            ${
                details.financial_block_reason
                    ? `<div style="font-size:12px; padding:6px 10px; border-radius:8px; background:var(--accent-red-soft); border:1px solid rgba(248,113,113,0.28); color:var(--accent-red);">Причина блокировки: ${escapeHtml(details.financial_block_reason)}</div>`
                    : ""
            }
            <form id="userRestrictionsForm" class="field" style="gap:8px;">
                <label class="toggle-row">
                    <span class="toggle-label">Финансовая блокировка</span>
                    <span class="switch">
                        <input name="financial_hold" type="checkbox" ${details.financial_hold ? "checked" : ""}>
                        <span class="switch-track"></span>
                    </span>
                </label>
                <label class="toggle-row">
                    <span class="toggle-label">Блокировка пополнений</span>
                    <span class="switch">
                        <input name="topup_blocked" type="checkbox" ${details.topup_blocked ? "checked" : ""}>
                        <span class="switch-track"></span>
                    </span>
                </label>
                <input name="note" class="input" type="text" maxlength="120" placeholder="Причина / комментарий (необязательно)">
                <button type="submit" class="btn btn-sm">Сохранить ограничения</button>
            </form>
        </section>

        <section class="card">
            <div class="row">
                <div class="card-title">История</div>
            </div>
            <div class="seg-group" id="userHistoryTabs">
                <button type="button" class="seg-btn active" data-htab="orders">Заказы</button>
                <button type="button" class="seg-btn" data-htab="ledger">Баланс</button>
                <button type="button" class="seg-btn" data-htab="devices">Устройства</button>
                <button type="button" class="seg-btn" data-htab="audit">Админ-лог</button>
            </div>
            <div id="userHistoryList" class="field" style="gap:8px;">
                ${_renderHistoryTabContent(activeHistoryTab, details)}
            </div>
        </section>

        <section class="danger-card">
            <div class="danger-card-title">Опасная зона</div>
            <label class="toggle-row">
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

    if (preserveScroll && savedScrollY !== null) {
        requestAnimationFrame(() => window.scrollTo(0, savedScrollY));
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

    const refreshAll = async () => {
        const currentScrollY = window.scrollY;
        void onReloadUser?.();
        await renderUserDetailView(root, userSummary, onBack, onReloadUser, state, {
            preserveScroll: true,
            scrollY: currentScrollY,
        });
    };

    const resyncBtn = root.querySelector("#detResyncUserBtn");
    resyncBtn.addEventListener("click", () => {
        withButtonLoading(resyncBtn, "Синхронизация...", async () => {
            try {
                const res = await api.resyncUser(details.id);
                showToast(res.message || "Синхронизация запущена");
                await refreshAll();
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
                onConfirm: async () => {
                    const res = await api.adjustUser(details.id, { action: "forgive_debt" });
                    showToast(res.message || "Задолженность списана");
                    await refreshAll();
                },
            });
        });
    }

    const bonusForm = root.querySelector("#userBonusForm");
    bonusForm.querySelectorAll("[data-add]").forEach((b) => {
        b.addEventListener("click", () => {
            bonusForm.querySelector("input[name='amount_rub']").value = b.dataset.add;
        });
    });
    bonusForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const amount = Number(bonusForm.querySelector("input[name='amount_rub']").value);
        if (!Number.isInteger(amount) || amount === 0) {
            showToast("Укажите ненулевую целую сумму в рублях", "error");
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
            onConfirm: async () => {
                const res = await api.adjustUser(details.id, {
                    action: "balance",
                    amount_rub: amount,
                    note,
                    notify_user: notifyUser,
                    send_note_to_user: sendNoteToUser,
                });
                showToast(res.message || "Баланс обновлён");
                await refreshAll();
            },
        });
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
        const rawDays = stdForm.querySelector("input[name='days']").value.trim();
        const deviceLimit = Number(stdForm.querySelector("input[name='device_limit']").value);
        const limitChanged = deviceLimit !== Number(stdLimit);
        if (rawDays === "" && !limitChanged) {
            showToast("Укажите количество дней или измените лимит устройств", "error");
            return;
        }
        const days = rawDays === "" ? 0 : Number(rawDays);
        if (!Number.isInteger(days) || days < -3650 || days > 3650) {
            showToast("Укажите корректное количество дней", "error");
            return;
        }
        if (!Number.isInteger(deviceLimit) || deviceLimit < 2 || deviceLimit > 10) {
            showToast("Лимит устройств должен быть от 2 до 10", "error");
            return;
        }
        const notifyUser = Boolean(stdForm.querySelector("input[name='notify_user']")?.checked);
        const payload = { action: "grant_standard", days, notify_user: notifyUser };
        if (days >= 0 || limitChanged) {
            payload.device_limit = deviceLimit;
        }
        const execute = async () => {
            const btn = stdForm.querySelector("button[type='submit']");
            await withButtonLoading(btn, "Сохранение...", async () => {
                const res = await api.adjustUser(details.id, payload);
                showToast(res.message || "Подписка Стандарт обновлена");
                await refreshAll();
            });
        };

        if (days < 0) {
            showConfirmModal({
                title: "Сократить подписку Стандарт?",
                message: `Срок подписки Стандарт пользователя ${uname} будет уменьшен на ${Math.abs(days)} дн. Если срок истечёт, доступ будет отключён.`,
                confirmText: "Сократить",
                cancelText: "Отмена",
                isDestructive: true,
                onConfirm: execute,
            });
            return;
        }

        try {
            await execute();
        } catch (err) {
            showToast(err.message || "Ошибка обновления подписки", "error");
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
        const rawDays = wiForm.querySelector("input[name='days']").value.trim();
        const deviceLimit = Number(wiForm.querySelector("input[name='device_limit']").value);
        const limitChanged = deviceLimit !== Number(wiLimit);
        if (rawDays === "" && !limitChanged) {
            showToast("Укажите количество дней или измените количество слотов", "error");
            return;
        }
        const days = rawDays === "" ? 0 : Number(rawDays);
        if (!Number.isInteger(days) || days < -3650 || days > 3650) {
            showToast("Укажите корректное количество дней", "error");
            return;
        }
        if (!Number.isInteger(deviceLimit) || deviceLimit < 1 || deviceLimit > 3) {
            showToast("Количество слотов должно быть от 1 до 3", "error");
            return;
        }
        const notifyUser = Boolean(wiForm.querySelector("input[name='notify_user']")?.checked);
        const payload = { action: "grant_wi", days, notify_user: notifyUser };
        if (days >= 0 || limitChanged) {
            payload.device_limit = deviceLimit;
        }
        const execute = async () => {
            const btn = wiForm.querySelector("button[type='submit']");
            await withButtonLoading(btn, "Сохранение...", async () => {
                const res = await api.adjustUser(details.id, payload);
                showToast(res.message || "Белый интернет обновлён");
                await refreshAll();
            });
        };

        if (days < 0) {
            showConfirmModal({
                title: "Сократить Белый интернет?",
                message: `Срок подписки «Белый интернет» пользователя ${uname} будет уменьшен на ${Math.abs(days)} дн. Если срок истечёт, доступ будет отозван.`,
                confirmText: "Сократить",
                cancelText: "Отмена",
                isDestructive: true,
                onConfirm: execute,
            });
            return;
        }

        try {
            await execute();
        } catch (err) {
            showToast(err.message || "Ошибка обновления подписки", "error");
        }
    });

    const trafficSelect = wiTrafficForm.querySelector("select[name='traffic_action']");
    const wiGbField = wiTrafficForm.querySelector("#wiGbField");
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
            const gb = Number(wiTrafficForm.querySelector("input[name='gb']").value);
            if (!Number.isInteger(gb) || gb < 1) {
                showToast("Укажите объём в ГБ (>= 1)", "error");
                return;
            }
            payload.gb = gb;
        }
        const execute = async () => {
            const btn = wiTrafficForm.querySelector("button[type='submit']");
            await withButtonLoading(btn, "Применение...", async () => {
                const res = await api.adjustUser(details.id, payload);
                showToast(res.message || "Трафик обновлён");
                await refreshAll();
            });
        };

        if (payload.traffic_action === "reset_used") {
            showConfirmModal({
                title: "Сбросить использованный трафик?",
                message: `Счётчик использованного трафика «Белый интернет» пользователя ${uname} будет обнулён.`,
                confirmText: "Сбросить",
                cancelText: "Отмена",
                isDestructive: true,
                onConfirm: execute,
            });
            return;
        }

        try {
            await execute();
        } catch (err) {
            showToast(err.message || "Ошибка обновления трафика", "error");
        }
    });

    const dmForm = root.querySelector("#userDmForm");
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
                onConfirm: async () => {
                    const res = await api.deleteUserDevice(details.id, hwid);
                    showToast(res.message || "Устройство удалено");
                    await refreshAll();
                },
            });
        });
    });

    const restrForm = root.querySelector("#userRestrictionsForm");
    const origFinancialHold = Boolean(details.financial_hold);
    const origTopupBlocked = Boolean(details.topup_blocked);
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
            onConfirm: async () => {
                const res = await api.adjustUser(details.id, payload);
                showToast(res.message || "Ограничения сохранены");
                await refreshAll();
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
    historyTabsEl.querySelectorAll("[data-htab]").forEach((btn) => {
        btn.addEventListener("click", () => {
            activeHistoryTab = btn.dataset.htab;
            historyTabsEl.querySelectorAll("[data-htab]").forEach((b) => {
                b.classList.toggle("active", b.dataset.htab === activeHistoryTab);
            });
            historyListEl.innerHTML = _renderHistoryTabContent(activeHistoryTab, details);
            wireHistoryCopyButtons();
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
            onConfirm: async () => {
                const res = await api.resetUserDevices(details.id, { notify_user: notifyUser });
                showToast(res.message || "Устройства сброшены");
                await refreshAll();
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
            onConfirm: async () => {
                const res = await api.rotateUserToken(details.id, { notify_user: notifyUser });
                showToast(res.message || "Токен перевыпущен");
                await refreshAll();
            },
        });
    });
}
