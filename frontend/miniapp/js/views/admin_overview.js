import { api } from "../api.js?v=2.8.0";
import { formatNodeDisabledReason, formatNodeHealthState } from "../admin_labels.js?v=2.8.0";
import {
    escapeHtml,
    roleLabel,
    showConfirmModal,
    showToast,
    withButtonLoading,
} from "../ui.js?v=2.8.0";

let _overviewReqId = 0;

function _translateWorkerState(state) {
    const s = String(state || "").trim().toLowerCase();
    const map = {
        ok: "ОК",
        healthy: "ОК",
        running: "Работает",
        idle: "Ожидание",
        starting: "Запуск",
        restarting: "Перезапуск",
        degraded: "Сбой",
        stale: "Устарел",
        cooldown: "Пауза",
        warn: "Внимание",
        warning: "Внимание",
        error: "Ошибка",
        failed: "Ошибка",
        dead: "Остановлен",
        stopped: "Остановлен",
    };
    return map[s] || state || "—";
}

function _formatAgeSec(sec) {
    if (sec == null || !Number.isFinite(Number(sec))) return "";
    const s = Math.max(0, Math.round(Number(sec)));
    if (s < 60) return `${s} сек назад`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m} мин назад`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h} ч назад`;
    const d = Math.floor(h / 24);
    return `${d} дн назад`;
}

function _translateBroadcastStatus(status) {
    const s = String(status || "").trim().toLowerCase();
    const map = {
        in_progress: "выполняется",
        completed: "завершена",
        cancelled: "остановлена",
        failed: "ошибка",
    };
    return map[s] || status || "выполняется";
}

function _normalizeWorkers(workersPayload) {
    if (!workersPayload) return [];
    const inner =
        typeof workersPayload === "object" &&
        !Array.isArray(workersPayload) &&
        workersPayload.workers !== undefined
            ? workersPayload.workers
            : workersPayload;
    if (!inner) return [];
    if (Array.isArray(inner)) {
        return inner.map((w, idx) => ({
            name: w.name || w.worker || `worker-${idx + 1}`,
            ...w,
        }));
    }
    if (typeof inner === "object") {
        return Object.entries(inner).map(([name, info]) => ({
            name,
            ...(info && typeof info === "object" ? info : { state: String(info ?? "") }),
        }));
    }
    return [];
}

function _workerStatusChip(state, failures) {
    const s = String(state || "").toLowerCase();
    const failCount = Number(failures || 0);
    const label = _translateWorkerState(state);
    if (failCount > 0 || s === "error" || s === "failed" || s === "dead" || s === "stopped") {
        return `<span class="chip chip-err">${escapeHtml(label === "—" ? "Ошибка" : label)}</span>`;
    }
    if (s === "degraded" || s === "warn" || s === "warning" || s === "stale" || s === "cooldown" || s === "starting" || s === "restarting") {
        return `<span class="chip chip-warn">${escapeHtml(label === "—" ? "Внимание" : label)}</span>`;
    }
    if (s === "ok" || s === "running" || s === "healthy" || s === "idle") {
        return `<span class="chip chip-ok">${escapeHtml(label === "—" ? "ОК" : label)}</span>`;
    }
    return `<span class="chip chip-muted">${escapeHtml(label)}</span>`;
}

function _problemLabel(p) {
    const parts = [];
    if (p.disabled_reason) parts.push(formatNodeDisabledReason(p.disabled_reason));
    if (p.ingress_problem) parts.push("проблема Ingress");
    if (p.cdn_problem) parts.push("проблема CDN");
    if (Number(p.module_errors_count || 0) > 0) parts.push(`ошибки модулей: ${p.module_errors_count}`);
    if (!parts.length && p.health_state) parts.push(formatNodeHealthState(p.health_state));
    return parts.join(" · ");
}

