import { api } from "../api.js?v=2.8.0";
import {
    escapeHtml,
    formatRelativeTime,
    healthChipInfo,
    openModal,
    parseDate,
    renderCountryFlagField,
    renderFlagHtml,
    roleLabel,
    showToast,
    wireCountryFlagFields,
    withButtonLoading,
} from "../ui.js?v=2.8.0";

const _REFRESH_ICON = `<svg class="icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>`;
const _ROLE_ORDER = { ru_hub: 0, eu_hub: 1, exit_node: 2 };

let currentFilter = "all";
let _subSettingsReqId = 0;

function _isLastApplyFailed(node) {
    const la = node && node.last_apply && typeof node.last_apply === "object" ? node.last_apply : null;
    if (!la) return false;
    const st = String(la.status || "").toLowerCase();
    return (
        la.ok === false ||
        st === "failed" ||
        st === "error" ||
        st === "rollback" ||
        Boolean(la.rollback_error || la.error || la.rolled_back)
    );
}

function _isProblemNode(node) {
    if (!node) return false;
    if (node.is_active && node.health_state !== "ONLINE") return true;
    if (Number(node.module_errors_count || 0) > 0) return true;
    if (node.ingress_problem || node.cdn_problem) return true;
    if (node.disabled_reason) return true;
    if (
        node.traffic_limit &&
        typeof node.traffic_limit === "object" &&
        Number(node.traffic_limit.pct || 0) >= 80
    ) {
        return true;
    }
    if (node.ssl_cert && node.ssl_cert.days_left != null && Number(node.ssl_cert.days_left) < 14) {
        return true;
    }
    if (_isLastApplyFailed(node)) {
        return true;
    }
    const sys = node.system_load && typeof node.system_load === "object" ? node.system_load : null;
    if (sys) {
        const cpu = sys.cpu_percent != null ? Number(sys.cpu_percent) : 0;
        const ram = sys.ram_percent != null ? Number(sys.ram_percent) : 0;
        if (Math.max(Number.isFinite(cpu) ? cpu : 0, Number.isFinite(ram) ? ram : 0) >= 85) {
            return true;
        }
    }
    return false;
}

