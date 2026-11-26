import { api, setOn2FARequired, setOnInitDataExpired } from "./api.js?v=2.8.0";
import { closeAllModals, escapeHtml, setPageBackButton, showToast } from "./ui.js?v=2.8.0";
import { clearBroadcastPollTimer, renderAdminBroadcastsView } from "./views/admin_broadcasts.js?v=2.8.0";
import { renderAdminFinancesView } from "./views/admin_finances.js?v=2.8.0";
import { renderAdminOverviewView } from "./views/admin_overview.js?v=2.8.0";
import { renderAdminUsersView } from "./views/admin_users.js?v=2.8.0";
import { renderNodeDetailView } from "./views/node_detail.js?v=2.8.0";
import { renderNodesView, renderSubscriptionSettingsView } from "./views/nodes.js?v=2.8.0";
import { renderSecurity2FA } from "./views/security_2fa.js?v=2.8.0";
import { loadAndRenderUserCabinet } from "./views/user_cabinet.js?v=2.8.0";
import { renderUserDetailView } from "./views/user_detail.js?v=2.8.0";

const _ADMIN_TABS = [
    { id: "overview", label: "Обзор" },
    { id: "nodes", label: "Ноды" },
    { id: "users", label: "Пользователи" },
    { id: "finances", label: "Финансы" },
    { id: "broadcasts", label: "Рассылки" },
    { id: "subscription", label: "Подписка" },
];
const _VALID_ADMIN_TABS = new Set(_ADMIN_TABS.map((t) => t.id));

let _tabSwitchSeq = 0;

function _parsePositiveInt(raw) {
    if (raw === null || raw === undefined || String(raw).trim() === "") return null;
    const n = Number(raw);
    return Number.isInteger(n) && n > 0 ? n : null;
}

function _parseDeepLink() {
    try {
        const params = new URLSearchParams(window.location.search || "");
        const mode = params.get("mode") || "";
        if (mode !== "admin") return null;
        const rawTab = params.get("tab") || "";
        const nodeIdRaw = params.get("node_id");
        const userIdRaw = params.get("user_id");
        const q = (params.get("q") || "").trim();
        const nodeId = _parsePositiveInt(nodeIdRaw);
        const userId = _parsePositiveInt(userIdRaw);
        let tab = _VALID_ADMIN_TABS.has(rawTab) ? rawTab : "";
        if (!tab) {
            if (nodeId != null) tab = "nodes";
            else if (userId != null) tab = "users";
            else if (q) tab = "users";
            else tab = "overview";
        }
        return { mode: "admin", tab, nodeId, userId, q };
    } catch {
        return null;
    }
}

function _applyDeepLink(target) {
    if (!target) return;
    appState.adminTab = target.tab || "overview";
    appState.selectedNodeId = target.nodeId ?? null;
    appState.selectedUserId = target.userId ?? null;
    if (target.q) {
        if (appState.adminTab === "finances") {
            appState.finQuery = target.q;
            appState.financesData = null;
            appState.financesLoadAttempted = false;
        } else {
            appState.usersQuery = target.q;
            appState.usersData = null;
        }
    }
}

