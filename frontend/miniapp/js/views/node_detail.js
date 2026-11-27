import { api } from "../api.js?v=2.8.0";
import { formatNodeDisabledReason } from "../admin_labels.js?v=2.8.0";
import {
    copyText,
    escapeHtml,
    formatDate,
    formatRelativeTime,
    healthChipInfo,
    openModal,
    renderCountryFlagField,
    renderFlagHtml,
    roleLabel,
    showConfirmModal,
    showToast,
    wireCountryFlagFields,
    withButtonLoading,
} from "../ui.js?v=2.8.0";
import { openLocationSubModal } from "./nodes.js?v=2.8.0";

function _renderModules(node) {
    const mods = node.modules || {};
    const names = Object.keys(mods);
    if (!names.length) {
        return `<span class="chip chip-muted">Модули не настроены</span>`;
    }
    return names
        .map((name) => {
            const info = mods[name];
            const enabled = Boolean(info && typeof info === "object" ? info.enabled : info);
            const port = info && typeof info === "object" && info.port ? `:${info.port}` : "";
            return `<span class="chip ${enabled ? "chip-ok" : "chip-muted"}">${escapeHtml(name)}${escapeHtml(port)}</span>`;
        })
        .join("");
}

function _renderModuleErrors(node) {
    const errs = node.module_errors || {};
    const entries = Object.entries(errs);
    if (!entries.length) return "";
    const lines = entries
        .map(([k, v]) => `<div>• <b>${escapeHtml(k)}</b>: ${escapeHtml(v)}</div>`)
        .join("");
    return `<section class="danger-card"><h2 class="danger-card-title">Ошибки модулей (${entries.length})</h2><div class="meta-list text-err">${lines}</div></section>`;
}

function _metricBarHtml(label, pctRaw, extraText = "") {
    if (pctRaw == null || !Number.isFinite(Number(pctRaw))) return "";
    const pct = Math.max(0, Math.min(100, Number(pctRaw)));
    const scale = (pct / 100).toFixed(4);
    const cls = pct >= 95 ? "err" : pct >= 80 ? "warn" : "";
    return `
        <div style="display:flex; flex-direction:column; gap:4px;">
            <div class="row" style="font-size:12px;">
                <span class="detail-label">${escapeHtml(label)}</span>
                <span class="tabular"><b>${pct.toFixed(1).replace(/\.0$/, "")}%</b>${extraText ? ` <span class="muted">(${escapeHtml(extraText)})</span>` : ""}</span>
            </div>
            <div class="metric-bar"><div class="metric-fill ${cls}" style="transform:scaleX(${scale})"></div></div>
        </div>
    `;
}

