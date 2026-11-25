import { api } from "../api.js?v=2.8.0";
import {
    escapeHtml,
    formatRelativeTime,
    healthChipInfo,
    openModal,
    renderCountryFlagField,
    renderFlagHtml,
    roleLabel,
    showToast,
    wireCountryFlagFields,
    withButtonLoading,
} from "../ui.js?v=2.8.0";

let currentFilter = "all";
let lastReloadedAt = null;

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
        <div class="meta-text" style="font-weight:600;">Белый Интернет — Yandex</div>
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
        <div class="meta-text" style="font-weight:600;">Белый Интернет — Turboflare</div>
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
            <div class="row" style="align-items:center;">
                <div class="node-compact-title">
                    ${renderFlagHtml(node.country_flag || node.location_code || "", 20)}
                    <span style="font-weight:600; font-size:14px;">${escapeHtml(node.vless_name || node.name)}</span>
                </div>
                <span class="btn btn-sm" aria-hidden="true">Настроить</span>
            </div>
            <div class="filter-pills">
                <span class="chip ${inStd ? "chip-ok" : "chip-muted"}">Стандарт: ${inStd ? "да" : "нет"}</span>
                <span class="chip ${inWi ? "chip-wi" : "chip-muted"}">БИ: ${inWi ? "да" : "нет"}</span>
            </div>
        `;
        row.addEventListener("click", () => {
            openLocationSubModal(node, onLocationSaved);
        });
        locListEl.appendChild(row);
    });
}

export async function renderSubscriptionSettingsView(body, state = null, onReloadNodes = null) {
    body.innerHTML = `<section class="card card-center"><div class="muted">Загрузка настроек подписки</div></section>`;
    let current;
    try {
        const res = await api.getIncySettings();
        current = res.settings || {};
    } catch (err) {
        showToast(err.message || "Не удалось загрузить настройки подписки", "error");
        body.innerHTML = `<section class="card"><div class="muted">${escapeHtml(err.message || "Не удалось загрузить настройки подписки")}</div></section>`;
        return;
    }

    const exitNodes = (state && Array.isArray(state.nodes) ? state.nodes : []).filter(
        (n) => n.role === "exit_node"
    );

    body.innerHTML = `
        <section class="card">
            <div class="row">
                <h2 class="card-title">Локации в подписке (Exit Nodes)</h2>
                <span class="chip chip-muted">${exitNodes.length}</span>
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
                        <input id="subUpdateInterval" name="profile_update_interval" class="input tabular" type="number" min="1" max="720" value="${escapeHtml(String(current.profile_update_interval || 6))}">
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
                <div class="field">
                    <label for="subMtprotoProxyUrl">Ссылка на прокси для Telegram (MTProto Proxy URL)</label>
                    <input id="subMtprotoProxyUrl" name="mtproto_proxy_url" class="input" maxlength="500" value="${escapeHtml(current.mtproto_proxy_url || "")}" placeholder="https://t.me/proxy?server=...&port=...&secret=...">
                    <div class="meta-text">Кнопка быстрого подключения прокси в боте и личном кабинете. Оставьте пустым, чтобы скрыть.</div>
                </div>

                <details class="guide-details">
                    <summary class="guide-summary">
                        <span>Поведение и сортировка</span>
                        <span id="subSortSummaryBadge" class="meta-text">${escapeHtml(current.sort_order || "none")} · ${escapeHtml(current.no_limit_mode || "all")}</span>
                    </summary>
                    <div class="guide-body">
                        <div class="meta-text">Порядок серверов, лимит памяти iOS для XHTTP и скрытие элементов интерфейса в клиенте.</div>
                        <div class="grid-2">
                            <div class="field">
                                <label for="subSortOrder">Сортировка серверов</label>
                                <select id="subSortOrder" name="sort_order" class="input">
                                    <option value="none" ${(current.sort_order || "none") === "none" ? "selected" : ""}>Как отдал сервер (none)</option>
                                    <option value="ping" ${current.sort_order === "ping" ? "selected" : ""}>По пингу (ping)</option>
                                    <option value="name" ${current.sort_order === "name" ? "selected" : ""}>По имени (name)</option>
                                </select>
                            </div>
                            <div class="field">
                                <label for="subNoLimitMode">No-Limit режим (память iOS)</label>
                                <select id="subNoLimitMode" name="no_limit_mode" class="input">
                                    <option value="all" ${(current.no_limit_mode || "all") === "all" ? "selected" : ""}>No-Limit-Enabled (все)</option>
                                    <option value="xhttp" ${current.no_limit_mode === "xhttp" ? "selected" : ""}>no-limit-xhttp-enabled (Happ)</option>
                                    <option value="off" ${current.no_limit_mode === "off" ? "selected" : ""}>Выключено</option>
                                </select>
                            </div>
                        </div>
                        <div class="grid-2">
                            <label for="subHideUrl" class="checkbox-row">
                                <input id="subHideUrl" name="hide_url" type="checkbox" ${current.hide_url !== false ? "checked" : ""}>
                                <span>Hide-Url (скрыть URL и QR)</span>
                            </label>
                            <label for="subHideCheck" class="checkbox-row">
                                <input id="subHideCheck" name="hide_check" type="checkbox" ${current.hide_check ? "checked" : ""}>
                                <span>Hide-Check (INCY: скрыть пинг)</span>
                            </label>
                        </div>
                    </div>
                </details>

                <details class="guide-details">
                    <summary class="guide-summary">
                        <span>Фрагментация TLS</span>
                        <span id="subFragSummaryBadge" class="chip ${current.fragmentation_enabled !== false ? "chip-ok" : "chip-muted"}">${current.fragmentation_enabled !== false ? "Вкл" : "Выкл"}</span>
                    </summary>
                    <div class="guide-body">
                        <div class="meta-text">Дробит TLS ClientHello (напр. tlshello, 10-30 байт каждые 10-30 мс) от DPI. Выключайте, если тормозит хендшейк.</div>
                        <label for="subFragEnabled" class="checkbox-row">
                            <input id="subFragEnabled" name="fragmentation_enabled" type="checkbox" ${current.fragmentation_enabled !== false ? "checked" : ""}>
                            <span>Включить fragmentation-enable</span>
                        </label>
                        <div class="grid-2">
                            <div class="field">
                                <label for="subFragPackets">Packets</label>
                                <input id="subFragPackets" name="fragmentation_packets" class="input" maxlength="64" value="${escapeHtml(current.fragmentation_packets || "tlshello")}" placeholder="tlshello">
                            </div>
                            <div class="field">
                                <label for="subFragLength">Length</label>
                                <input id="subFragLength" name="fragmentation_length" class="input" maxlength="32" value="${escapeHtml(current.fragmentation_length || "10-30")}" placeholder="10-30">
                            </div>
                        </div>
                        <div class="field">
                            <label for="subFragInterval">Interval</label>
                            <input id="subFragInterval" name="fragmentation_interval" class="input" maxlength="32" value="${escapeHtml(current.fragmentation_interval || "10-30")}" placeholder="10-30">
                        </div>
                    </div>
                </details>

                <details class="guide-details">
                    <summary class="guide-summary">
                        <span>DoH Pre-resolve (INCY / Happ)</span>
                        <span id="subDnsSummaryBadge" class="chip ${current.dns_resolve_enabled ? "chip-ok" : "chip-muted"}">${current.dns_resolve_enabled ? "Вкл" : "Выкл"}</span>
                    </summary>
                    <div class="guide-body">
                        <div class="meta-text">Резолвит домен сервера через DoH до старта туннеля (защита от DNS-подмены; пример: dns.google + 8.8.8.8).</div>
                        <label for="subDnsEnabled" class="checkbox-row">
                            <input id="subDnsEnabled" name="dns_resolve_enabled" type="checkbox" ${current.dns_resolve_enabled ? "checked" : ""}>
                            <span>Включить Server-Address-Resolve-Enable</span>
                        </label>
                        <div class="grid-2">
                            <div class="field">
                                <label for="subDnsDomain">DoH Домен</label>
                                <input id="subDnsDomain" name="dns_resolve_domain" class="input" maxlength="253" value="${escapeHtml(current.dns_resolve_domain || "")}" placeholder="dns.google">
                            </div>
                            <div class="field">
                                <label for="subDnsIp">Bootstrap DNS IP</label>
                                <input id="subDnsIp" name="dns_resolve_ip" class="input" maxlength="64" value="${escapeHtml(current.dns_resolve_ip || "")}" placeholder="8.8.8.8">
                            </div>
                        </div>
                    </div>
                </details>

                <details class="guide-details">
                    <summary class="guide-summary">
                        <span>Раздельное туннелирование (Android)</span>
                        <span id="subPerAppSummaryBadge" class="chip ${current.per_app_proxy_enabled ? "chip-ok" : "chip-muted"}">${current.per_app_proxy_enabled ? "Вкл" : "Выкл"}</span>
                    </summary>
                    <div class="guide-body">
                        <div class="meta-text">Исключает выбранные приложения (bypass, напр. банки) или пускает только их (proxy). Список через запятую или с новой строки.</div>
                        <label for="subPerAppEnabled" class="checkbox-row">
                            <input id="subPerAppEnabled" name="per_app_proxy_enabled" type="checkbox" ${current.per_app_proxy_enabled ? "checked" : ""}>
                            <span>Включить Per-App-Proxy-Enable</span>
                        </label>
                        <div class="field">
                            <label for="subPerAppMode">Режим</label>
                            <select id="subPerAppMode" name="per_app_proxy_mode" class="input">
                                <option value="bypass" ${(current.per_app_proxy_mode || "bypass") === "bypass" ? "selected" : ""}>Исключить (bypass)</option>
                                <option value="proxy" ${current.per_app_proxy_mode === "proxy" ? "selected" : ""}>Только выбранные (proxy)</option>
                            </select>
                        </div>
                        <div class="field">
                            <label for="subPerAppList">Список приложений (package names)</label>
                            <textarea id="subPerAppList" name="per_app_proxy_list" class="textarea" maxlength="2000" style="min-height:64px;" placeholder="ru.sberbankmobile,com.whatsapp">${escapeHtml(current.per_app_proxy_list || "")}</textarea>
                        </div>
                    </div>
                </details>

                <button type="submit" class="btn btn-primary">Сохранить настройки</button>
            </form>
        </section>
    `;

    const locListEl = body.querySelector("#subLocationsList");
    const handleLocationSaved = async () => {
        // Re-render only #subLocationsList so unsaved edits in #subSettingsForm stay intact
        _renderSubLocationsList(locListEl, exitNodes, handleLocationSaved);
        if (state && Array.isArray(state.nodes)) {
            api.listNodes()
                .then((res) => {
                    if (res && Array.isArray(res.nodes)) {
                        state.nodes = res.nodes;
                    }
                })
                .catch(() => {});
        } else if (typeof onReloadNodes === "function") {
            // Fallback if no shared state array was provided
            await onReloadNodes();
        }
    };
    _renderSubLocationsList(locListEl, exitNodes, handleLocationSaved);

    const form = body.querySelector("#subSettingsForm");
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(form);
        const btn = form.querySelector("button[type='submit']");
        await withButtonLoading(btn, "Сохранение...", async () => {
            try {
                const sortOrder = String(fd.get("sort_order") || "none");
                const noLimitMode = String(fd.get("no_limit_mode") || "all");
                const fragEnabled = Boolean(fd.get("fragmentation_enabled"));
                const dnsEnabled = Boolean(fd.get("dns_resolve_enabled"));
                const perAppEnabled = Boolean(fd.get("per_app_proxy_enabled"));
                const intervalRaw = parseInt(String(fd.get("profile_update_interval") || "6"), 10);
                await api.updateIncySettings({
                    profile_title: String(fd.get("profile_title") || "").trim(),
                    profile_update_interval: Number.isFinite(intervalRaw) ? Math.max(1, Math.min(720, intervalRaw)) : 6,
                    profile_show_devices: Boolean(fd.get("profile_show_devices")),
                    announce: String(fd.get("announce") || "").trim(),
                    announce_url: String(fd.get("announce_url") || "").trim(),
                    support_url: String(fd.get("support_url") || "").trim(),
                    web_page_url: String(fd.get("web_page_url") || "").trim(),
                    mtproto_proxy_url: String(fd.get("mtproto_proxy_url") || "").trim(),
                    sort_order: sortOrder,
                    no_limit_mode: noLimitMode,
                    hide_url: Boolean(fd.get("hide_url")),
                    hide_check: Boolean(fd.get("hide_check")),
                    fragmentation_enabled: fragEnabled,
                    fragmentation_packets: String(fd.get("fragmentation_packets") || "").trim(),
                    fragmentation_length: String(fd.get("fragmentation_length") || "").trim(),
                    fragmentation_interval: String(fd.get("fragmentation_interval") || "").trim(),
                    dns_resolve_enabled: dnsEnabled,
                    dns_resolve_domain: String(fd.get("dns_resolve_domain") || "").trim(),
                    dns_resolve_ip: String(fd.get("dns_resolve_ip") || "").trim(),
                    per_app_proxy_enabled: perAppEnabled,
                    per_app_proxy_mode: String(fd.get("per_app_proxy_mode") || "bypass"),
                    per_app_proxy_list: String(fd.get("per_app_proxy_list") || "").trim(),
                });
                const sortBadge = form.querySelector("#subSortSummaryBadge");
                if (sortBadge) sortBadge.textContent = `${sortOrder} · ${noLimitMode}`;
                const updateChip = (selector, on) => {
                    const el = form.querySelector(selector);
                    if (!el) return;
                    el.className = `chip ${on ? "chip-ok" : "chip-muted"}`;
                    el.textContent = on ? "Вкл" : "Выкл";
                };
                updateChip("#subFragSummaryBadge", fragEnabled);
                updateChip("#subDnsSummaryBadge", dnsEnabled);
                updateChip("#subPerAppSummaryBadge", perAppEnabled);
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
        const cpu = sys.cpu_percent != null ? Math.round(Number(sys.cpu_percent)) : null;
        const ram = sys.ram_percent != null ? Math.round(Number(sys.ram_percent)) : null;
        const maxLoad = Math.max(cpu ?? 0, ram ?? 0);
        const cls = maxLoad >= 95 ? "chip-err" : maxLoad >= 85 ? "chip-warn" : "chip-muted";
        const parts = [];
        if (cpu !== null) parts.push(`CPU ${cpu}%`);
        if (ram !== null) parts.push(`RAM ${ram}%`);
        badges.push(`<span class="chip ${cls}">${parts.join(" · ")}</span>`);
    }
    const tl = node.traffic_limit && typeof node.traffic_limit === "object" ? node.traffic_limit : null;
    if (tl && Number(tl.pct || 0) >= 80) {
        const cls = tl.exceeded || Number(tl.pct) >= 95 ? "chip-err" : "chip-warn";
        badges.push(`<span class="chip ${cls}">Трафик ${tl.pct}%</span>`);
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
    const filtered = [...filteredRaw].sort(
        (a, b) => Number(_isProblemNode(b)) - Number(_isProblemNode(a))
    );

    const reloadLabel = lastReloadedAt
        ? `Обновлено в ${lastReloadedAt.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`
        : "Обновить список";

    root.innerHTML = `
        <section class="stats-strip">
            <div class="stat-box"><div class="stat-val">${counts.all}</div><div class="stat-lbl">Всего</div></div>
            <div class="stat-box"><div class="stat-val">${counts.ru_hub}</div><div class="stat-lbl">RU Hub</div></div>
            <div class="stat-box"><div class="stat-val">${counts.eu_hub}</div><div class="stat-lbl">EU Hub</div></div>
            <div class="stat-box"><div class="stat-val">${counts.exit_node}</div><div class="stat-lbl">Exit</div></div>
        </section>

        <section class="card">
            <div class="row" style="flex-wrap:wrap; gap:8px;">
                <h2 class="card-title">Список нод</h2>
                <div class="actions-row" style="gap:8px;">
                    <button id="addNodeOpenBtn" type="button" class="btn btn-sm btn-primary">+ Добавить</button>
                    <button id="syncTopologyBtn" type="button" class="btn btn-sm">Синк топологии</button>
                    <button id="reloadNodesBtn" type="button" class="btn btn-sm">${escapeHtml(reloadLabel)}</button>
                </div>
            </div>
            <div class="filter-pills">
                <button type="button" class="pill-btn ${currentFilter === "all" ? "active" : ""}" data-filter="all">Все (${counts.all})</button>
                <button type="button" class="pill-btn ${currentFilter === "problems" ? "active" : ""}" data-filter="problems">Проблемы (${counts.problems})</button>
                <button type="button" class="pill-btn ${currentFilter === "ru_hub" ? "active" : ""}" data-filter="ru_hub">RU Hub (${counts.ru_hub})</button>
                <button type="button" class="pill-btn ${currentFilter === "eu_hub" ? "active" : ""}" data-filter="eu_hub">EU Hub (${counts.eu_hub})</button>
                <button type="button" class="pill-btn ${currentFilter === "exit_node" ? "active" : ""}" data-filter="exit_node">Exit Node (${counts.exit_node})</button>
            </div>
            <div id="nodesListContainer" class="nodes-list"></div>
        </section>
    `;

    root.querySelector("#addNodeOpenBtn").addEventListener("click", () => {
        _openAddNodeModal(onReload, onSelectNode);
    });

    root.querySelector("#syncTopologyBtn").addEventListener("click", async (e) => {
        const btn = e.currentTarget;
        await withButtonLoading(btn, "Синк...", async () => {
            try {
                const res = await api.syncTopology();
                const synced = Number(res?.synced || 0);
                const failed = Number(res?.failed || 0);
                showToast(
                    `Топология синхронизирована: ${synced} успешно, ${failed} с ошибкой`,
                    failed > 0 ? "warn" : "success"
                );
            } catch (err) {
                showToast(err.message || "Ошибка синка топологии", "error");
            }
        });
    });

    root.querySelector("#reloadNodesBtn").addEventListener("click", async (e) => {
        const btn = e.currentTarget;
        await withButtonLoading(btn, "Обновление...", async () => {
            try {
                lastReloadedAt = new Date();
                await onReload();
            } catch (err) {
                showToast(err.message || "Не удалось обновить список", "error");
            }
        });
    });

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
            <div class="row" style="align-items:center;">
                <div class="node-compact-title">
                    ${renderFlagHtml(node.country_flag || node.location_code || "", 20)}
                    <span style="font-weight:600; font-size:14px;">${escapeHtml(node.name)}</span>
                    <span class="meta-text">#${node.id}</span>
                </div>
                <div class="row" style="gap:8px; align-items:center;">
                    <span class="chip chip-muted">${escapeHtml(roleLabel(node.role))}</span>
                    <span class="chip ${health.cls}">${escapeHtml(health.text)}</span>
                    <span class="node-chevron" aria-hidden="true">›</span>
                </div>
            </div>
            <div class="row meta-text">
                <span class="mono">${escapeHtml(node.domain || node.api_url)}</span>
                <span>${health.text === "Отключена" ? escapeHtml(checkText) : `${node.is_active ? "● " : ""}${escapeHtml(checkText)}`}</span>
            </div>
            ${scopeBadges || diagBadges ? `<div class="filter-pills">${scopeBadges}${diagBadges}</div>` : ""}
        `;

        card.addEventListener("click", () => {
            if (typeof onSelectNode === "function") {
                onSelectNode(node.id);
            }
        });

        listEl.appendChild(card);
    });
}