const appState = {
    user: null,
    security: null,
    twoFactorPassed: false,
    mode: "cabinet",
    adminTab: "overview",
    overviewData: null,
    nodes: [],
    nodesLoaded: false,
    selectedNodeId: null,
    usersData: null,
    usersQuery: "",
    usersFilter: "all",
    usersOffset: 0,
    selectedUserId: null,
    broadcastsData: null,
    massTab: "bonus",
    finSubTab: "orders",
    financesData: null,
    financesLoadAttempted: false,
    finQuery: "",
    finStatus: "",
    finAttentionOnly: false,
    auditLogsData: null,
    auditLoadAttempted: false,
    auditAction: "",
    auditTargetType: "",
    auditQuery: "",
    webhooksData: null,
    webhooksLoadAttempted: false,
    pendingDeepLink: _parseDeepLink(),
    onOpenUser: (userId) => {
        const id = _parsePositiveInt(userId);
        if (id == null) return;
        closeAllModals();
        appState.mode = "admin";
        appState.adminTab = "users";
        appState.selectedNodeId = null;
        appState.selectedUserId = id;
        _renderAdminView();
    },
    onOpenNode: (nodeId) => {
        const id = nodeId == null ? null : _parsePositiveInt(nodeId);
        if (nodeId != null && id == null) return;
        closeAllModals();
        appState.mode = "admin";
        appState.adminTab = "nodes";
        appState.selectedUserId = null;
        appState.selectedNodeId = id;
        if (id != null && (!appState.nodesLoaded || !appState.nodes.some((n) => n.id === id))) {
            appState.nodesLoaded = false;
        }
        _renderAdminView();
    },
    onOpenFinances: (query = "", attentionOnly = false, subTab = "orders") => {
        closeAllModals();
        appState.mode = "admin";
        appState.adminTab = "finances";
        appState.finSubTab = subTab || "orders";
        appState.finQuery = String(query || "");
        appState.finStatus = "";
        appState.finAttentionOnly = Boolean(attentionOnly);
        appState.financesData = null;
        appState.financesLoadAttempted = false;
        if (appState.finSubTab === "webhooks") {
            appState.webhooksData = null;
            appState.webhooksLoadAttempted = false;
        } else if (appState.finSubTab === "audit") {
            appState.auditAction = "";
            appState.auditLogsData = null;
            appState.auditLoadAttempted = false;
        }
        appState.selectedNodeId = null;
        appState.selectedUserId = null;
        _renderAdminView();
    },
    onOpenAudit: (query = "", targetType = "") => {
        closeAllModals();
        appState.mode = "admin";
        appState.adminTab = "finances";
        appState.finSubTab = "audit";
        appState.auditAction = "";
        appState.auditQuery = String(query || "");
        appState.auditTargetType = String(targetType || "");
        appState.auditLogsData = null;
        appState.auditLoadAttempted = false;
        appState.selectedNodeId = null;
        appState.selectedUserId = null;
        _renderAdminView();
    },
    onOpenUsersFilter: (filter = "all", query = "") => {
        closeAllModals();
        appState.mode = "admin";
        appState.adminTab = "users";
        appState.usersFilter = String(filter || "all");
        appState.usersQuery = String(query || "");
        appState.usersOffset = 0;
        appState.usersData = null;
        appState.selectedNodeId = null;
        appState.selectedUserId = null;
        _renderAdminView();
    },
    onOpenBroadcasts: () => {
        closeAllModals();
        appState.mode = "admin";
        appState.adminTab = "broadcasts";
        appState.selectedNodeId = null;
        appState.selectedUserId = null;
        _renderAdminView();
    },
    onRequire2FA: () => {
        clearBroadcastPollTimer();
        closeAllModals();
        appState.twoFactorPassed = false;
        const root = document.getElementById("appRoot");
        if (root && appState.mode === "admin") {
            _openAdmin2FAGate(root);
        }
    },
};

function _initTelegramChrome() {
    const tg = window.Telegram && window.Telegram.WebApp;
    if (!tg) return;
    try {
        tg.ready();
        tg.expand();
        tg.disableVerticalSwipes?.();
        tg.setHeaderColor("#000000");
        tg.setBackgroundColor("#000000");
    } catch {
        // Non-critical in older Telegram clients
    }
}

function _updateTelegramBackButton(onBack) {
    setPageBackButton(onBack);
}

function _renderErrorCard(root, title, desc, showRetry = false) {
    root.innerHTML = `
        <section class="card card-center">
            <span class="chip chip-muted">Доступ</span>
            <h1 class="card-title">${escapeHtml(title)}</h1>
            <p class="muted">${escapeHtml(desc)}</p>
            ${showRetry ? '<button id="bootstrapRetryBtn" type="button" class="btn btn-primary btn-sm">Повторить</button>' : ""}
        </section>
    `;
    if (showRetry) {
        root.querySelector("#bootstrapRetryBtn")?.addEventListener("click", () => {
            root.innerHTML = `<section class="card card-center"><div class="muted">Проверка доступа...</div></section>`;
            bootstrap();
        });
    }
}