export function openLocationSubModal(node, onDone) {
    const inStdChecked = node.in_standard !== false;
    const inWiChecked = Boolean(node.in_white_internet);
    const baseLocName = String(node.vless_name || node.name || "").trim();
    const defaultYandexName = baseLocName ? `${baseLocName} Yandex` : "Yandex";
    const defaultTurboflareName = baseLocName ? `${baseLocName} Turboflare` : "Turboflare";
    const box = document.createElement("form");
    box.className = "field";
    box.style.gap = "12px";
    box.innerHTML = `
        ${renderCountryFlagField({ value: node.country_flag || "" })}
        <label for="locInStd" class="checkbox-row">
            <input id="locInStd" name="in_standard" type="checkbox" ${inStdChecked ? "checked" : ""}>
            <span>Входит в Стандарт</span>
        </label>
        <label for="locInWi" class="checkbox-row">
            <input id="locInWi" name="in_white_internet" type="checkbox" ${inWiChecked ? "checked" : ""}>
            <span>Входит в Белый Интернет</span>
        </label>
        <hr style="border:none; border-top:1px solid var(--border-subtle); margin:4px 0;">
        <div class="meta-text" style="font-weight:600;">Стандарт</div>
        <div class="grid-2">
            <div class="field">
                <label for="locStdName">Название</label>
                <input id="locStdName" name="vless_name" class="input" maxlength="120" value="${escapeHtml(node.vless_name || "")}" placeholder="${escapeHtml(node.name)}">
            </div>
            <div class="field">
                <label for="locStdBadge">Бейдж (до 30 симв.)</label>
                <input id="locStdBadge" name="server_description" class="input" maxlength="30" value="${escapeHtml(node.server_description || "")}" placeholder="Опционально">
            </div>
        </div>
        <div class="meta-text" style="font-weight:600;">Белый Интернет · Yandex</div>
        <div class="grid-2">
            <div class="field">
                <label for="locYanName">Название</label>
                <input id="locYanName" name="wi_yandex_name" class="input" maxlength="120" value="${escapeHtml(node.wi_yandex_name || "")}" placeholder="${escapeHtml(defaultYandexName)}">
            </div>
            <div class="field">
                <label for="locYanBadge">Бейдж (до 30 симв.)</label>
                <input id="locYanBadge" name="wi_yandex_badge" class="input" maxlength="30" value="${escapeHtml(node.wi_yandex_badge || "")}" placeholder="Опционально">
            </div>
        </div>
        <div class="meta-text" style="font-weight:600;">Белый Интернет · Turboflare</div>
        <div class="grid-2">
            <div class="field">
                <label for="locTfName">Название</label>
                <input id="locTfName" name="wi_turboflare_name" class="input" maxlength="120" value="${escapeHtml(node.wi_turboflare_name || "")}" placeholder="${escapeHtml(defaultTurboflareName)}">
            </div>
            <div class="field">
                <label for="locTfBadge">Бейдж (до 30 симв.)</label>
                <input id="locTfBadge" name="wi_turboflare_badge" class="input" maxlength="30" value="${escapeHtml(node.wi_turboflare_badge || "")}" placeholder="Опционально">
            </div>
        </div>
        <div id="locPreviewBox" class="notice-box" style="flex-direction:column; align-items:flex-start; gap:4px; font-size:12px;"></div>
        <button type="submit" class="btn btn-primary">Сохранить локацию</button>
    `;
    const previewEl = box.querySelector("#locPreviewBox");
    const refreshLocPreview = () => {
        const flagVal = String(box.querySelector("input[name='country_flag']")?.value || "").trim();
        const stdOn = Boolean(box.querySelector("input[name='in_standard']")?.checked);
        const wiOn = Boolean(box.querySelector("input[name='in_white_internet']")?.checked);
        const stdNameRaw = String(box.querySelector("input[name='vless_name']")?.value || "").trim();
        const stdName = stdNameRaw || node.name || "";
        const stdBadge = String(box.querySelector("input[name='server_description']")?.value || "").trim();
        const yanDefault = stdName ? `${stdName} Yandex` : defaultYandexName;
        const tfDefault = stdName ? `${stdName} Turboflare` : defaultTurboflareName;
        const yanName = String(box.querySelector("input[name='wi_yandex_name']")?.value || "").trim() || yanDefault;
        const yanBadge = String(box.querySelector("input[name='wi_yandex_badge']")?.value || "").trim();
        const tfName = String(box.querySelector("input[name='wi_turboflare_name']")?.value || "").trim() || tfDefault;
        const tfBadge = String(box.querySelector("input[name='wi_turboflare_badge']")?.value || "").trim();
        const lines = [];
        if (stdOn) {
            lines.push(`<div><b>Стандарт:</b> ${renderFlagHtml(flagVal, 16)} ${escapeHtml(stdName)}${stdBadge ? ` <span class="muted">(${escapeHtml(stdBadge)})</span>` : ""}</div>`);
        }
        if (wiOn) {
            lines.push(`<div><b>БИ Yandex:</b> ${renderFlagHtml(flagVal, 16)} ${escapeHtml(yanName)}${yanBadge ? ` <span class="muted">(${escapeHtml(yanBadge)})</span>` : ""}</div>`);
            lines.push(`<div><b>БИ Turboflare:</b> ${renderFlagHtml(flagVal, 16)} ${escapeHtml(tfName)}${tfBadge ? ` <span class="muted">(${escapeHtml(tfBadge)})</span>` : ""}</div>`);
        }
        if (!lines.length) {
            lines.push(`<div class="muted">Локация скрыта из всех подписок</div>`);
        }
        previewEl.innerHTML = `<div class="meta-text">Как будет выглядеть в клиенте:</div>${lines.join("")}`;
    };
    wireCountryFlagFields(box);
    box.addEventListener("input", refreshLocPreview);
    box.addEventListener("change", refreshLocPreview);
    refreshLocPreview();
    const close = openModal(`${node.name} (#${node.id})`, box);
    box.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(box);
        const btn = box.querySelector("button[type='submit']");
        await withButtonLoading(btn, "Сохранение...", async () => {
            try {
                const res = await api.patchNode(node.id, {
                    country_flag: String(fd.get("country_flag") || "").trim(),
                    in_standard: Boolean(box.querySelector("input[name='in_standard']")?.checked),
                    in_white_internet: Boolean(box.querySelector("input[name='in_white_internet']")?.checked),
                    vless_name: String(fd.get("vless_name") || "").trim(),
                    server_description: String(fd.get("server_description") || "").trim(),
                    wi_yandex_name: String(fd.get("wi_yandex_name") || "").trim(),
                    wi_yandex_badge: String(fd.get("wi_yandex_badge") || "").trim(),
                    wi_turboflare_name: String(fd.get("wi_turboflare_name") || "").trim(),
                    wi_turboflare_badge: String(fd.get("wi_turboflare_badge") || "").trim(),
                });
                if (res && res.node && typeof res.node === "object") {
                    Object.assign(node, res.node);
                }
                showToast("Локация обновлена");
                close();
                if (typeof onDone === "function") {
                    await onDone(res?.node || node);
                }
            } catch (err) {
                showToast(err.message || "Ошибка обновления локации", "error");
            }
        });
    });
}