function _renderTelemetryCard(node) {
    const sys = node.system_load && typeof node.system_load === "object" ? node.system_load : null;
    const disk = node.disk_usage && typeof node.disk_usage === "object" ? node.disk_usage : null;
    const tl = node.traffic_limit && typeof node.traffic_limit === "object" ? node.traffic_limit : null;
    const ssl = node.ssl_cert && typeof node.ssl_cert === "object" ? node.ssl_cert : null;
    const lastApply = node.last_apply && typeof node.last_apply === "object" ? node.last_apply : null;

    const cpuPct = sys && sys.cpu_percent != null ? sys.cpu_percent : null;
    const ramPct = sys && sys.ram_percent != null ? sys.ram_percent : null;
    const diskPct =
        sys && sys.disk_percent != null
            ? sys.disk_percent
            : disk && disk.used_percent != null
                ? disk.used_percent
                : null;
    const diskExtra =
        disk && disk.used_gb != null && disk.total_gb != null
            ? `${disk.used_gb} / ${disk.total_gb} ГБ`
            : disk && disk.used_bytes != null && disk.total_bytes != null
                ? `${(Number(disk.used_bytes) / 1024 ** 3).toFixed(1)} / ${(Number(disk.total_bytes) / 1024 ** 3).toFixed(1)} ГБ`
                : "";

    const trafficUsedGb = tl ? (Number(tl.monthly_bytes || tl.used_bytes || 0) / (1024 ** 3)).toFixed(2) : null;
    const trafficLimitGb = tl && tl.limit_bytes ? (Number(tl.limit_bytes) / (1024 ** 3)).toFixed(0) : null;
    const trafficText = tl
        ? `${trafficUsedGb} ГБ${trafficLimitGb ? ` / ${trafficLimitGb} ГБ` : ""}${tl.reset_day ? ` · сброс ${tl.reset_day}-го` : ""}`
        : "";

    const hasOutageInfo =
        Boolean(node.disabled_reason) ||
        Boolean(node.ingress_problem) ||
        Boolean(node.cdn_problem) ||
        Number(node.consecutive_fails || 0) > 0 ||
        Number(node.consecutive_ingress_fails || 0) > 0 ||
        Number(node.consecutive_cdn_fails || 0) > 0 ||
        Boolean(node.problem_started_at) ||
        Boolean(ssl && (ssl.days_left != null || ssl.expires_at)) ||
        Boolean(lastApply);

    const sslDaysLeft = ssl && ssl.days_left != null && Number.isFinite(Number(ssl.days_left)) ? Math.floor(Number(ssl.days_left)) : null;
    const sslCls = sslDaysLeft != null ? (sslDaysLeft < 7 ? "chip-err" : sslDaysLeft < 14 ? "chip-warn" : "chip-ok") : "chip-muted";

    const applyStatusRaw = lastApply ? String(lastApply.status || "").toLowerCase() : "";
    const applyFailed = Boolean(
        lastApply &&
        (lastApply.ok === false ||
            applyStatusRaw === "failed" ||
            applyStatusRaw === "error" ||
            applyStatusRaw === "rollback" ||
            lastApply.rollback_error ||
            lastApply.error ||
            lastApply.rolled_back)
    );
    const applyBadgeCls = applyFailed ? "chip-err" : "chip-ok";
    const applyLabel = lastApply?.rolled_back
        ? "Откат"
        : applyFailed
          ? "Ошибка"
          : "Успешно";
    const applyAt = lastApply ? (lastApply.applied_at || lastApply.timestamp || lastApply.ts || lastApply.at || null) : null;
    const applyErrText = lastApply ? (lastApply.rollback_error || lastApply.error || "") : "";

    const hasEpochs = node.vless_synced_epoch != null || node.xray_instance_epoch != null;
    const epochMismatch =
        node.vless_synced_epoch != null &&
        node.xray_instance_epoch != null &&
        String(node.vless_synced_epoch) !== String(node.xray_instance_epoch);

    const bars = [
        _metricBarHtml("CPU", cpuPct),
        _metricBarHtml("RAM", ramPct),
        _metricBarHtml("Диск", diskPct, diskExtra),
        tl && tl.limit_bytes ? _metricBarHtml("Трафик хостинга", tl.pct, trafficText) : "",
    ]
        .filter(Boolean)
        .join("");

    return `
        <section class="card">
            <div class="row">
                <h2 class="card-title">Телеметрия и диагностика</h2>
                <button type="button" id="copyDiagBtn" class="btn btn-sm">Скопировать диагностику</button>
            </div>
            ${bars ? `<div class="stack-sm">${bars}</div>` : ""}
            <div class="detail-grid">
                ${tl && !tl.limit_bytes ? `
                <div class="detail-item">
                    <span class="detail-label">Трафик за месяц</span>
                    <span>${escapeHtml(trafficText)}</span>
                </div>` : ""}
                ${tl && tl.exceeded ? `
                <div class="detail-item">
                    <span class="detail-label">Лимит трафика</span>
                    <span class="chip chip-err">Превышен</span>
                </div>` : ""}
                ${node.disabled_reason ? `
                <div class="detail-item">
                    <span class="detail-label">Причина отключения</span>
                    <span class="chip chip-err">${escapeHtml(formatNodeDisabledReason(node.disabled_reason))}</span>
                </div>` : ""}
                ${node.ingress_problem || Number(node.consecutive_ingress_fails || 0) > 0 ? `
                <div class="detail-item">
                    <span class="detail-label">Ingress</span>
                    <span class="chip ${node.ingress_problem ? "chip-err" : "chip-warn"}">
                        ${node.ingress_problem ? "Проблема" : "Сбои"} (ошибок: ${Number(node.consecutive_ingress_fails || 0)})
                    </span>
                </div>` : ""}
                ${node.cdn_problem || Number(node.consecutive_cdn_fails || 0) > 0 ? `
                <div class="detail-item">
                    <span class="detail-label">CDN</span>
                    <span class="chip ${node.cdn_problem ? "chip-err" : "chip-warn"}">
                        ${node.cdn_problem ? "Проблема" : "Сбои"} (ошибок: ${Number(node.consecutive_cdn_fails || 0)})
                    </span>
                </div>` : ""}
                ${Number(node.consecutive_fails || 0) > 0 ? `
                <div class="detail-item">
                    <span class="detail-label">Ошибки опроса подряд</span>
                    <span class="chip chip-warn">${Number(node.consecutive_fails)}</span>
                </div>` : ""}
                ${node.problem_started_at ? `
                <div class="detail-item">
                    <span class="detail-label">Начало проблемы</span>
                    <span>${escapeHtml(formatRelativeTime(node.problem_started_at))} <span class="muted">(${escapeHtml(formatDate(node.problem_started_at))})</span></span>
                </div>` : ""}
                ${hasEpochs ? `
                <div class="detail-item">
                    <span class="detail-label">Epoch (Синхр. / Ядро)</span>
                    <span class="mono">
                        ${escapeHtml(node.vless_synced_epoch ?? "—")} / ${escapeHtml(node.xray_instance_epoch ?? "—")}
                        ${epochMismatch ? ` <span class="chip chip-warn">Рассинхрон</span>` : ""}
                    </span>
                </div>` : ""}
                ${ssl && (sslDaysLeft != null || ssl.expires_at) ? `
                <div class="detail-item">
                    <span class="detail-label">SSL-сертификат</span>
                    <span>
                        ${sslDaysLeft != null ? `<span class="chip ${sslCls}">${sslDaysLeft <= 0 ? "истёк" : `осталось ${sslDaysLeft} дн.`}</span>` : ""}
                        ${ssl.expires_at ? ` <span class="muted">(до ${escapeHtml(formatDate(ssl.expires_at))})</span>` : ""}
                    </span>
                </div>` : ""}
                ${lastApply ? `
                <div class="detail-item">
                    <span class="detail-label">Последний apply</span>
                    <span>
                        <span class="chip ${applyBadgeCls}">${escapeHtml(applyLabel)}</span>
                        ${applyAt ? ` <span class="muted">${escapeHtml(formatRelativeTime(applyAt))} (${escapeHtml(formatDate(applyAt))})</span>` : ""}
                    </span>
                </div>` : ""}
                <div class="detail-item">
                    <span class="detail-label">Последняя проверка</span>
                    <span>${escapeHtml(formatRelativeTime(node.last_successful_check))}${node.last_successful_check ? ` <span class="muted">(${escapeHtml(formatDate(node.last_successful_check))})</span>` : ""}</span>
                </div>
                ${!bars && !hasOutageInfo && !hasEpochs ? `<div class="muted" style="font-size:12px;">Метрики нагрузки пока не поступали.</div>` : ""}
            </div>
            ${applyErrText ? `<div class="text-err" style="margin-top:8px; padding:8px 12px; border-radius:8px; background:var(--accent-red-soft); border:1px solid rgba(248,113,113,0.32); font-size:12px; white-space:pre-wrap;">Ошибка применения: ${escapeHtml(applyErrText)}</div>` : ""}
        </section>
    `;
}

