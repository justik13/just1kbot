import { api, setAdminSessionToken } from "../api.js";
import { copyText, escapeHtml, showToast } from "../ui.js";

function _applyClipCode(input, rawText) {
    const digits = String(rawText || "").replace(/\D+/g, "").slice(0, 6);
    if (!digits) {
        showToast("В буфере нет цифр кода");
        return false;
    }
    input.value = digits;
    input.focus();
    return true;
}

function _bindPasteCodeButton(btn, input) {
    if (!btn || !input) return;
    btn.addEventListener("click", async () => {
        const tgWebApp = window.Telegram && window.Telegram.WebApp;
        if (tgWebApp && typeof tgWebApp.readTextFromClipboard === "function") {
            let handled = false;
            try {
                tgWebApp.readTextFromClipboard((clipText) => {
                    if (handled) return;
                    handled = true;
                    if (clipText !== null && clipText !== undefined) {
                        _applyClipCode(input, clipText);
                    } else {
                        _tryNavigatorClipboard(input);
                    }
                });
                return;
            } catch {
                // Fall through to navigator.clipboard
            }
        }
        await _tryNavigatorClipboard(input);
    });
}

async function _tryNavigatorClipboard(input) {
    if (navigator.clipboard && typeof navigator.clipboard.readText === "function") {
        try {
            const text = await navigator.clipboard.readText();
            _applyClipCode(input, text);
            return;
        } catch {
            // Clipboard permission blocked inside WebView
        }
    }
    input.focus();
    showToast("Нажмите и удерживайте поле, чтобы вставить код");
}

export function renderSecurity2FA(root, securityMeta, onVerified) {
    const mode = (securityMeta && securityMeta.two_factor_mode) || "totp";
    const configured = Boolean(securityMeta && securityMeta.two_factor_configured);

    if (!configured || mode === "setup_required") {
        _renderSetupScreen(root, onVerified);
        return;
    }

    root.innerHTML = `
        <section class="card">
            <div class="row">
                <div class="card-title">Подтверждение входа</div>
                <span class="chip chip-info">TOTP 2FA</span>
            </div>
            <p class="muted">
                Введите 6-значный код из приложения-аутентификатора.
            </p>
            <form id="twoFaForm" class="field">
                <label for="twoFaCodeInput">Одноразовый код (6 цифр)</label>
                <div class="row" style="flex-wrap:nowrap;">
                    <input id="twoFaCodeInput" class="input" type="text"
                        inputmode="numeric" maxlength="6" required
                        placeholder="000000" autocomplete="off">
                    <button id="twoFaPasteBtn" type="button" class="btn btn-sm">Вставить</button>
                </div>
                <button id="twoFaSubmitBtn" type="submit" class="btn btn-primary" style="margin-top:6px;">
                    Войти в панель
                </button>
            </form>
        </section>
    `;

    const form = root.querySelector("#twoFaForm");
    const btn = root.querySelector("#twoFaSubmitBtn");
    const input = root.querySelector("#twoFaCodeInput");
    _bindPasteCodeButton(root.querySelector("#twoFaPasteBtn"), input);

    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        btn.disabled = true;
        try {
            const res = await api.verify2FA(input.value.trim());
            if (res.verified && res.session_token) {
                setAdminSessionToken(res.session_token);
                showToast("Доступ подтверждён");
                onVerified();
            }
        } catch (err) {
            showToast(err.message || "Неверный код");
        } finally {
            btn.disabled = false;
        }
    });
}

function _renderSetupScreen(root, onVerified) {
    let setupState = null;

    function draw() {
        root.innerHTML = `
            <section class="card">
                <div class="row">
                    <div class="card-title">Настройка TOTP 2FA администратора</div>
                    <span class="chip chip-warn">Первая настройка</span>
                </div>
                <p class="muted">
                    Привяжите приложение-аутентификатор (Google Authenticator, 2FAS, Aegis и др.).
                    После настройки вход в панель будет требовать 6-значный код.
                </p>
                ${_totpSetupHtml(setupState)}
            </section>
        `;

        const genBtn = root.querySelector("#genTotpBtn");
        if (genBtn) {
            genBtn.addEventListener("click", async () => {
                genBtn.disabled = true;
                try {
                    setupState = await api.setup2FA();
                    draw();
                } catch (err) {
                    showToast(err.message || "Ошибка генерации ключа");
                } finally {
                    genBtn.disabled = false;
                }
            });
        }

        const copySecretBtn = root.querySelector("#copyTotpSecretBtn");
        if (copySecretBtn && setupState) {
            copySecretBtn.addEventListener("click", () => copyText(setupState.secret, "Ключ скопирован"));
        }

        const copyUriBtn = root.querySelector("#copyTotpUriBtn");
        if (copyUriBtn && setupState && setupState.otpauth_uri) {
            copyUriBtn.addEventListener("click", () => copyText(setupState.otpauth_uri, "Ссылка otpauth скопирована"));
        }

        const verifyForm = root.querySelector("#verifyTotpSetupForm");
        if (verifyForm && setupState) {
            const setupInput = root.querySelector("#setupTotpCodeInput");
            _bindPasteCodeButton(root.querySelector("#setupTotpPasteBtn"), setupInput);

            verifyForm.addEventListener("submit", async (e) => {
                e.preventDefault();
                const btn = root.querySelector("#confirmTotpBtn");
                const code = setupInput.value.trim();
                btn.disabled = true;
                try {
                    const res = await api.verify2FA(code, setupState.setup_token);
                    if (res.verified && res.session_token) {
                        setAdminSessionToken(res.session_token);
                        showToast("TOTP 2FA включена");
                        onVerified();
                    }
                } catch (err) {
                    showToast(err.message || "Неверный код");
                } finally {
                    btn.disabled = false;
                }
            });
        }
    }

    draw();
}

function _totpSetupHtml(setupState) {
    if (!setupState) {
        return `
            <button id="genTotpBtn" type="button" class="btn btn-primary">
                Сгенерировать TOTP-ключ
            </button>
        `;
    }
    return `
        <div class="field">
            <label>Секретный ключ (Base32)</label>
            <div class="sub-url-box">${escapeHtml(setupState.secret)}</div>
            <div class="btn-group">
                <button id="copyTotpSecretBtn" type="button" class="btn btn-sm">Копировать ключ</button>
                ${
                    setupState.otpauth_uri
                        ? '<button id="copyTotpUriBtn" type="button" class="btn btn-sm">Копировать otpauth://</button>'
                        : ""
                }
            </div>
        </div>
        <form id="verifyTotpSetupForm" class="field">
            <label for="setupTotpCodeInput">Введите 6-значный код из приложения для подтверждения</label>
            <div class="row" style="flex-wrap:nowrap;">
                <input id="setupTotpCodeInput" class="input" type="text" inputmode="numeric" maxlength="6" required placeholder="000000" autocomplete="off">
                <button id="setupTotpPasteBtn" type="button" class="btn btn-sm">Вставить</button>
            </div>
            <button id="confirmTotpBtn" type="submit" class="btn btn-primary">Подтвердить и войти</button>
        </form>
    `;
}