function _renderSubLocationsList(locListEl, exitNodes, onLocationSaved) {
    if (!locListEl) return;
    locListEl.innerHTML = "";
    if (!exitNodes.length) {
        locListEl.innerHTML = `<div class="muted" style="text-align:center; padding:16px;">Нет exit-нод. Добавьте их во вкладке «Ноды».</div>`;
        return;
    }
    exitNodes.forEach((node) => {
        const inStd = node.in_standard !== false;
        const inWi = Boolean(node.in_white_internet);
        const row = document.createElement("button");
        row.type = "button";
        row.className = "node-card node-card-compact card-btn";
        row.innerHTML = `
            <span class="row" style="align-items:center;">
                <span class="node-compact-title">
                    ${renderFlagHtml(node.country_flag || node.location_code || "", 20)}
                    <span style="font-weight:600; font-size:14px;">${escapeHtml(node.vless_name || node.name)}</span>
                </span>
                <span class="btn btn-sm" aria-hidden="true">Настроить</span>
            </span>
            <span class="filter-pills">
                <span class="chip ${inStd ? "chip-ok" : "chip-muted"}">Стандарт: ${inStd ? "да" : "нет"}</span>
                <span class="chip ${inWi ? "chip-wi" : "chip-muted"}">БИ: ${inWi ? "да" : "нет"}</span>
            </span>
        `;
        row.addEventListener("click", () => {
            openLocationSubModal(node, onLocationSaved);
        });
        locListEl.appendChild(row);
    });
}

