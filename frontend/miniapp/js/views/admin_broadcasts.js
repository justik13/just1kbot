import { api } from "../api.js?v=2.8.0";
import { escapeHtml, formatDate, renderPagination, showConfirmModal, showToast, withButtonLoading } from "../ui.js?v=2.8.0";

let _pollTimer = null;
let _bcReqId = 0;

export function clearBroadcastPollTimer() {
    if (_pollTimer !== null) {
        clearInterval(_pollTimer);
        _pollTimer = null;
    }
}

function _buildAudienceOptions({ includeTest = true, includeEmptyPlaceholder = false, defaultValue = "" } = {}) {
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
    const items = includeEmptyPlaceholder
        ? [{ value: "", label: "— Выберите аудиторию —" }, ...base]
        : base;
    return items
        .map(
            (opt) =>
                `<option value="${escapeHtml(opt.value)}" ${opt.value === defaultValue ? "selected" : ""}>${escapeHtml(
                    opt.label
                )}</option>`
        )
        .join("");
}

function _confirmModal(title, summaryText, onConfirm, confirmText = "Подтвердить", isDestructive = false) {
    showConfirmModal({
        title,
        message: summaryText,
        confirmText,
        cancelText: "Отмена",
        isDestructive,
        onConfirm,
    });
}

function _statusChip(status) {
    if (status === "completed") return `<span class="chip chip-ok">Завершена</span>`;
    if (status === "in_progress") return `<span class="chip chip-warn">В процессе</span>`;
    if (status === "cancelled") return `<span class="chip chip-warn">Остановлена</span>`;
    return `<span class="chip chip-err">Ошибка</span>`;
}

function _renderMassOpFormHtml(state) {
    const massTab = state.massTab || "bonus";
    const massAudienceHtml = _buildAudienceOptions({
        includeTest: false,
        includeEmptyPlaceholder: true,
        defaultValue: "",
    });
    return `
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
        <button id="massSubmitBtn" type="submit" class="btn btn-primary">Применить массово</button>
    `;
}

function _updateCharCounter(root) {
    const textInput = root.querySelector("#bcTextInput");
    const mediaInput = root.querySelector("#bcMediaInput");
    const counterEl = root.querySelector("#bcCharCounter");
    if (!textInput || !counterEl) return;
    const len = textInput.value.length;
    const hasMedia = Boolean(mediaInput && mediaInput.value.trim());
    if (hasMedia && len > 1024) {
        counterEl.innerHTML = `<span style="color:var(--warn, #f59e0b); font-weight:600;">${len} / 4096 · С фото > 1024 симв. — сообщение будет разбито на 2 части</span>`;
    } else {
        counterEl.textContent = `${len} / 4096`;
    }
}

