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
            await api.patchNode(node.id, {
                name: String(fd.get("name") || "").trim(),
                country_flag: String(fd.get("country_flag") || "").trim(),
                allow_bootstrap_tls: Boolean(fd.get("allow_bootstrap_tls")),
            });
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
                    <span class="detail-label">Последняя проверка</span>
                    <span class="muted">${node.last_successful_check ? new Date(node.last_successful_check).toLocaleString("ru-RU") : "—"}</span>
                </div>
            </div>
        </section>

        <section class="card">
            <div class="card-title">Модули и шлюзы</div>
            <div class="filter-pills" style="margin-bottom:6px;">${_renderModules(node)}</div>
            ${node.inbounds && node.inbounds.length ? `
                <div class="detail-item" style="margin-top:6px;">
                    <span class="detail-label">Inbounds:</span>
                    <span class="mono">${node.inbounds.map((ib) => escapeHtml(ib)).join(", ")}</span>
                </div>
            ` : ""}
        </section>

        ${_renderModuleErrors(node)}

        <section class="card">
            <div class="card-title">Настройки и обслуживание</div>
            <div class="actions-row">
                <button type="button" id="detailEditBtn" class="btn btn-sm">Изменить</button>
                <button type="button" id="detailCertBtn" class="btn btn-sm">TLS сертификат</button>
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

    root.querySelector("#detailCertBtn").addEventListener("click", () => {
        _openCertModal(node, onReload);
    });

    root.querySelector("#detailDeleteBtn").addEventListener("click", async () => {
        if (!confirm(`Удалить сервер "${node.name}" (#${node.id})?`)) return;
        try {
            await api.deleteNode(node.id);
            showToast(`Сервер #${node.id} удалён`);
            onBack();
            await onReload();
        } catch (err) {
            showToast(err.message || "Ошибка удаления");
        }
    });
}