function _renderTabErrorCard(container, title, message, onRetry) {
    container.innerHTML = `
        <section class="card card-center">
            <h2 class="card-title">${escapeHtml(title)}</h2>
            <p class="muted">${escapeHtml(message || "Ошибка запроса")}</p>
            <button id="adminTabRetryBtn" type="button" class="btn btn-primary btn-sm">Повторить</button>
        </section>
    `;
    container.querySelector("#adminTabRetryBtn")?.addEventListener("click", () => {
        if (typeof onRetry === "function") onRetry();
    });
}

function _syncModeSwitch() {
    const modeSwitch = document.getElementById("modeSwitch");
    const profilePill = document.getElementById("profilePillBtn");
    const userChip = document.getElementById("userChip");
    const shell = document.querySelector(".app-shell");
    const isAdmin = Boolean(appState.user && appState.user.is_admin);
    const inAdminMode = isAdmin && appState.mode === "admin";
    const inCabinet = !inAdminMode;
    if (shell) shell.classList.toggle("mode-admin", inAdminMode);
    if (profilePill) profilePill.hidden = !inCabinet;
    if (userChip) userChip.hidden = inCabinet;
    if (!modeSwitch) return;
    modeSwitch.hidden = !isAdmin;
    if (!isAdmin) return;
    modeSwitch.querySelectorAll("[data-mode]").forEach((btn) => {
        const isCurrent = btn.dataset.mode === appState.mode;
        btn.classList.toggle("active", isCurrent);
        if (isCurrent) {
            btn.setAttribute("aria-current", "page");
        } else {
            btn.removeAttribute("aria-current");
        }
    });
}

