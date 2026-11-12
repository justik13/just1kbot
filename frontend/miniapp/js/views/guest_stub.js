import { escapeHtml } from "../ui.js";

export function renderGuestStub(root, user = null, unauthorizedReason = "") {
    const name = user && user.name ? escapeHtml(user.name) : "гость";
    const desc = unauthorizedReason
        ? escapeHtml(unauthorizedReason)
        : "Личный кабинет находится в обновлении. Функции подключения появятся после завершения настройки инфраструктуры.";

    root.innerHTML = `
        <section class="card card-center">
            <span class="chip chip-info">Доступ в настройке</span>
            <div class="card-title">Привет, ${name}</div>
            <p class="muted">${desc}</p>
        </section>
    `;
}