function _formatLinkItem(item) {
    if (item && typeof item === "object") {
        return item.name || item.domain || item.ip || item.host || item.id || JSON.stringify(item);
    }
    return String(item ?? "—");
}

function _formatCascadeLinks(links) {
    if (!links || typeof links !== "object") return "";
    const entries = Object.entries(links);
    if (!entries.length) return "";
    return entries
        .map(([group, val]) => {
            if (val && typeof val === "object" && !Array.isArray(val)) {
                const sub = Object.entries(val)
                    .map(([k, v]) => {
                        const txt = Array.isArray(v)
                            ? v.map(_formatLinkItem).join(", ") || "—"
                            : typeof v === "object" && v !== null
                              ? _formatLinkItem(v)
                              : String(v ?? "—");
                        return `<span class="chip chip-muted">${escapeHtml(k)}: ${escapeHtml(txt)}</span>`;
                    })
                    .join(" ");
                return `<div class="detail-item"><span class="detail-label">${escapeHtml(group)}</span><div class="filter-pills">${sub || "—"}</div></div>`;
            }
            const formatted = Array.isArray(val)
                ? val.map(_formatLinkItem).join(", ") || "—"
                : String(val ?? "—");
            return `<div class="detail-item"><span class="detail-label">${escapeHtml(group)}</span><span class="mono">${escapeHtml(formatted)}</span></div>`;
        })
        .join("");
}

