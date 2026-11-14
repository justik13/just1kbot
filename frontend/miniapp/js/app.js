import { api } from "./api.js";
import { escapeHtml, showToast } from "./ui.js";
import { renderSecurity2FA } from "./views/security_2fa.js";
import { renderNodesView } from "./views/nodes.js";
import { renderNodeDetailView } from "./views/node_detail.js";
import { renderAdminUsersView } from "./views/admin_users.js";
import { loadAndRenderUserCabinet } from "./views/user_cabinet.js";

const appState = {
    user: null,
    security: null,
    twoFactorPassed: false,
    mode: "cabinet",
    adminTab: "nodes",
    nodes: [],
    selectedNodeId: null,
    usersData: null,
    usersQuery: "",
};

let _tgBackListener = null;

function _initTelegramChrome() {
    const tg = window.Telegram && window.Telegram.WebApp;
    if (!tg) return;
    try {
        tg.ready();
        tg.expand();
        tg.setHeaderColor("#050505");
        tg.setBackgroundColor("#050505");
    } catch {
        // Non-critical in older Telegram clients
    }
}

function _updateTelegramBackButton(onBack) {
    const tg = window.Telegram && window.Telegram.WebApp;
    if (!tg || !tg.BackButton) return;
    try {
        if (_tgBackListener) {
            tg.BackButton.offClick(_tgBackListener);
            _tgBackListener = null;
        }
        if (onBack) {
            _tgBackListener = onBack;
            tg.BackButton.onClick(_tgBackListener);
            tg.BackButton.show();
        } else {
            tg.BackButton.hide();
        }
    } catch {
        // Non-critical
    }
}

function _renderErrorCard(root, title, desc) {
    root.innerHTML = `
        <section class="card card-center">
            <span class="chip chip-info">Доступ</span>
            <div class="card-title">${escapeHtml(title)}</div>
            <p class="muted">${escapeHtml(desc)}</p>
        </section>
    `;
}

function _syncModeSwitch() {
    const modeSwitch = document.getElementById("modeSwitch");
    if (!modeSwitch) return;
    const isAdmin = Boolean(appState.user && appState.user.is_admin);
    modeSwitch.hidden = !isAdmin;
    if (!isAdmin) return;
    modeSwitch.querySelectorAll("[data-mode]").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.mode === appState.mode);
    });
}

function _renderAdminView() {
    const root = document.getElementById("appRoot");
    if (!root) return;
    _syncModeSwitch();

    if (appState.mode === "cabinet") {
        appState.selectedNodeId = null;
        _updateTelegramBackButton(null);
        loadAndRenderUserCabinet(root);
        return;
    }

    if (appState.adminTab === "nodes" && appState.selectedNodeId != null) {
        const node = appState.nodes.find((n) => n.id === appState.selectedNodeId);
        if (node) {
            const onBack = () => {
                appState.selectedNodeId = null;
                _updateTelegramBackButton(null);
                _renderAdminView();
            };
            _updateTelegramBackButton(onBack);
            renderNodeDetailView(root, node, onBack, _reloadNodesAndRender);
            return;
        }
        appState.selectedNodeId = null;
    }

    _updateTelegramBackButton(null);
    root.innerHTML = `
        <nav class="tabs-bar">
            <button type="button" class="tab-btn ${appState.adminTab === "nodes" ? "active" : ""}" data-atab="nodes">Ноды</button>
            <button type="button" class="tab-btn ${appState.adminTab === "users" ? "active" : ""}" data-atab="users">Пользователи</button>
        </nav>
        <div id="adminTabBody" class="app-main"></div>
    `;

    root.querySelectorAll("[data-atab]").forEach((btn) => {
        btn.addEventListener("click", async () => {
            appState.adminTab = btn.dataset.atab;
            appState.selectedNodeId = null;
            if (appState.adminTab === "users" && !appState.usersData) {
                try {
                    appState.usersData = await api.listUsers(appState.usersQuery || "");
                } catch (err) {
                    showToast(err.message || "Не удалось загрузить пользователей");
                }
            }
            _renderAdminView();
        });
    });

    const body = root.querySelector("#adminTabBody");
    if (appState.adminTab === "nodes") {
        renderNodesView(body, appState, _reloadNodesAndRender, (id) => {
            appState.selectedNodeId = id;
            _renderAdminView();
        });
    } else {
        renderAdminUsersView(body, appState);
    }
}

async function _reloadNodesAndRender() {
    try {
        const res = await api.listNodes();
        appState.nodes = res.nodes || [];
    } catch (err) {
        if (err && err.code === "2fa_required") {
            appState.twoFactorPassed = false;
            const root = document.getElementById("appRoot");
            if (root && appState.mode === "admin") {
                _openAdmin2FAGate(root);
            }
            return;
        }
        showToast(err.message || "Не удалось загрузить ноды");
    }
    _renderAdminView();
}

function _openAdmin2FAGate(root) {
    _syncModeSwitch();
    _updateTelegramBackButton(() => {
        appState.mode = "cabinet";
        _renderAdminView();
    });
    renderSecurity2FA(root, appState.security, async () => {
        appState.twoFactorPassed = true;
        appState.mode = "admin";
        await _reloadNodesAndRender();
    });
}

async function bootstrap() {
    _initTelegramChrome();

    const root = document.getElementById("appRoot");
    const userChip = document.getElementById("userChip");
    const roleChip = document.getElementById("roleChip");
    const modeSwitch = document.getElementById("modeSwitch");

    if (modeSwitch && !modeSwitch.dataset.bound) {
        modeSwitch.dataset.bound = "1";
        modeSwitch.querySelectorAll("[data-mode]").forEach((btn) => {
            btn.addEventListener("click", async () => {
                const nextMode = btn.dataset.mode;
                if (nextMode === appState.mode) return;
                appState.mode = nextMode;
                if (nextMode === "admin") {
                    if (!appState.twoFactorPassed) {
                        _openAdmin2FAGate(root);
                        return;
                    }
                    if (!appState.nodes.length) {
                        await _reloadNodesAndRender();
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
            roleChip.hidden = true;
            if (modeSwitch) modeSwitch.hidden = true;
            await loadAndRenderUserCabinet(root);
            return;
        }

        roleChip.hidden = true;
        appState.twoFactorPassed = Boolean(me.security && me.security.two_factor_verified);
        appState.mode = "cabinet";
        _renderAdminView();
    } catch (err) {
        _renderErrorCard(
            root,
            "Вход через Telegram",
            err && err.status === 401
                ? "Откройте панель через кнопку внутри Telegram-бота."
                : "Сервис временно недоступен."
        );
    }
}

bootstrap();
