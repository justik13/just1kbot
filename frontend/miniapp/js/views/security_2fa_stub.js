import { api } from "../api.js";
import { escapeHtml, showToast } from "../ui.js";

export function renderSecurity2FAStub(root, securityMeta, onVerified) {
    const todoText = escapeHtml(
        (securityMeta && securityMeta.two_factor_todo) || "TODO: TOTP 2FA verification pending"
    );

    root.innerHTML = `
        <section class="card">
            <div class="row">
                <div class="card-title">Двухфакторная проверка</div>
                <span class="chip chip-warn">2FA TODO</span>
            </div>
            <p class="muted">
                Временная заглушка перед подключением TOTP-генератора (Google Authenticator / 2FA).
            </p>
            <div class="meta-list">
                <div class="mono">${todoText}</div>
            </div>
            <form id="twoFaStubForm" class="field">
                <label for="twoFaCodeInput">Одноразовый код (необязательно для заглушки)</label>
                <input id="twoFaCodeInput" class="input" type="text" inputmode="numeric" maxlength="16" placeholder="000000">
                <button id="twoFaSubmitBtn" type="submit" class="btn btn-primary" style="margin-top:6px;">
                    Продолжить в панель (TODO 2FA)
                </button>
            </form>
        </section>
    `;

    const form = root.querySelector("#twoFaStubForm");
    const btn = root.querySelector("#twoFaSubmitBtn");
    const input = root.querySelector("#twoFaCodeInput");
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        btn.disabled = true;
        try {
            const res = await api.verify2FAStub(input.value || "");
            if (res.verified) {
                showToast("Вход разрешён (2FA заглушка)");
                onVerified();
            }
        } catch (err) {
            showToast(err.message || "Ошибка проверки 2FA");
        } finally {
            btn.disabled = false;
        }
    });
}