async function _renderAdminView() {
    const root = document.getElementById("appRoot");
    if (!root) return;
    if (appState.mode === "admin" && !appState.twoFactorPassed) {
        _openAdmin2FAGate(root);
        return;
    }
    const seq = ++_tabSwitchSeq;
    _syncModeSwitch();

    if (appState.mode === "cabinet") {
        clearBroadcastPollTimer();
        appState.selectedNodeId = null;
        appState.selectedUserId = null;
        _updateTelegramBackButton(null);
        await loadAndRenderUserCabinet(root, {
            isActive: () => seq === _tabSwitchSeq && appState.mode === "cabinet",
        });
        return;
    }

    if (appState.adminTab !== "broadcasts") {
        clearBroadcastPollTimer();
    }

    let nodesFetchedThisRender = false;
    let nodesFetchError = null;

    if (appState.adminTab === "nodes" && appState.selectedNodeId != null) {
        if (!appState.nodesLoaded) {
            root.innerHTML = `<section class="card card-center"><div class="muted">Загрузка ноды...</div></section>`;
            nodesFetchedThisRender = true;
            try {
                const res = await api.listNodes();
                if (seq !== _tabSwitchSeq || appState.mode !== "admin") return;
                appState.nodes = res.nodes || [];
                appState.nodesLoaded = true;
            } catch (err) {
                if (seq !== _tabSwitchSeq || appState.mode !== "admin") return;
                if (err && err.code === "2fa_required") return;
                nodesFetchError = err;
                showToast(err.message || "Не удалось загрузить ноды", "error");
            }
        }
        if (nodesFetchError && !appState.nodesLoaded) {
            const onBack = () => {
                appState.selectedNodeId = null;
                _updateTelegramBackButton(null);
                _renderAdminView();
            };
            _updateTelegramBackButton(onBack);
            root.innerHTML = `
                <div class="detail-header-nav">
                    <button id="nodeLinkErrorBackBtn" type="button" class="btn btn-sm btn-ghost" style="white-space:nowrap; flex-shrink:0;">← Все ноды</button>
                </div>
                <div id="nodeLinkErrorBody"></div>
            `;
            root.querySelector("#nodeLinkErrorBackBtn")?.addEventListener("click", onBack);
            _renderTabErrorCard(
                root.querySelector("#nodeLinkErrorBody"),
                "Не удалось загрузить ноду",
                nodesFetchError.message || "Ошибка запроса",
                () => _renderAdminView()
            );
            return;
        }
        const node = appState.nodes.find((n) => n.id === appState.selectedNodeId);
        if (node) {
            const onBack = () => {
                appState.selectedNodeId = null;
                _updateTelegramBackButton(null);
                _renderAdminView();
            };
            _updateTelegramBackButton(onBack);
            renderNodeDetailView(root, node, onBack, _reloadNodesAndRender);
            window.scrollTo(0, 0);
            return;
        }
        appState.selectedNodeId = null;
    }

    if (appState.adminTab === "users" && appState.selectedUserId != null) {
        const usersList = (appState.usersData && appState.usersData.users) || [];
        const userSummary = usersList.find((u) => u.id === appState.selectedUserId) || {
            id: appState.selectedUserId,
            telegram_id: null,
        };
        const onBack = () => {
            appState.selectedUserId = null;
            _updateTelegramBackButton(null);
            _renderAdminView();
        };
        const reloadSeq = seq;
        const onReloadUser = async () => {
            try {
                const nextUsers = await api.listUsers(
                    appState.usersQuery || "",
                    appState.usersOffset || 0,
                    25,
                    appState.usersFilter || "all"
                );
                if (reloadSeq !== _tabSwitchSeq || appState.mode !== "admin") return;
                appState.usersData = nextUsers;
            } catch (err) {
                if (reloadSeq !== _tabSwitchSeq || appState.mode !== "admin") return;
                if (err && err.code === "2fa_required") return;
                showToast(err.message || "Не удалось обновить список пользователей", "error");
            }
        };
        _updateTelegramBackButton(onBack);
        window.scrollTo(0, 0);
        await renderUserDetailView(root, userSummary, onBack, onReloadUser, appState);
        return;
    }

    _updateTelegramBackButton(null);
    const activeTabMeta = _ADMIN_TABS.find((t) => t.id === appState.adminTab) || _ADMIN_TABS[0];
    const tabsHtml = _ADMIN_TABS
        .map((t) => {
            const isActive = appState.adminTab === t.id;
            return `<button type="button" class="tab-btn ${isActive ? "active" : ""}" data-atab="${t.id}"${isActive ? ' aria-current="page"' : ""}>${t.label}</button>`;
        })
        .join("");

    root.innerHTML = `
        <nav class="tabs-bar" aria-label="Разделы админки">${tabsHtml}</nav>
        <div class="admin-page-head">
            <h1 class="admin-page-title">${escapeHtml(activeTabMeta.label)}</h1>
        </div>
        <div id="adminTabBody" class="app-main">
            <section class="card card-center"><div class="muted">Загрузка...</div></section>
        </div>
    `;

    const activeBtn = root.querySelector(".tabs-bar .tab-btn.active");
    activeBtn?.scrollIntoView({ inline: "center", block: "nearest", behavior: "instant" });

    root.querySelectorAll("[data-atab]").forEach((btn) => {
        btn.addEventListener("click", () => {
            const nextTab = btn.dataset.atab;
            if (!nextTab || nextTab === appState.adminTab) return;
            closeAllModals();
            if (appState.adminTab === "broadcasts" && nextTab !== "broadcasts") {
                clearBroadcastPollTimer();
            }
            appState.adminTab = nextTab;
            appState.selectedNodeId = null;
            appState.selectedUserId = null;
            _renderAdminView();
        });
    });

    const body = root.querySelector("#adminTabBody");
    if (!body) return;

    try {
        if (appState.adminTab === "overview") {
            await renderAdminOverviewView(body, appState);
        } else if (appState.adminTab === "nodes") {
            if (!appState.nodesLoaded && !nodesFetchedThisRender) {
                try {
                    const res = await api.listNodes();
                    if (seq !== _tabSwitchSeq || appState.mode !== "admin") return;
                    appState.nodes = res.nodes || [];
                    appState.nodesLoaded = true;
                } catch (err) {
                    if (seq !== _tabSwitchSeq || appState.mode !== "admin") return;
                    if (err && err.code === "2fa_required") return;
                    nodesFetchError = err;
                    showToast(err.message || "Не удалось загрузить ноды", "error");
                }
            }
            if (nodesFetchError && !appState.nodesLoaded) {
                _renderTabErrorCard(
                    body,
                    "Не удалось загрузить ноды",
                    nodesFetchError.message || "Ошибка запроса",
                    () => _renderAdminView()
                );
                return;
            }
            renderNodesView(body, appState, _reloadNodesAndRender, (id) => {
                appState.selectedNodeId = id;
                _renderAdminView();
            });
        } else if (appState.adminTab === "subscription") {
            let subNodesErr = null;
            if (!appState.nodesLoaded) {
                try {
                    const res = await api.listNodes();
                    if (seq !== _tabSwitchSeq || appState.mode !== "admin") return;
                    appState.nodes = res.nodes || [];
                    appState.nodesLoaded = true;
                } catch (err) {
                    if (err && err.code === "2fa_required") throw err;
                    subNodesErr = err;
                }
            }
            if (seq !== _tabSwitchSeq || appState.mode !== "admin") return;
            if (subNodesErr && !appState.nodesLoaded) {
                showToast(subNodesErr.message || "Не удалось загрузить ноды", "error");
                _renderTabErrorCard(
                    body,
                    "Не удалось загрузить локации подписки",
                    subNodesErr.message || "Ошибка запроса",
                    () => _renderAdminView()
                );
                return;
            }
            await renderSubscriptionSettingsView(body, appState, _reloadNodesAndRender);
        } else if (appState.adminTab === "users") {
            let usersErr = null;
            if (!appState.usersData) {
                try {
                    const res = await api.listUsers(
                        appState.usersQuery || "",
                        appState.usersOffset || 0,
                        25,
                        appState.usersFilter || "all"
                    );
                    if (seq !== _tabSwitchSeq || appState.mode !== "admin") return;
                    appState.usersData = res;
                } catch (err) {
                    if (err && err.code === "2fa_required") throw err;
                    usersErr = err;
                }
            }
            if (seq !== _tabSwitchSeq || appState.mode !== "admin") return;
            if (usersErr && !appState.usersData) {
                showToast(usersErr.message || "Не удалось загрузить пользователей", "error");
                _renderTabErrorCard(
                    body,
                    "Не удалось загрузить пользователей",
                    usersErr.message || "Ошибка запроса",
                    () => _renderAdminView()
                );
                return;
            }
            renderAdminUsersView(body, appState, (u) => {
                appState.selectedUserId = u.id;
                _renderAdminView();
            });
        } else if (appState.adminTab === "broadcasts") {
            let bcErr = null;
            if (!appState.broadcastsData) {
                try {
                    const res = await api.listBroadcasts();
                    if (seq !== _tabSwitchSeq || appState.mode !== "admin") return;
                    appState.broadcastsData = res.broadcasts || [];
                    appState.broadcastsTotal = res.total ?? appState.broadcastsData.length;
                    appState.broadcastsOffset = res.offset ?? 0;
                    appState.broadcastsLimit = res.limit ?? 15;
                } catch (err) {
                    if (err && err.code === "2fa_required") throw err;
                    bcErr = err;
                }
            }
            if (seq !== _tabSwitchSeq || appState.mode !== "admin") return;
            if (bcErr && !appState.broadcastsData) {
                showToast(bcErr.message || "Не удалось загрузить рассылки", "error");
                _renderTabErrorCard(
                    body,
                    "Не удалось загрузить рассылки",
                    bcErr.message || "Ошибка запроса",
                    () => _renderAdminView()
                );
                return;
            }
            renderAdminBroadcastsView(body, appState);
        } else {
            const subTab = appState.finSubTab || "orders";
            if (subTab === "audit") {
                if (appState.auditLogsData == null && !appState.auditLoadAttempted) {
                    try {
                        const res = await api.listAuditLogs(
                            appState.auditAction || "",
                            appState.auditQuery || "",
                            0,
                            40,
                            appState.auditTargetType || ""
                        );
                        if (seq !== _tabSwitchSeq || appState.mode !== "admin") return;
                        appState.auditLogsData = res;
                        appState.auditLoadAttempted = true;
                    } catch (err) {
                        if (err && err.code === "2fa_required") throw err;
                        appState.auditLoadAttempted = true;
                        showToast(err.message || "Не удалось загрузить аудит-лог", "error");
                    }
                }
            } else if (subTab === "webhooks") {
                if (appState.webhooksData == null && !appState.webhooksLoadAttempted) {
                    try {
                        const res = await api.getAdminWebhooks();
                        if (seq !== _tabSwitchSeq || appState.mode !== "admin") return;
                        appState.webhooksData = res.webhooks || [];
                        appState.webhooksLoadAttempted = true;
                    } catch (err) {
                        if (err && err.code === "2fa_required") throw err;
                        appState.webhooksLoadAttempted = true;
                        showToast(err.message || "Не удалось загрузить вебхуки", "error");
                    }
                }
            } else if (appState.financesData == null && !appState.financesLoadAttempted) {
                const kind = ["orders", "subscriptions", "topups"].includes(subTab) ? subTab : "orders";
                try {
                    const res = await api.listFinances(
                        kind,
                        appState.finStatus || "",
                        appState.finQuery || "",
                        0,
                        25,
                        Boolean(appState.finAttentionOnly)
                    );
                    if (seq !== _tabSwitchSeq || appState.mode !== "admin") return;
                    appState.financesData = res;
                    appState.financesLoadAttempted = true;
                } catch (err) {
                    if (err && err.code === "2fa_required") throw err;
                    appState.financesLoadAttempted = true;
                    showToast(err.message || "Не удалось загрузить финансы", "error");
                }
            }
            if (seq !== _tabSwitchSeq || appState.mode !== "admin") return;
            renderAdminFinancesView(body, appState);
        }
    } catch (err) {
        if (seq !== _tabSwitchSeq || appState.mode !== "admin") return;
        if (err && err.code === "2fa_required") return;
        showToast(err.message || "Ошибка загрузки раздела", "error");
        _renderTabErrorCard(
            body,
            "Ошибка загрузки раздела",
            err.message || "Попробуйте повторить запрос",
            () => _renderAdminView()
        );
    }
}