export function renderAdminBroadcastsView(root, state) {
    clearBroadcastPollTimer();
    const broadcasts = state.broadcastsData || [];
    const massTab = state.massTab || "bonus";
    const audienceHtml = _buildAudienceOptions({ includeTest: true, defaultValue: "test" });

    root.innerHTML = `
        <section class="card">
            <div class="row">
                <div class="card-title">Рассылка в Telegram</div>
                <span class="chip chip-muted">HTML · фото URL</span>
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
                    <div id="bcCharCounter" class="muted" style="font-size:11px;">0 / 4096</div>
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
                ${_renderMassOpFormHtml(state)}
            </form>
        </section>

        <section class="card">
            <div class="row">
                <div class="card-title">История рассылок</div>
                <button id="reloadBcBtn" type="button" class="btn btn-sm">Обновить</button>
            </div>
            <div id="bcPagerTop"></div>
            <div id="bcListContainer" style="display:flex; flex-direction:column; gap:8px;"></div>
            <div id="bcPagerBottom"></div>
        </section>
    `;

    const reloadBroadcasts = async (silent = false, offset = state.broadcastsOffset || 0) => {
        const reqId = ++_bcReqId;
        try {
            const res = await api.listBroadcasts(offset, 15);
            if (reqId !== _bcReqId) return;
            state.broadcastsData = res.broadcasts || [];
            state.broadcastsTotal = res.total || 0;
            state.broadcastsOffset = res.offset || 0;
            state.broadcastsLimit = res.limit || 15;
            if (state.adminTab === "broadcasts" && state.mode === "admin") {
                _renderHistoryList(root, root.querySelector("#bcListContainer"), state, reloadBroadcasts);
                _renderBcPagination(root, state, reloadBroadcasts);
                const hasRunning = state.broadcastsData.some((b) => b.status === "in_progress");
                if (hasRunning && _pollTimer === null) {
                    _pollTimer = setInterval(() => reloadBroadcasts(true, state.broadcastsOffset || 0), 4000);
                } else if (!hasRunning) {
                    clearBroadcastPollTimer();
                }
            }
        } catch (err) {
            if (reqId !== _bcReqId) return;
            if (!silent) showToast(err.message || "Не удалось обновить список рассылок", "error");
        }
    };

    const bcTextInput = root.querySelector("#bcTextInput");
    const bcMediaInput = root.querySelector("#bcMediaInput");
    bcTextInput.addEventListener("input", () => _updateCharCounter(root));
    bcMediaInput.addEventListener("input", () => _updateCharCounter(root));
    _updateCharCounter(root);

    root.querySelector("#bcAudienceSelect").addEventListener("change", () => {
        const hintEl = root.querySelector("#bcPreviewHint");
        if (hintEl) hintEl.textContent = "";
    });

    root.querySelector("#reloadBcBtn").addEventListener("click", () => reloadBroadcasts(false, state.broadcastsOffset || 0));

    root.querySelector("#bcPreviewBtn").addEventListener("click", async () => {
        const audience = root.querySelector("#bcAudienceSelect").value;
        const hintEl = root.querySelector("#bcPreviewHint");
        hintEl.textContent = "Подсчёт...";
        try {
            const res = await api.previewBroadcast(audience);
            hintEl.textContent = `Аудитория «${res.label}»: получат ${res.reachable_count} (всего в сегменте ${res.total_count})`;
        } catch (err) {
            hintEl.textContent = "";
            showToast(err.message || "Ошибка предпросмотра", "error");
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
            showToast("Введите текст рассылки", "error");
            return;
        }
        const launchBtn = root.querySelector("#bcLaunchBtn");
        const runLaunch = async () => {
            await withButtonLoading(launchBtn, "Запуск…", async () => {
                try {
                    const res = await api.startBroadcast({
                        target_audience: audience,
                        text,
                        media_url: mediaUrl,
                    });
                    showToast(res.message || "Рассылка запущена");
                    if (audience !== "test") {
                        root.querySelector("#bcTextInput").value = "";
                        root.querySelector("#bcMediaInput").value = "";
                        _updateCharCounter(root);
                    }
                    state.broadcastsOffset = 0;
                    await reloadBroadcasts(false, 0);
                } catch (err) {
                    showToast(err.message || "Ошибка запуска рассылки", "error");
                }
            });
        };

        if (audience === "test") {
            await runLaunch();
            return;
        }

        try {
            const preview = await withButtonLoading(launchBtn, "Подсчёт…", () => api.previewBroadcast(audience));
            const snippet = text.length > 120 ? `${text.slice(0, 120)}…` : text;
            _confirmModal(
                "Подтвердите рассылку",
                `Аудитория «${preview.label || audienceLabel}»: получат ${preview.reachable_count} чел.\n\nТекст: «${snippet}»\n\nЗапустить рассылку? Отменить можно в списке ниже.`,
                runLaunch,
                "Запустить",
                true
            );
        } catch (err) {
            showToast(err.message || "Ошибка предпросмотра аудитории", "error");
        }
    });

    root.querySelectorAll("[data-masstab]").forEach((btn) => {
        btn.addEventListener("click", () => {
            state.massTab = btn.dataset.masstab;
            root.querySelectorAll("[data-masstab]").forEach((b) => {
                b.classList.toggle("active", b.dataset.masstab === state.massTab);
            });
            const form = root.querySelector("#massOpForm");
            if (form) {
                form.innerHTML = _renderMassOpFormHtml(state);
            }
        });
    });

    root.querySelector("#massOpForm").addEventListener("submit", async (e) => {
        e.preventDefault();
        const activeMassTab = state.massTab || "bonus";
        const massSelect = root.querySelector("#massAudienceSelect");
        const audience = massSelect.value;
        if (!audience) {
            showToast("Выберите аудиторию", "error");
            return;
        }
        const audienceLabel = massSelect.selectedOptions[0]?.textContent || audience;
        const reason = root.querySelector("#massReasonInput").value.trim();
        const notifyUsers = root.querySelector("#massNotifyCheckbox").checked;
        const submitBtn = root.querySelector("#massSubmitBtn");

        if (activeMassTab === "bonus") {
            const amount = Number(root.querySelector("#massAmountInput").value);
            if (!Number.isInteger(amount) || amount < 1 || amount > 50000) {
                showToast("Сумма должна быть от 1 до 50000 ₽", "error");
                return;
            }
            try {
                const preview = await withButtonLoading(submitBtn, "Подсчёт…", () => api.previewBroadcast(audience));
                const totalRub = amount * Number(preview.reachable_count || 0);
                _confirmModal(
                    "Массовое начисление бонуса",
                    `Начислить +${amount} ₽ сегменту «${preview.label || audienceLabel}»?\nПолучателей: ${preview.reachable_count} · Итого: ${totalRub} ₽.\nДействие необратимо.`,
                    async () => {
                        try {
                            const res = await api.massBonus({
                                target_audience: audience,
                                amount_rub: amount,
                                reason,
                                notify_users: notifyUsers,
                            });
                            state.usersData = null;
                            state.financesData = null;
                            showToast(res.message || "Массовое начисление запущено");
                        } catch (err) {
                            showToast(err.message || "Ошибка начисления", "error");
                        }
                    },
                    "Начислить",
                    true
                );
            } catch (err) {
                showToast(err.message || "Ошибка предпросмотра аудитории", "error");
            }
        } else {
            const scope = root.querySelector("#massScopeSelect").value;
            const days = Number(root.querySelector("#massDaysInput").value);
            if (!Number.isInteger(days) || days < 1 || days > 365) {
                showToast("Срок должен быть от 1 до 365 дней", "error");
                return;
            }
            const scopeLabel = scope === "white_internet" ? "Белый интернет" : "Стандарт";
            try {
                const preview = await withButtonLoading(submitBtn, "Подсчёт…", () => api.previewBroadcast(audience));
                _confirmModal(
                    "Массовое продление подписки",
                    `Продлить «${scopeLabel}» на +${days} дн. для сегмента «${preview.label || audienceLabel}»?\nПолучателей: ${preview.reachable_count}.`,
                    async () => {
                        try {
                            const res = await api.massExtend({
                                service_scope: scope,
                                target_audience: audience,
                                days,
                                reason,
                                notify_users: notifyUsers,
                            });
                            state.usersData = null;
                            state.financesData = null;
                            showToast(res.message || "Массовое продление запущено");
                        } catch (err) {
                            showToast(err.message || "Ошибка продления", "error");
                        }
                    },
                    "Продлить",
                    true
                );
            } catch (err) {
                showToast(err.message || "Ошибка предпросмотра аудитории", "error");
            }
        }
    });

    _renderHistoryList(root, root.querySelector("#bcListContainer"), state, reloadBroadcasts);
    _renderBcPagination(root, state, reloadBroadcasts);
    if (broadcasts.some((b) => b.status === "in_progress")) {
        _pollTimer = setInterval(() => reloadBroadcasts(true, state.broadcastsOffset || 0), 4000);
    }
}

