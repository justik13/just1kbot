import { api, setAdminSessionToken } from "../api.js";
import { copyText, escapeHtml, showToast } from "../ui.js";

export function renderSecurity2FA(root, securityMeta, onVerified) {
    const mode = (securityMeta && securityMeta.two_factor_mode) || "totp";
    const configured = Boolean(securityMeta && securityMeta.two_factor_configured);

    if (!configured || mode === "setup_required") {
        _renderSetupScreen(root, onVerified);
        return;
    }

    const isPin = mode === "pin";
    root.innerHTML = `
        <section class="card">
            <div class="row">
                <div class="card-title">Подтверждение входа</div>
                <span class="chip chip-info">${isPin ? "PIN" : "TOTP 2FA"}</span>
            </div>
            <p class="muted">
                ${isPin ? "Введите секретный PIN-код администратора." : "Введите 6-значный код из приложения-аутентификатора."}
            </p>
            <form id="twoFaForm" class="field">
                <label for="twoFaCodeInput">${isPin ? "Секретный PIN" : "Одноразовый код (6 цифр)"}</label>
                <input id="twoFaCodeInput" class="input" type="${isPin ? "password" : "text"}"
                    ${isPin ? "" : 'inputmode="numeric" maxlength="6"'} required
                    placeholder="${isPin ? "••••" : "000000"}" autocomplete="one-time-code">
                <button id="twoFaSubmitBtn" type="submit" class="btn btn-primary" style="margin-top:6px;">
                    Войти в панель
                </button>
            </form>
        </section>
    `;

    const form = root.querySelector("#twoFaForm");
    const btn = root.querySelector("#twoFaSubmitBtn");
    const input = root.querySelector("#twoFaCodeInput");
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
    let activeTab = "totp";
    let setupState = null;

    function draw() {
        root.innerHTML = `
            <section class="card">
                <div class="row">
                    <div class="card-title">Настройка 2FA администратора</div>
                    <span class="chip chip-warn">Первая настройка</span>
                </div>
                <p class="muted">
                    Выберите способ защиты админ-панели. После настройки вход будет требовать код.
                </p>
                <div class="filter-pills">
                    <button type="button" class="pill-btn ${activeTab === "totp" ? "active" : ""}" data-tab="totp">
                        TOTP (Аутентификатор)
                    </button>
                    <button type="button" class="pill-btn ${activeTab === "pin" ? "active" : ""}" data-tab="pin">
                        Секретный PIN
                    </button>
                </div>

                ${activeTab === "totp" ? _totpSetupHtml(setupState) : _pinSetupHtml()}
            </section>
        `;

        root.querySelectorAll("[data-tab]").forEach((b) => {
            b.addEventListener("click", () => {
                activeTab = b.dataset.tab;
                draw();
            });
        });

        if (activeTab === "totp") {
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
            const verifyForm = root.querySelector("#verifyTotpSetupForm");
            if (verifyForm && setupState) {
                verifyForm.addEventListener("submit", async (e) => {
                    e.preventDefault();
                    const btn = root.querySelector("#confirmTotpBtn");
                    const code = root.querySelector("#setupTotpCodeInput").value.trim();
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
        } else {
            const pinForm = root.querySelector("#setupPinForm");
            pinForm.addEventListener("submit", async (e) => {
                e.preventDefault();
                const pin1 = root.querySelector("#pinInput1").value.trim();
                const pin2 = root.querySelector("#pinInput2").value.trim();
                if (pin1.length < 4) {
                    showToast("Минимум 4 символа");
                    return;
                }
                if (pin1 !== pin2) {
                    showToast("PIN-коды не совпадают");
                    return;
                }
                const btn = root.querySelector("#savePinBtn");
                btn.disabled = true;
                try {
                    const res = await api.setup2FAPin(pin1);
                    if (res.verified && res.session_token) {
                        setAdminSessionToken(res.session_token);
                        showToast("PIN установлен");
                        onVerified();
                    }
                } catch (err) {
                    showToast(err.message || "Ошибка сохранения PIN");
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
            <button id="copyTotpSecretBtn" type="button" class="btn btn-sm">Копировать ключ</button>
        </div>
        <form id="verifyTotpSetupForm" class="field">
            <label for="setupTotpCodeInput">Введите 6-значный код из приложения для подтверждения</label>
            <input id="setupTotpCodeInput" class="input" type="text" inputmode="numeric" maxlength="6" required placeholder="000000">
            <button id="confirmTotpBtn" type="submit" class="btn btn-primary">Подтвердить и войти</button>
        </form>
    `;
}

function _pinSetupHtml() {
    return `
        <form id="setupPinForm" class="field">
            <label for="pinInput1">Придумайте PIN (от 4 до 32 символов)</label>
            <input id="pinInput1" class="input" type="password" minlength="4" maxlength="32" required placeholder="••••">
            <label for="pinInput2">Повторите PIN</label>
            <input id="pinInput2" class="input" type="password" minlength="4" maxlength="32" required placeholder="••••">
            <button id="savePinBtn" type="submit" class="btn btn-primary" style="margin-top:6px;">
                Сохранить PIN и войти
            </button>
        </form>
    `;
}