export async function renderSubscriptionSettingsView(body, state = null, onReloadNodes = null) {
    const reqId = ++_subSettingsReqId;
    body.innerHTML = `<section class="card card-center"><div class="muted">Загрузка настроек подписки...</div></section>`;
    let current;
    try {
        const res = await api.getIncySettings();
        if (reqId !== _subSettingsReqId || !body.isConnected || (state && (state.adminTab !== "subscription" || state.mode !== "admin"))) {
            return;
        }
        current = res.settings || {};
    } catch (err) {
        if (reqId !== _subSettingsReqId || !body.isConnected || (state && (state.adminTab !== "subscription" || state.mode !== "admin"))) {
            return;
        }
        if (err && err.code === "init_data_expired") return;
        showToast(err.message || "Не удалось загрузить настройки подписки", "error");
        body.innerHTML = `
            <section class="card card-center">
                <h2 class="card-title">Не удалось загрузить настройки подписки</h2>
                <p class="muted">${escapeHtml(err.message || "Ошибка запроса")}</p>
                <button id="subSettingsRetryBtn" type="button" class="btn btn-primary btn-sm">Повторить</button>
            </section>
        `;
        body.querySelector("#subSettingsRetryBtn")?.addEventListener("click", () => {
            renderSubscriptionSettingsView(body, state, onReloadNodes);
        });
        return;
    }

    const getExitNodes = () =>
        (state && Array.isArray(state.nodes) ? state.nodes : []).filter((n) => n.role === "exit_node");
    const exitNodes = getExitNodes();

    body.innerHTML = `
        <section class="card">
            <div class="row">
                <h2 class="card-title">Локации в подписке (Exit Nodes)</h2>
                <span id="subLocationsCount" class="chip chip-muted">${exitNodes.length}</span>
            </div>
            <div id="subLocationsList" class="nodes-list"></div>
        </section>

        <section class="card">
            <h2 class="card-title">Оформление подписки (INCY / Happ)</h2>
            <div class="notice-box" style="flex-direction:column; align-items:flex-start; gap:4px; font-size:12px;">
                <strong>Пример карточки в INCY / Happ:</strong>
                <span><b>\u2726 Just1k</b> <span class="muted">(Profile-Title, автообновление 6 ч)</span></span>
                <span><i>Стандарт: 6 дн. • ∞ • устр. 1/2</i><br><i style="padding-left:16px;">Белый интернет: 29 дн. • 0/50 ГБ • устр. 1/1</i> <span class="muted">(Announce)</span></span>
                <span>Кнопки: <b>Поддержка</b> (Support-Url) • <b>Канал / Бот</b> (Announce-Url)</span>
            </div>
            <form id="subSettingsForm" class="field" style="gap:12px;">
                <div class="grid-2">
                    <div class="field">
                        <label for="subProfileTitle">Название (Profile-Title)</label>
                        <input id="subProfileTitle" name="profile_title" class="input" maxlength="120" value="${escapeHtml(current.profile_title || "")}" placeholder="\u2726 Just1k">
                    </div>
                    <div class="field">
                        <label for="subUpdateInterval">Автообновление (часы)</label>
                        <input id="subUpdateInterval" name="profile_update_interval" class="input tabular" type="number" min="1" max="720" step="1" required value="${escapeHtml(String(current.profile_update_interval || 6))}">
                    </div>
                </div>
                <label for="subShowDevices" class="checkbox-row">
                    <input id="subShowDevices" name="profile_show_devices" type="checkbox" ${current.profile_show_devices ? "checked" : ""}>
                    <span>Показывать счётчик устройств в Announce (при пустом поле Announce)</span>
                </label>
                <div class="field">
                    <label for="subAnnounce">Баннер / Анонс (Announce)</label>
                    <textarea id="subAnnounce" name="announce" class="textarea" maxlength="300" style="min-height:64px;" placeholder="Оставьте пустым для авто-статуса в 2 строки (дни + ГБ)">${escapeHtml(current.announce || "")}</textarea>
                    <div class="meta-text">Жёлтая плашка сверху. Пусто = авто-сводка по срокам и ГБ обоих тарифов.</div>
                </div>
                <div class="field">
                    <label for="subAnnounceUrl">Ссылка анонса (Announce-Url)</label>
                    <input id="subAnnounceUrl" name="announce_url" class="input" maxlength="500" value="${escapeHtml(current.announce_url || "")}" placeholder="https://t.me/...">
                </div>
                <div class="grid-2">
                    <div class="field">
                        <label for="subSupportUrl">Support-Url (кнопка «Поддержка»)</label>
                        <input id="subSupportUrl" name="support_url" class="input" maxlength="500" value="${escapeHtml(current.support_url || "")}" placeholder="По умолчанию из настроек бота">
                    </div>
                    <div class="field">
                        <label for="subWebPageUrl">Profile-Web-Page-Url (сайт/бот)</label>
                        <input id="subWebPageUrl" name="web_page_url" class="input" maxlength="500" value="${escapeHtml(current.web_page_url || "")}" placeholder="По умолчанию ссылка на бота">
                    </div>
                </div>

                <div class="grid-3">
                    <div class="field">
                        <label for="subCdnLabelMode">Подписи маршрутов</label>
                        <select id="subCdnLabelMode" name="cdn_label_mode" class="input">
                            <option value="name" ${(current.cdn_label_mode || "name") === "name" ? "selected" : ""}>В названии (name)</option>
                            <option value="badge" ${current.cdn_label_mode === "badge" ? "selected" : ""}>В бейдже (badge)</option>
                            <option value="none" ${current.cdn_label_mode === "none" ? "selected" : ""}>Скрыть (none)</option>
                        </select>
                    </div>
                    <div class="field">
                        <label for="subCdnYandexLabel">Метка канала Y</label>
                        <input id="subCdnYandexLabel" name="cdn_yandex_label" class="input" maxlength="32" value="${escapeHtml(current.cdn_yandex_label || "Yandex")}" placeholder="Yandex">
                    </div>
                    <div class="field">
                        <label for="subCdnTurboflareLabel">Метка канала T</label>
                        <input id="subCdnTurboflareLabel" name="cdn_turboflare_label" class="input" maxlength="32" value="${escapeHtml(current.cdn_turboflare_label || "Turboflare")}" placeholder="Turboflare">
                    </div>
                </div>

                <button type="submit" class="btn btn-primary">Сохранить настройки</button>
            </form>
        </section>
    `;

    const locListEl = body.querySelector("#subLocationsList");
    const locCountEl = body.querySelector("#subLocationsCount");
    const handleLocationSaved = async () => {
        // Re-render only #subLocationsList so unsaved edits in #subSettingsForm stay intact
        _renderSubLocationsList(locListEl, getExitNodes(), handleLocationSaved);
        if (state && Array.isArray(state.nodes)) {
            try {
                const res = await api.listNodes();
                if (!body.isConnected || state.adminTab !== "subscription" || state.mode !== "admin") return;
                if (res && Array.isArray(res.nodes)) {
                    state.nodes = res.nodes;
                    state.nodesLoaded = true;
                    const updatedExitNodes = getExitNodes();
                    if (locCountEl) locCountEl.textContent = String(updatedExitNodes.length);
                    _renderSubLocationsList(locListEl, updatedExitNodes, handleLocationSaved);
                }
            } catch {
                // Local updated node fields are already rendered above
            }
        } else if (typeof onReloadNodes === "function") {
            await onReloadNodes({ skipRender: true });
        }
    };
    _renderSubLocationsList(locListEl, exitNodes, handleLocationSaved);

    const form = body.querySelector("#subSettingsForm");
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(form);
        const intervalInput = form.querySelector("#subUpdateInterval");
        const rawInterval = String(fd.get("profile_update_interval") ?? "").trim();
        if (!/^\d+$/.test(rawInterval)) {
            showToast("Интервал автообновления должен быть целым числом от 1 до 720 часов", "error");
            intervalInput?.focus();
            return;
        }
        const intervalVal = Number(rawInterval);
        if (!Number.isInteger(intervalVal) || intervalVal < 1 || intervalVal > 720) {
            showToast("Интервал автообновления должен быть от 1 до 720 часов", "error");
            intervalInput?.focus();
            return;
        }
        const btn = form.querySelector("button[type='submit']");
        await withButtonLoading(btn, "Сохранение...", async () => {
            try {
                await api.updateIncySettings({
                    profile_title: String(fd.get("profile_title") || "").trim(),
                    profile_update_interval: intervalVal,
                    profile_show_devices: Boolean(fd.get("profile_show_devices")),
                    announce: String(fd.get("announce") || "").trim(),
                    announce_url: String(fd.get("announce_url") || "").trim(),
                    support_url: String(fd.get("support_url") || "").trim(),
                    web_page_url: String(fd.get("web_page_url") || "").trim(),
                    cdn_label_mode: String(fd.get("cdn_label_mode") || "name"),
                    cdn_yandex_label: String(fd.get("cdn_yandex_label") || "").trim(),
                    cdn_turboflare_label: String(fd.get("cdn_turboflare_label") || "").trim(),
                });
                showToast("Настройки подписки сохранены");
            } catch (err) {
                showToast(err.message || "Ошибка сохранения", "error");
            }
        });
    });
}

