import { api } from "../api.js";
import { escapeHtml, roleLabel, showToast } from "../ui.js";

let selectedNodeId = null;

function _extractHost(apiUrl) {
    try {
        return new URL(apiUrl).hostname;
    } catch {
        return "";
    }
}

function _buildRoleTemplate(node) {
    const cur = node.links && Object.keys(node.links).length ? node.links : null;
    if (cur) return cur;

    if (node.role === "ru_hub") {
        return {
            standard: { bridges: [], sni_routes: [], locations: [] },
            bi: { bridges: [], sni_routes: [], locations: [] },
        };
    }
    if (node.role === "eu_hub") {
        return {
            allowed_ru_ips: [],
            incoming_ru_hubs: [],
            exit_nodes: [],
            routes: [],
        };
    }
    if (node.role === "exit_node") {
        return {
            allowed_hub_ips: [],
            incoming_hubs: [],
        };
    }
    return {};
}

function _injectPeerIntoJson(rawText, targetNode, peerNode, channelKey) {
    let data = {};
    try {
        data = rawText.trim() ? JSON.parse(rawText) : _buildRoleTemplate(targetNode);
    } catch {
        data = _buildRoleTemplate(targetNode);
    }
    const peerHost = _extractHost(peerNode.api_url);
    const code = `node_${peerNode.id}`;

    if (targetNode.role === "ru_hub" && peerNode.role === "eu_hub") {
        const sectionKey = channelKey === "bi" ? "bi" : "standard";
        if (!data[sectionKey]) data[sectionKey] = { bridges: [], sni_routes: [], locations: [] };
        if (!Array.isArray(data[sectionKey].bridges)) data[sectionKey].bridges = [];
        data[sectionKey].bridges.push({
            code,
            ip: peerHost,
            port: 443,
            sni: peerNode.domain || "",
            uuid: peerNode.bridge_uuid || "",
            is_default: data[sectionKey].bridges.length === 0,
        });
    } else if (targetNode.role === "eu_hub" && peerNode.role === "exit_node") {
        if (!Array.isArray(data.exit_nodes)) data.exit_nodes = [];
        data.exit_nodes.push({
            code,
            ip: peerHost,
            port: 443,
            sni: peerNode.domain || "",
            uuid: peerNode.bridge_uuid || "",
            is_default: data.exit_nodes.length === 0,
        });
    } else if (targetNode.role === "eu_hub" && peerNode.role === "ru_hub") {
        if (!Array.isArray(data.allowed_ru_ips)) data.allowed_ru_ips = [];
        if (peerHost && !data.allowed_ru_ips.includes(peerHost)) data.allowed_ru_ips.push(peerHost);
    } else if (targetNode.role === "exit_node") {
        if (!Array.isArray(data.allowed_hub_ips)) data.allowed_hub_ips = [];
        if (peerHost && !data.allowed_hub_ips.includes(peerHost)) data.allowed_hub_ips.push(peerHost);
    }
    return JSON.stringify(data, null, 2);
}

export function renderTopologyView(root, state, onReload) {
    const nodes = state.nodes || [];
    if (!nodes.length) {
        root.innerHTML = `<section class="card card-center"><div class="muted">Сначала добавьте хотя бы одну ноду.</div></section>`;
        return;
    }

    if (!selectedNodeId || !nodes.some((n) => n.id === selectedNodeId)) {
        selectedNodeId = nodes[0].id;
    }
    const target = nodes.find((n) => n.id === selectedNodeId) || nodes[0];
    const peers = nodes.filter((n) => n.id !== target.id);
    const initialJson = JSON.stringify(_buildRoleTemplate(target), null, 2);

    root.innerHTML = `
        <section class="card">
            <div class="card-title">Управление каскадом (PUT /v1/node/links)</div>
            <div class="field">
                <label>Целевая нода</label>
                <select id="topoTargetSelect" class="select">
                    ${nodes
                        .map(
                            (n) =>
                                `<option value="${n.id}" ${n.id === target.id ? "selected" : ""}>#${n.id} ${escapeHtml(n.name)} (${escapeHtml(roleLabel(n.role))})</option>`
                        )
                        .join("")}
                </select>
            </div>

            ${
                peers.length
                    ? `
                <div class="grid-2">
                    <div class="field">
                        <label>Быстро подставить связь</label>
                        <select id="topoPeerSelect" class="select">
                            ${peers
                                .map(
                                    (p) =>
                                        `<option value="${p.id}">#${p.id} ${escapeHtml(p.name)} (${escapeHtml(roleLabel(p.role))})</option>`
                                )
                                .join("")}
                        </select>
                    </div>
                    <div class="field">
                        <label>Контур (для RU Hub)</label>
                        <select id="topoChannelSelect" class="select" ${target.role === "ru_hub" ? "" : "disabled"}>
                            <option value="standard">Стандарт (TLS)</option>
                            <option value="bi">Расширенный (XHTTP + CDN)</option>
                        </select>
                    </div>
                </div>
                <button id="topoInjectBtn" type="button" class="btn btn-sm">Добавить выбранную ноду в конфиг</button>
            `
                    : ""
            }

            <div class="field">
                <label>JSON связей (links.json)</label>
                <textarea id="topoJsonInput" class="textarea">${escapeHtml(initialJson)}</textarea>
            </div>

            <div class="row">
                <button id="topoResetBtn" type="button" class="btn btn-sm">Сбросить шаблон роли</button>
                <button id="topoSaveBtn" type="button" class="btn btn-primary">Применить на ноде</button>
            </div>
        </section>
    `;

    root.querySelector("#topoTargetSelect").addEventListener("change", (e) => {
        selectedNodeId = Number(e.target.value);
        renderTopologyView(root, state, onReload);
    });

    const jsonBox = root.querySelector("#topoJsonInput");
    const injectBtn = root.querySelector("#topoInjectBtn");
    if (injectBtn) {
        injectBtn.addEventListener("click", () => {
            const peerId = Number(root.querySelector("#topoPeerSelect").value);
            const peer = nodes.find((n) => n.id === peerId);
            if (!peer) return;
            const channel = root.querySelector("#topoChannelSelect")?.value || "standard";
            jsonBox.value = _injectPeerIntoJson(jsonBox.value, target, peer, channel);
            showToast(`Добавлено в шаблон (${roleLabel(peer.role)})`);
        });
    }

    root.querySelector("#topoResetBtn").addEventListener("click", () => {
        jsonBox.value = JSON.stringify(_buildRoleTemplate({ ...target, links: {} }), null, 2);
    });

    const saveBtn = root.querySelector("#topoSaveBtn");
    saveBtn.addEventListener("click", async () => {
        let parsed = null;
        try {
            parsed = JSON.parse(jsonBox.value);
        } catch {
            showToast("Некорректный JSON");
            return;
        }
        saveBtn.disabled = true;
        try {
            const res = await api.updateNodeLinks(target.id, parsed);
            showToast(
                res.apply_queued
                    ? `Связи отправлены, пересборка на #${target.id} запущена`
                    : `Связи сохранены на #${target.id}`
            );
            await onReload();
        } catch (err) {
            showToast(err.message || "Ошибка сохранения связей");
        } finally {
            saveBtn.disabled = false;
        }
    });
}
