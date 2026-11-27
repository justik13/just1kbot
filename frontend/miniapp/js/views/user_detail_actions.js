import { api } from "../api.js?v=2.8.0";
import { renderHistoryTabContent } from "./user_detail_history.js?v=2.8.0";
import {
    copyText,
    showConfirmModal,
    showToast,
    withButtonLoading,
} from "../ui.js?v=2.8.0";

function _formatShortHwid(hwid) {
    const s = String(hwid || "");
    if (s.length <= 16) return s;
    return `${s.slice(0, 8)}…${s.slice(-6)}`;
}

export function bindUserDetailActions({
    root,
    details,
    state,
    uname,
    debt,
    stdLimit,
    wiLimit,
    origFinancialHold,
    origTopupBlocked,
    bonusForm,
    stdForm,
    wiForm,
    wiTrafficForm,
    trafficSelect,
    wiGbField,
    dmForm,
    restrForm,
    getActiveHistoryTab,
    setActiveHistoryTab,
    refreshAll,
    schedulePostModalFocus,
}) {
    const forgiveDebtBtn = root.querySelector("#detForgiveDebtBtn");
    if (forgiveDebtBtn) {
        forgiveDebtBtn.addEventListener("click", () => {
            showConfirmModal({
                title: "Погасить задолженность?",
                message: `Списать долг ${debt} ₽ пользователю ${uname}?`,
                confirmText: "Погасить долг",
                cancelText: "Отмена",
                isDestructive: true,
                restoreFocusSelector: "#userDetailBackBtn",
                onConfirm: async () => {
                    const res = await api.adjustUser(details.id, { action: "forgive_debt" });
                    showToast(res.message || "Задолженность списана");
                    await refreshAll();
                    schedulePostModalFocus("#userDetailBackBtn");
                },
            });
        });
    }

    const bonusAmountInput = bonusForm.querySelector("input[name='amount_rub']");
    bonusAmountInput.addEventListener("input", () => bonusAmountInput.removeAttribute("aria-invalid"));
    bonusForm.querySelectorAll("[data-add]").forEach((b) => {
        b.addEventListener("click", () => {
            bonusAmountInput.value = b.dataset.add;
            bonusAmountInput.removeAttribute("aria-invalid");
        });
    });
    bonusForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const rawAmount = bonusAmountInput.value.trim();
        const amount = /^-?\d+$/.test(rawAmount) ? Number(rawAmount) : NaN;
        if (!Number.isInteger(amount) || amount === 0 || amount < -100000 || amount > 100000) {
            bonusAmountInput.setAttribute("aria-invalid", "true");
            bonusAmountInput.focus();
            showToast("Укажите ненулевое целое число от -100000 до 100000 ₽", "error");
            return;
        }
        const note = bonusForm.querySelector("input[name='note']").value.trim() || null;
        const notifyUser = Boolean(bonusForm.querySelector("input[name='notify_user']")?.checked);
        const sendNoteToUser = Boolean(bonusForm.querySelector("input[name='send_note_to_user']")?.checked);
        const isNegative = amount < 0;
        showConfirmModal({
            title: isNegative ? "Списать средства с баланса?" : "Начислить бонус на баланс?",
            message: `${isNegative ? "Списание" : "Начисление"}: ${amount > 0 ? `+${amount}` : amount} ₽ для ${uname}.${notifyUser ? " Пользователь получит уведомление в Telegram." : ""}`,
            confirmText: isNegative ? "Списать" : "Начислить",
            cancelText: "Отмена",
            isDestructive: isNegative,
            restoreFocusSelector: "#userBonusAmount",
            onConfirm: async () => {
                const res = await api.adjustUser(details.id, {
                    action: "balance",
                    amount_rub: amount,
                    note,
                    notify_user: notifyUser,
                    send_note_to_user: sendNoteToUser,
                });
                showToast(res.message || "Баланс обновлён");
                bonusForm.reset();
                await refreshAll();
                schedulePostModalFocus("#userBonusAmount");
            },
        });
    });

    const stdDaysInput = stdForm.querySelector("input[name='days']");
    const stdLimitInput = stdForm.querySelector("input[name='device_limit']");
    const toggleStdEditBtn = root.querySelector("#toggleStdEditBtn");
    stdDaysInput.addEventListener("input", () => stdDaysInput.removeAttribute("aria-invalid"));
    stdLimitInput.addEventListener("input", () => stdLimitInput.removeAttribute("aria-invalid"));
    toggleStdEditBtn.addEventListener("click", () => {
        stdForm.hidden = !stdForm.hidden;
        toggleStdEditBtn.setAttribute("aria-expanded", String(!stdForm.hidden));
    });
    stdForm.querySelectorAll("[data-std-days]").forEach((b) => {
        b.addEventListener("click", () => {
            stdDaysInput.value = b.dataset.stdDays;
            stdDaysInput.removeAttribute("aria-invalid");
        });
    });
    stdForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const rawDays = stdDaysInput.value.trim();
        const rawLimit = stdLimitInput.value.trim();
        if (rawDays !== "" && !/^-?\d+$/.test(rawDays)) {
            stdDaysInput.setAttribute("aria-invalid", "true");
            stdDaysInput.focus();
            showToast("Укажите целое количество дней (от -3650 до 3650)", "error");
            return;
        }
        const days = rawDays === "" ? 0 : Number(rawDays);
        if (!Number.isInteger(days) || days < -3650 || days > 3650) {
            stdDaysInput.setAttribute("aria-invalid", "true");
            stdDaysInput.focus();
            showToast("Укажите корректное количество дней (от -3650 до 3650)", "error");
            return;
        }
        const deviceLimit = /^\d+$/.test(rawLimit) ? Number(rawLimit) : NaN;
        if (!Number.isInteger(deviceLimit) || deviceLimit < 2 || deviceLimit > 10) {
            stdLimitInput.setAttribute("aria-invalid", "true");
            stdLimitInput.focus();
            showToast("Лимит устройств должен быть целым числом от 2 до 10", "error");
            return;
        }
        const limitChanged = deviceLimit !== Number(stdLimit);
        if (days === 0 && !limitChanged) {
            stdDaysInput.setAttribute("aria-invalid", "true");
            stdDaysInput.focus();
            showToast("Укажите ненулевое количество дней или измените лимит устройств", "error");
            return;
        }
        const notifyUser = Boolean(stdForm.querySelector("input[name='notify_user']")?.checked);
        const payload = { action: "grant_standard", days, notify_user: notifyUser };
        if (days >= 0 || limitChanged) {
            payload.device_limit = deviceLimit;
        }
        const execute = async (fromModal = false) => {
            const btn = stdForm.querySelector("button[type='submit']");
            await withButtonLoading(btn, "Сохранение...", async () => {
                const res = await api.adjustUser(details.id, payload);
                showToast(res.message || "Подписка Стандарт обновлена");
                await refreshAll({
                    focusSelector: fromModal ? null : "#toggleStdEditBtn",
                });
                if (fromModal) schedulePostModalFocus("#toggleStdEditBtn");
            });
        };

        if (days < 0) {
            showConfirmModal({
                title: "Сократить подписку Стандарт?",
                message: `Срок подписки Стандарт пользователя ${uname} будет уменьшен на ${Math.abs(days)} дн. Если срок истечёт, доступ будет отключён.`,
                confirmText: "Сократить",
                cancelText: "Отмена",
                isDestructive: true,
                restoreFocusSelector: "#toggleStdEditBtn",
                onConfirm: () => execute(true),
            });
            return;
        }

        try {
            await execute(false);
        } catch (err) {
            showToast(err.message || "Ошибка обновления подписки", "error");
        }
    });

    const wiDaysInput = wiForm.querySelector("input[name='days']");
    const wiLimitInput = wiForm.querySelector("input[name='device_limit']");
    const toggleWiEditBtn = root.querySelector("#toggleWiEditBtn");
    const toggleWiTrafficBtn = root.querySelector("#toggleWiTrafficBtn");
    wiDaysInput.addEventListener("input", () => wiDaysInput.removeAttribute("aria-invalid"));
    wiLimitInput.addEventListener("input", () => wiLimitInput.removeAttribute("aria-invalid"));
    toggleWiEditBtn.addEventListener("click", () => {
        wiForm.hidden = !wiForm.hidden;
        if (!wiForm.hidden) wiTrafficForm.hidden = true;
        toggleWiEditBtn.setAttribute("aria-expanded", String(!wiForm.hidden));
        toggleWiTrafficBtn.setAttribute("aria-expanded", String(!wiTrafficForm.hidden));
    });
    toggleWiTrafficBtn.addEventListener("click", () => {
        wiTrafficForm.hidden = !wiTrafficForm.hidden;
        if (!wiTrafficForm.hidden) wiForm.hidden = true;
        toggleWiTrafficBtn.setAttribute("aria-expanded", String(!wiTrafficForm.hidden));
        toggleWiEditBtn.setAttribute("aria-expanded", String(!wiForm.hidden));
    });
    wiForm.querySelectorAll("[data-wi-days]").forEach((b) => {
        b.addEventListener("click", () => {
            wiDaysInput.value = b.dataset.wiDays;
            wiDaysInput.removeAttribute("aria-invalid");
        });
    });
    wiForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const rawDays = wiDaysInput.value.trim();
        const rawLimit = wiLimitInput.value.trim();
        if (rawDays !== "" && !/^-?\d+$/.test(rawDays)) {
            wiDaysInput.setAttribute("aria-invalid", "true");
            wiDaysInput.focus();
            showToast("Укажите целое количество дней (от -3650 до 3650)", "error");
            return;
        }
        const days = rawDays === "" ? 0 : Number(rawDays);
        if (!Number.isInteger(days) || days < -3650 || days > 3650) {
            wiDaysInput.setAttribute("aria-invalid", "true");
            wiDaysInput.focus();
            showToast("Укажите корректное количество дней (от -3650 до 3650)", "error");
            return;
        }
        const deviceLimit = /^\d+$/.test(rawLimit) ? Number(rawLimit) : NaN;
        if (!Number.isInteger(deviceLimit) || deviceLimit < 1 || deviceLimit > 3) {
            wiLimitInput.setAttribute("aria-invalid", "true");
            wiLimitInput.focus();
            showToast("Количество слотов должно быть целым числом от 1 до 3", "error");
            return;
        }
        const limitChanged = deviceLimit !== Number(wiLimit);
        if (days === 0 && !limitChanged) {
            wiDaysInput.setAttribute("aria-invalid", "true");
            wiDaysInput.focus();
            showToast("Укажите ненулевое количество дней или измените количество слотов", "error");
            return;
        }
        const notifyUser = Boolean(wiForm.querySelector("input[name='notify_user']")?.checked);
        const payload = { action: "grant_wi", days, notify_user: notifyUser };
        if (days >= 0 || limitChanged) {
            payload.device_limit = deviceLimit;
        }
        const execute = async (fromModal = false) => {
            const btn = wiForm.querySelector("button[type='submit']");
            await withButtonLoading(btn, "Сохранение...", async () => {
                const res = await api.adjustUser(details.id, payload);
                showToast(res.message || "Белый интернет обновлён");
                await refreshAll({
                    focusSelector: fromModal ? null : "#toggleWiEditBtn",
                });
                if (fromModal) schedulePostModalFocus("#toggleWiEditBtn");
            });
        };

        if (days < 0) {
            showConfirmModal({
                title: "Сократить Белый интернет?",
                message: `Срок подписки «Белый интернет» пользователя ${uname} будет уменьшен на ${Math.abs(days)} дн. Если срок истечёт, доступ будет отозван.`,
                confirmText: "Сократить",
                cancelText: "Отмена",
                isDestructive: true,
                restoreFocusSelector: "#toggleWiEditBtn",
                onConfirm: () => execute(true),
            });
            return;
        }

        try {
            await execute(false);
        } catch (err) {
            showToast(err.message || "Ошибка обновления подписки", "error");
        }
    });

    const wiGbInput = wiTrafficForm.querySelector("input[name='gb']");
    wiGbInput.addEventListener("input", () => wiGbInput.removeAttribute("aria-invalid"));
    trafficSelect.addEventListener("change", () => {
        wiGbField.hidden = trafficSelect.value === "reset_used";
    });
    wiTrafficForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const notifyUser = Boolean(wiTrafficForm.querySelector("input[name='notify_user']")?.checked);
        const payload = {
            action: "wi_traffic",
            traffic_action: trafficSelect.value,
            notify_user: notifyUser,
        };
        if (payload.traffic_action !== "reset_used") {
            const rawGb = wiGbInput.value.trim();
            const gb = /^\d+$/.test(rawGb) ? Number(rawGb) : NaN;
            if (!Number.isInteger(gb) || gb < 1 || gb > 5000) {
                wiGbInput.setAttribute("aria-invalid", "true");
                wiGbInput.focus();
                showToast("Укажите целое число ГБ от 1 до 5000", "error");
                return;
            }
            payload.gb = gb;
        }
        const execute = async (fromModal = false) => {
            const btn = wiTrafficForm.querySelector("button[type='submit']");
            await withButtonLoading(btn, "Применение...", async () => {
                const res = await api.adjustUser(details.id, payload);
                showToast(res.message || "Трафик обновлён");
                await refreshAll({
                    focusSelector: fromModal ? null : "#toggleWiTrafficBtn",
                });
                if (fromModal) schedulePostModalFocus("#toggleWiTrafficBtn");
            });
        };

        if (payload.traffic_action === "reset_used") {
            showConfirmModal({
                title: "Сбросить использованный трафик?",
                message: `Счётчик использованного трафика «Белый интернет» пользователя ${uname} будет обнулён.`,
                confirmText: "Сбросить",
                cancelText: "Отмена",
                isDestructive: true,
                restoreFocusSelector: "#toggleWiTrafficBtn",
                onConfirm: () => execute(true),
            });
            return;
        }

        try {
            await execute(false);
        } catch (err) {
            showToast(err.message || "Ошибка обновления трафика", "error");
        }
    });

    dmForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const text = dmForm.querySelector("textarea[name='text']").value.trim();
        if (!text) {
            showToast("Введите текст сообщения", "error");
            return;
        }
        const btn = dmForm.querySelector("button[type='submit']");
        try {
            await withButtonLoading(btn, "Отправка...", async () => {
                const res = await api.sendUserMessage(details.id, text);
                dmForm.reset();
                showToast(res.message || "Сообщение отправлено");
            });
        } catch (err) {
            showToast(err.message || "Ошибка отправки сообщения", "error");
        }
    });

    const devicesContainer = root.querySelector("#detDevicesList");
    devicesContainer.querySelectorAll("[data-copy-hwid]").forEach((btn) => {
        btn.addEventListener("click", () => {
            copyText(btn.dataset.copyHwid, "HWID скопирован");
        });
    });
    devicesContainer.querySelectorAll("[data-del-hwid]").forEach((btn) => {
        btn.addEventListener("click", () => {
            const hwid = btn.dataset.delHwid;
            showConfirmModal({
                title: "Удалить устройство?",
                message: `Устройство ${_formatShortHwid(hwid)} будет отвязано от аккаунта ${uname}.`,
                confirmText: "Удалить",
                cancelText: "Отмена",
                isDestructive: true,
                restoreFocusSelector: "#detDevicesList",
                onConfirm: async () => {
                    const res = await api.deleteUserDevice(details.id, hwid);
                    showToast(res.message || "Устройство удалено");
                    await refreshAll();
                    schedulePostModalFocus("#detDevicesList button, #detResetHwidBtn");
                },
            });
        });
    });

    restrForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const financialHold = Boolean(restrForm.querySelector("input[name='financial_hold']")?.checked);
        const topupBlocked = Boolean(restrForm.querySelector("input[name='topup_blocked']")?.checked);
        const note = restrForm.querySelector("input[name='note']")?.value.trim() || null;
        const holdChanged = financialHold !== origFinancialHold;
        const topupChanged = topupBlocked !== origTopupBlocked;
        if (!holdChanged && !topupChanged) {
            showToast("Ограничения не изменились", "warn");
            return;
        }
        const payload = { action: "restrictions", note };
        if (holdChanged) payload.financial_hold = financialHold;
        if (topupChanged) payload.topup_blocked = topupBlocked;
        const enablingRestriction = (holdChanged && financialHold) || (topupChanged && topupBlocked);
        showConfirmModal({
            title: "Сохранить ограничения?",
            message: `Применить настройки ограничений для ${uname}?${holdChanged && financialHold ? " Финансовая блокировка ограничит доступ." : ""}`,
            confirmText: "Сохранить",
            cancelText: "Отмена",
            isDestructive: enablingRestriction,
            restoreFocusSelector: "#restrHoldInput",
            onConfirm: async () => {
                const res = await api.adjustUser(details.id, payload);
                showToast(res.message || "Ограничения сохранены");
                await refreshAll();
                schedulePostModalFocus("#restrHoldInput");
            },
        });
    });

    const historyTabsEl = root.querySelector("#userHistoryTabs");
    const historyListEl = root.querySelector("#userHistoryList");
    historyListEl.addEventListener("click", (e) => {
        const copyBtnEl = e.target.closest("[data-copy-val]");
        if (copyBtnEl && historyListEl.contains(copyBtnEl)) {
            copyText(copyBtnEl.dataset.copyVal, copyBtnEl.dataset.copyMsg || "Скопировано");
            return;
        }
        const finBtn = e.target.closest("#detOpenFinancesBtn");
        if (finBtn && historyListEl.contains(finBtn)) {
            state?.onOpenFinances?.(String(details.telegram_id));
            return;
        }
        const auditBtn = e.target.closest("#detOpenAuditBtn");
        if (auditBtn && historyListEl.contains(auditBtn)) {
            state?.onOpenAudit?.(String(details.id));
        }
    });

    const historyTabButtons = Array.from(historyTabsEl.querySelectorAll("[data-htab]"));
    const activateHistoryTab = (nextTab, focusTab = false) => {
        setActiveHistoryTab(nextTab);
        const currentTab = getActiveHistoryTab();
        historyTabButtons.forEach((b) => {
            const isActive = b.dataset.htab === currentTab;
            b.classList.toggle("active", isActive);
            b.setAttribute("aria-selected", isActive ? "true" : "false");
            b.setAttribute("tabindex", isActive ? "0" : "-1");
            if (isActive && focusTab) {
                b.focus({ preventScroll: true });
            }
        });
        historyListEl.setAttribute("aria-labelledby", `htab-${currentTab}`);
        historyListEl.innerHTML = renderHistoryTabContent(currentTab, details);
    };

    historyTabButtons.forEach((btn, idx) => {
        btn.addEventListener("click", () => {
            activateHistoryTab(btn.dataset.htab, false);
        });
        btn.addEventListener("keydown", (e) => {
            let targetIdx = null;
            if (e.key === "ArrowRight") {
                targetIdx = (idx + 1) % historyTabButtons.length;
            } else if (e.key === "ArrowLeft") {
                targetIdx = (idx - 1 + historyTabButtons.length) % historyTabButtons.length;
            } else if (e.key === "Home") {
                targetIdx = 0;
            } else if (e.key === "End") {
                targetIdx = historyTabButtons.length - 1;
            }
            if (targetIdx !== null) {
                e.preventDefault();
                activateHistoryTab(historyTabButtons[targetIdx].dataset.htab, true);
            }
        });
    });

    const getDangerNotify = () => Boolean(root.querySelector("#dangerNotifyUser")?.checked);

    root.querySelector("#detResetHwidBtn").addEventListener("click", () => {
        const notifyUser = getDangerNotify();
        showConfirmModal({
            title: "Сбросить привязку устройств?",
            message: `Все привязанные устройства пользователя ${uname} будут отвязаны. Ему потребуется заново обновить подписку в приложении.`,
            confirmText: "Сбросить HWID",
            cancelText: "Отмена",
            isDestructive: true,
            restoreFocusSelector: "#detResetHwidBtn",
            onConfirm: async () => {
                const res = await api.resetUserDevices(details.id, { notify_user: notifyUser });
                showToast(res.message || "Устройства сброшены");
                await refreshAll();
                schedulePostModalFocus("#detResetHwidBtn");
            },
        });
    });

    root.querySelector("#detRotateTokenBtn").addEventListener("click", () => {
        const notifyUser = getDangerNotify();
        showConfirmModal({
            title: "Перевыпустить токен подписки?",
            message: "Старая ссылка подключения перестанет работать. Пользователю нужно будет заново добавить подписку.",
            confirmText: "Перевыпустить",
            cancelText: "Отмена",
            isDestructive: true,
            restoreFocusSelector: "#detRotateTokenBtn",
            onConfirm: async () => {
                const res = await api.rotateUserToken(details.id, { notify_user: notifyUser });
                showToast(res.message || "Токен перевыпущен");
                await refreshAll();
                schedulePostModalFocus("#detRotateTokenBtn");
            },
        });
    });
}
