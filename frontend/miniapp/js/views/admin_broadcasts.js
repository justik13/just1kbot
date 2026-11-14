import { api } from "../api.js";
import { escapeHtml, formatDate, openModal, showToast } from "../ui.js";

let _pollTimer = null;

export function clearBroadcastPollTimer() {
    if (_pollTimer !== null) {
        clearInterval(_pollTimer);
        _pollTimer = null;
    }
}

function _buildAudienceOptions({ includeTest = true } = {}) {
    const base = [
        { value: "all", label: "Все пользователи" },
        { value: "active", label: "С активной подпиской" },
        { value: "active_standard", label: "Активный «Стандарт»" },
        { value: "active_wi", label: "Активный «Белый интернет»" },
        { value: "expiring_3d", label: "Истекает ≤ 3 дней" },
        { value: "expired", label: "Подписка истекла" },
        { value: "no_sub", label: "Без активной подписки" },
        { value: "never", label: "Никогда не покупали" },
    ];
    if (includeTest) {
        base.push({ value: "test", label: "Тест (только мне)" });
    }
    return base
        .map((opt) => `<option value="${escapeHtml(opt.value)}">${escapeHtml(opt.label)}</option>`)
        .join("");
}

function _confirmModal(title, summaryText, onConfirm) {
    const box = document.createElement("div");
    box.className = "field";
    box.style.gap = "12px";
    box.innerHTML = `
        <p class="muted" style="line-height:1.45;">${escapeHtml(summaryText)}</p>
        <div class="btn-group" style="justify-content:flex-end;">
            <button type="button" class="btn btn-sm" id="cfmCancelBtn">Отмена</button>
            <button type="button" class="btn btn-primary btn-sm" id="cfmOkBtn">Подтвердить</button>
        </div>
    `;
    const close = openModal(title, box);
    box.querySelector("#cfmCancelBtn").addEventListener("click", close);
    const okBtn = box.querySelector("#cfmOkBtn");
    okBtn.addEventListener("click", async () => {
        okBtn.disabled = true;
        try {
            await onConfirm();
            close();
        } finally {
            okBtn.disabled = false;
        }
    });
}

function _statusChip(status) {
    if (status === "completed") return `<span class="chip chip-ok">Завершена</span>`;
    if (status === "in_progress") return `<span class="chip chip-info">В процессе</span>`;
    if (status === "cancelled") return `<span class="chip chip-warn">Остановлена</span>`;
    return `<span class="chip chip-err">Ошибка</span>`;
}