async function _reloadNodesAndRender({ skipRender = false, removeNodeId = null } = {}) {
    const seq = _tabSwitchSeq;
    if (removeNodeId != null) {
        appState.nodes = appState.nodes.filter((n) => n.id !== removeNodeId);
    }
    try {
        const res = await api.listNodes();
        if (seq !== _tabSwitchSeq || appState.mode !== "admin") return false;
        appState.nodes = res.nodes || [];
        appState.nodesLoaded = true;
    } catch (err) {
        if (err && err.code === "2fa_required") return false;
        if (seq !== _tabSwitchSeq || appState.mode !== "admin") return false;
        showToast(err.message || "Не удалось загрузить ноды", "error");
    }
    if (
        !skipRender &&
        seq === _tabSwitchSeq &&
        appState.mode === "admin" &&
        (appState.adminTab === "nodes" || appState.adminTab === "subscription")
    ) {
        _renderAdminView();
    }
    return true;
}

function _openAdmin2FAGate(root) {
    clearBroadcastPollTimer();
    ++_tabSwitchSeq;
    _syncModeSwitch();
    _updateTelegramBackButton(() => {
        closeAllModals();
        appState.pendingDeepLink = null;
        appState.mode = "cabinet";
        _renderAdminView();
    });
    renderSecurity2FA(root, appState.security, async (res) => {
        const isConfigured = Boolean(
            !res || res.totp_configured || res.configured || res.two_factor_configured || res.verified
        );
        appState.security = {
            ...(appState.security || {}),
            totp_enabled: true,
            totp_verified: true,
            totp_configured: isConfigured,
            configured: isConfigured,
            two_factor_configured: isConfigured,
            two_factor_verified: true,
            two_factor_mode: "totp",
        };
        appState.twoFactorPassed = true;
        appState.mode = "admin";
        if (appState.pendingDeepLink) {
            _applyDeepLink(appState.pendingDeepLink);
            appState.pendingDeepLink = null;
        }
        await _renderAdminView();
    });
}

