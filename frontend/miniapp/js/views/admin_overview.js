import { api, setAdminSessionToken } from "../api.js?v=2.8.0";
import {
    escapeHtml,
    formatRelativeTime,
    roleLabel,
    showConfirmModal,
    showToast,
    withButtonLoading,
} from "../ui.js?v=2.8.0";

let _overviewReqId = 0;

function _normalizeWorkers(workers) {
    if (!workers) return [];
    if (Array.isArray(workers)) {
        return workers.map((w, idx) => ({
            name: w.name || w.worker || `worker-${idx + 1}`,
            ...w,
        }));
    }
    if (typeof workers === "object") {
        return Object.entries(workers).map(([name, info]) => ({
            name,
            ...(info && typeof info === "object" ? info : { status: String(info ?? "") }),
        }));
    }
    return [];
}

function _workerStatusChip(status, failures) {
    const s = String(status || "").toLowerCase();
    const failCount = Number(failures || 0);
    if (failCount > 0 || s === "error" || s === "failed" || s === "dead") {
        return `<span class="chip chip-err">${escapeHtml(status || "Ошибка")}</span>`;
    }
    if (s === "degraded" || s === "warn" || s === "warning" || s === "stale") {
        return `<span class="chip chip-warn">${escapeHtml(status || "Внимание")}</span>`;
    }
    if (s === "ok" || s === "running" || s === "healthy" || s === "idle") {
        return `<span class="chip chip-ok">${escapeHtml(status || "ОК")}</span>`;
    }
    return `<span class="chip chip-muted">${escapeHtml(status || "—")}</span>`;
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
            <div class="alert-banner warn" data-nav="finances" role="button" tabindex="0">
                <span>Техническое обслуживание платежей включено</span>
                <span class="chip chip-warn">Финансы →</span>
            </div>
        `);
    }
    for (const p of problems) {
        banners.push(`
            <div class="alert-banner err" data-open-node="${escapeHtml(p.id)}" role="button" tabindex="0">
                <div>
                    <strong>${escapeHtml(p.name || `Нода #${p.id}`)}</strong>
                    <span class="muted">(${escapeHtml(roleLabel(p.role))})</span>
                    <div class="pulse-sub">${escapeHtml(_problemLabel(p))}</div>
                </div>
                <span class="chip chip-err">Открыть →</span>
            </div>
        `);
    }
    if (Number(finances.attention_orders_count || 0) > 0) {
        banners.push(`
            <div class="alert-banner warn" data-nav="attention-orders" role="button" tabindex="0">
                <span>Заказы требуют внимания: <strong>${Number(finances.attention_orders_count)}</strong></span>
                <span class="chip chip-warn">Проверить →</span>
            </div>
        `);
    }
    if (Number(webhooks.dead || 0) > 0) {
        banners.push(`
            <div class="alert-banner err" data-nav="finances" role="button" tabindex="0">
                <span>Ошибки доставки вебхуков (dead): <strong>${Number(webhooks.dead)}</strong></span>
                <span class="chip chip-err">Финансы →</span>
            </div>
        `);
    }

    const degradedOrOffline = Number(nodes.degraded || 0) + Number(nodes.offline || 0);
    const workersWithErrors = workers.filter(
        (w) => Number(w.consecutive_failures || 0) > 0 || Boolean(w.last_error)
    ).length;

    const fourthTileHtml = activeBroadcast
        ? `
            <div class="pulse-card" data-nav="broadcasts" role="button" tabindex="0">
                <span class="pulse-label">Активная рассылка</span>
                <span class="pulse-val">${Number(activeBroadcast.sent_count || 0)} / ${Number(activeBroadcast.total_count || 0)}</span>
                <span class="pulse-sub">Статус: ${escapeHtml(activeBroadcast.status || "в процессе")}</span>
            </div>
        `
        : `
            <div class="pulse-card" data-nav="finances" role="button" tabindex="0">
                <span class="pulse-label">Вебхуки</span>
                <span class="pulse-val">${Number(webhooks.pending || 0)} в очереди</span>
                <span class="pulse-sub">Ошибок (dead): ${Number(webhooks.dead || 0)}</span>
            </div>
        `;

    const workersHtml = workers.length
        ? workers
            .map((w) => {
                const fails = Number(w.consecutive_failures || 0);
                const lastRun = w.last_run_at || w.updated_at || w.last_success_at || "";
                return `
                    <div class="history-row">
                        <div class="history-row-head">
                            <strong>${escapeHtml(w.name)}</strong>
                            ${_workerStatusChip(w.status, fails)}
                        </div>
                        <div class="history-row-meta">
                            <span>Сбоев подряд: ${fails}</span>
                            <span>${lastRun ? escapeHtml(formatRelativeTime(lastRun)) : "—"}</span>
                        </div>
                        ${w.last_error ? `<div class="pulse-sub" style="color:#fecaca;">${escapeHtml(w.last_error)}</div>` : ""}
                    </div>
                `;
            })
            .join("")
        : `<div class="muted" style="font-size:12px;">Нет данных о фоновых воркерах.</div>`;

    root.innerHTML = `
        <div class="stack" style="display:flex; flex-direction:column; gap:12px;">
            ${banners.length ? `<div style="display:flex; flex-direction:column; gap:8px;">${banners.join("")}</div>` : ""}

            <div class="overview-grid">
                <div class="pulse-card" data-nav="nodes" role="button" tabindex="0">
                    <span class="pulse-label">Ноды</span>
                    <span class="pulse-val">${Number(nodes.online || 0)} / ${Number(nodes.total || 0)}</span>
                    <span class="pulse-sub">Проблемы: ${degradedOrOffline} · Откл: ${Number(nodes.disabled || 0)}</span>
                </div>
                <div class="pulse-card" data-nav="users" role="button" tabindex="0">
                    <span class="pulse-label">Пользователи</span>
                    <span class="pulse-val">${Number(users.total || 0)}</span>
                    <span class="pulse-sub">Std: ${Number(users.active_standard || 0)} · БИ: ${Number(users.active_wi || 0)}</span>
                    <button type="button" class="btn btn-sm btn-ghost" data-nav="expiring-users" style="align-self:flex-start; padding:2px 0; margin-top:2px;">
                        Истекают (3д): ${Number(users.expiring_3d || 0)} →
                    </button>
                </div>
                <div class="pulse-card" data-nav="finances" role="button" tabindex="0">
                    <span class="pulse-label">Выручка</span>
                    <span class="pulse-val">${Number(finances.revenue_24h || 0)} ₽</span>
                    <span class="pulse-sub">24ч · 7д: ${Number(finances.revenue_7d || 0)} ₽${finances.attention_orders_count ? ` · Внимание: ${finances.attention_orders_count}` : ""}</span>
                </div>
                ${fourthTileHtml}
            </div>

            <section class="card">
                <form id="overviewQuickSearchForm" class="row" style="gap:8px;">
                    <input
                        id="overviewQuickSearchInput"
                        class="input"
                        type="search"
                        placeholder="Быстрый поиск пользователя (@username, ID)..."
                        autocomplete="off"
                    />
                    <button type="submit" class="btn btn-primary btn-sm">Найти</button>
                </form>
            </section>

            <details class="guide-details">
                <summary class="guide-summary">
                    <span>Фоновые воркеры (${workers.length})${workersWithErrors ? ` · Ошибки: ${workersWithErrors}` : ""}</span>
                </summary>
                <div class="history-list" style="margin-top:10px;">
                    ${workersHtml}
                </div>
            </details>

            <div class="row" style="justify-content:space-between; gap:8px; flex-wrap:wrap;">
                <button id="overviewRefreshBtn" type="button" class="btn btn-sm">Обновить</button>
                <button id="overviewLogoutAllBtn" type="button" class="btn btn-sm btn-danger">Выйти со всех устройств</button>
            </div>
        </div>
    `;

    root.querySelectorAll("[data-open-node]").forEach((el) => {
        const openNode = () => {
            const nodeId = Number(el.dataset.openNode);
            if (Number.isFinite(nodeId)) state.onOpenNode?.(nodeId);
        };
        el.addEventListener("click", openNode);
        el.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                openNode();
            }
        });
    });

    root.querySelectorAll("[data-nav]").forEach((el) => {
        const handleNav = (e) => {
            if (e.target !== el && e.target.closest("[data-nav]") !== el) return;
            const target = el.dataset.nav;
            if (target === "finances") {
                state.onOpenFinances?.("", false);
            } else if (target === "attention-orders") {
                state.onOpenFinances?.("", true);
            } else if (target === "nodes") {
                state.onOpenNode?.(null);
            } else if (target === "users") {
                state.onOpenUsersFilter?.("all", "");
            } else if (target === "expiring-users") {
                e.stopPropagation();
                state.onOpenUsersFilter?.("expiring", "");
            } else if (target === "broadcasts") {
                state.onOpenBroadcasts?.();
            }
        };
        el.addEventListener("click", handleNav);
        if (el.tagName !== "BUTTON") {
            el.addEventListener("keydown", (e) => {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    handleNav(e);
                }
            });
        }
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
            withButtonLoading(refreshBtn, "Обновление...", () => renderAdminOverviewView(root, state));
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
                <div class="muted">Загрузка обзора...</div>
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