export function renderAdminBroadcastsView(root, state) {
    clearBroadcastPollTimer();
    const broadcasts = state.broadcastsData || [];
    const massTab = state.massTab || "bonus";
    const audienceHtml = _buildAudienceOptions({ includeTest: true });
    const massAudienceHtml = _buildAudienceOptions({ includeTest: false });

    root.innerHTML = `
        <section class="card">
            <div class="row">
                <div class="card-title">Рассылка в Telegram</div>
                <span class="chip chip-info">HTML · фото URL</span>
            </div>
            <form id="broadcastForm" class="field" style="gap:10px;">
                <div class="field">
                    <label for="bcAudienceSelect">Аудитория</label>
                    <div class="row" style="flex-wrap:nowrap;">
                        <select id="bcAudienceSelect" class="select">${audienceHtml}</select>
                        <button id="bcPreviewBtn" type="button" class="btn btn-sm">Охват</button>
                    </div>
                    <div id="bcPreviewHint" class="muted" style="font-size:11px;"></div>
                </div>
                <div class="field">
                    <label for="bcTextInput">Текст сообщения (HTML: &lt;b&gt;, &lt;i&gt;, &lt;code&gt;, &lt;a&gt;)</label>
                    <textarea id="bcTextInput" class="textarea" rows="5" maxlength="4096" required
                        placeholder="Текст рассылки..."></textarea>
                </div>
                <div class="field">
                    <label for="bcMediaInput">Фото (необязательно: https://... или file_id)</label>
                    <input id="bcMediaInput" class="input" type="text" maxlength="500"
                        placeholder="https://example.com/banner.jpg">
                </div>
                <button id="bcLaunchBtn" type="submit" class="btn btn-primary">Запустить рассылку</button>
            </form>
        </section>

        <section class="card">
            <div class="row">
                <div class="card-title">Массовые операции</div>
                <div class="seg-group" style="max-width:240px;">
                    <button type="button" class="seg-btn ${massTab === "bonus" ? "active" : ""}" data-masstab="bonus">Бонус ₽</button>
                    <button type="button" class="seg-btn ${massTab === "extend" ? "active" : ""}" data-masstab="extend">Продление</button>
                </div>
            </div>
            <form id="massOpForm" class="field" style="gap:10px;">
                <div class="field">
                    <label for="massAudienceSelect">Аудитория</label>
                    <select id="massAudienceSelect" class="select">${massAudienceHtml}</select>
                </div>
                ${
                    massTab === "bonus"
                        ? `
                    <div class="grid-2">
                        <div class="field">
                            <label for="massAmountInput">Сумма бонуса (₽)</label>
                            <input id="massAmountInput" class="input tabular" type="number" min="1" max="50000" step="1" value="100" required>
                        </div>
                        <div class="field">
                            <label for="massReasonInput">Комментарий</label>
                            <input id="massReasonInput" class="input" type="text" maxlength="200" placeholder="Компенсация">
                        </div>
                    </div>
                `
                        : `
                    <div class="grid-2">
                        <div class="field">
                            <label for="massScopeSelect">Сервис</label>
                            <select id="massScopeSelect" class="select">
                                <option value="standard">Стандарт</option>
                                <option value="white_internet">Белый интернет</option>
                            </select>
                        </div>
                        <div class="field">
                            <label for="massDaysInput">Дней (+)</label>
                            <input id="massDaysInput" class="input tabular" type="number" min="1" max="365" step="1" value="3" required>
                        </div>
                    </div>
                    <div class="field">
                        <label for="massReasonInput">Комментарий</label>
                        <input id="massReasonInput" class="input" type="text" maxlength="200" placeholder="Продление">
                    </div>
                `
                }
                <label class="muted" style="display:flex; align-items:center; gap:8px; font-size:12px;">
                    <input id="massNotifyCheckbox" type="checkbox" checked>
                    Отправить уведомление пользователям в бот
                </label>
                <button type="submit" class="btn btn-primary">Применить массово</button>
            </form>
        </section>

        <section class="card">
            <div class="row">
                <div class="card-title">История рассылок</div>
                <button id="reloadBcBtn" type="button" class="btn btn-sm">Обновить</button>
            </div>
            <div id="bcListContainer" style="display:flex; flex-direction:column; gap:8px;"></div>
        </section>
    `;

    const reloadBroadcasts = async (silent = false) => {
        try {
            const res = await api.listBroadcasts();
            state.broadcastsData = res.broadcasts || [];
            if (state.adminTab === "broadcasts" && state.mode === "admin") {
                _renderHistoryList(root.querySelector("#bcListContainer"), state, reloadBroadcasts);
                const hasRunning = state.broadcastsData.some((b) => b.status === "in_progress");
                if (hasRunning && _pollTimer === null) {
                    _pollTimer = setInterval(() => reloadBroadcasts(true), 4000);
                } else if (!hasRunning) {
                    clearBroadcastPollTimer();
                }
            }
        } catch (err) {
            if (!silent) showToast(err.message || "Не удалось обновить список рассылок");
        }
    };

    root.querySelector("#reloadBcBtn").addEventListener("click", () => reloadBroadcasts(false));

    root.querySelector("#bcPreviewBtn").addEventListener("click", async () => {
        const audience = root.querySelector("#bcAudienceSelect").value;
        const hintEl = root.querySelector("#bcPreviewHint");
        hintEl.textContent = "Подсчёт...";
        try {
            const res = await api.previewBroadcast(audience);
            hintEl.textContent = `Аудитория «${res.label}»: получат ${res.reachable_count} (всего в сегменте ${res.total_count})`;
        } catch (err) {
            hintEl.textContent = "";
            showToast(err.message || "Ошибка предпросмотра");
        }
    });

    root.querySelector("#broadcastForm").addEventListener("submit", async (e) => {
        e.preventDefault();
        const audienceSelect = root.querySelector("#bcAudienceSelect");
        const audience = audienceSelect.value;
        const audienceLabel = audienceSelect.selectedOptions[0]?.textContent || audience;
        const text = root.querySelector("#bcTextInput").value.trim();
        const mediaUrl = root.querySelector("#bcMediaInput").value.trim() || null;
        if (!text) {
            showToast("Введите текст рассылки");
            return;
        }
        const runLaunch = async () => {
            const btn = root.querySelector("#bcLaunchBtn");
            btn.disabled = true;
            try {
                const res = await api.startBroadcast({
                    target_audience: audience,
                    text,
                    media_url: mediaUrl,
                });
                showToast(res.message || "Рассылка запущена");
                root.querySelector("#bcTextInput").value = "";
                root.querySelector("#bcMediaInput").value = "";
                await reloadBroadcasts(false);
            } catch (err) {
                showToast(err.message || "Ошибка запуска рассылки");
            } finally {
                btn.disabled = false;
            }
        };

        if (audience === "test") {
            await runLaunch();
            return;
        }
        _confirmModal(
            "Подтвердите рассылку",
            `Запустить рассылку на сегмент «${audienceLabel}»? Отменить можно в списке ниже.`,
            runLaunch
        );
    });

    root.querySelectorAll("[data-masstab]").forEach((btn) => {
        btn.addEventListener("click", () => {
            state.massTab = btn.dataset.masstab;
            renderAdminBroadcastsView(root, state);
        });
    });

    root.querySelector("#massOpForm").addEventListener("submit", (e) => {
        e.preventDefault();
        const massSelect = root.querySelector("#massAudienceSelect");
        const audience = massSelect.value;
        const audienceLabel = massSelect.selectedOptions[0]?.textContent || audience;
        const reason = root.querySelector("#massReasonInput").value.trim();
        const notifyUsers = root.querySelector("#massNotifyCheckbox").checked;

        if (massTab === "bonus") {
            const amount = Number(root.querySelector("#massAmountInput").value);
            if (!Number.isInteger(amount) || amount < 1 || amount > 50000) {
                showToast("Сумма должна быть от 1 до 50000 ₽");
                return;
            }
            _confirmModal(
                "Массовое начисление бонуса",
                `Начислить +${amount} ₽ сегменту «${audienceLabel}»? Действие необратимо.`,
                async () => {
                    try {
                        const res = await api.massBonus({
                            target_audience: audience,
                            amount_rub: amount,
                            reason,
                            notify_users: notifyUsers,
                        });
                        showToast(res.message || "Массовое начисление запущено");
                    } catch (err) {
                        showToast(err.message || "Ошибка начисления");
                    }
                }
            );
        } else {
            const scope = root.querySelector("#massScopeSelect").value;
            const days = Number(root.querySelector("#massDaysInput").value);
            if (!Number.isInteger(days) || days < 1 || days > 365) {
                showToast("Срок должен быть от 1 до 365 дней");
                return;
            }
            const scopeLabel = scope === "white_internet" ? "Белый интернет" : "Стандарт";
            _confirmModal(
                "Массовое продление подписки",
                `Продлить «${scopeLabel}» на +${days} дн. для сегмента «${audienceLabel}»?`,
                async () => {
                    try {
                        const res = await api.massExtend({
                            service_scope: scope,
                            target_audience: audience,
                            days,
                            reason,
                            notify_users: notifyUsers,
                        });
                        showToast(res.message || "Массовое продление запущено");
                    } catch (err) {
                        showToast(err.message || "Ошибка продления");
                    }
                }
            );
        }
    });

    _renderHistoryList(root.querySelector("#bcListContainer"), state, reloadBroadcasts);
    if (broadcasts.some((b) => b.status === "in_progress")) {
        _pollTimer = setInterval(() => reloadBroadcasts(true), 4000);
    }
}

