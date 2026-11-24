import { api } from "../api.js?v=2.7.8";
import { escapeHtml, healthChipInfo, openModal, renderCountryFlagField, renderFlagHtml, roleLabel, showToast, wireCountryFlagFields } from "../ui.js?v=2.7.8";

let currentFilter = "all";

function _openLocationSubModal(node, onDone) {
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
        <label class="checkbox-row">
            <input name="in_standard" type="checkbox" ${inStdChecked ? "checked" : ""}>
            <span>Входит в Стандарт</span>
        </label>
        <label class="checkbox-row">
            <input name="in_white_internet" type="checkbox" ${inWiChecked ? "checked" : ""}>
            <span>Входит в Белый Интернет</span>
        </label>
        <hr style="border:none; border-top:1px solid var(--border-subtle); margin:2px 0;">
        <div class="muted" style="font-size:12px; font-weight:600;">Стандарт</div>
        <div class="grid-2">
            <div class="field">
                <label>Название</label>
                <input name="vless_name" class="input" maxlength="120" value="${escapeHtml(baseLocName)}" placeholder="${escapeHtml(node.name)}">
            </div>
            <div class="field">
                <label>Бейдж (до 30 симв.)</label>
                <input name="server_description" class="input" maxlength="30" value="${escapeHtml(node.server_description || "")}" placeholder="Опционально">
            </div>
        </div>
        <div class="muted" style="font-size:12px; font-weight:600;">Белый Интернет — Yandex</div>
        <div class="grid-2">
            <div class="field">
                <label>Название</label>
                <input name="wi_yandex_name" class="input" maxlength="120" value="${escapeHtml(node.wi_yandex_name || defaultYandexName)}" placeholder="${escapeHtml(defaultYandexName)}">
            </div>
            <div class="field">
                <label>Бейдж (до 30 симв.)</label>
                <input name="wi_yandex_badge" class="input" maxlength="30" value="${escapeHtml(node.wi_yandex_badge || "")}" placeholder="Опционально">
            </div>
        </div>
        <div class="muted" style="font-size:12px; font-weight:600;">Белый Интернет — Turboflare</div>
        <div class="grid-2">
            <div class="field">
                <label>Название</label>
                <input name="wi_turboflare_name" class="input" maxlength="120" value="${escapeHtml(node.wi_turboflare_name || defaultTurboflareName)}" placeholder="${escapeHtml(defaultTurboflareName)}">
            </div>
            <div class="field">
                <label>Бейдж (до 30 симв.)</label>
                <input name="wi_turboflare_badge" class="input" maxlength="30" value="${escapeHtml(node.wi_turboflare_badge || "")}" placeholder="Опционально">
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
        const stdName = String(box.querySelector("input[name='vless_name']")?.value || "").trim() || baseLocName;
        const stdBadge = String(box.querySelector("input[name='server_description']")?.value || "").trim();
        const yanName = String(box.querySelector("input[name='wi_yandex_name']")?.value || "").trim() || defaultYandexName;
        const yanBadge = String(box.querySelector("input[name='wi_yandex_badge']")?.value || "").trim();
        const tfName = String(box.querySelector("input[name='wi_turboflare_name']")?.value || "").trim() || defaultTurboflareName;
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
        previewEl.innerHTML = `<div class="muted" style="font-size:11px;">Как будет выглядеть в клиенте:</div>${lines.join("")}`;
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
        btn.disabled = true;
        try {
            await api.patchNode(node.id, {
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
            showToast("Локация обновлена");
            close();
            if (typeof onDone === "function") {
                await onDone();
            }
        } catch (err) {
            showToast(err.message || "Ошибка обновления локации");
        } finally {
            btn.disabled = false;
        }
    });
}