function _renderTopologyCard(node) {
    const cdnDomains = node.cdn_domains && typeof node.cdn_domains === "object" ? Object.entries(node.cdn_domains) : [];
    const xhttpLocs = Array.isArray(node.xhttp_locations) ? node.xhttp_locations : [];
    const sniRoutes = Array.isArray(node.sni_routes) ? node.sni_routes : [];
    const linksHtml = _formatCascadeLinks(node.links);

    if (!cdnDomains.length && !xhttpLocs.length && !sniRoutes.length && !linksHtml) {
        return "";
    }

    return `
        <section class="card">
            <h2 class="card-title">Multi-CDN и каскад</h2>
            <div class="detail-grid">
                ${cdnDomains.map(([k, v]) => `
                    <div class="detail-item">
                        <span class="detail-label">CDN (${escapeHtml(k)})</span>
                        <button type="button" class="mono-copy" data-copy="${escapeHtml(v)}">${escapeHtml(v)}</button>
                    </div>
                `).join("")}
                ${xhttpLocs.length ? `
                    <div class="detail-item">
                        <span class="detail-label" title="XHTTP Locations">Пути XHTTP</span>
                        <div class="filter-pills">
                            ${xhttpLocs.map((loc) => `<span class="chip chip-muted">${escapeHtml(typeof loc === "object" && loc !== null ? (loc.path || loc.location || JSON.stringify(loc)) : String(loc))}</span>`).join("")}
                        </div>
                    </div>
                ` : ""}
                ${sniRoutes.length ? `
                    <div class="detail-item">
                        <span class="detail-label" title="SNI Routes">Маршруты SNI</span>
                        <div class="filter-pills">
                            ${sniRoutes.map((r) => `<span class="chip chip-muted">${escapeHtml(typeof r === "object" && r !== null ? (r.sni || r.domain || JSON.stringify(r)) : String(r))}</span>`).join("")}
                        </div>
                    </div>
                ` : ""}
                ${linksHtml}
            </div>
        </section>
    `;
}