function _nodeScopeBadges(node) {
    if (node.role === "exit_node") {
        const hasStd = node.in_standard !== false;
        const hasBi = Boolean(node.in_white_internet);
        const badges = [];
        if (hasStd) badges.push(`<span class="chip chip-muted">Стандарт</span>`);
        if (hasBi) badges.push(`<span class="chip chip-wi">БИ</span>`);
        if (!hasStd && !hasBi) badges.push(`<span class="chip chip-muted">Скрыта</span>`);
        return badges.join("");
    }
    if (node.role === "eu_hub") {
        return `<span class="chip chip-muted">Мост</span>`;
    }
    const caps = Array.isArray(node.capabilities) ? node.capabilities : [];
    const links = node.links && typeof node.links === "object" ? node.links : {};
    const hasStd = caps.includes("vless") || caps.includes("standard") || Boolean(links.standard && Object.keys(links.standard).length);
    const hasBi = caps.includes("white_internet") || Boolean(links.bi && Object.keys(links.bi).length);
    const badges = [];
    if (hasStd) badges.push(`<span class="chip chip-muted">Стандарт</span>`);
    if (hasBi) badges.push(`<span class="chip chip-wi">БИ</span>`);
    return badges.join("");
}

function _openAddNodeModal(onReload, onSelectNode) {
    const form = document.createElement("form");
    form.className = "field";
    form.style.gap = "12px";
    form.innerHTML = `
        <div class="field">
            <label for="addApiUrl">URL API ноды</label>
            <input id="addApiUrl" name="api_url" class="input" type="url" required placeholder="https://node.domain.com:8444">
        </div>
        <div class="field">
            <label for="addApiKey">API Токен</label>
            <input id="addApiKey" name="api_key" class="input" type="password" required placeholder="Bearer / X-API-Key токен">
        </div>
        <div class="grid-2">
            <div class="field">
                <label for="addName">Название (опционально)</label>
                <input id="addName" name="name" class="input" type="text" maxlength="255">
            </div>
            ${renderCountryFlagField({ id: "addFlag", label: "Страна (опционально)", emptyLabel: "Авто (GeoIP)" })}
        </div>
        <button id="addSubmitBtn" type="submit" class="btn btn-primary">Проверить и добавить</button>
    `;
    wireCountryFlagFields(form);
    const close = openModal("Добавить ноду (v2 auto-discovery)", form);

    const addBtn = form.querySelector("#addSubmitBtn");
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        await withButtonLoading(addBtn, "Проверка...", async () => {
            try {
                const res = await api.addNode({
                    api_url: form.querySelector("#addApiUrl").value.trim(),
                    api_key: form.querySelector("#addApiKey").value.trim(),
                    name: form.querySelector("#addName").value.trim(),
                    country_flag: form.querySelector("#addFlag").value.trim() || null,
                });
                showToast("Нода добавлена и выключена. Откройте её для включения.");
                close();
                const ok = await onReload({ skipRender: true });
                if (!ok) return;
                if (res && res.node && res.node.id && typeof onSelectNode === "function") {
                    onSelectNode(res.node.id);
                }
            } catch (err) {
                showToast(err.message || "Не удалось добавить ноду", "error");
            }
        });
    });
}

