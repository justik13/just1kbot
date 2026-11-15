import { api } from "../api.js";
import { escapeHtml, healthChipInfo, openModal, roleLabel, showToast } from "../ui.js";

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
    return `<div class="card" style="border-color: rgba(248, 113, 113, 0.4);"><div class="card-title" style="color:var(--accent-red);">Ошибки модулей</div><div class="meta-list" style="color:var(--accent-red);">${lines}</div></div>`;
}

function _openEditModal(node, onDone) {
    const box = document.createElement("form");
    box.className = "field";
    box.style.gap = "10px";
    box.innerHTML = `
        <div class="field">
            <label>Название</label>
            <input name="name" class="input" required maxlength="255" value="${escapeHtml(node.name)}">
        </div>
        <div class="field">
            <label>Флаг</label>
            <input name="country_flag" class="input" maxlength="10" value="${escapeHtml(node.country_flag || "")}">
        </div>
        <div class="field">
            <label>URL API ноды</label>
            <input name="api_url" class="input" type="url" required maxlength="255" value="${escapeHtml(node.api_url || "")}">
        </div>
        <div class="field">
            <label>Новый API Токен (оставьте пустым, чтобы не менять)</label>
            <input name="api_key" class="input" type="password" maxlength="255" placeholder="••••••••">
        </div>
        <label class="checkbox-row">
            <input name="allow_bootstrap_tls" type="checkbox" ${node.tls_bootstrap ? "checked" : ""}>
            <span>Bootstrap self-signed TLS</span>
        </label>
        <button type="submit" class="btn btn-primary">Сохранить</button>
    `;
    const close = openModal(`Редактировать #${node.id}`, box);
    box.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(box);
        const btn = box.querySelector("button[type='submit']");
        btn.disabled = true;
        try {
            const payload = {
                name: String(fd.get("name") || "").trim(),
                country_flag: String(fd.get("country_flag") || "").trim(),
                allow_bootstrap_tls: Boolean(fd.get("allow_bootstrap_tls")),
            };
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
            showToast(err.message || "Ошибка обновления");
        } finally {
            btn.disabled = false;
        }
    });
}

function _openNodeIncyModal(node, onDone) {
    const relayCodes = [];
    const seen = new Set();
    const addCode = (c) => {
        const clean = String(c || "").trim();
        if (clean && !seen.has(clean)) {
            seen.add(clean);
            relayCodes.push(clean);
        }
    };
    (node.relays || []).forEach((r) => addCode(r && (r.code || r.name)));
    (node.xhttp_locations || []).forEach((loc) => addCode(loc && (loc.code || loc.name || loc.location)));

    const relayNames = node.relay_names || {};
    const relayBadges = node.relay_badges || {};
    const relayHiddenSet = new Set(Array.isArray(node.relay_hidden) ? node.relay_hidden : []);

    const relaysHtml = relayCodes.length
        ? `<div class="field" style="margin-top:6px;">
            <label>Локации / Relay (имя, бейдж, скрытие)</label>
            ${relayCodes
                .map(
                    (code) => `
                <div class="card" style="padding:8px; margin:0; display:flex; flex-direction:column; gap:6px;">
                    <div style="font-size:12px; font-weight:600;">${escapeHtml(code)}</div>
                    <div class="grid-2">
                        <input data-relay-name="${escapeHtml(code)}" class="input" placeholder="Имя в списке" value="${escapeHtml(relayNames[code] || "")}">
                        <input data-relay-badge="${escapeHtml(code)}" class="input" placeholder="Бейдж (или none)" value="${escapeHtml(relayBadges[code] || "")}">
                    </div>
                    <label class="checkbox-row">
                        <input data-relay-hidden="${escapeHtml(code)}" type="checkbox" ${relayHiddenSet.has(code) ? "checked" : ""}>
                        <span>Скрыть локацию</span>
                    </label>
                </div>`
                )
                .join("")}
        </div>`
        : "";

    const box = document.createElement("form");
    box.className = "field";
    box.style.gap = "10px";
    box.innerHTML = `
        <div class="field">
            <label>Имя Origin в INCY / Happ (origin_tag)</label>
            <input name="origin_tag" class="input" maxlength="100" value="${escapeHtml(node.origin_tag || "")}" placeholder="По умолчанию — имя сервера">
        </div>
        <div class="field">
            <label>Бейдж Origin (origin_badge, «none» = убрать)</label>
            <input name="origin_badge" class="input" maxlength="120" value="${escapeHtml(node.origin_badge || "")}">
        </div>
        <label class="checkbox-row">
            <input name="origin_hidden" type="checkbox" ${node.origin_hidden ? "checked" : ""}>
            <span>Скрыть прямой Origin из подписки</span>
        </label>
        <div class="field">
            <label>Ссылка поддержки для ноды (Support-Url)</label>
            <input name="support_url" class="input" maxlength="500" value="${escapeHtml(node.support_url || "")}" placeholder="https://t.me/...">
        </div>
        <div class="field">
            <label>Ссылка канала для ноды (Channel-Url)</label>
            <input name="channel_url" class="input" maxlength="500" value="${escapeHtml(node.channel_url || "")}" placeholder="https://t.me/...">
        </div>
        ${relaysHtml}
        <button type="submit" class="btn btn-primary">Сохранить оформление</button>
    `;

    const close = openModal(`INCY / Happ — #${node.id}`, box);
    box.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(box);
        const rNames = {};
        const rBadges = {};
        const rHidden = [];
        relayCodes.forEach((code) => {
            const nVal = box.querySelector(`[data-relay-name="${CSS.escape(code)}"]`)?.value.trim() || "";
            const bVal = box.querySelector(`[data-relay-badge="${CSS.escape(code)}"]`)?.value.trim() || "";
            const hVal = Boolean(box.querySelector(`[data-relay-hidden="${CSS.escape(code)}"]`)?.checked);
            if (nVal) rNames[code] = nVal;
            if (bVal) rBadges[code] = bVal;
            if (hVal) rHidden.push(code);
        });

        const btn = box.querySelector("button[type='submit']");
        btn.disabled = true;
        try {
            await api.patchNode(node.id, {
                origin_tag: String(fd.get("origin_tag") || "").trim(),
                origin_badge: String(fd.get("origin_badge") || "").trim(),
                origin_hidden: Boolean(fd.get("origin_hidden")),
                support_url: String(fd.get("support_url") || "").trim(),
                channel_url: String(fd.get("channel_url") || "").trim(),
                relay_names: rNames,
                relay_badges: rBadges,
                relay_hidden: rHidden,
            });
            showToast("Оформление ноды сохранено");
            close();
            await onDone();
        } catch (err) {
            showToast(err.message || "Ошибка сохранения");
        } finally {
            btn.disabled = false;
        }
    });
}