function _buildDiagnosticsText(node) {
    const health = healthChipInfo(node);
    const sys = node.system_load && typeof node.system_load === "object" ? node.system_load : null;
    const tl = node.traffic_limit && typeof node.traffic_limit === "object" ? node.traffic_limit : null;
    const cdnDomainsList = node.cdn_domains && typeof node.cdn_domains === "object" ? Object.entries(node.cdn_domains) : [];
    const cdnLine = cdnDomainsList.length
        ? `CDN Domains: ${cdnDomainsList.map(([k, v]) => `${k}=${v}`).join(", ")}`
        : (node.cdn_domain ? `CDN Domain: ${node.cdn_domain}` : null);
    const lines = [
        `Node #${node.id}: ${node.name} (${roleLabel(node.role)})`,
        `Health: ${health.text} (${node.health_state || "—"}) | Active: ${Boolean(node.is_active)} | Lifecycle: ${node.lifecycle_status || "ACTIVE"}`,
        node.disabled_reason ? `Disabled reason: ${node.disabled_reason}` : null,
        `API URL: ${node.api_url || "—"}`,
        node.domain ? `Domain: ${node.domain}` : null,
        cdnLine,
        `Fails: total=${node.consecutive_fails || 0}, ingress=${node.consecutive_ingress_fails || 0} (problem=${Boolean(node.ingress_problem)}), cdn=${node.consecutive_cdn_fails || 0} (problem=${Boolean(node.cdn_problem)})`,
        sys ? `Load: CPU=${sys.cpu_percent ?? "—"}%, RAM=${sys.ram_percent ?? "—"}%, Disk=${sys.disk_percent ?? node.disk_usage?.used_percent ?? "—"}%` : null,
        tl ? `Traffic: ${tl.pct ?? 0}% (exceeded=${Boolean(tl.exceeded)}, reset_day=${tl.reset_day ?? "—"})` : null,
        `Epochs: sync=${node.vless_synced_epoch ?? "—"} / core=${node.xray_instance_epoch ?? "—"}`,
        `Last check: ${node.last_successful_check || "—"}`,
        node.module_errors && Object.keys(node.module_errors).length ? `Module errors: ${JSON.stringify(node.module_errors)}` : null,
    ];
    return lines.filter(Boolean).join("\n");
}

function _openEditModal(node, onDone) {
    const box = document.createElement("form");
    box.className = "field";
    box.style.gap = "12px";
    box.innerHTML = `
        <div class="field">
            <label for="editNodeNameInput">Название</label>
            <input id="editNodeNameInput" name="name" class="input" required maxlength="255" value="${escapeHtml(node.name)}">
        </div>
        ${renderCountryFlagField({ value: node.country_flag || "" })}
        <div class="field">
            <label for="editNodeApiUrlInput">URL API ноды</label>
            <input id="editNodeApiUrlInput" name="api_url" class="input" type="url" required maxlength="500" value="${escapeHtml(node.api_url || "")}">
        </div>
        <div class="field">
            <label for="editNodeApiKeyInput">Новый API Токен (оставьте пустым, чтобы не менять)</label>
            <input id="editNodeApiKeyInput" name="api_key" class="input" type="password" maxlength="512" placeholder="••••••••">
        </div>
        <button type="submit" class="btn btn-primary">Сохранить</button>
    `;
    const close = openModal(`Редактировать #${node.id}`, box);
    wireCountryFlagFields(box);
    box.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(box);
        const btn = box.querySelector("button[type='submit']");
        await withButtonLoading(btn, "Сохранение...", async () => {
            try {
                const payload = {
                    name: String(fd.get("name") || "").trim(),
                };
                const newFlag = String(fd.get("country_flag") || "").trim();
                if (newFlag !== (node.country_flag || "")) {
                    payload.country_flag = newFlag;
                }
                const newUrl = String(fd.get("api_url") || "").trim();
                if (newUrl && newUrl !== node.api_url) {
                    payload.api_url = newUrl;
                }
                const newKey = String(fd.get("api_key") || "").trim();
                if (newKey) {
                    payload.api_key = newKey;
                }
                await api.patchNode(node.id, payload);
                showToast("Параметры обновлены");
                close();
                await onDone();
            } catch (err) {
                showToast(err.message || "Ошибка обновления", "error");
            }
        });
    });
}

