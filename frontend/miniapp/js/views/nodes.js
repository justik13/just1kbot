import { api } from "../api.js";
import { escapeHtml, healthChipInfo, openModal, roleLabel, showToast } from "../ui.js";

let currentFilter = "all";

function _renderModules(node) {
    const mods = node.modules || {};
    const names = Object.keys(mods);
    if (!names.length) {
        return `<span class="chip chip-muted">Модули не обнаружены</span>`;
    }
    return names
        .map((name) => {
            const info = mods[name];
            const enabled = Boolean(info && typeof info === "object" ? info.enabled : info);
            return `<span class="chip ${enabled ? "chip-ok" : "chip-muted"}">${escapeHtml(name)}</span>`;
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
    return `<div class="meta-list" style="color:var(--accent-red);">${lines}</div>`;
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
            <input name="country_flag" class="input" maxlength="10" value="${escapeHtml(node.country_flag || "")}" placeholder="🇷🇺 / 🇳🇱">
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

export function renderNodesView(root, state, onReload) {
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
            <form id="addNodeForm" class="field" style="gap:9px;">
                <div class="field">
                    <label>URL API ноды</label>
                    <input id="addApiUrl" class="input" type="url" required placeholder="https://1.2.3.4:8444">
                </div>
                <div class="field">
                    <label>API Токен</label>
                    <input id="addApiKey" class="input" type="password" required placeholder="Bearer / X-API-Key токен">
                </div>
                <div class="grid-2">
                    <div class="field">
                        <label>Название (опционально)</label>
                        <input id="addName" class="input" type="text" maxlength="255" placeholder="Авто по домену">
                    </div>
                    <div class="field">
                        <label>Флаг (опционально)</label>
                        <input id="addFlag" class="input" type="text" maxlength="10" placeholder="🇷🇺 / 🇳🇱">
                    </div>
                </div>
                <label class="checkbox-row">
                    <input id="addBootstrapTls" type="checkbox" checked>
                    <span>Разрешить self-signed TLS (свежая нода)</span>
                </label>
                <button id="addSubmitBtn" type="submit" class="btn btn-primary">Проверить и добавить</button>
            </form>
        </section>

        <section class="card">
            <div class="row">
                <div class="card-title">Список нод</div>
                <button id="reloadNodesBtn" type="button" class="btn btn-sm">Обновить список</button>
            </div>
            <div class="filter-pills">
                ${["all", "ru_hub", "eu_hub", "exit_node"]
                    .map((key) => {
                        const label = key === "all" ? "Все" : roleLabel(key);
                        const active = currentFilter === key ? "active" : "";
                        return `<button type="button" class="pill-btn ${active}" data-filter="${key}">${label} (${counts[key] ?? 0})</button>`;
                    })
                    .join("")}
            </div>
            <div id="nodesListContainer" class="field" style="gap:10px;"></div>
        </section>
    `;

    root.querySelector("#reloadNodesBtn").addEventListener("click", onReload);
    root.querySelectorAll("[data-filter]").forEach((btn) => {
        btn.addEventListener("click", () => {
            currentFilter = btn.dataset.filter;
            renderNodesView(root, state, onReload);
        });
    });

    const addForm = root.querySelector("#addNodeForm");
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
                allow_bootstrap_tls: root.querySelector("#addBootstrapTls").checked,
            });
            addForm.reset();
            root.querySelector("#addBootstrapTls").checked = true;
            showToast("Нода добавлена и выключена. Включите её кнопкой «Включить».");
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
        card.className = "node-card";
        card.innerHTML = `
            <div class="row">
                <div style="font-weight:600;">${escapeHtml(node.country_flag || "🌐")} ${escapeHtml(node.name)} <span class="muted">#${node.id}</span></div>
                <div class="row" style="gap:5px;">
                    <span class="chip chip-info">${escapeHtml(roleLabel(node.role))}</span>
                    <span class="chip ${health.cls}">${escapeHtml(health.text)}</span>
                </div>
            </div>
            <div class="meta-list">
                <div class="mono">API: ${escapeHtml(node.api_url)}</div>
                ${node.domain ? `<div class="mono">Domain: ${escapeHtml(node.domain)}</div>` : ""}
                ${node.cdn_domain ? `<div class="mono">CDN: ${escapeHtml(node.cdn_domain)}</div>` : ""}
                ${node.bridge_uuid ? `<div class="mono">Bridge UUID: ${escapeHtml(node.bridge_uuid)}</div>` : ""}
                <div>Bootstrap TLS: ${node.tls_bootstrap ? "да" : "нет"}</div>
            </div>
            <div class="filter-pills">${_renderModules(node)}</div>
            ${_renderModuleErrors(node)}
            <div class="actions-row">
                <button type="button" class="btn btn-sm" data-act="refresh">Опросить</button>
                <button type="button" class="btn btn-sm" data-act="toggle">${node.is_active ? "Выключить" : "Включить"}</button>
                <button type="button" class="btn btn-sm" data-act="edit">Изменить</button>
                <button type="button" class="btn btn-sm" data-act="cert">TLS</button>
                <button type="button" class="btn btn-sm btn-danger" data-act="delete">Удалить</button>
            </div>
        `;

        card.querySelector('[data-act="refresh"]').addEventListener("click", async (ev) => {
            const b = ev.currentTarget;
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

        card.querySelector('[data-act="toggle"]').addEventListener("click", async (ev) => {
            const b = ev.currentTarget;
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

        card.querySelector('[data-act="edit"]').addEventListener("click", () => {
            _openEditModal(node, onReload);
        });

        card.querySelector('[data-act="cert"]').addEventListener("click", () => {
            _openCertModal(node, onReload);
        });

        card.querySelector('[data-act="delete"]').addEventListener("click", async (ev) => {
            if (!window.confirm(`Удалить ноду #${node.id} (${node.name})?`)) return;
            const b = ev.currentTarget;
            b.disabled = true;
            try {
                await api.deleteNode(node.id);
                showToast(`Нода #${node.id} удалена`);
                await onReload();
            } catch (err) {
                showToast(err.message || "Ошибка удаления");
            } finally {
                b.disabled = false;
            }
        });

        listEl.appendChild(card);
    });
}