function _nodeDiagnosticBadges(node) {
    const badges = [];
    const errCount = Number(node.module_errors_count || 0);
    if (errCount > 0) {
        badges.push(`<span class="chip chip-err">Ошибки: ${errCount}</span>`);
    }
    const sys = node.system_load && typeof node.system_load === "object" ? node.system_load : null;
    if (sys && (sys.cpu_percent != null || sys.ram_percent != null)) {
        const formatPct = (val) => {
            const num = Number(val);
            if (!Number.isFinite(num)) return "—";
            return num < 10 ? num.toFixed(1).replace(/\.0$/, "") : String(Math.round(num));
        };
        const cpu = sys.cpu_percent != null ? Number(sys.cpu_percent) : null;
        const ram = sys.ram_percent != null ? Number(sys.ram_percent) : null;
        const maxLoad = Math.max(cpu ?? 0, ram ?? 0);
        const cls = maxLoad >= 95 ? "chip-err" : maxLoad >= 85 ? "chip-warn" : "chip-muted";
        const parts = [];
        if (cpu !== null) parts.push(`CPU ${formatPct(cpu)}%`);
        if (ram !== null) parts.push(`RAM ${formatPct(ram)}%`);
        badges.push(`<span class="chip ${cls}">${parts.join(" · ")}</span>`);
        if (sys.load_1m != null && Number.isFinite(Number(sys.load_1m))) {
            const la = Number(sys.load_1m);
            const cores = Math.max(1, Number(sys.cpu_cores || 1));
            if (la >= cores * 0.85) {
                badges.push(`<span class="chip chip-muted">LA ${la.toFixed(2)}</span>`);
            }
        }
    }
    const tl = node.traffic_limit && typeof node.traffic_limit === "object" ? node.traffic_limit : null;
    if (tl && Number(tl.pct || 0) >= 80) {
        const cls = tl.exceeded || Number(tl.pct) >= 95 ? "chip-err" : "chip-warn";
        badges.push(`<span class="chip ${cls}">Трафик ${tl.pct}%</span>`);
    }
    const ssl = node.ssl_cert && typeof node.ssl_cert === "object" ? node.ssl_cert : null;
    if (ssl && ssl.days_left != null && Number.isFinite(Number(ssl.days_left)) && Number(ssl.days_left) < 14) {
        const rawDays = Math.floor(Number(ssl.days_left));
        const sslLabel = rawDays <= 0 ? "SSL: истёк" : `SSL: ${rawDays} дн.`;
        badges.push(`<span class="chip ${rawDays < 7 ? "chip-err" : "chip-warn"}">${sslLabel}</span>`);
    }
    if (_isLastApplyFailed(node)) {
        badges.push(`<span class="chip chip-err">Ошибка apply</span>`);
    }
    return badges.join("");
}