export async function renderSubscriptionSettingsView(body, state = null, onReloadNodes = null) {
    body.innerHTML = `<section class="card card-center"><div class="muted">Загрузка настроек подписки...</div></section>`;
    let current;
    try {
        const res = await api.getIncySettings();
        current = res.settings || {};
    } catch (err) {
        body.innerHTML = `<section class="card"><div class="muted">${escapeHtml(err.message || "Не удалось загрузить настройки подписки")}</div></section>`;
        return;
    }

    const exitNodes = (state && Array.isArray(state.nodes) ? state.nodes : []).filter(
        (n) => n.role === "exit_node"
    );

    body.innerHTML = `
        <section class="card">
            <div class="row">
                <div class="card-title">Локации в подписке (Exit Nodes)</div>
                <span class="chip chip-muted">${exitNodes.length}</span>
            </div>
            <div id="subLocationsList" class="nodes-list"></div>
        </section>

        <section class="card">
            <div class="card-title">Оформление подписки (INCY / Happ)</div>
            <div class="notice-box" style="flex-direction:column; align-items:flex-start; gap:4px; font-size:12px; margin-bottom:12px;">
                <strong>Пример карточки в INCY / Happ:</strong>
                <span><b>✦ Just1k</b> <span class="muted">(Profile-Title, автообновление 6 ч)</span></span>
                <span><i>Стандарт: 6 дн. • ∞ • устр. 1/2</i><br><i style="padding-left:16px;">Белый интернет: 29 дн. • 0/50 ГБ • устр. 1/1</i> <span class="muted">(Announce)</span></span>
                <span>Кнопки: <b>Поддержка</b> (Support-Url) • <b>Канал / Бот</b> (Announce-Url)</span>
            </div>
            <form id="subSettingsForm" class="field" style="gap:10px;">
                <div class="grid-2">
                    <div class="field">
                        <label>Название (Profile-Title)</label>
                        <input name="profile_title" class="input" maxlength="120" value="${escapeHtml(current.profile_title || "")}" placeholder="✦ Just1k">
                    </div>
                    <div class="field">
                        <label>Автообновление (часы)</label>
                        <input name="profile_update_interval" class="input" type="number" min="1" max="720" value="${escapeHtml(String(current.profile_update_interval || 6))}">
                    </div>
                </div>
                <label class="checkbox-row">
                    <input name="profile_show_devices" type="checkbox" ${current.profile_show_devices ? "checked" : ""}>
                    <span>Показывать счётчик устройств в Announce (при пустом поле Announce)</span>
                </label>
                <div class="field">
                    <label>Баннер / Анонс (Announce)</label>
                    <textarea name="announce" class="textarea" maxlength="300" style="min-height:64px;" placeholder="Оставьте пустым для авто-статуса в 2 строки (дни + ГБ)">${escapeHtml(current.announce || "")}</textarea>
                    <div class="muted" style="font-size:11px;">Жёлтая плашка сверху. Пусто = авто-сводка по срокам и ГБ обоих тарифов.</div>
                </div>
                <div class="field">
                    <label>Ссылка анонса (Announce-Url)</label>
                    <input name="announce_url" class="input" maxlength="500" value="${escapeHtml(current.announce_url || "")}" placeholder="https://t.me/...">
                </div>
                <div class="grid-2">
                    <div class="field">
                        <label>Support-Url (кнопка «Поддержка»)</label>
                        <input name="support_url" class="input" maxlength="500" value="${escapeHtml(current.support_url || "")}" placeholder="По умолчанию из настроек бота">
                    </div>
                    <div class="field">
                        <label>Profile-Web-Page-Url (сайт/бот)</label>
                        <input name="web_page_url" class="input" maxlength="500" value="${escapeHtml(current.web_page_url || "")}" placeholder="По умолчанию ссылка на бота">
                    </div>
                </div>
                <div class="field">
                    <label>Ссылка на прокси для Telegram (MTProto Proxy URL)</label>
                    <input name="mtproto_proxy_url" class="input" maxlength="500" value="${escapeHtml(current.mtproto_proxy_url || "")}" placeholder="https://t.me/proxy?server=...&port=...&secret=...">
                    <div class="muted" style="font-size:11px;">Кнопка быстрого подключения прокси в боте и личном кабинете. Оставьте пустым, чтобы скрыть.</div>
                </div>

                <hr style="border:none; border-top:1px solid var(--border-subtle); margin:2px 0;">
                <div class="card-title" style="font-size:13px;">Поведение и сортировка</div>
                <div class="muted" style="font-size:11px;">Порядок серверов, лимит памяти iOS для XHTTP и скрытие элементов интерфейса в клиенте.</div>
                <div class="grid-2">
                    <div class="field">
                        <label>Сортировка серверов</label>
                        <select name="sort_order" class="input">
                            <option value="none" ${(current.sort_order || "none") === "none" ? "selected" : ""}>Как отдал сервер (none)</option>
                            <option value="ping" ${current.sort_order === "ping" ? "selected" : ""}>По пингу (ping)</option>
                            <option value="name" ${current.sort_order === "name" ? "selected" : ""}>По имени (name)</option>
                        </select>
                    </div>
                    <div class="field">
                        <label>No-Limit режим (память iOS)</label>
                        <select name="no_limit_mode" class="input">
                            <option value="all" ${(current.no_limit_mode || "all") === "all" ? "selected" : ""}>No-Limit-Enabled (все)</option>
                            <option value="xhttp" ${current.no_limit_mode === "xhttp" ? "selected" : ""}>no-limit-xhttp-enabled (Happ)</option>
                            <option value="off" ${current.no_limit_mode === "off" ? "selected" : ""}>Выключено</option>
                        </select>
                    </div>
                </div>
                <div class="grid-2">
                    <label class="checkbox-row">
                        <input name="hide_url" type="checkbox" ${current.hide_url !== false ? "checked" : ""}>
                        <span>Hide-Url (скрыть URL и QR)</span>
                    </label>
                    <label class="checkbox-row">
                        <input name="hide_check" type="checkbox" ${current.hide_check ? "checked" : ""}>
                        <span>Hide-Check (INCY: скрыть пинг)</span>
                    </label>
                </div>

                <hr style="border:none; border-top:1px solid var(--border-subtle); margin:2px 0;">
                <div class="card-title" style="font-size:13px;">Фрагментация TLS</div>
                <div class="muted" style="font-size:11px;">Дробит TLS ClientHello (напр. tlshello, 10-30 байт каждые 10-30 мс) от DPI. Выключайте, если тормозит хендшейк.</div>
                <label class="checkbox-row">
                    <input name="fragmentation_enabled" type="checkbox" ${current.fragmentation_enabled !== false ? "checked" : ""}>
                    <span>Включить fragmentation-enable</span>
                </label>
                <div class="grid-2">
                    <div class="field">
                        <label>Packets</label>
                        <input name="fragmentation_packets" class="input" maxlength="64" value="${escapeHtml(current.fragmentation_packets || "tlshello")}" placeholder="tlshello">
                    </div>
                    <div class="field">
                        <label>Length</label>
                        <input name="fragmentation_length" class="input" maxlength="32" value="${escapeHtml(current.fragmentation_length || "10-30")}" placeholder="10-30">
                    </div>
                </div>
                <div class="field">
                    <label>Interval</label>
                    <input name="fragmentation_interval" class="input" maxlength="32" value="${escapeHtml(current.fragmentation_interval || "10-30")}" placeholder="10-30">
                </div>

                <hr style="border:none; border-top:1px solid var(--border-subtle); margin:2px 0;">
                <div class="card-title" style="font-size:13px;">DoH Pre-resolve (INCY / Happ)</div>
                <div class="muted" style="font-size:11px;">Резолвит домен сервера через DoH до старта туннеля (защита от DNS-подмены; пример: dns.google + 8.8.8.8).</div>
                <label class="checkbox-row">
                    <input name="dns_resolve_enabled" type="checkbox" ${current.dns_resolve_enabled ? "checked" : ""}>
                    <span>Включить Server-Address-Resolve-Enable</span>
                </label>
                <div class="grid-2">
                    <div class="field">
                        <label>DoH Домен</label>
                        <input name="dns_resolve_domain" class="input" maxlength="253" value="${escapeHtml(current.dns_resolve_domain || "")}" placeholder="dns.google">
                    </div>
                    <div class="field">
                        <label>Bootstrap DNS IP</label>
                        <input name="dns_resolve_ip" class="input" maxlength="64" value="${escapeHtml(current.dns_resolve_ip || "")}" placeholder="8.8.8.8">
                    </div>
                </div>

                <hr style="border:none; border-top:1px solid var(--border-subtle); margin:2px 0;">
                <div class="card-title" style="font-size:13px;">Раздельное туннелирование (Android)</div>
                <div class="muted" style="font-size:11px;">Исключает выбранные приложения (bypass, напр. банки) или пускает только их (proxy). Список через запятую или с новой строки.</div>
                <label class="checkbox-row">
                    <input name="per_app_proxy_enabled" type="checkbox" ${current.per_app_proxy_enabled ? "checked" : ""}>
                    <span>Включить Per-App-Proxy-Enable</span>
                </label>
                <div class="field">
                    <label>Режим</label>
                    <select name="per_app_proxy_mode" class="input">
                        <option value="bypass" ${(current.per_app_proxy_mode || "bypass") === "bypass" ? "selected" : ""}>Исключить (bypass)</option>
                        <option value="proxy" ${current.per_app_proxy_mode === "proxy" ? "selected" : ""}>Только выбранные (proxy)</option>
                    </select>
                </div>
                <div class="field">
                    <label>Список приложений (package names)</label>
                    <textarea name="per_app_proxy_list" class="textarea" maxlength="2000" style="min-height:56px;" placeholder="ru.sberbankmobile,com.whatsapp">${escapeHtml(current.per_app_proxy_list || "")}</textarea>
                </div>

                <button type="submit" class="btn btn-primary">Сохранить настройки</button>
            </form>
        </section>
    `;

    const locListEl = body.querySelector("#subLocationsList");
    if (!exitNodes.length) {
        locListEl.innerHTML = `<div class="muted" style="text-align:center; padding:10px;">Нет exit-нод. Добавьте их во вкладке «Ноды».</div>`;
    } else {
        exitNodes.forEach((node) => {
            const inStd = node.in_standard !== false;
            const inWi = Boolean(node.in_white_internet);
            const row = document.createElement("div");
            row.className = "node-card node-card-compact";
            row.style.cursor = "pointer";
            row.innerHTML = `
                <div class="row" style="align-items:center;">
                    <div class="node-compact-title">
                        ${renderFlagHtml(node.country_flag || node.location_code || "", 20)}
                        <span style="font-weight:600; font-size:14px;">${escapeHtml(node.vless_name || node.name)}</span>
                    </div>
                    <button type="button" class="btn btn-sm">Настроить</button>
                </div>
                <div class="filter-pills" style="margin-top:4px;">
                    <span class="chip ${inStd ? "chip-ok" : "chip-muted"}">Стандарт: ${inStd ? "да" : "нет"}</span>
                    <span class="chip ${inWi ? "chip-wi" : "chip-muted"}">БИ: ${inWi ? "да" : "нет"}</span>
                </div>
            `;
            row.addEventListener("click", () => {
                _openLocationSubModal(node, onReloadNodes);
            });
            locListEl.appendChild(row);
        });
    }

    const form = body.querySelector("#subSettingsForm");
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(form);
        const btn = form.querySelector("button[type='submit']");
        btn.disabled = true;
        try {
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
                sort_order: String(fd.get("sort_order") || "none"),
                no_limit_mode: String(fd.get("no_limit_mode") || "all"),
                hide_url: Boolean(fd.get("hide_url")),
                hide_check: Boolean(fd.get("hide_check")),
                fragmentation_enabled: Boolean(fd.get("fragmentation_enabled")),
                fragmentation_packets: String(fd.get("fragmentation_packets") || "").trim(),
                fragmentation_length: String(fd.get("fragmentation_length") || "").trim(),
                fragmentation_interval: String(fd.get("fragmentation_interval") || "").trim(),
                dns_resolve_enabled: Boolean(fd.get("dns_resolve_enabled")),
                dns_resolve_domain: String(fd.get("dns_resolve_domain") || "").trim(),
                dns_resolve_ip: String(fd.get("dns_resolve_ip") || "").trim(),
                per_app_proxy_enabled: Boolean(fd.get("per_app_proxy_enabled")),
                per_app_proxy_mode: String(fd.get("per_app_proxy_mode") || "bypass"),
                per_app_proxy_list: String(fd.get("per_app_proxy_list") || "").trim(),
            });
            showToast("Настройки подписки сохранены");
        } catch (err) {
            showToast(err.message || "Ошибка сохранения");
        } finally {
            btn.disabled = false;
        }
    });
}

