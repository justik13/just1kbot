import {
    ORDER_KIND_MAP,
    ORDER_STATUS_MAP,
    formatAuditAction,
    formatLedgerEntryType,
    formatMetadataFlag,
    formatOrderAmountLabel,
    formatOrderPaymentBreakdown,
} from "../admin_labels.js?v=2.8.0";
import { escapeHtml, formatDate } from "../ui.js?v=2.8.0";

function _formatShortHwid(hwid) {
    const raw = String(hwid || "");
    if (raw.length <= 20) return raw;
    return `${raw.slice(0, 10)}…${raw.slice(-8)}`;
}

export function renderHistoryTabContent(tab, details) {
    if (tab === "orders") {
        const orders = details.recent_orders || [];
        const ordersHtml = !orders.length
            ? `<div class="muted" style="text-align:center; padding:16px;">Заказов нет</div>`
            : orders
                  .map((o) => {
                      const stInfo = ORDER_STATUS_MAP[o.status] || {
                          text: o.status || "—",
                          cls: "chip-muted",
                      };
                      const kindLabel = ORDER_KIND_MAP[o.kind] || o.kind || "";
                      const paymentBreakdown = formatOrderPaymentBreakdown(o);
                      const amountLabel = formatOrderAmountLabel(o);
                      const flags = Array.isArray(o.metadata_flags) ? o.metadata_flags : [];
                      const flagsHtml = flags
                          .map((flag) => {
                              const info = formatMetadataFlag(flag);
                              return `<span class="chip ${info.cls}">${escapeHtml(info.text)}</span>`;
                          })
                          .join("");
                      return `
                    <div class="item-row">
                        <div class="row">
                            <span style="font-weight:600; font-size:13px;">${escapeHtml(kindLabel)}${o.description ? ` · ${escapeHtml(o.description)}` : ""}</span>
                            <span class="tabular" style="font-weight:600; flex-shrink:0; white-space:nowrap;">${escapeHtml(amountLabel)}</span>
                        </div>
                        ${
                            paymentBreakdown
                                ? `<div class="meta-text">Оплата: ${escapeHtml(paymentBreakdown)}</div>`
                                : ""
                        }
                        <div class="history-row-meta">
                            <span>${formatDate(o.created_at)}</span>
                            <span class="chip ${stInfo.cls}">${escapeHtml(stInfo.text)}</span>
                        </div>
                        <div class="filter-pills" style="margin-top:4px;">
                            <button type="button" class="mono-copy" data-copy-val="${escapeHtml(o.id)}" data-copy-msg="ID заказа скопирован">ID: ${escapeHtml(String(o.id).slice(0, 8))}…</button>
                            ${
                                o.external_id
                                    ? `<button type="button" class="mono-copy" data-copy-val="${escapeHtml(o.external_id)}" data-copy-msg="ID ЮKassa скопирован">ЮKassa: ${escapeHtml(String(o.external_id).slice(0, 12))}…</button>`
                                    : ""
                            }
                            ${flagsHtml}
                        </div>
                    </div>
                `;
                  })
                  .join("");
        return `
            ${ordersHtml}
            <button id="detOpenFinancesBtn" type="button" class="btn btn-sm" style="align-self:flex-start; margin-top:4px;">Все заказы в Финансах →</button>
        `;
    }

    if (tab === "ledger") {
        const entries = details.recent_ledger || [];
        if (!entries.length) {
            return `<div class="muted" style="text-align:center; padding:16px;">Операций по балансу нет</div>`;
        }
        return entries
            .map((entry) => {
                const amt = Number(entry.amount || 0);
                const signStr = amt > 0 ? `+${amt} ₽` : `${amt} ₽`;
                const amtCls = amt > 0 ? "amount-pos" : amt < 0 ? "amount-err" : "tabular";
                const bucketLabel = entry.bucket === "bonus" ? "бонус" : "основной";
                const typeLabel = formatLedgerEntryType(entry.entry_type);
                return `
                    <div class="item-row">
                        <div class="row">
                            <span style="font-weight:600; font-size:13px;">${escapeHtml(typeLabel)} <span class="chip chip-muted">${bucketLabel}</span></span>
                            <span class="${amtCls}" style="font-weight:600;">${escapeHtml(signStr)}</span>
                        </div>
                        ${
                            entry.description
                                ? `<div class="meta-text">${escapeHtml(entry.description)}</div>`
                                : ""
                        }
                        <div class="history-row-meta">
                            <span>${formatDate(entry.created_at)}</span>
                        </div>
                    </div>
                `;
            })
            .join("");
    }

    if (tab === "devices") {
        const events = details.recent_device_events || [];
        if (!events.length) {
            return `<div class="muted" style="text-align:center; padding:16px;">Событий устройств нет</div>`;
        }
        return events
            .map((ev) => {
                const title = [ev.device_model, ev.platform].filter(Boolean).join(" · ") || "Устройство";
                return `
                    <div class="item-row">
                        <div class="row">
                            <span style="font-weight:600; font-size:13px;">${escapeHtml(title)}</span>
                            <span class="meta-text">${formatDate(ev.created_at)}</span>
                        </div>
                        ${
                            ev.hwid
                                ? `<div style="margin-top:4px;"><button type="button" class="mono-copy" data-copy-val="${escapeHtml(ev.hwid)}" data-copy-msg="HWID скопирован">${escapeHtml(_formatShortHwid(ev.hwid))}</button></div>`
                                : ""
                        }
                    </div>
                `;
            })
            .join("");
    }

    const audits = details.recent_audit || [];
    const auditsHtml = !audits.length
        ? `<div class="muted" style="text-align:center; padding:16px;">Записей аудита нет</div>`
        : audits
              .map((a) => {
                  const actionLabel = formatAuditAction(a.action);
                  const detailsStr =
                      typeof a.details === "string"
                          ? a.details
                          : a.details
                            ? JSON.stringify(a.details)
                            : "";
                  return `
                <div class="item-row">
                    <div class="row">
                        <span class="chip chip-muted">${escapeHtml(actionLabel)}</span>
                        <span class="meta-text">${formatDate(a.created_at)}</span>
                    </div>
                    ${
                        detailsStr
                            ? `<div class="mono meta-text" style="white-space:pre-wrap; word-break:break-word;">${escapeHtml(detailsStr)}</div>`
                            : ""
                    }
                </div>
            `;
              })
              .join("");
    return `
        ${auditsHtml}
        <button id="detOpenAuditBtn" type="button" class="btn btn-sm" style="align-self:flex-start; margin-top:4px;">Весь аудит пользователя →</button>
    `;
}