export function renderNodesView(root, state, onReload, onSelectNode) {
    const nodes = state.nodes || [];
    const problemNodes = nodes.filter(_isProblemNode);
    const counts = {
        all: nodes.length,
        problems: problemNodes.length,
        ru_hub: nodes.filter((n) => n.role === "ru_hub").length,
        eu_hub: nodes.filter((n) => n.role === "eu_hub").length,
        exit_node: nodes.filter((n) => n.role === "exit_node").length,
    };
    const filteredRaw =
        currentFilter === "all"
            ? nodes
            : currentFilter === "problems"
                ? problemNodes
                : nodes.filter((n) => n.role === currentFilter);
    const filtered = [...filteredRaw].sort((a, b) => {
        const probDiff = Number(_isProblemNode(b)) - Number(_isProblemNode(a));
        if (probDiff !== 0) return probDiff;
        const roleDiff = (_ROLE_ORDER[a.role] ?? 3) - (_ROLE_ORDER[b.role] ?? 3);
        if (roleDiff !== 0) return roleDiff;
        return Number(a.id || 0) - Number(b.id || 0);
    });

    const latestCheckDate = nodes.reduce((latest, n) => {
        const dt = parseDate(n.last_successful_check);
        if (!dt) return latest;
        return !latest || dt.getTime() > latest.getTime() ? dt : latest;
    }, null);
    const pollStatusLabel = latestCheckDate
        ? `Опрос нод: ${formatRelativeTime(latestCheckDate)}`
        : "Ноды ещё не опрашивались";

    root.innerHTML = `
        <section class="stats-strip">
            <div class="stat-box"><div class="stat-val">${counts.all}</div><div class="stat-lbl">Всего</div></div>
            <div class="stat-box"><div class="stat-val">${counts.ru_hub}</div><div class="stat-lbl">RU Hub</div></div>
            <div class="stat-box"><div class="stat-val">${counts.eu_hub}</div><div class="stat-lbl">EU Hub</div></div>
            <div class="stat-box"><div class="stat-val">${counts.exit_node}</div><div class="stat-lbl">Exit</div></div>
        </section>

        <section class="card">
            <div class="row" style="flex-wrap:wrap; gap:8px; align-items:center;">
                <div class="nodes-head-title">
                    <h2 class="card-title">Список нод</h2>
                    <span class="meta-text">${escapeHtml(pollStatusLabel)}</span>
                </div>
                <div class="actions-row" style="gap:8px; flex-shrink:0;">
                    <button id="addNodeOpenBtn" type="button" class="btn btn-sm btn-primary">+ Добавить</button>
                    <button id="reloadNodesBtn" type="button" class="btn btn-sm">${_REFRESH_ICON}<span>Обновить</span></button>
                </div>
            </div>
            <div class="filter-pills nodes-filter-pills">
                <button type="button" class="pill-btn ${currentFilter === "all" ? "active" : ""}" data-filter="all" aria-pressed="${currentFilter === "all" ? "true" : "false"}">Все (${counts.all})</button>
                <button type="button" class="pill-btn ${currentFilter === "problems" ? "active" : ""}" data-filter="problems" aria-pressed="${currentFilter === "problems" ? "true" : "false"}">Проблемы (${counts.problems})</button>
                <button type="button" class="pill-btn ${currentFilter === "ru_hub" ? "active" : ""}" data-filter="ru_hub" aria-pressed="${currentFilter === "ru_hub" ? "true" : "false"}">RU Hub (${counts.ru_hub})</button>
                <button type="button" class="pill-btn ${currentFilter === "eu_hub" ? "active" : ""}" data-filter="eu_hub" aria-pressed="${currentFilter === "eu_hub" ? "true" : "false"}">EU Hub (${counts.eu_hub})</button>
                <button type="button" class="pill-btn ${currentFilter === "exit_node" ? "active" : ""}" data-filter="exit_node" aria-pressed="${currentFilter === "exit_node" ? "true" : "false"}">Exit (${counts.exit_node})</button>
            </div>
            <div id="nodesListContainer" class="nodes-list"></div>
        </section>
    `;

    root.querySelector("#addNodeOpenBtn").addEventListener("click", () => {
        _openAddNodeModal(onReload, onSelectNode);
    });

    const reloadBtn = root.querySelector("#reloadNodesBtn");
    if (reloadBtn) {
        let reloadInFlight = false;
        reloadBtn.addEventListener("click", async () => {
            if (reloadInFlight) return;
            reloadInFlight = true;
            reloadBtn.disabled = true;
            reloadBtn.setAttribute("aria-busy", "true");
            const labelEl = reloadBtn.querySelector("span");
            if (labelEl) labelEl.textContent = "Опрос...";
            try {
                await onReload({ probe: true });
            } catch (err) {
                showToast(err.message || "Не удалось обновить список", "error");
            } finally {
                reloadInFlight = false;
                reloadBtn.disabled = false;
                reloadBtn.removeAttribute("aria-busy");
                if (labelEl) labelEl.textContent = "Обновить";
            }
        });
    }

    root.querySelectorAll("[data-filter]").forEach((btn) => {
        btn.addEventListener("click", () => {
            currentFilter = btn.dataset.filter;
            renderNodesView(root, state, onReload, onSelectNode);
        });
    });

    const listEl = root.querySelector("#nodesListContainer");
    if (!filtered.length) {
        listEl.innerHTML = `<div class="muted" style="text-align:center; padding:16px;">Ноды не найдены</div>`;
        return;
    }

    filtered.forEach((node) => {
        const health = healthChipInfo(node);
        const scopeBadges = _nodeScopeBadges(node);
        const diagBadges = _nodeDiagnosticBadges(node);
        const checkText = formatRelativeTime(node.last_successful_check);
        const card = document.createElement("button");
        card.type = "button";
        card.className = "node-card node-card-compact card-btn";
        card.innerHTML = `
            <span class="row" style="align-items:center; gap:8px;">
                <span class="node-compact-title">
                    ${renderFlagHtml(node.country_flag || node.location_code || "", 20)}
                    <span class="node-compact-name">${escapeHtml(node.name)}</span>
                    <span class="meta-text" style="flex-shrink:0;">#${node.id}</span>
                </span>
                <span class="row" style="gap:6px; align-items:center; flex-shrink:0; width:auto;">
                    <span class="chip ${health.cls}">${escapeHtml(health.text)}</span>
                    <span class="node-chevron" aria-hidden="true">›</span>
                </span>
            </span>
            <span class="row meta-text" style="gap:8px; align-items:center;">
                <span class="node-domain-line"><span class="node-role-inline">${escapeHtml(roleLabel(node.role))}</span> · <span class="mono">${escapeHtml(node.domain || node.api_url)}</span></span>
                <span>${health.text === "Отключена" ? escapeHtml(checkText) : `${node.is_active ? "● " : ""}${escapeHtml(checkText)}`}</span>
            </span>
            ${scopeBadges || diagBadges ? `
                <span class="nodes-badges-row">
                    ${scopeBadges ? `<span class="nodes-badges-group">${scopeBadges}</span>` : ""}
                    ${diagBadges ? `<span class="nodes-badges-group">${diagBadges}</span>` : ""}
                </span>
            ` : ""}
        `;

        card.addEventListener("click", () => {
            if (typeof onSelectNode === "function") {
                onSelectNode(node.id);
            }
        });

        listEl.appendChild(card);
    });
}
