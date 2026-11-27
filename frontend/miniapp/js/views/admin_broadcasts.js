import { api } from "../api.js?v=2.8.0";
import { escapeHtml, formatDate, renderPagination, showConfirmModal, showToast, withButtonLoading } from "../ui.js?v=2.8.0";

let _pollTimer = null;
let _bcReqId = 0;
let _previewReqId = 0;

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
        ? [{ value: "", label: "Выберите аудиторию" }, ...base]
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

function _confirmModal(
    title,
    summaryText,
    onConfirm,
    confirmText = "Подтвердить",
    isDestructive = false,
    restoreFocusSelector = null
) {
    showConfirmModal({
        title,
        message: summaryText,
        confirmText,
        cancelText: "Отмена",
        isDestructive,
        restoreFocusSelector,
        onConfirm,
    });
}

function _statusChip(status) {
    if (status === "completed") return `<span class="chip chip-ok">Завершена</span>`;
    if (status === "in_progress") return `<span class="chip chip-warn">В процессе</span>`;
    if (status === "cancelled") return `<span class="chip chip-warn">Остановлена</span>`;
    return `<span class="chip chip-err">Ошибка</span>`;
}

function _renderMassOpFormHtml(state, draft = {}) {
    const massTab = state.massTab || "bonus";
    const massAudienceHtml = _buildAudienceOptions({
        includeTest: false,
        includeEmptyPlaceholder: true,
        defaultValue: draft.audience ?? "",
    });
    const reasonVal = escapeHtml(draft.reason ?? "");
    const notifyChecked = draft.notify !== undefined ? Boolean(draft.notify) : true;
    const amountVal = escapeHtml(draft.amount ?? "100");
    const scopeVal = draft.scope ?? "standard";
    const daysVal = escapeHtml(draft.days ?? "3");
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
                    <input id="massAmountInput" class="input tabular" type="number" min="1" max="50000" step="1" value="${amountVal}" required>
                </div>
                <div class="field">
                    <label for="massReasonInput">Комментарий</label>
                    <input id="massReasonInput" class="input" type="text" maxlength="200" value="${reasonVal}" placeholder="Компенсация">
                </div>
            </div>
        `
                : `
            <div class="grid-2">
                <div class="field">
                    <label for="massScopeSelect">Сервис</label>
                    <select id="massScopeSelect" class="select">
                        <option value="standard" ${scopeVal === "standard" ? "selected" : ""}>Стандарт</option>
                        <option value="white_internet" ${scopeVal === "white_internet" ? "selected" : ""}>Белый интернет</option>
                    </select>
                </div>
                <div class="field">
                    <label for="massDaysInput">Дней (+)</label>
                    <input id="massDaysInput" class="input tabular" type="number" min="1" max="365" step="1" value="${daysVal}" required>
                </div>
            </div>
            <div class="field">
                <label for="massReasonInput">Комментарий</label>
                <input id="massReasonInput" class="input" type="text" maxlength="200" value="${reasonVal}" placeholder="Продление">
            </div>
        `
        }
        <label for="massNotifyCheckbox" class="checkbox-row">
            <input id="massNotifyCheckbox" type="checkbox" ${notifyChecked ? "checked" : ""}>
            <span>Отправить уведомление пользователям в бот</span>
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
        counterEl.innerHTML = `<span style="color:var(--accent-amber); font-weight:600;">${len} / 4096 · С фото > 1024 симв.: сообщение будет разбито на 2 части</span>`;
    } else {
        counterEl.textContent = `${len} / 4096`;
    }
}

export function renderAdminBroadcastsView(root, state) {
    clearBroadcastPollTimer();
    ++_bcReqId;
    ++_previewReqId;
    const broadcasts = state.broadcastsData || [];
    const massTab = state.massTab || "bonus";
    const audienceHtml = _buildAudienceOptions({ includeTest: true, defaultValue: "test" });
    const massDraft = state.massDraft || {
        audience: "",
        amount: "100",
        scope: "standard",
        days: "3",
        reason: "",
        notify: true,
    };
    state.massDraft = massDraft;

    root.innerHTML = `
        <section class="card">
            <div class="row">
                <h2 class="card-title">Рассылка в Telegram</h2>
                <span class="chip chip-muted">HTML · фото URL</span>
            </div>
            <form id="broadcastForm" class="field" style="gap:12px;">
                <div class="field">
                    <label for="bcAudienceSelect">Аудитория</label>
                    <div class="row-nowrap">
                        <select id="bcAudienceSelect" class="select">${audienceHtml}</select>
                        <button id="bcPreviewBtn" type="button" class="btn btn-sm">Охват</button>
                    </div>
                    <div id="bcPreviewHint" class="meta-text"></div>
                </div>
                <div class="field">
                    <label for="bcTextInput">Текст сообщения (HTML: &lt;b&gt;, &lt;i&gt;, &lt;code&gt;, &lt;a&gt;)</label>
                    <textarea id="bcTextInput" class="textarea" rows="5" maxlength="4096" required
                        placeholder="Текст рассылки"></textarea>
                    <div id="bcCharCounter" class="meta-text">0 / 4096</div>
                </div>
                <div class="field">
                    <label for="bcMediaInput">Фото (необязательно: https://... или file_id)</label>
                    <input id="bcMediaInput" class="input" type="text" maxlength="500"
                        placeholder="https://... или Telegram file_id">
                </div>
                <button id="bcLaunchBtn" type="submit" class="btn btn-primary">Запустить рассылку</button>
            </form>
        </section>

        <section class="card">
            <div class="row" style="gap:8px; flex-wrap:wrap;">
                <h2 class="card-title">Массовые операции</h2>
                <div class="seg-group" style="width:auto; flex:0 0 auto;">
                    <button type="button" class="seg-btn ${massTab === "bonus" ? "active" : ""}" style="white-space:nowrap; min-width:max-content; padding:8px 14px;" data-masstab="bonus" aria-pressed="${massTab === "bonus" ? "true" : "false"}">Бонус ₽</button>
                    <button type="button" class="seg-btn ${massTab === "extend" ? "active" : ""}" style="white-space:nowrap; min-width:max-content; padding:8px 14px;" data-masstab="extend" aria-pressed="${massTab === "extend" ? "true" : "false"}">Продление</button>
                </div>
            </div>
            <form id="massOpForm" class="field" style="gap:12px;">
                ${_renderMassOpFormHtml(state, massDraft)}
            </form>
        </section>

        <section class="card">
            <div class="row">
                <h2 class="card-title">История рассылок</h2>
                <button id="reloadBcBtn" type="button" class="btn btn-sm">Обновить</button>
            </div>
            <div id="bcPagerTop"></div>
            <div id="bcListContainer" class="stack-sm"></div>
            <div id="bcPagerBottom"></div>
        </section>
    `;

    const isStillActive = (reqId) =>
        reqId === _bcReqId &&
        root.isConnected &&
        state.adminTab === "broadcasts" &&
        state.mode === "admin";

    const reloadBroadcasts = async (silent = false, offset = state.broadcastsOffset || 0) => {
        const reqId = ++_bcReqId;
        try {
            const res = await api.listBroadcasts(offset, 15);
            if (!isStillActive(reqId)) {
                if (reqId === _bcReqId) clearBroadcastPollTimer();
                return;
            }
            state.broadcastsData = res.broadcasts || [];
            state.broadcastsTotal = res.total || 0;
            state.broadcastsOffset = res.offset || 0;
            state.broadcastsLimit = res.limit || 15;
            _renderHistoryList(root, root.querySelector("#bcListContainer"), state, reloadBroadcasts);
            _renderBcPagination(root, state, reloadBroadcasts);
            const hasRunning = state.broadcastsData.some((b) => b.status === "in_progress");
            if (hasRunning && _pollTimer === null) {
                _pollTimer = setInterval(() => {
                    if (!root.isConnected || state.adminTab !== "broadcasts" || state.mode !== "admin") {
                        clearBroadcastPollTimer();
                        return;
                    }
                    reloadBroadcasts(true, state.broadcastsOffset || 0);
                }, 4000);
            } else if (!hasRunning) {
                clearBroadcastPollTimer();
            }
        } catch (err) {
            if (err && err.code === "2fa_required") {
                clearBroadcastPollTimer();
                return;
            }
            if (reqId !== _bcReqId) return;
            clearBroadcastPollTimer();
            if (!root.isConnected || state.adminTab !== "broadcasts" || state.mode !== "admin") return;
            if (!silent) showToast(err.message || "Не удалось обновить список рассылок", "error");
        }
    };

    const bcTextInput = root.querySelector("#bcTextInput");
    const bcMediaInput = root.querySelector("#bcMediaInput");
    bcTextInput.addEventListener("input", () => _updateCharCounter(root));
    bcMediaInput.addEventListener("input", () => _updateCharCounter(root));
    _updateCharCounter(root);

    root.querySelector("#bcAudienceSelect").addEventListener("change", () => {
        ++_previewReqId;
        const hintEl = root.querySelector("#bcPreviewHint");
        if (hintEl) hintEl.textContent = "";
    });

    root.querySelector("#reloadBcBtn").addEventListener("click", () => reloadBroadcasts(false, state.broadcastsOffset || 0));

    root.querySelector("#bcPreviewBtn").addEventListener("click", async () => {
        const reqId = ++_previewReqId;
        const audience = root.querySelector("#bcAudienceSelect").value;
        const hintEl = root.querySelector("#bcPreviewHint");
        hintEl.textContent = "Подсчёт...";
        try {
            const res = await api.previewBroadcast(audience);
            if (reqId !== _previewReqId || !root.isConnected || state.adminTab !== "broadcasts" || state.mode !== "admin") return;
            hintEl.textContent = `Аудитория «${res.label}»: получат ${res.reachable_count} (всего в сегменте ${res.total_count})`;
        } catch (err) {
            if (reqId !== _previewReqId || !root.isConnected || state.adminTab !== "broadcasts" || state.mode !== "admin") return;
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
                const res = await api.startBroadcast({
                    target_audience: audience,
                    text,
                    media_url: mediaUrl,
                });
                showToast(res.message || "Рассылка запущена");
                if (root.isConnected && audience !== "test") {
                    const tEl = root.querySelector("#bcTextInput");
                    const mEl = root.querySelector("#bcMediaInput");
                    if (tEl) tEl.value = "";
                    if (mEl) mEl.value = "";
                    _updateCharCounter(root);
                }
                state.broadcastsOffset = 0;
                await reloadBroadcasts(false, 0);
            });
        };

        if (audience === "test") {
            try {
                await runLaunch();
            } catch (err) {
                showToast(err.message || "Ошибка запуска рассылки", "error");
            }
            return;
        }

        try {
            const preview = await withButtonLoading(launchBtn, "Подсчёт…", () => api.previewBroadcast(audience));
            if (!root.isConnected || state.adminTab !== "broadcasts" || state.mode !== "admin") return;
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

    const syncMassDraft = () => {
        const audEl = root.querySelector("#massAudienceSelect");
        const amtEl = root.querySelector("#massAmountInput");
        const scopeEl = root.querySelector("#massScopeSelect");
        const daysEl = root.querySelector("#massDaysInput");
        const reasonEl = root.querySelector("#massReasonInput");
        const notifyEl = root.querySelector("#massNotifyCheckbox");
        if (audEl) massDraft.audience = audEl.value;
        if (amtEl) massDraft.amount = amtEl.value;
        if (scopeEl) massDraft.scope = scopeEl.value;
        if (daysEl) massDraft.days = daysEl.value;
        if (reasonEl) massDraft.reason = reasonEl.value;
        if (notifyEl) massDraft.notify = notifyEl.checked;
    };

    const massForm = root.querySelector("#massOpForm");
    massForm.addEventListener("input", syncMassDraft);
    massForm.addEventListener("change", syncMassDraft);

    root.querySelectorAll("[data-masstab]").forEach((btn) => {
        btn.addEventListener("click", () => {
            syncMassDraft();
            state.massTab = btn.dataset.masstab;
            root.querySelectorAll("[data-masstab]").forEach((b) => {
                const isActive = b.dataset.masstab === state.massTab;
                b.classList.toggle("active", isActive);
                b.setAttribute("aria-pressed", isActive ? "true" : "false");
            });
            if (massForm) {
                massForm.innerHTML = _renderMassOpFormHtml(state, massDraft);
            }
        });
    });

    massForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        syncMassDraft();
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
            const rawAmount = root.querySelector("#massAmountInput").value.trim();
            const amount = /^\d+$/.test(rawAmount) ? Number(rawAmount) : NaN;
            if (!Number.isInteger(amount) || amount < 1 || amount > 50000) {
                showToast("Сумма должна быть целым числом от 1 до 50000 ₽", "error");
                return;
            }
            try {
                const preview = await withButtonLoading(submitBtn, "Подсчёт…", () =>
                    api.previewBroadcast(audience, {
                        service_scope: scope,
                        include_bot_blocked: true,
                    })
                );
                if (!root.isConnected || state.adminTab !== "broadcasts" || state.mode !== "admin") return;
                const totalCount = Number(preview.total_count ?? preview.reachable_count ?? 0);
                const reachableCount = Number(preview.reachable_count ?? 0);
                const totalRub = amount * totalCount;
                _confirmModal(
                    "Массовое начисление бонуса",
                    `Начислить +${amount} ₽ сегменту «${preview.label || audienceLabel}»?\nЗатронуто пользователей: ${totalCount} (из них получат push в боте: ${reachableCount}) · Итого: ${totalRub} ₽.\nДействие необратимо.`,
                    async () => {
                        const res = await api.massBonus({
                            target_audience: audience,
                            amount_rub: amount,
                            reason,
                            notify_users: notifyUsers,
                        });
                        state.usersData = null;
                        state.financesData = null;
                        state.financesLoadAttempted = false;
                        showToast(res.message || "Массовое начисление запущено");
                    },
                    "Начислить",
                    true
                );
            } catch (err) {
                showToast(err.message || "Ошибка предпросмотра аудитории", "error");
            }
        } else {
            const scope = root.querySelector("#massScopeSelect").value;
            const rawDays = root.querySelector("#massDaysInput").value.trim();
            const days = /^\d+$/.test(rawDays) ? Number(rawDays) : NaN;
            if (!Number.isInteger(days) || days < 1 || days > 365) {
                showToast("Срок должен быть целым числом от 1 до 365 дней", "error");
                return;
            }
            const scopeLabel = scope === "white_internet" ? "Белый интернет" : "Стандарт";
            try {
                const preview = await withButtonLoading(submitBtn, "Подсчёт…", () =>
                    api.previewBroadcast(audience, {
                        service_scope: scope,
                        include_bot_blocked: true,
                    })
                );
                if (!root.isConnected || state.adminTab !== "broadcasts" || state.mode !== "admin") return;
                const totalCount = Number(preview.total_count ?? preview.reachable_count ?? 0);
                const reachableCount = Number(preview.reachable_count ?? 0);
                _confirmModal(
                    "Массовое продление подписки",
                    `Продлить «${scopeLabel}» на +${days} дн. для сегмента «${preview.label || audienceLabel}»?\nЗатронуто пользователей: ${totalCount} (из них получат push в боте: ${reachableCount}).`,
                    async () => {
                        const res = await api.massExtend({
                            service_scope: scope,
                            target_audience: audience,
                            days,
                            reason,
                            notify_users: notifyUsers,
                        });
                        state.usersData = null;
                        state.financesData = null;
                        state.financesLoadAttempted = false;
                        showToast(res.message || "Массовое продление запущено");
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
        _pollTimer = setInterval(() => {
            if (!root.isConnected || state.adminTab !== "broadcasts" || state.mode !== "admin") {
                clearBroadcastPollTimer();
                return;
            }
            reloadBroadcasts(true, state.broadcastsOffset || 0);
        }, 4000);
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
        container.innerHTML = `<div class="muted" style="text-align:center; padding:16px;">Рассылок пока нет</div>`;
        return;
    }

    broadcasts.forEach((b) => {
        const row = document.createElement("div");
        row.className = "item-row";
        row.innerHTML = `
            <div class="row">
                <div style="display:flex; align-items:center; flex-wrap:wrap; gap:8px;">
                    <span style="font-weight:600;">#${b.id} · ${escapeHtml(b.label)}</span>
                    ${b.has_media ? `<span class="chip chip-muted">Фото</span>` : ""}
                    <span class="meta-text">${formatDate(b.created_at)}</span>
                </div>
                ${_statusChip(b.status)}
            </div>
            <div class="meta-text" style="word-break:break-word;">${escapeHtml(b.text_preview)}</div>
            <div class="row" style="gap:8px; flex-wrap:wrap;">
                <span class="tabular meta-text">
                    Успешно: <b>${b.success_count}</b> / ${b.total_count} (${b.progress_pct}%) · Ошибок: ${b.fail_count}
                </span>
                <div style="display:flex; gap:8px; align-items:center;">
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
                const mediaInput = root.querySelector("#bcMediaInput");
                if (textInput) {
                    const hasFullText = typeof b.text === "string" && b.text.length > 0;
                    const textToCopy = hasFullText ? b.text : String(b.text_preview || "");
                    textInput.value = textToCopy;
                    if (mediaInput) {
                        mediaInput.value = b.media_url ? String(b.media_url) : "";
                    }
                    _updateCharCounter(root);
                    textInput.focus();
                    const prefersReduced =
                        window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
                    textInput.scrollIntoView({ behavior: prefersReduced ? "auto" : "smooth", block: "center" });
                    const looksTruncated =
                        !hasFullText &&
                        (textToCopy.endsWith("...") ||
                            textToCopy.endsWith("…") ||
                            textToCopy.length >= 180);
                    if (looksTruncated) {
                        showToast("Скопировано превью (до 180 симв.), проверьте полный текст", "warn");
                    } else {
                        showToast("Текст скопирован в редактор");
                    }
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
                            const res = await api.cancelBroadcast(b.id);
                            showToast(res.message || "Рассылка остановлена");
                            await reloadBroadcasts(false);
                        });
                    },
                    "Остановить",
                    true,
                    `[data-cancel-bc="${b.id}"], [data-repeat-bc="${b.id}"], #reloadBcBtn`
                );
            });
        }
        container.appendChild(row);
    });
}