setOn2FARequired(() => {
    clearBroadcastPollTimer();
    closeAllModals();
    appState.twoFactorPassed = false;
    const root = document.getElementById("appRoot");
    if (root && appState.mode === "admin") {
        _openAdmin2FAGate(root);
    }
});

setOnInitDataExpired(() => {
    clearBroadcastPollTimer();
    closeAllModals();
    showToast("Сессия Telegram устарела. Закройте и откройте Mini App заново.", "error");
    const root = document.getElementById("appRoot");
    if (root && !appState.user) {
        _renderErrorCard(
            root,
            "Сессия устарела",
            "Закройте и откройте Mini App заново через Telegram-бота."
        );
    }
});

async function bootstrap() {
    _initTelegramChrome();

    const root = document.getElementById("appRoot");
    const userChip = document.getElementById("userChip");
    const modeSwitch = document.getElementById("modeSwitch");

    if (modeSwitch && !modeSwitch.dataset.bound) {
        modeSwitch.dataset.bound = "1";
        modeSwitch.querySelectorAll("[data-mode]").forEach((btn) => {
            btn.addEventListener("click", async () => {
                const nextMode = btn.dataset.mode;
                if (nextMode === appState.mode) return;
                closeAllModals();
                if (nextMode === "cabinet" && !appState.twoFactorPassed) {
                    appState.pendingDeepLink = null;
                }
                appState.mode = nextMode;
                if (nextMode === "admin") {
                    if (!appState.twoFactorPassed) {
                        _openAdmin2FAGate(root);
                        return;
                    }
                }
                _renderAdminView();
            });
        });
    }

    try {
        const me = await api.getMe();
        appState.user = me.user;
        appState.security = me.security;
        userChip.textContent = me.user.name || "";

        if (!me.user.is_admin) {
            if (modeSwitch) modeSwitch.hidden = true;
            _syncModeSwitch();
            await loadAndRenderUserCabinet(root);
            return;
        }

        appState.twoFactorPassed = Boolean(me.security && me.security.two_factor_verified);

        if (appState.pendingDeepLink && appState.pendingDeepLink.mode === "admin") {
            appState.mode = "admin";
            if (!appState.twoFactorPassed) {
                await _openAdmin2FAGate(root);
                return;
            }
            _applyDeepLink(appState.pendingDeepLink);
            appState.pendingDeepLink = null;
            await _renderAdminView();
            return;
        }

        appState.mode = "cabinet";
        await _renderAdminView();
    } catch (err) {
        if (err && err.code === "init_data_expired") {
            return;
        }
        const isUnauthorized = Boolean(err && err.status === 401);
        _renderErrorCard(
            root,
            "Вход через Telegram",
            isUnauthorized
                ? "Откройте панель через кнопку внутри Telegram-бота."
                : "Сервис временно недоступен.",
            !isUnauthorized
        );
    }
}

bootstrap();
