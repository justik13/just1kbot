import { api } from "../api.js";
import { escapeHtml, healthChipInfo, roleLabel, showToast } from "../ui.js";

let currentFilter = "all";

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
                        <input id="addName" class="input" type="text" maxlength="255">
                    </div>
                    <div class="field">
                        <label>Флаг (опционально)</label>
                        <input id="addFlag" class="input" type="text" maxlength="10">
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
                    <span style="font-size:18px;">${escapeHtml(node.country_flag || "🌐")}</span>
                    <span style="font-weight:600; font-size:14px;">${escapeHtml(node.name)}</span>
                    <span class="muted" style="font-size:12px;">#${node.id}</span>
                </div>
                <div class="row" style="gap:6px; align-items:center;">
                    <span class="chip chip-info">${escapeHtml(roleLabel(node.role))}</span>
                    <span class="chip ${health.cls}">${escapeHtml(health.text)}</span>
                    <span class="node-chevron">›</span>
                </div>
            </div>
            <div class="row muted" style="font-size:12px; margin-top:3px;">
                <span class="mono">${escapeHtml(node.domain || node.api_url)}</span>
                <span>${node.is_active ? "● Включен" : "○ Выключен"}</span>
            </div>
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