function _renderHistoryList(container, state, reloadBroadcasts) {
    if (!container) return;
    const broadcasts = state.broadcastsData || [];
    container.innerHTML = "";
    if (!broadcasts.length) {
        container.innerHTML = `<div class="muted" style="text-align:center; padding:12px;">Рассылок пока нет</div>`;
        return;
    }

    broadcasts.forEach((b) => {
        const row = document.createElement("div");
        row.className = "item-row";
        row.innerHTML = `
            <div class="row">
                <div>
                    <span style="font-weight:600;">#${b.id} · ${escapeHtml(b.label)}</span>
                    <span class="muted" style="font-size:11px; margin-left:6px;">${formatDate(b.created_at)}</span>
                </div>
                ${_statusChip(b.status)}
            </div>
            <div class="muted" style="font-size:12px; word-break:break-word;">${escapeHtml(b.text_preview)}</div>
            <div class="row">
                <span class="tabular muted" style="font-size:12px;">
                    Успешно: <b>${b.success_count}</b> / ${b.total_count} (${b.progress_pct}%) · Ошибок: ${b.fail_count}
                </span>
                ${
                    b.status === "in_progress"
                        ? `<button type="button" class="btn btn-danger btn-sm" data-cancel-bc="${b.id}">Остановить</button>`
                        : ""
                }
            </div>
        `;
        const cancelBtn = row.querySelector(`[data-cancel-bc="${b.id}"]`);
        if (cancelBtn) {
            cancelBtn.addEventListener("click", async () => {
                cancelBtn.disabled = true;
                try {
                    const res = await api.cancelBroadcast(b.id);
                    showToast(res.message || "Рассылка остановлена");
                    await reloadBroadcasts(false);
                } catch (err) {
                    showToast(err.message || "Ошибка остановки");
                } finally {
                    cancelBtn.disabled = false;
                }
            });
        }
        container.appendChild(row);
    });
}
