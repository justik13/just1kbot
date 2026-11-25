import { api, setAdminSessionToken } from "../api.js?v=2.8.0";
import { copyText, escapeHtml, showToast } from "../ui.js?v=2.8.0";

function _applyClipCode(input, rawText) {
    const digits = String(rawText || "").replace(/\D+/g, "").slice(0, 6);
    if (!digits) {
        showToast("В буфере нет цифр кода", "warn");
        return false;
    }
    input.value = digits;
    input.focus({ preventScroll: true });
    input.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
}

function _bindPasteCodeButton(btn, input) {
    if (!btn || !input) return;
    btn.addEventListener("click", async () => {
        const tgWebApp = window.Telegram && window.Telegram.WebApp;
        if (tgWebApp && typeof tgWebApp.readTextFromClipboard === "function") {
            let handled = false;
            const timer = setTimeout(() => {
                if (handled) return;
                handled = true;
                _tryNavigatorClipboard(input);
            }, 350);
            try {
                tgWebApp.readTextFromClipboard((clipText) => {
                    if (handled) return;
                    handled = true;
                    clearTimeout(timer);
                    if (clipText) {
                        _applyClipCode(input, clipText);
                    } else {
                        _tryNavigatorClipboard(input);
                    }
                });
                return;
            } catch {
                clearTimeout(timer);
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
            if (text) {
                _applyClipCode(input, text);
                return;
            }
        } catch {
            // Clipboard permission blocked inside WebView
        }
    }
    input.focus();
    try {
        if (document.execCommand && document.execCommand("paste") && input.value) {
            _applyClipCode(input, input.value);
            return;
        }
    } catch {
        // Ignore execCommand failure
    }
    showToast("Доступ к буферу ограничен — удерживайте поле и нажмите «Вставить»");
}

function _bindTotpForm({ form, input, btn, submitLabel, onSubmit }) {
    if (!form || !input || !btn) return;
    let submitting = false;
    let retryTimer = null;

    const clearRetryTimer = () => {
        if (retryTimer) {
            clearInterval(retryTimer);
            retryTimer = null;
        }
    };

    const startRetryCountdown = (seconds) => {
        clearRetryTimer();
        let left = Math.max(1, Math.ceil(Number(seconds) || 0));
        btn.disabled = true;
        btn.textContent = `Повторите через ${left} с`;
        retryTimer = setInterval(() => {
            if (!document.contains(btn)) {
                clearRetryTimer();
                return;
            }
            left -= 1;
            if (left <= 0) {
                clearRetryTimer();
                btn.disabled = false;
                btn.textContent = submitLabel;
                input.focus({ preventScroll: true });
                return;
            }
            btn.textContent = `Повторите через ${left} с`;
        }, 1000);
    };

    requestAnimationFrame(() => {
        if (document.contains(input)) {
            input.focus({ preventScroll: true });
        }
    });

    input.addEventListener("input", (e) => {
        if (e && e.isComposing) return;
        const cleaned = input.value.replace(/\D+/g, "").slice(0, 6);
        if (input.value !== cleaned) {
            input.value = cleaned;
        }
        if (cleaned.length === 6 && !submitting && !retryTimer) {
            if (typeof form.requestSubmit === "function") {
                form.requestSubmit();
            } else {
                form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
            }
        }
    });

    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (submitting || retryTimer) return;
        const code = input.value.replace(/\D+/g, "").slice(0, 6);
        if (code.length !== 6) {
            showToast("Введите 6 цифр кода", "warn");
            input.focus({ preventScroll: true });
            return;
        }
        submitting = true;
        btn.disabled = true;
        btn.textContent = "Проверка";
        try {
            await onSubmit(code);
            clearRetryTimer();
        } catch (err) {
            showToast((err && err.message) || "Неверный код", "error");
            const retryAfter = err && ((err.data && err.data.retry_after) || (err.payload && err.payload.retry_after));
            input.select();
            input.focus({ preventScroll: true });
            if (retryAfter && Number(retryAfter) > 0) {
                startRetryCountdown(retryAfter);
            } else {
                btn.disabled = false;
                btn.textContent = submitLabel;
            }
            return;
        } finally {
            submitting = false;
        }
        if (!retryTimer) {
            btn.disabled = false;
            btn.textContent = submitLabel;
        }
    });
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
                <h1 class="card-title">Подтверждение входа</h1>
                <span class="chip chip-muted">TOTP 2FA</span>
            </div>
            <p class="muted">
                Введите 6-значный код из приложения-аутентификатора.
            </p>
            <form id="twoFaForm" class="field">
                <label for="twoFaCodeInput">Одноразовый код (6 цифр)</label>
                <div class="row" style="flex-wrap:nowrap;">
                    <input id="twoFaCodeInput" class="input" type="text"
                        inputmode="numeric" maxlength="6" required
                        placeholder="000000" autocomplete="one-time-code">
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

    _bindTotpForm({
        form,
        input,
        btn,
        submitLabel: "Войти в панель",
        onSubmit: async (code) => {
            const res = await api.verify2FA(code);
            if (res.verified && res.session_token) {
                setAdminSessionToken(res.session_token);
                showToast("Доступ подтверждён");
                onVerified(res);
            }
        },
    });
}

function _renderSetupScreen(root, onVerified) {
    let setupState = null;

    function draw() {
        root.innerHTML = `
            <section class="card">
                <div class="row">
                    <h1 class="card-title">Настройка TOTP 2FA администратора</h1>
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
                    const nextSetup = await api.setup2FA();
                    if (!root.isConnected || !document.contains(genBtn)) return;
                    setupState = nextSetup;
                    draw();
                } catch (err) {
                    if (!root.isConnected) return;
                    showToast(err.message || "Ошибка генерации ключа", "error");
                } finally {
                    if (document.contains(genBtn)) {
                        genBtn.disabled = false;
                    }
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
            const confirmBtn = root.querySelector("#confirmTotpBtn");
            _bindPasteCodeButton(root.querySelector("#setupTotpPasteBtn"), setupInput);

            _bindTotpForm({
                form: verifyForm,
                input: setupInput,
                btn: confirmBtn,
                submitLabel: "Подтвердить и войти",
                onSubmit: async (code) => {
                    const res = await api.verify2FA(code, setupState.setup_token);
                    if (res.verified && res.session_token) {
                        setAdminSessionToken(res.session_token);
                        showToast("TOTP 2FA включена");
                        onVerified(res);
                    }
                },
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
            <span class="meta-text">Секретный ключ (Base32)</span>
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
