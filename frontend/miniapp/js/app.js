import { api } from "./api.js";
import { showToast } from "./ui.js";
import { renderGuestStub } from "./views/guest_stub.js";
import { renderSecurity2FAStub } from "./views/security_2fa_stub.js";
import { renderNodesView } from "./views/nodes.js";

const appState = {
    user: null,
    security: null,
    twoFactorPassed: false,
    nodes: [],
};

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

async function _reloadNodesAndRender() {
    try {
        const res = await api.listNodes();
        appState.nodes = res.nodes || [];
    } catch (err) {
        showToast(err.message || "Не удалось загрузить ноды");
    }
    renderNodesView(document.getElementById("appRoot"), appState, _reloadNodesAndRender);
}

async function bootstrap() {
    _initTelegramChrome();

    const root = document.getElementById("appRoot");
    const userChip = document.getElementById("userChip");
    const roleChip = document.getElementById("roleChip");

    try {
        const me = await api.getMe();
        appState.user = me.user;
        appState.security = me.security;
        userChip.textContent = me.user.name || "";

        if (!me.user.is_admin) {
            roleChip.textContent = "Пользователь";
            roleChip.hidden = false;
            renderGuestStub(root, me.user);
            return;
        }

        roleChip.textContent = "Admin";
        roleChip.className = "chip chip-ok";
        roleChip.hidden = false;

        if (!appState.twoFactorPassed) {
            renderSecurity2FAStub(root, me.security, async () => {
                appState.twoFactorPassed = true;
                await _reloadNodesAndRender();
            });
            return;
        }

        await _reloadNodesAndRender();
    } catch (err) {
        renderGuestStub(
            root,
            null,
            err && err.status === 401
                ? "Откройте панель через кнопку внутри Telegram-бота."
                : "Сервис временно недоступен."
        );
    }
}

bootstrap();
