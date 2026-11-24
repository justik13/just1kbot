import { api } from "../api.js?v=2.7.8";
import { escapeHtml, healthChipInfo, openModal, renderCountryFlagField, renderFlagHtml, roleLabel, showConfirmModal, showToast, wireCountryFlagFields } from "../ui.js?v=2.7.8";

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
    box.style.gap = "12px";
    box.innerHTML = `
        <div class="field">
            <label>Название</label>
            <input name="name" class="input" required maxlength="255" value="${escapeHtml(node.name)}">
        </div>
        ${renderCountryFlagField({ value: node.country_flag || "" })}
        <div class="field">
            <label>URL API ноды</label>
            <input name="api_url" class="input" type="url" required maxlength="500" value="${escapeHtml(node.api_url || "")}">
        </div>
        <div class="field">
            <label>Новый API Токен (оставьте пустым, чтобы не менять)</label>
            <input name="api_key" class="input" type="password" maxlength="512" placeholder="••••••••">
        </div>
        <button type="submit" class="btn btn-primary">Сохранить</button>
    `;
    const close = openModal(`Редактировать #${node.id}`, box);
    wireCountryFlagFields(box);
    box.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(box);
        const btn = box.querySelector("button[type='submit']");
        btn.disabled = true;
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
            showToast(err.message || "Ошибка обновления");
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
            <span class="detail-sticky-meta">${escapeHtml(node.name)}</span>
        </div>

        <section class="card">
            <div class="row">
                <div style="display:flex; align-items:center; gap:12px;">
                    ${renderFlagHtml(node.country_flag || node.location_code || "", 26)}
                    <div>
                        <div style="font-size:16px; font-weight:600;">${escapeHtml(node.name)}</div>
                        <div class="muted">Сервер #${node.id}</div>
                    </div>
                </div>
                <div class="row" style="gap:8px;">
                    <span class="chip chip-muted">${escapeHtml(roleLabel(node.role))}</span>
                    <span class="chip ${health.cls}">${escapeHtml(health.text)}</span>
                </div>
            </div>

            <div class="actions-row" style="margin-top:8px;">
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
                ${node.traffic_limit && typeof node.traffic_limit === "object" ? `
                <div class="detail-item">
                    <span class="detail-label">Трафик за месяц</span>
                    <span>${((Number(node.traffic_limit.monthly_bytes || node.traffic_limit.used_bytes || 0)) / (1024 ** 3)).toFixed(2)} ГБ${node.traffic_limit.limit_bytes ? ` / ${(Number(node.traffic_limit.limit_bytes) / (1024 ** 3)).toFixed(0)} ГБ` : ""}</span>
                </div>` : ""}
                ${node.role === "exit_node" ? `
                <div class="detail-item">
                    <span class="detail-label">Код локации</span>
                    <span class="mono">${escapeHtml(node.location_code || "—")}</span>
                </div>` : ""}
                <div class="detail-item">
                    <span class="detail-label">Последняя проверка</span>
                    <span class="muted">${node.last_successful_check ? new Date(node.last_successful_check).toLocaleString("ru-RU") : "—"}</span>
                </div>
            </div>
        </section>

        <section class="card">
            <div class="card-title">Модули</div>
            <div class="filter-pills" style="margin-bottom:8px;">${_renderModules(node)}</div>
            ${node.inbounds && node.inbounds.length ? `
                <div class="detail-item" style="margin-top:8px;">
                    <span class="detail-label">Inbounds</span>
                    <span class="mono">${node.inbounds.map((ib) => escapeHtml(ib)).join(", ")}</span>
                </div>
            ` : ""}
        </section>

        ${_renderModuleErrors(node)}

        <section class="card">
            <div class="card-title">Настройки и обслуживание</div>
            <div class="actions-row">
                <button type="button" id="detailEditBtn" class="btn btn-sm">Изменить</button>
                <button type="button" id="detailDeleteBtn" class="btn btn-sm btn-danger">Удалить сервер</button>
            </div>
        </section>
    `;

    root.querySelector("#detailBackBtn").addEventListener("click", onBack);

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
            },
        });
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
                    onBack();
                    await onReload();
                } catch (err) {
                    if (err && err.status === 409) {
                        showConfirmModal({
                            title: "Принудительное удаление",
                            message: `${err.message}\n\nВсё равно принудительно удалить сервер?`,
                            confirmText: "Удалить принудительно",
                            cancelText: "Отмена",
                            isDestructive: true,
                            onConfirm: async () => {
                                try {
                                    await api.deleteNode(node.id, true);
                                    showToast(`Сервер #${node.id} принудительно удалён`);
                                    onBack();
                                    await onReload();
                                } catch (err2) {
                                    showToast(err2.message || "Ошибка удаления");
                                }
                            },
                        });
                        return;
                    }
                    showToast(err.message || "Ошибка удаления");
                }
            },
        });
    });
}
