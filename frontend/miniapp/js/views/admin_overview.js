import { api, setAdminSessionToken } from "../api.js?v=2.8.0";
import {
    escapeHtml,
    roleLabel,
    showConfirmModal,
    showToast,
    withButtonLoading,
} from "../ui.js?v=2.8.0";

let _overviewReqId = 0;

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
    if (failCount > 0 || s === "error" || s === "failed" || s === "dead" || s === "stopped") {
        return `<span class="chip chip-err">${escapeHtml(state || "Ошибка")}</span>`;
    }
    if (s === "degraded" || s === "warn" || s === "warning" || s === "stale" || s === "cooldown" || s === "starting" || s === "restarting") {
        return `<span class="chip chip-warn">${escapeHtml(state || "Внимание")}</span>`;
    }
    if (s === "ok" || s === "running" || s === "healthy" || s === "idle") {
        return `<span class="chip chip-ok">${escapeHtml(state || "ОК")}</span>`;
    }
    return `<span class="chip chip-muted">${escapeHtml(state || "—")}</span>`;
}

function _problemLabel(p) {
    const parts = [];
    if (p.disabled_reason) parts.push(p.disabled_reason);
    if (p.ingress_problem) parts.push("проблема Ingress");
    if (p.cdn_problem) parts.push("проблема CDN");
    if (Number(p.module_errors_count || 0) > 0) parts.push(`ошибки модулей: ${p.module_errors_count}`);
    if (!parts.length && p.health_state) parts.push(p.health_state);
    return parts.join(" · ");
}

function _renderOverviewContent(root, state, data) {
    const nodes = data.nodes || {};
    const problems = Array.isArray(nodes.problems) ? nodes.problems : [];
    const finances = data.finances || {};
    const users = data.users || {};
    const webhooks = data.webhooks || {};
    const activeBroadcast = data.active_broadcast || null;
    const workers = _normalizeWorkers(data.workers);

    const banners = [];
    if (data.payments_maintenance_enabled) {
        banners.push(`
            <button type="button" class="alert-banner warn" data-nav="finances">
                <span>Техническое обслуживание платежей включено</span>
                <span class="chip chip-warn">Финансы →</span>
            </button>
        `);
    }
    for (const p of problems) {
        banners.push(`
            <button type="button" class="alert-banner err" data-open-node="${escapeHtml(p.id)}">
                <div>
                    <strong>${escapeHtml(p.name || `Нода #${p.id}`)}</strong>
                    <span class="muted">(${escapeHtml(roleLabel(p.role))})</span>
                    <div class="pulse-sub">${escapeHtml(_problemLabel(p))}</div>
                </div>
                <span class="chip chip-err">Открыть →</span>
            </button>
        `);
    }
    if (Number(finances.attention_orders_count || 0) > 0) {
        banners.push(`
            <button type="button" class="alert-banner warn" data-nav="attention-orders">
                <span>Заказы требуют внимания: <strong>${Number(finances.attention_orders_count)}</strong></span>
                <span class="chip chip-warn">Проверить →</span>
            </button>
        `);
    }
    if (Number(webhooks.dead || 0) > 0) {
        banners.push(`
            <button type="button" class="alert-banner err" data-nav="webhooks">
                <span>Ошибки доставки вебхуков: <strong>${Number(webhooks.dead)}</strong></span>
                <span class="chip chip-err">Вебхуки →</span>
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
        ? `<span class="chip chip-err">Fatal shutdown</span>`
        : supervisorHealthy === true
            ? `<span class="chip chip-ok">Supervisor OK</span>`
            : supervisorHealthy === false
                ? `<span class="chip chip-warn">Degraded</span>`
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
                <span class="pulse-sub">Статус: ${escapeHtml(activeBroadcast.status || "в процессе")}</span>
            </button>
        `
        : `
            <button type="button" class="pulse-card" data-nav="webhooks">
                <span class="pulse-label">Вебхуки</span>
                <span class="pulse-val">${Number(webhooks.pending || 0)} в очереди</span>
                <span class="pulse-sub">Сбои доставки: ${Number(webhooks.dead || 0)}</span>
            </button>
        `;

    const workersHtml = workers.length
        ? workers
            .map((w) => {
                const fails = Number(w.consecutive_failures || 0);
                const stateVal = w.state || "—";
                const errorType = w.last_error_type || "";
                const criticalBadge = w.critical
                    ? ` <span class="chip chip-muted">critical</span>`
                    : "";
                return `
                    <div class="history-row">
                        <div class="history-row-head">
                            <span><strong>${escapeHtml(w.name)}</strong>${criticalBadge}</span>
                            ${_workerStatusChip(stateVal, fails)}
                        </div>
                        <div class="history-row-meta">
                            <span>Состояние: ${escapeHtml(stateVal)}</span>
                            <span>Сбоев подряд: ${fails}</span>
                        </div>
                        ${errorType ? `<div class="pulse-sub text-err">Последняя ошибка: ${escapeHtml(errorType)}</div>` : ""}
                    </div>
                `;
            })
            .join("")
        : `<div class="meta-text">Нет данных о фоновых воркерах.</div>`;

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

            <div class="row">
                <button id="overviewRefreshBtn" type="button" class="btn btn-sm">Обновить</button>
                <button id="overviewLogoutAllBtn" type="button" class="btn btn-sm btn-danger">Выйти со всех устройств</button>
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
            } else if (target === "webhooks") {
                state.onOpenFinances?.("", false, "webhooks");
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

    const refreshBtn = root.querySelector("#overviewRefreshBtn");
    if (refreshBtn) {
        refreshBtn.addEventListener("click", () => {
            withButtonLoading(refreshBtn, "Обновление", () => renderAdminOverviewView(root, state));
        });
    }

    const logoutAllBtn = root.querySelector("#overviewLogoutAllBtn");
    if (logoutAllBtn) {
        logoutAllBtn.addEventListener("click", () => {
            showConfirmModal({
                title: "Выйти со всех устройств?",
                message: "Все активные админ-сессии 2FA будут завершены. Потребуется повторный ввод кода.",
                confirmText: "Выйти",
                isDestructive: true,
                onConfirm: async () => {
                    await api.adminLogout();
                    setAdminSessionToken("");
                    showToast("Все админ-сессии завершены");
                    state.onRequire2FA?.();
                },
            });
        });
    }
}

export async function renderAdminOverviewView(root, state) {
    const reqId = ++_overviewReqId;
    if (!state.overviewData) {
        root.innerHTML = `
            <section class="card card-center">
                <div class="muted">Загрузка обзора</div>
            </section>
        `;
    }
    try {
        const data = await api.getAdminOverview();
        if (reqId !== _overviewReqId) return;
        state.overviewData = data;
        _renderOverviewContent(root, state, data);
    } catch (err) {
        if (reqId !== _overviewReqId) return;
        if (err && err.code === "2fa_required") return;
        showToast(err.message || "Не удалось загрузить обзор", "error");
        root.innerHTML = `
            <section class="card card-center">
                <div class="card-title">Не удалось загрузить обзор</div>
                <p class="muted">${escapeHtml(err.message || "Ошибка запроса")}</p>
                <button id="overviewRetryBtn" type="button" class="btn btn-sm">Повторить</button>
            </section>
        `;
        root.querySelector("#overviewRetryBtn")?.addEventListener("click", () => {
            renderAdminOverviewView(root, state);
        });
    }
}