function _renderBridgeHealthCard(node, allNodes = []) {
    if (node.role !== "ru_hub") return "";
    const bh = node.bridge_health_state && typeof node.bridge_health_state === "object" ? node.bridge_health_state : {};
    const blockedIds = new Set((node.blocked_eu_ids || []).map((x) => Number(x)).filter(Number.isFinite));
    const euMap = new Map();
    (allNodes || []).forEach((n) => {
        if (n && n.role === "eu_hub") euMap.set(Number(n.id), n);
    });
    const allIds = new Set([...euMap.keys(), ...blockedIds]);
    Object.keys(bh).forEach((k) => {
        const n = Number(k);
        if (Number.isFinite(n)) allIds.add(n);
    });
    if (!allIds.size) return "";

    const rowsHtml = [...allIds]
        .sort((a, b) => a - b)
        .map((eid) => {
            const entry = bh[String(eid)] && typeof bh[String(eid)] === "object" ? bh[String(eid)] : null;
            const euNode = euMap.get(eid);
            const euLabel = euNode ? `${euNode.name} (#${eid})` : `Внешний шлюз #${eid}`;
            const isBlocked = blockedIds.has(eid) || Boolean(entry && entry.blocked);
            const fails = Number(entry?.fails ?? entry?.consecutive_fails ?? 0);
            let badgeHtml = `<span class="chip chip-muted">Нет данных</span>`;
            if (isBlocked) {
                badgeHtml = `<span class="chip chip-err">ТСПУ-блокировка</span>`;
            } else if (fails > 0) {
                badgeHtml = `<span class="chip chip-warn">Сбои (${fails})</span>`;
            } else if (entry && entry.last_checked_at != null) {
                badgeHtml = `<span class="chip chip-ok">Доступен</span>`;
            }
            const errNote = entry && entry.last_error && (isBlocked || fails > 0)
                ? `<div class="meta-text" style="margin-top:2px;">${escapeHtml(String(entry.last_error).slice(0, 140))}</div>`
                : "";
            return `
                <div class="detail-item" style="grid-column: 1 / -1;">
                    <div class="row" style="gap:8px;">
                        <span>${escapeHtml(euLabel)}</span>
                        ${badgeHtml}
                    </div>
                    ${errNote}
                </div>
            `;
        })
        .join("");

    return `
        <section class="card">
            <h2 class="card-title">Маршруты к внешним шлюзам</h2>
            <div class="detail-grid">
                ${rowsHtml}
            </div>
        </section>
    `;
}