function _renderBcPagination(root, state, reloadBroadcasts) {
    const listEl = root.querySelector("#bcListContainer");
    const onPage = (newOffset) => reloadBroadcasts(false, newOffset);
    renderPagination({
        container: root.querySelector("#bcPagerTop"),
        total: state.broadcastsTotal || 0,
        offset: state.broadcastsOffset || 0,
        limit: state.broadcastsLimit || 15,
        onPage,
        scrollTarget: listEl,
        entityName: "рассылок",
    });
    renderPagination({
        container: root.querySelector("#bcPagerBottom"),
        total: state.broadcastsTotal || 0,
        offset: state.broadcastsOffset || 0,
        limit: state.broadcastsLimit || 15,
        onPage,
        scrollTarget: listEl,
        entityName: "рассылок",
    });
}

function _renderHistoryList(root, container, state, reloadBroadcasts) {
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
                <div style="display:flex; align-items:center; flex-wrap:wrap; gap:6px;">
                    <span style="font-weight:600;">#${b.id} · ${escapeHtml(b.label)}</span>
                    ${b.has_media ? `<span class="chip chip-muted">📷 Фото</span>` : ""}
                    <span class="muted" style="font-size:11px;">${formatDate(b.created_at)}</span>
                </div>
                ${_statusChip(b.status)}
            </div>
            <div class="muted" style="font-size:12px; word-break:break-word;">${escapeHtml(b.text_preview)}</div>
            <div class="row" style="gap:6px; flex-wrap:wrap;">
                <span class="tabular muted" style="font-size:12px;">
                    Успешно: <b>${b.success_count}</b> / ${b.total_count} (${b.progress_pct}%) · Ошибок: ${b.fail_count}
                </span>
                <div style="display:flex; gap:6px; align-items:center;">
                    <button type="button" class="btn btn-sm" data-repeat-bc="${b.id}">Повторить</button>
                    ${
                        b.status === "in_progress"
                            ? `<button type="button" class="btn btn-danger btn-sm" data-cancel-bc="${b.id}">Остановить</button>`
                            : ""
                    }
                </div>
            </div>
        `;

        const repeatBtn = row.querySelector(`[data-repeat-bc="${b.id}"]`);
        if (repeatBtn) {
            repeatBtn.addEventListener("click", () => {
                const textInput = root.querySelector("#bcTextInput");
                if (textInput) {
                    textInput.value = b.text_preview || "";
                    _updateCharCounter(root);
                    textInput.focus();
                    textInput.scrollIntoView({ behavior: "smooth", block: "center" });
                    showToast("Текст скопирован в редактор");
                }
            });
        }

        const cancelBtn = row.querySelector(`[data-cancel-bc="${b.id}"]`);
        if (cancelBtn) {
            cancelBtn.addEventListener("click", () => {
                _confirmModal(
                    "Остановить рассылку?",
                    `Остановить рассылку #${b.id} («${b.label}»)? Уже отправленные сообщения не будут удалены.`,
                    async () => {
                        await withButtonLoading(cancelBtn, "Остановка…", async () => {
                            try {
                                const res = await api.cancelBroadcast(b.id);
                                showToast(res.message || "Рассылка остановлена");
                                await reloadBroadcasts(false);
                            } catch (err) {
                                showToast(err.message || "Ошибка остановки", "error");
                            }
                        });
                    },
                    "Остановить",
                    true
                );
            });
        }
        container.appendChild(row);
    });
}