function _openMigrateModal(node, allNodes, onDone) {
    const targets = (allNodes || []).filter(
        (n) =>
            n.id !== node.id &&
            n.is_active &&
            Array.isArray(n.capabilities) &&
            n.capabilities.includes("xray_origin")
    );
    if (!targets.length) {
        showToast("Нет активных целевых нод для переноса");
        return;
    }
    const box = document.createElement("form");
    box.className = "field";
    box.style.gap = "10px";
    box.innerHTML = `
        <div class="muted" style="font-size:12px;">
            Перенос всех активных подписок (${node.wi_subscriptions_count || 0} шт.) с ноды <b>#${node.id} ${escapeHtml(node.name)}</b>. Исходная нода будет переведена в режим вывода (decommissioning).
        </div>
        <div class="field">
            <label>Целевая нода</label>
            <select name="target_server_id" class="select" required>
                ${targets
                    .map(
                        (t) =>
                            `<option value="${t.id}">#${t.id} ${escapeHtml(t.country_flag || "")} ${escapeHtml(t.name)}</option>`
                    )
                    .join("")}
            </select>
        </div>
        <button type="submit" class="btn btn-primary">Перенести подписки</button>
    `;
    const close = openModal(`Миграция с #${node.id}`, box);
    box.addEventListener("submit", async (e) => {
        e.preventDefault();
        const targetId = Number(new FormData(box).get("target_server_id"));
        const btn = box.querySelector("button[type='submit']");
        btn.disabled = true;
        try {
            const res = await api.migrateNode(node.id, targetId);
            showToast(`Перенесено подписок: ${res.migrated_count ?? 0}`);
            close();
            await onDone();
        } catch (err) {
            showToast(err.message || "Ошибка миграции");
        } finally {
            btn.disabled = false;
        }
    });
}

function _openCertModal(node, onDone) {
    const box = document.createElement("form");
    box.className = "field";
    box.style.gap = "10px";
    box.innerHTML = `
        <div class="field">
            <label>Домен сертификата</label>
            <input name="domain" class="input" required value="${escapeHtml(node.domain || "")}" placeholder="edge.example.com">
        </div>
        <div class="field">
            <label>Fullchain PEM</label>
            <textarea name="fullchain_pem" class="textarea" required placeholder="-----BEGIN CERTIFICATE-----"></textarea>
        </div>
        <div class="field">
            <label>Private Key PEM</label>
            <textarea name="privkey_pem" class="textarea" required placeholder="-----BEGIN PRIVATE KEY-----"></textarea>
        </div>
        <button type="submit" class="btn btn-primary">Отправить на ноду</button>
    `;
    const close = openModal(`TLS сертификат (#${node.id})`, box);
    box.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(box);
        const btn = box.querySelector("button[type='submit']");
        btn.disabled = true;
        try {
            await api.deployNodeCert(node.id, {
                domain: String(fd.get("domain") || "").trim(),
                fullchain_pem: String(fd.get("fullchain_pem") || ""),
                privkey_pem: String(fd.get("privkey_pem") || ""),
            });
            showToast("Сертификат отправлен на ноду");
            close();
            await onDone();
        } catch (err) {
            showToast(err.message || "Ошибка отправки сертификата");
        } finally {
            btn.disabled = false;
        }
    });
}