export function renderNodeDetailView(root, node, onBack, onReload, allNodes = []) {
    const health = healthChipInfo(node);
    const isDecommissioning = String(node.lifecycle_status || "").toUpperCase() === "DECOMMISSIONING";

    root.innerHTML = `
        <div class="detail-header-nav">
            <button id="detailBackBtn" type="button" class="btn btn-sm btn-ghost" style="white-space:nowrap; flex-shrink:0;">← Все серверы</button>
            <span class="detail-sticky-meta">${escapeHtml(node.name)}</span>
        </div>

        <section class="card">
            <div class="row">
                <div style="display:flex; align-items:center; gap:12px;">
                    ${renderFlagHtml(node.country_flag || node.location_code || "", 26)}
                    <div>
                        <h1 class="detail-hero-title">${escapeHtml(node.name)}</h1>
                        <div class="muted">Сервер #${node.id}</div>
                    </div>
                </div>
                <div class="row" style="gap:8px; flex-wrap:wrap; justify-content:flex-end;">
                    <span class="chip chip-muted">${escapeHtml(roleLabel(node.role))}</span>
                    <span class="chip ${health.cls}">${escapeHtml(health.text)}</span>
                </div>
            </div>

            <div class="actions-row" style="margin-top:8px; flex-wrap:wrap;">
                <button type="button" id="detailToggleBtn" class="btn btn-sm ${node.is_active ? "" : "btn-primary"}">
                    ${node.is_active ? "Выключить" : "Включить"}
                </button>
                <button type="button" id="detailRefreshBtn" class="btn btn-sm">Опросить ноду</button>
                ${node.role === "ru_hub" ? `<button type="button" id="detailLifecycleBtn" class="btn btn-sm" title="${isDecommissioning ? "Вернуть ноду в активное обслуживание" : "Перевести ноду в режим вывода из эксплуатации"}">
                    ${isDecommissioning ? "Вернуть в работу" : "Вывести из работы"}
                </button>` : ""}
                ${node.role === "exit_node" ? `<button type="button" id="detailSubLocBtn" class="btn btn-sm">Настроить в подписке</button>` : ""}
            </div>
        </section>

        ${_renderModuleErrors(node)}

        ${_renderTelemetryCard(node)}

        ${_renderBridgeHealthCard(node, allNodes)}

        <section class="card">
            <h2 class="card-title">Сетевые параметры</h2>
            <div class="detail-grid">
                <div class="detail-item">
                    <span class="detail-label">API URL</span>
                    <button type="button" class="mono-copy" data-copy="${escapeHtml(node.api_url)}">${escapeHtml(node.api_url)}</button>
                </div>
                ${node.domain ? `
                <div class="detail-item">
                    <span class="detail-label">Домен</span>
                    <button type="button" class="mono-copy" data-copy="${escapeHtml(node.domain)}">${escapeHtml(node.domain)}</button>
                </div>` : ""}
                <div class="detail-item">
                    <span class="detail-label">Роль</span>
                    <span>${escapeHtml(roleLabel(node.role))}</span>
                </div>
                ${node.cdn_domain ? `
                <div class="detail-item">
                    <span class="detail-label">CDN Домен</span>
                    <button type="button" class="mono-copy" data-copy="${escapeHtml(node.cdn_domain)}">${escapeHtml(node.cdn_domain)}</button>
                </div>` : ""}
                ${node.bridge_uuid ? `
                <div class="detail-item">
                    <span class="detail-label">Bridge UUID</span>
                    <button type="button" class="mono-copy" data-copy="${escapeHtml(node.bridge_uuid)}">${escapeHtml(node.bridge_uuid)}</button>
                </div>` : ""}
                ${node.role === "exit_node" ? `
                <div class="detail-item">
                    <span class="detail-label">Код локации</span>
                    <span class="mono">${escapeHtml(node.location_code || "—")}</span>
                </div>` : ""}
            </div>
        </section>

        ${_renderTopologyCard(node)}

        <section class="card">
            <h2 class="card-title">Модули</h2>
            <div class="filter-pills" style="margin-bottom:8px;">${_renderModules(node)}</div>
            ${node.inbounds && node.inbounds.length ? `
                <div class="detail-item" style="margin-top:8px;">
                    <span class="detail-label" title="Inbounds">Входящие (Inbounds)</span>
                    <span class="mono">${node.inbounds.map((ib) => escapeHtml(ib)).join(", ")}</span>
                </div>
            ` : ""}
        </section>

        <section class="card">
            <h2 class="card-title">Настройки</h2>
            <div class="actions-row">
                <button type="button" id="detailEditBtn" class="btn btn-sm">Изменить</button>
            </div>
        </section>

        <section class="danger-card">
            <h2 class="danger-card-title">Опасная зона</h2>
            <div class="meta-text">Удаление сервера уберёт его из базы и запустит пересчёт топологии.</div>
            <div class="actions-row">
                <button type="button" id="detailDeleteBtn" class="btn btn-sm btn-danger">Удалить сервер</button>
            </div>
        </section>
    `;

    root.querySelector("#detailBackBtn").addEventListener("click", onBack);

    root.querySelectorAll("[data-copy]").forEach((el) => {
        el.addEventListener("click", () => {
            copyText(el.dataset.copy);
        });
    });

    const copyDiagBtn = root.querySelector("#copyDiagBtn");
    if (copyDiagBtn) {
        copyDiagBtn.addEventListener("click", () => {
            copyText(_buildDiagnosticsText(node), "Диагностика скопирована");
        });
    }

    root.querySelector("#detailToggleBtn").addEventListener("click", (e) => {
        const b = e.currentTarget;
        const willDisable = Boolean(node.is_active);
        showConfirmModal({
            title: willDisable ? `Выключить сервер #${node.id}?` : `Включить сервер #${node.id}?`,
            message: willDisable
                ? `Сервер «${node.name}» перестанет принимать новых клиентов и выдаваться в подписках.`
                : `Сервер «${node.name}» будет включён и начнёт обслуживать трафик.`,
            confirmText: willDisable ? "Выключить" : "Включить",
            cancelText: "Отмена",
            isDestructive: willDisable,
            onConfirm: async () => {
                await withButtonLoading(b, "Применение...", async () => {
                    await api.toggleNode(node.id);
                    showToast(`Статус ноды #${node.id} изменён`);
                    await onReload();
                });
            },
        });
    });

    root.querySelector("#detailRefreshBtn").addEventListener("click", async (e) => {
        const b = e.currentTarget;
        await withButtonLoading(b, "Опрос...", async () => {
            try {
                await api.refreshNode(node.id);
                showToast(`Нода #${node.id} обновлена`);
                await onReload();
            } catch (err) {
                showToast(err.message || "Ошибка опроса", "error");
            }
        });
    });

    const lifecycleBtn = root.querySelector("#detailLifecycleBtn");
    if (lifecycleBtn) {
        lifecycleBtn.addEventListener("click", (e) => {
            const b = e.currentTarget;
            const targetStatus = isDecommissioning ? "active" : "decommissioning";
            showConfirmModal({
                title: isDecommissioning ? `Вернуть ноду #${node.id} в работу?` : `Вывести ноду #${node.id} из работы?`,
                message: isDecommissioning
                    ? `Нода «${node.name}» снова начнёт принимать новых клиентов и трафик.`
                    : `Нода «${node.name}» перейдёт в режим вывода из эксплуатации и перестанет выдаваться новым клиентам.`,
                confirmText: isDecommissioning ? "Вернуть в работу" : "Вывести из работы",
                cancelText: "Отмена",
                isDestructive: !isDecommissioning,
                onConfirm: async () => {
                    await withButtonLoading(b, "Применение...", async () => {
                        await api.setNodeLifecycle(node.id, targetStatus);
                        showToast(
                            isDecommissioning
                                ? `Нода #${node.id} возвращена в работу`
                                : `Нода #${node.id} переведена в режим вывода из работы`
                        );
                        await onReload();
                    });
                },
            });
        });
    }

    const subLocBtn = root.querySelector("#detailSubLocBtn");
    if (subLocBtn) {
        subLocBtn.addEventListener("click", () => {
            openLocationSubModal(node, onReload);
        });
    }

    root.querySelector("#detailEditBtn").addEventListener("click", () => {
        _openEditModal(node, onReload);
    });

    root.querySelector("#detailDeleteBtn").addEventListener("click", () => {
        showConfirmModal({
            title: `Удалить сервер #${node.id}?`,
            message: `Сервер «${node.name}» будет удалён из базы.`,
            confirmText: "Удалить",
            cancelText: "Отмена",
            isDestructive: true,
            onConfirm: async () => {
                try {
                    await api.deleteNode(node.id);
                    showToast(`Сервер #${node.id} удалён`);
                    const ok = await onReload({ skipRender: true, removeNodeId: node.id });
                    if (ok !== false) onBack();
                } catch (err) {
                    if (err && err.status === 409) {
                        showConfirmModal({
                            title: "Принудительное удаление",
                            message: `${err.message}\n\nВсё равно принудительно удалить сервер?`,
                            confirmText: "Удалить принудительно",
                            cancelText: "Отмена",
                            isDestructive: true,
                            restoreFocusSelector: "#detailDeleteBtn",
                            onConfirm: async () => {
                                await api.deleteNode(node.id, true);
                                showToast(`Сервер #${node.id} принудительно удалён`);
                                const ok = await onReload({ skipRender: true, removeNodeId: node.id });
                                if (ok !== false) onBack();
                            },
                        });
                        return;
                    }
                    throw err;
                }
            },
        });
    });
}