function _nodeScopeBadges(node) {
    const caps = Array.isArray(node.capabilities) ? node.capabilities : [];
    const links = node.links && typeof node.links === "object" ? node.links : {};
    const hasStd = caps.includes("vless") || caps.includes("standard") || Boolean(links.standard && Object.keys(links.standard).length);
    const hasBi = caps.includes("white_internet") || Boolean(links.bi && Object.keys(links.bi).length);
    const badges = [];
    if (hasStd) badges.push(`<span class="chip chip-muted">Стандарт</span>`);
    if (hasBi) badges.push(`<span class="chip chip-wi">БИ</span>`);
    return badges.join("");
}

export function renderNodesView(root, state, onReload, onSelectNode) {
    const nodes = state.nodes || [];
    const counts = {
        all: nodes.length,
        ru_hub: nodes.filter((n) => n.role === "ru_hub").length,
        eu_hub: nodes.filter((n) => n.role === "eu_hub").length,
        exit_node: nodes.filter((n) => n.role === "exit_node").length,
    };
    const filtered = currentFilter === "all" ? nodes : nodes.filter((n) => n.role === currentFilter);

    root.innerHTML = `
        <section class="stats-strip">
            <div class="stat-box"><div class="stat-val">${counts.all}</div><div class="stat-lbl">Всего</div></div>
            <div class="stat-box"><div class="stat-val">${counts.ru_hub}</div><div class="stat-lbl">RU Hub</div></div>
            <div class="stat-box"><div class="stat-val">${counts.eu_hub}</div><div class="stat-lbl">EU Hub</div></div>
            <div class="stat-box"><div class="stat-val">${counts.exit_node}</div><div class="stat-lbl">Exit</div></div>
        </section>

        <section class="card">
            <div class="row">
                <div class="card-title">Добавить ноду (v2 auto-discovery)</div>
            </div>
            <form id="addNodeForm" class="field" style="gap:12px;">
                <div class="field">
                    <label>URL API ноды</label>
                    <input id="addApiUrl" class="input" type="url" required placeholder="https://node.domain.com:8444">
                </div>
                <div class="field">
                    <label>API Токен</label>
                    <input id="addApiKey" class="input" type="password" required placeholder="Bearer / X-API-Key токен">
                </div>
                <div class="grid-2">
                    <div class="field">
                        <label>Название (опционально)</label>
                        <input id="addName" class="input" type="text" maxlength="255">
                    </div>
                    ${renderCountryFlagField({ id: "addFlag", label: "Страна (опционально)", emptyLabel: "Авто (GeoIP)" })}
                </div>
                <button id="addSubmitBtn" type="submit" class="btn btn-primary">Проверить и добавить</button>
            </form>
        </section>

        <section class="card">
            <div class="row">
                <div class="card-title">Список нод</div>
                <button id="reloadNodesBtn" type="button" class="btn btn-sm">Обновить список</button>
            </div>
            <div class="filter-pills">
                <button type="button" class="pill-btn ${currentFilter === "all" ? "active" : ""}" data-filter="all">Все (${counts.all})</button>
                <button type="button" class="pill-btn ${currentFilter === "ru_hub" ? "active" : ""}" data-filter="ru_hub">RU Hub (${counts.ru_hub})</button>
                <button type="button" class="pill-btn ${currentFilter === "eu_hub" ? "active" : ""}" data-filter="eu_hub">EU Hub (${counts.eu_hub})</button>
                <button type="button" class="pill-btn ${currentFilter === "exit_node" ? "active" : ""}" data-filter="exit_node">Exit Node (${counts.exit_node})</button>
            </div>
            <div id="nodesListContainer" class="nodes-list"></div>
        </section>
    `;

    root.querySelector("#reloadNodesBtn").addEventListener("click", onReload);
    root.querySelectorAll("[data-filter]").forEach((btn) => {
        btn.addEventListener("click", () => {
            currentFilter = btn.dataset.filter;
            renderNodesView(root, state, onReload, onSelectNode);
        });
    });

    const addForm = root.querySelector("#addNodeForm");
    wireCountryFlagFields(addForm);
    const addBtn = root.querySelector("#addSubmitBtn");
    addForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        addBtn.disabled = true;
        try {
            await api.addNode({
                api_url: root.querySelector("#addApiUrl").value.trim(),
                api_key: root.querySelector("#addApiKey").value.trim(),
                name: root.querySelector("#addName").value.trim(),
                country_flag: root.querySelector("#addFlag").value.trim() || null,
            });
            addForm.reset();
            showToast("Нода добавлена и выключена. Откройте её для включения.");
            await onReload();
        } catch (err) {
            showToast(err.message || "Не удалось добавить ноду");
        } finally {
            addBtn.disabled = false;
        }
    });

    const listEl = root.querySelector("#nodesListContainer");
    if (!filtered.length) {
        listEl.innerHTML = `<div class="muted" style="text-align:center; padding:14px;">Ноды не найдены</div>`;
        return;
    }

    filtered.forEach((node) => {
        const health = healthChipInfo(node);
        const card = document.createElement("div");
        card.className = "node-card node-card-compact";
        card.setAttribute("role", "button");
        card.setAttribute("tabindex", "0");
        card.innerHTML = `
            <div class="row" style="align-items:center;">
                <div class="node-compact-title">
                    ${renderFlagHtml(node.country_flag || node.location_code || "", 20)}
                    <span style="font-weight:600; font-size:14px;">${escapeHtml(node.name)}</span>
                    <span class="muted" style="font-size:12px;">#${node.id}</span>
                </div>
                <div class="row" style="gap:6px; align-items:center;">
                    <span class="chip chip-muted">${escapeHtml(roleLabel(node.role))}</span>
                    <span class="chip ${health.cls}">${escapeHtml(health.text)}</span>
                    <span class="node-chevron">›</span>
                </div>
            </div>
            <div class="row muted" style="font-size:12px; margin-top:3px;">
                <span class="mono">${escapeHtml(node.domain || node.api_url)}</span>
                <span>${node.is_active ? "● Включен" : "○ Выключен"}</span>
            </div>
            <div class="filter-pills" style="margin-top:4px;">${_nodeScopeBadges(node)}</div>
        `;

        card.addEventListener("click", () => {
            if (typeof onSelectNode === "function") {
                onSelectNode(node.id);
            }
        });

        card.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                if (typeof onSelectNode === "function") {
                    onSelectNode(node.id);
                }
            }
        });

        listEl.appendChild(card);
    });
}