function _renderOverviewContent(root, state, data) {
    const nodes = data.nodes || {};
    const problems = Array.isArray(nodes.problems) ? nodes.problems : [];
    const finances = data.finances || {};
    const users = data.users || {};
    const activeBroadcast = data.active_broadcast || null;
    const workers = _normalizeWorkers(data.workers);

    const banners = [];
    if (data.payments_maintenance_enabled) {
        banners.push(`
            <button type="button" class="alert-banner warn" data-nav="finances">
                <span style="min-width:0; flex:1;">Техническое обслуживание платежей включено</span>
                <span class="chip chip-warn" style="white-space:nowrap; flex-shrink:0;">Финансы →</span>
            </button>
        `);
    }
    for (const p of problems) {
        banners.push(`
            <button type="button" class="alert-banner err" data-open-node="${escapeHtml(p.id)}">
                <div style="min-width:0; flex:1;">
                    <strong>${escapeHtml(p.name || `Нода #${p.id}`)}</strong>
                    <span class="muted">(${escapeHtml(roleLabel(p.role))})</span>
                    <div class="pulse-sub">${escapeHtml(_problemLabel(p))}</div>
                </div>
                <span class="chip chip-err" style="white-space:nowrap; flex-shrink:0;">Открыть →</span>
            </button>
        `);
    }
    if (Number(finances.attention_orders_count || 0) > 0) {
        banners.push(`
            <button type="button" class="alert-banner warn" data-nav="attention-orders">
                <span style="min-width:0; flex:1;">Заказы требуют внимания: <strong>${Number(finances.attention_orders_count)}</strong></span>
                <span class="chip chip-warn" style="white-space:nowrap; flex-shrink:0;">Проверить →</span>
            </button>
        `);
    }

    const degradedOrOffline = Number(nodes.degraded || 0) + Number(nodes.offline || 0);
    const workerSnapshot =
        data.workers && typeof data.workers === "object" && !Array.isArray(data.workers)
            ? data.workers
            : null;
    const supervisorHealthy = workerSnapshot && "supervisor_healthy" in workerSnapshot
        ? Boolean(workerSnapshot.supervisor_healthy)
        : null;
    const fatalShutdown = workerSnapshot ? Boolean(workerSnapshot.fatal_shutdown) : false;
    const supervisorBadgeHtml = fatalShutdown
        ? `<span class="chip chip-err">Аварийная остановка</span>`
        : supervisorHealthy === true
            ? `<span class="chip chip-ok">Воркеры ОК</span>`
            : supervisorHealthy === false
                ? `<span class="chip chip-warn">Сбой</span>`
                : "";
    const workersWithErrors = workers.filter((w) => {
        const st = String(w.state || "").toLowerCase();
        return (
            Number(w.consecutive_failures || 0) > 0 ||
            Boolean(w.last_error_type) ||
            st === "failed" ||
            st === "error" ||
            st === "dead" ||
            st === "cooldown"
        );
    }).length;

    const activeProcessed = activeBroadcast
        ? Number(activeBroadcast.processed_count ?? activeBroadcast.success_count ?? 0)
        : 0;
    const fourthTileHtml = activeBroadcast
        ? `
            <button type="button" class="pulse-card" data-nav="broadcasts">
                <span class="pulse-label">Активная рассылка</span>
                <span class="pulse-val">${activeProcessed} / ${Number(activeBroadcast.total_count || 0)}</span>
                <span class="pulse-sub">Статус: ${escapeHtml(_translateBroadcastStatus(activeBroadcast.status))}</span>
            </button>
        `
        : "";

    const workersHtml = workers.length
        ? workers
            .map((w) => {
                const fails = Number(w.consecutive_failures || 0);
                const stateVal = w.state || "—";
                const stateLabel = _translateWorkerState(stateVal);
                const errorType = w.last_error_type || "";
                const startedAge = _formatAgeSec(w.last_started_age_sec);
                const errorAge = _formatAgeSec(w.last_error_age_sec);
                const criticalBadge = w.critical
                    ? ` <span class="chip chip-muted">критич.</span>`
                    : "";
                return `
                    <div class="history-row">
                        <div class="history-row-head">
                            <span><strong>${escapeHtml(w.name)}</strong>${criticalBadge}</span>
                            ${_workerStatusChip(stateVal, fails)}
                        </div>
                        <div class="history-row-meta">
                            <span>Состояние: ${escapeHtml(stateLabel)}</span>
                            <span>Сбоев подряд: ${fails}</span>
                        </div>
                        ${startedAge ? `<div class="meta-text">Запуск: ${escapeHtml(startedAge)}</div>` : ""}
                        ${errorType ? `<div class="pulse-sub text-err">Последняя ошибка: ${escapeHtml(errorType)}${errorAge ? ` (${escapeHtml(errorAge)})` : ""}</div>` : ""}
                    </div>
                `;
            })
            .join("")
        : `<div class="meta-text">Нет данных о фоновых воркерах.</div>`;

    const mtprotoUrl = state.mtprotoProxyUrl ?? "";
    const settingsLoaded = Boolean(state.incySettingsLoaded);

    root.innerHTML = `
        <div class="stack-md">
            ${banners.length ? `<div class="stack-sm">${banners.join("")}</div>` : ""}

            <div class="overview-grid">
                <button type="button" class="pulse-card" data-nav="nodes">
                    <span class="pulse-label">Ноды</span>
                    <span class="pulse-val">${Number(nodes.online || 0)} / ${Number(nodes.total || 0)}</span>
                    <span class="pulse-sub">Проблемы: ${degradedOrOffline} · Откл.: ${Number(nodes.disabled || 0)}</span>
                </button>
                <div class="pulse-card">
                    <span class="pulse-label">Пользователи</span>
                    <span class="pulse-val">${Number(users.total || 0)}</span>
                    <span class="pulse-sub">Стандарт: ${Number(users.active_standard || 0)} · БИ: ${Number(users.active_wi || 0)}</span>
                    <div class="row row-start">
                        <button type="button" class="btn btn-sm btn-ghost btn-ghost-inline" data-nav="users">
                            Все →
                        </button>
                        <button type="button" class="btn btn-sm btn-ghost btn-ghost-inline" data-nav="expiring-users">
                            Истекают (3д): ${Number(users.expiring_3d || 0)} →
                        </button>
                    </div>
                </div>
                <button type="button" class="pulse-card" data-nav="finances">
                    <span class="pulse-label">Выручка</span>
                    <span class="pulse-val">${Number(finances.revenue_24h || 0)} ₽</span>
                    <span class="pulse-sub">24ч · 7д: ${Number(finances.revenue_7d || 0)} ₽${finances.attention_orders_count ? ` · Внимание: ${finances.attention_orders_count}` : ""}</span>
                </button>
                ${fourthTileHtml}
            </div>

            <section class="card">
                <form id="overviewQuickSearchForm" class="row-nowrap">
                    <label for="overviewQuickSearchInput" class="visually-hidden">Быстрый поиск пользователя</label>
                    <input
                        id="overviewQuickSearchInput"
                        class="input"
                        type="search"
                        placeholder="Поиск пользователя (@username, ID)"
                        autocomplete="off"
                    />
                    <button type="submit" class="btn btn-primary btn-sm">Найти</button>
                </form>
            </section>

            <details class="guide-details">
                <summary class="guide-summary">
                    <span>Фоновые воркеры (${workers.length})${workersWithErrors ? ` · Ошибки: ${workersWithErrors}` : ""}</span>
                    ${supervisorBadgeHtml}
                </summary>
                <div class="history-list" style="margin-top:12px;">
                    ${workersHtml}
                </div>
            </details>

            <section class="card">
                <div class="row">
                    <h2 class="card-title">Системные настройки</h2>
                    <span class="chip chip-muted">Telegram</span>
                </div>
                <form id="overviewSystemSettingsForm" class="field" style="gap:8px;">
                    <label for="overviewMtprotoProxyInput">Ссылка на доступ к Telegram (MTProto Proxy URL)</label>
                    ${
                        !settingsLoaded
                            ? `<div class="text-err" style="font-size:12px;">Не удалось загрузить текущие настройки. Сохранение отключено, чтобы не перезаписать ссылку.</div>`
                            : ""
                    }
                    <div class="row-nowrap">
                        <input
                            id="overviewMtprotoProxyInput"
                            name="mtproto_proxy_url"
                            class="input"
                            type="text"
                            maxlength="500"
                            value="${escapeHtml(mtprotoUrl)}"
                            placeholder="https://t.me/proxy?server=...&port=...&secret=..."
                            ${settingsLoaded ? "" : "disabled"}
                        />
                        <button id="overviewSaveMtprotoBtn" type="submit" class="btn btn-primary btn-sm" style="flex-shrink:0;" ${settingsLoaded ? "" : "disabled"}>Сохранить</button>
                    </div>
                    <div class="meta-text">Кнопка быстрого подключения в боте и личном кабинете. Оставьте пустым, чтобы скрыть.</div>
                </form>
            </section>

            <div class="row">
                <button id="overviewRefreshBtn" type="button" class="btn btn-sm">Обновить</button>
            </div>
        </div>
    `;

    root.querySelectorAll("[data-open-node]").forEach((el) => {
        el.addEventListener("click", () => {
            const nodeId = Number(el.dataset.openNode);
            if (Number.isFinite(nodeId)) state.onOpenNode?.(nodeId);
        });
    });

    root.querySelectorAll("[data-nav]").forEach((el) => {
        el.addEventListener("click", (e) => {
            const target = el.dataset.nav;
            if (target === "finances") {
                state.onOpenFinances?.("", false, "orders");
            } else if (target === "attention-orders") {
                state.onOpenFinances?.("", true, "orders");
            } else if (target === "nodes") {
                state.onOpenNode?.(null);
            } else if (target === "users") {
                state.onOpenUsersFilter?.("all", "");
            } else if (target === "expiring-users") {
                e.stopPropagation();
                state.onOpenUsersFilter?.("expiring_3d", "");
            } else if (target === "broadcasts") {
                state.onOpenBroadcasts?.();
            }
        });
    });

    const searchForm = root.querySelector("#overviewQuickSearchForm");
    const searchInput = root.querySelector("#overviewQuickSearchInput");
    if (searchForm && searchInput) {
        searchForm.addEventListener("submit", (e) => {
            e.preventDefault();
            const q = searchInput.value.trim().replace(/^@/, "");
            state.onOpenUsersFilter?.("all", q);
        });
    }

    const sysForm = root.querySelector("#overviewSystemSettingsForm");
    const mtprotoInput = root.querySelector("#overviewMtprotoProxyInput");
    const saveMtprotoBtn = root.querySelector("#overviewSaveMtprotoBtn");
    if (sysForm && mtprotoInput) {
        mtprotoInput.addEventListener("input", () => mtprotoInput.removeAttribute("aria-invalid"));
        sysForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            if (!state.incySettingsLoaded) {
                showToast("Не удалось загрузить текущие настройки. Обновите обзор.", "error");
                return;
            }
            const val = mtprotoInput.value.trim();
            if (val && !/^(https:\/\/t\.me\/proxy\?|tg:\/\/proxy\?)/i.test(val)) {
                mtprotoInput.setAttribute("aria-invalid", "true");
                mtprotoInput.focus();
                showToast("Ссылка должна начинаться с https://t.me/proxy? или tg://proxy?", "error");
                return;
            }
            await withButtonLoading(saveMtprotoBtn, "Сохранение...", async () => {
                try {
                    const res = await api.updateIncySettings({ mtproto_proxy_url: val });
                    const savedVal = res?.settings?.mtproto_proxy_url ?? val;
                    state.mtprotoProxyUrl = savedVal;
                    if (root.isConnected) mtprotoInput.value = savedVal;
                    showToast("Системные настройки сохранены");
                } catch (err) {
                    showToast(err.message || "Не удалось сохранить настройки", "error");
                }
            });
        });
    }

    const refreshBtn = root.querySelector("#overviewRefreshBtn");
    if (refreshBtn) {
        refreshBtn.addEventListener("click", () => {
            withButtonLoading(refreshBtn, "Обновление...", () => renderAdminOverviewView(root, state));
        });
    }
}

export async function renderAdminOverviewView(root, state) {
    const reqId = ++_overviewReqId;
    if (state.overviewData) {
        _renderOverviewContent(root, state, state.overviewData);
    } else {
        root.innerHTML = `
            <section class="card card-center">
                <div class="muted">Загрузка обзора...</div>
            </section>
        `;
    }
    try {
        const [overviewRes, settingsRes] = await Promise.allSettled([
            api.getAdminOverview(),
            api.getIncySettings(),
        ]);
        if (reqId !== _overviewReqId || !root.isConnected || state.adminTab !== "overview" || state.mode !== "admin") return;
        if (overviewRes.status === "rejected") {
            throw overviewRes.reason;
        }
        const data = overviewRes.value;
        state.overviewData = data;
        if (settingsRes.status === "fulfilled" && settingsRes.value?.settings) {
            state.mtprotoProxyUrl = settingsRes.value.settings.mtproto_proxy_url || "";
            state.incySettingsLoaded = true;
        } else {
            state.incySettingsLoaded = false;
        }
        const activeId = document.activeElement && root.contains(document.activeElement) ? document.activeElement.id : "";
        const searchDraft = root.querySelector("#overviewQuickSearchInput")?.value ?? "";
        const mtprotoDraft = root.querySelector("#overviewMtprotoProxyInput")?.value ?? "";
        _renderOverviewContent(root, state, data);
        if (searchDraft) {
            const nextSearch = root.querySelector("#overviewQuickSearchInput");
            if (nextSearch) nextSearch.value = searchDraft;
        }
        if (activeId === "overviewMtprotoProxyInput") {
            const nextMtproto = root.querySelector("#overviewMtprotoProxyInput");
            if (nextMtproto) nextMtproto.value = mtprotoDraft;
        }
        if (activeId) {
            root.querySelector(`#${CSS.escape(activeId)}`)?.focus({ preventScroll: true });
        }
    } catch (err) {
        if (reqId !== _overviewReqId || !root.isConnected || state.adminTab !== "overview" || state.mode !== "admin") return;
        if (err && err.code === "init_data_expired") return;
        showToast(err.message || "Не удалось загрузить обзор", "error");
        if (state.overviewData) return;
        root.innerHTML = `
            <section class="card card-center">
                <h2 class="card-title">Не удалось загрузить обзор</h2>
                <p class="muted">${escapeHtml(err.message || "Ошибка запроса")}</p>
                <button id="overviewRetryBtn" type="button" class="btn btn-primary btn-sm">Повторить</button>
            </section>
        `;
        root.querySelector("#overviewRetryBtn")?.addEventListener("click", () => {
            renderAdminOverviewView(root, state);
        });
    }
}