function _openLinksModal(node, onDone) {
    const box = document.createElement("form");
    box.className = "field";
    box.style.gap = "10px";
    const initialJson = JSON.stringify(node.links || {}, null, 2);
    box.innerHTML = `
        <div class="field">
            <label>Конфигурация связей (standard / bi / exit_nodes / routes)</label>
            <textarea name="links_json" class="textarea" required style="min-height:200px;">${escapeHtml(initialJson)}</textarea>
        </div>
        <button type="submit" class="btn btn-primary">Сохранить связи на ноде</button>
    `;
    const close = openModal(`Связи контуров (#${node.id})`, box);
    box.addEventListener("submit", async (e) => {
        e.preventDefault();
        const raw = String(new FormData(box).get("links_json") || "").trim();
        let parsed;
        try {
            parsed = JSON.parse(raw);
        } catch {
            showToast("Некорректный JSON");
            return;
        }
        const btn = box.querySelector("button[type='submit']");
        btn.disabled = true;
        try {
            await api.updateNodeLinks(node.id, parsed);
            showToast("Связи обновлены");
            close();
            await onDone();
        } catch (err) {
            showToast(err.message || "Ошибка сохранения связей");
        } finally {
            btn.disabled = false;
        }
    });
}

export function renderNodeDetailView(root, node, onBack, onReload) {
    const health = healthChipInfo(node);

    root.innerHTML = `
        <div class="detail-header-nav">
            <button id="detailBackBtn" type="button" class="btn btn-sm btn-ghost">← Все серверы</button>
        </div>

        <section class="card">
            <div class="row">
                <div style="display:flex; align-items:center; gap:10px;">
                    <span style="font-size:24px;">${escapeHtml(node.country_flag || "🌐")}</span>
                    <div>
                        <div style="font-size:16px; font-weight:700;">${escapeHtml(node.name)}</div>
                        <div class="muted">Сервер #${node.id}</div>
                    </div>
                </div>
                <div class="row" style="gap:6px;">
                    <span class="chip chip-info">${escapeHtml(roleLabel(node.role))}</span>
                    <span class="chip ${health.cls}">${escapeHtml(health.text)}</span>
                </div>
            </div>

            <div class="actions-row" style="margin-top:6px;">
                <button type="button" id="detailToggleBtn" class="btn btn-sm ${node.is_active ? "" : "btn-primary"}">
                    ${node.is_active ? "Выключить" : "Включить"}
                </button>
                <button type="button" id="detailRefreshBtn" class="btn btn-sm">Опросить ноду</button>
            </div>
        </section>

        <section class="card">
            <div class="card-title">Сетевые параметры</div>
            <div class="detail-grid">
                <div class="detail-item">
                    <span class="detail-label">API URL</span>
                    <span class="mono">${escapeHtml(node.api_url)}</span>
                </div>
                ${node.domain ? `
                <div class="detail-item">
                    <span class="detail-label">Домен</span>
                    <span class="mono">${escapeHtml(node.domain)}</span>
                </div>` : ""}
                <div class="detail-item">
                    <span class="detail-label">Роль</span>
                    <span>${escapeHtml(roleLabel(node.role))}</span>
                </div>
                <div class="detail-item">
                    <span class="detail-label">Протокол</span>
                    <span>${escapeHtml(node.protocol || "—")}</span>
                </div>
                ${node.cdn_domain ? `
                <div class="detail-item">
                    <span class="detail-label">CDN Домен</span>
                    <span class="mono">${escapeHtml(node.cdn_domain)}</span>
                </div>` : ""}
                ${node.bridge_uuid ? `
                <div class="detail-item">
                    <span class="detail-label">Bridge UUID</span>
                    <span class="mono">${escapeHtml(node.bridge_uuid)}</span>
                </div>` : ""}
                <div class="detail-item">
                    <span class="detail-label">Bootstrap TLS</span>
                    <span>${node.tls_bootstrap ? "Да (self-signed разрешен)" : "Нет (строгий TLS)"}</span>
                </div>
                <div class="detail-item">
                    <span class="detail-label">Подписок доп. опции</span>
                    <span>${Number(node.wi_subscriptions_count || 0)}</span>
                </div>
                ${node.traffic_limit && typeof node.traffic_limit === "object" ? `
                <div class="detail-item">
                    <span class="detail-label">Трафик за месяц</span>
                    <span>${((Number(node.traffic_limit.monthly_bytes || node.traffic_limit.used_bytes || 0)) / (1024 ** 3)).toFixed(2)} ГБ${node.traffic_limit.limit_bytes ? ` / ${(Number(node.traffic_limit.limit_bytes) / (1024 ** 3)).toFixed(0)} ГБ` : ""}</span>
                </div>` : ""}
                <div class="detail-item">
                    <span class="detail-label">Последняя проверка</span>
                    <span class="muted">${node.last_successful_check ? new Date(node.last_successful_check).toLocaleString("ru-RU") : "—"}</span>
                </div>
            </div>
        </section>

        <section class="card">
            <div class="card-title">Модули и привязки контуров</div>
            <div class="filter-pills" style="margin-bottom:6px;">${_renderModules(node)}</div>
            ${node.inbounds && node.inbounds.length ? `
                <div class="detail-item" style="margin-top:6px;">
                    <span class="detail-label">Inbounds:</span>
                    <span class="mono">${node.inbounds.map((ib) => escapeHtml(ib)).join(", ")}</span>
                </div>
            ` : ""}
            <div class="detail-item" style="margin-top:6px;">
                <span class="detail-label">Привязка Standard / Доп. опция</span>
                <button type="button" id="detailLinksBtn" class="btn btn-sm">Настроить связи (JSON)</button>
            </div>
        </section>

        ${_renderModuleErrors(node)}

        <section class="card">
            <div class="card-title">Настройки и обслуживание</div>
            <div class="actions-row">
                <button type="button" id="detailEditBtn" class="btn btn-sm">Изменить</button>
                <button type="button" id="detailIncyBtn" class="btn btn-sm">Оформление INCY</button>
                <button type="button" id="detailCertBtn" class="btn btn-sm">TLS сертификат</button>
                ${(node.wi_subscriptions_count > 0 || (Array.isArray(node.capabilities) && node.capabilities.includes("xray_origin"))) ? `<button type="button" id="detailMigrateBtn" class="btn btn-sm">Миграция подписок</button>` : ""}
                <button type="button" id="detailDeleteBtn" class="btn btn-sm btn-danger">Удалить сервер</button>
            </div>
        </section>
    `;

    root.querySelector("#detailBackBtn").addEventListener("click", onBack);

    root.querySelector("#detailToggleBtn").addEventListener("click", async (e) => {
        const b = e.currentTarget;
        b.disabled = true;
        try {
            await api.toggleNode(node.id);
            showToast(`Статус ноды #${node.id} изменён`);
            await onReload();
        } catch (err) {
            showToast(err.message || "Ошибка переключения");
        } finally {
            b.disabled = false;
        }
    });

    root.querySelector("#detailRefreshBtn").addEventListener("click", async (e) => {
        const b = e.currentTarget;
        b.disabled = true;
        try {
            await api.refreshNode(node.id);
            showToast(`Нода #${node.id} обновлена`);
            await onReload();
        } catch (err) {
            showToast(err.message || "Ошибка опроса");
        } finally {
            b.disabled = false;
        }
    });

    root.querySelector("#detailEditBtn").addEventListener("click", () => {
        _openEditModal(node, onReload);
    });

    root.querySelector("#detailIncyBtn").addEventListener("click", () => {
        _openNodeIncyModal(node, onReload);
    });

    const migrateBtn = root.querySelector("#detailMigrateBtn");
    if (migrateBtn) {
        migrateBtn.addEventListener("click", () => {
            _openMigrateModal(node, state.nodes || [], onReload);
        });
    }

    root.querySelector("#detailCertBtn").addEventListener("click", () => {
        _openCertModal(node, onReload);
    });

    root.querySelector("#detailLinksBtn").addEventListener("click", () => {
        _openLinksModal(node, onReload);
    });

    root.querySelector("#detailDeleteBtn").addEventListener("click", async () => {
        if (!confirm(`Удалить сервер "${node.name}" (#${node.id})?`)) return;
        try {
            await api.deleteNode(node.id);
            showToast(`Сервер #${node.id} удалён`);
            onBack();
            await onReload();
        } catch (err) {
            if (err && err.status === 409) {
                if (confirm(`${err.message}\n\nВсё равно принудительно удалить сервер?`)) {
                    try {
                        await api.deleteNode(node.id, true);
                        showToast(`Сервер #${node.id} принудительно удалён`);
                        onBack();
                        await onReload();
                    } catch (err2) {
                        showToast(err2.message || "Ошибка удаления");
                    }
                }
                return;
            }
            showToast(err.message || "Ошибка удаления");
        }
    });
}
