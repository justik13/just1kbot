import { copyText, escapeHtml, openExternalLink, openModal } from "../ui.js?v=2.8.0";

let _guidesDataCache = null;
let _guidesDataPromise = null;

export async function loadGuidesData() {
    if (_guidesDataCache) return _guidesDataCache;
    if (!_guidesDataPromise) {
        _guidesDataPromise = fetch("/app/assets/guides_data.json?v=2.8.0", { cache: "no-cache" })
            .then((resp) => {
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                return resp.json();
            })
            .then((data) => {
                _guidesDataCache = data;
                return data;
            })
            .catch((err) => {
                _guidesDataPromise = null;
                throw err;
            });
    }
    return _guidesDataPromise;
}

export function getPlatformApps(platform) {
    const appsMap = (_guidesDataCache && _guidesDataCache.platform_apps) || {};
    const links = (_guidesDataCache && _guidesDataCache.links) || {};
    const list = appsMap[platform] || appsMap.ios || [];
    return list.map((item) => ({
        name: item.name,
        sub: item.sub,
        url: links[item.link_key] || "",
    })).filter((item) => Boolean(item.url));
}

function _openImageModal(src, title) {
    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.style.gap = "8px";
    wrap.innerHTML = `<img src="${escapeHtml(src)}" alt="${escapeHtml(title)}" class="guide-modal-img">`;
    openModal(title || "Скриншот", wrap);
}

function _renderVideoCard(video) {
    if (!video) return "";
    return `
        <details class="guide-details guide-video-priority">
            <summary class="guide-summary guide-video-summary">
                <span class="guide-video-play" aria-hidden="true">
                    <svg viewBox="0 0 24 24" fill="currentColor"><polygon points="6 4 20 12 6 20 6 4"/></svg>
                </span>
                <span class="guide-video-info">
                    <span class="guide-video-title-row">
                        <span class="guide-video-title">${escapeHtml(video.title)}</span>
                        <span class="guide-video-tag">${escapeHtml(video.tag)}</span>
                    </span>
                    <span class="guide-video-meta">${escapeHtml(video.meta)}</span>
                </span>
                <span class="disclosure-pill"><span>Смотреть</span><svg class="guide-video-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></span>
            </summary>
            <div class="guide-body">
                <video class="guide-video" controls playsinline preload="none" poster="/app/assets/guides/${escapeHtml(video.poster_file)}">
                    <source src="/app/assets/guides/${escapeHtml(video.video_file)}" type="video/mp4">
                    <track kind="captions" srclang="ru" label="Русские субтитры" src="${escapeHtml(video.vtt_src)}">
                </video>
                <p class="muted" style="font-size:12px; margin-top:6px;">${escapeHtml(video.caption)}</p>
            </div>
        </details>
    `;
}

function _renderStepCard(step, guideData) {
    const links = guideData.links || {};
    const bodyHtml = step.html_miniapp || step.html || "";
    const shot = step.shot;
    const kzRows = (step.has_kz_address && guideData.kz_billing_address && guideData.kz_billing_address.rows) || [];
    const buttons = step.miniapp_buttons || [];

    return `
        <div class="guide-step-card">
            <div class="guide-step-head"><span class="guide-step-num">${escapeHtml(step.num)}</span><span>${escapeHtml(step.title)}</span></div>
            ${bodyHtml ? `<p class="muted">${bodyHtml}</p>` : ""}
            ${step.linux_cmd ? `<div class="sub-url-box" style="font-size:12px;">${escapeHtml(step.linux_cmd)}</div>` : ""}
            ${kzRows.length ? `
                <div class="guide-kv-list">
                    ${kzRows.map((row) => `
                        <div class="guide-kv-row"><span>${escapeHtml(row.label)}</span><span class="guide-kv-val"><code>${escapeHtml(row.display)}</code><button type="button" class="btn btn-sm guide-kv-copy" data-copy="${escapeHtml(row.copy)}">Копировать</button></span></div>
                    `).join("")}
                </div>
            ` : ""}
            ${(buttons.length || step.linux_cmd) ? `
                <div class="connect-pair">
                    ${step.linux_cmd ? `<button type="button" class="btn btn-sm" data-copy="${escapeHtml(step.linux_cmd)}">Копировать команду</button>` : ""}
                    ${buttons.map((b) => {
                        const url = links[b.link_key] || "";
                        const cls = b.primary ? "btn btn-primary btn-sm" : "btn btn-sm";
                        return url ? `<button type="button" class="${cls}" data-ext="${escapeHtml(url)}">${escapeHtml(b.label)}</button>` : "";
                    }).join("")}
                </div>
            ` : ""}
            ${shot ? `
                <button type="button" class="guide-shot-btn" data-shot="/app/assets/guides/${escapeHtml(shot.file)}" data-shot-title="${escapeHtml(shot.alt)}">
                    <img src="/app/assets/guides/${escapeHtml(shot.file)}" alt="${escapeHtml(shot.alt)}" loading="lazy">
                    <span class="guide-shot-hint">Увеличить</span>
                </button>
            ` : ""}
            ${step.note ? `<p class="muted">${step.note}</p>` : ""}
        </div>
    `;
}

function _renderPlatformGuideContent(sectionData, selectedMethodId, guideData) {
    if (!sectionData || !Array.isArray(sectionData.methods)) return "";
    const method = sectionData.methods.find((m) => m.id === selectedMethodId) || sectionData.methods[0];
    if (!method) return "";

    const parts = [];
    if (method.has_video) {
        parts.push(_renderVideoCard(guideData.video));
    }
    for (const c of (method.callouts || [])) {
        const cls = c.tone === "warn" ? "notice-box notice-warn" : "notice-box";
        parts.push(`<div class="${cls}"><span>${c.html}</span></div>`);
    }
    const methodSteps = method.steps || [];
    for (const st of methodSteps) {
        parts.push(_renderStepCard(st, guideData));
    }
    for (const [idx, st] of (sectionData.common_steps || []).entries()) {
        parts.push(_renderStepCard({ ...st, num: methodSteps.length + idx + 1 }, guideData));
    }
    return parts.join("");
}

function _renderFaqContent(faqItems, deviceLimit) {
    return (faqItems || [])
        .map((item, idx) => {
            const answer = String(item.a_html || "").replace("{device_limit}", String(deviceLimit || 2));
            return `
                <details class="guide-details" ${idx === 0 ? "open" : ""}>
                    <summary class="guide-summary">
                        <span>${escapeHtml(item.q)}</span>
                        <span class="disclosure-pill"><span>Ответ</span><svg class="guide-video-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></span>
                    </summary>
                    <div class="guide-body">
                        <p class="muted">${answer}</p>
                    </div>
                </details>
            `;
        })
        .join("");
}

function _pauseGuideVideos(container) {
    if (!container) return;
    container.querySelectorAll("video").forEach((v) => {
        try {
            if (!v.paused) v.pause();
        } catch {
            // Non-critical
        }
    });
}

export function renderUserGuidesView(root, state, data, onBack) {
    const validTabs = ["ios", "android", "desktop", "faq"];
    if (!validTabs.includes(state.guideTab)) {
        if (state.platform === "android") state.guideTab = "android";
        else if (state.platform === "windows" || state.platform === "macos") state.guideTab = "desktop";
        else state.guideTab = "ios";
    }
    if (!state.guideIosMethod || state.guideIosMethod === "tf") state.guideIosMethod = "region";
    if (!state.guideAndroidMethod) state.guideAndroidMethod = "play";
    if (!state.guideDesktopMethod) {
        state.guideDesktopMethod = state.platform === "macos" ? "mac" : "win";
    }

    if (!_guidesDataCache) {
        root.innerHTML = `
            <div class="detail-header-nav">
                <button id="guidesBackBtn" type="button" class="btn btn-sm btn-ghost">← Назад</button>
                <span class="detail-sticky-meta">Центр инструкций</span>
            </div>
            <section class="card card-center"><div class="muted">Загрузка инструкций...</div></section>
        `;
        root.querySelector("#guidesBackBtn")?.addEventListener("click", onBack);
        loadGuidesData()
            .then(() => {
                if (root.isConnected && state.tab === "guides") {
                    renderUserGuidesView(root, state, data, onBack);
                }
            })
            .catch(() => {
                if (root.isConnected && state.tab === "guides") {
                    root.innerHTML = `
                        <div class="detail-header-nav">
                            <button id="guidesBackBtn" type="button" class="btn btn-sm btn-ghost">← Назад</button>
                            <span class="detail-sticky-meta">Центр инструкций</span>
                        </div>
                        <section class="card card-center">
                            <div class="muted">Не удалось загрузить инструкции</div>
                        </section>
                    `;
                    root.querySelector("#guidesBackBtn")?.addEventListener("click", onBack);
                }
            });
        return;
    }

    const guideData = _guidesDataCache;
    const stdLimit = Number((data && data.standard && data.standard.device_limit) || 2);

    let subTabsHtml = "";
    let contentHtml = "";

    if (state.guideTab === "ios") {
        const methods = guideData.ios?.methods || [];
        subTabsHtml = `
            <div class="filter-pills" role="group" aria-label="Способ установки iOS">
                ${methods.map((m) => `
                    <button type="button" class="pill-btn ${state.guideIosMethod === m.id ? "active" : ""}" aria-pressed="${state.guideIosMethod === m.id ? "true" : "false"}" data-ios-m="${escapeHtml(m.id)}">${escapeHtml(m.title)}</button>
                `).join("")}
            </div>
        `;
        contentHtml = _renderPlatformGuideContent(guideData.ios, state.guideIosMethod, guideData);
    } else if (state.guideTab === "android") {
        const methods = guideData.android?.methods || [];
        subTabsHtml = `
            <div class="filter-pills" role="group" aria-label="Способ установки Android">
                ${methods.map((m) => `
                    <button type="button" class="pill-btn ${state.guideAndroidMethod === m.id ? "active" : ""}" aria-pressed="${state.guideAndroidMethod === m.id ? "true" : "false"}" data-and-m="${escapeHtml(m.id)}">${escapeHtml(m.title)}</button>
                `).join("")}
            </div>
        `;
        contentHtml = _renderPlatformGuideContent(guideData.android, state.guideAndroidMethod, guideData);
    } else if (state.guideTab === "desktop") {
        const methods = guideData.desktop?.methods || [];
        subTabsHtml = `
            <div class="filter-pills" role="group" aria-label="Операционная система">
                ${methods.map((m) => `
                    <button type="button" class="pill-btn ${state.guideDesktopMethod === m.id ? "active" : ""}" aria-pressed="${state.guideDesktopMethod === m.id ? "true" : "false"}" data-desk-m="${escapeHtml(m.id)}">${escapeHtml(m.title)}</button>
                `).join("")}
            </div>
        `;
        contentHtml = _renderPlatformGuideContent(guideData.desktop, state.guideDesktopMethod, guideData);
    } else {
        contentHtml = _renderFaqContent(guideData.faq, stdLimit);
    }

    root.innerHTML = `
        <div class="detail-header-nav">
            <button id="guidesBackBtn" type="button" class="btn btn-sm btn-ghost">← Назад</button>
            <span class="detail-sticky-meta">Центр инструкций</span>
        </div>

        <section class="hero-title-group">
            <h1 class="hero-headline-lg">Инструкции</h1>
            <p class="hero-sub">Пошаговая установка и настройка на вашем устройстве</p>
        </section>

        <div class="seg-group" role="tablist" aria-label="Платформы">
            <button id="guideTab_ios" type="button" class="seg-btn seg-btn-with-icon ${state.guideTab === "ios" ? "active" : ""}" role="tab" aria-selected="${state.guideTab === "ios" ? "true" : "false"}" aria-controls="guideTabPanel" tabindex="${state.guideTab === "ios" ? "0" : "-1"}" data-gtab="ios">
                <svg class="seg-btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="6" y="2" width="12" height="20" rx="2.5"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>
                <span>iOS</span>
            </button>
            <button id="guideTab_android" type="button" class="seg-btn seg-btn-with-icon ${state.guideTab === "android" ? "active" : ""}" role="tab" aria-selected="${state.guideTab === "android" ? "true" : "false"}" aria-controls="guideTabPanel" tabindex="${state.guideTab === "android" ? "0" : "-1"}" data-gtab="android">
                <svg class="seg-btn-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6 18c0 .55.45 1 1 1h1v3.5c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5V19h2v3.5c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5V19h1c.55 0 1-.45 1-1V8H6v10zM3.5 8C2.67 8 2 8.67 2 9.5v7c0 .83.67 1.5 1.5 1.5S5 17.33 5 16.5v-7C5 8.67 4.33 8 3.5 8zm17 0c-.83 0-1.5.67-1.5 1.5v7c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5v-7c0-.83-.67-1.5-1.5-1.5zm-4.97-4.84l1.3-1.3c.2-.2.2-.51 0-.71-.2-.2-.51-.2-.71 0l-1.48 1.48C13.85 2.23 12.95 2 12 2c-.96 0-1.86.23-2.66.63L7.85 1.15c-.2-.2-.51-.2-.71 0-.2.2-.2.51 0 .71l1.31 1.31C6.97 4.26 6 5.92 6 7.8h12c0-1.88-.97-3.54-2.47-4.64zM9 6c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1zm6 0c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1z"/></svg>
                <span>Android</span>
            </button>
            <button id="guideTab_desktop" type="button" class="seg-btn seg-btn-with-icon ${state.guideTab === "desktop" ? "active" : ""}" role="tab" aria-selected="${state.guideTab === "desktop" ? "true" : "false"}" aria-controls="guideTabPanel" tabindex="${state.guideTab === "desktop" ? "0" : "-1"}" data-gtab="desktop">
                <svg class="seg-btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="2"/><line x1="2" y1="20" x2="22" y2="20"/></svg>
                <span>ПК / Mac</span>
            </button>
            <button id="guideTab_faq" type="button" class="seg-btn seg-btn-with-icon ${state.guideTab === "faq" ? "active" : ""}" role="tab" aria-selected="${state.guideTab === "faq" ? "true" : "false"}" aria-controls="guideTabPanel" tabindex="${state.guideTab === "faq" ? "0" : "-1"}" data-gtab="faq">
                <svg class="seg-btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                <span>Вопросы</span>
            </button>
        </div>

        ${subTabsHtml}

        <div id="guideTabPanel" class="field" style="gap:12px;" role="tabpanel" aria-labelledby="guideTab_${state.guideTab}">
            ${contentHtml}
        </div>
    `;

    root.querySelector("#guidesBackBtn").addEventListener("click", () => {
        _pauseGuideVideos(root);
        onBack();
    });

    root.querySelectorAll("details").forEach((det) => {
        det.addEventListener("toggle", () => {
            if (!det.open) _pauseGuideVideos(det);
        });
    });

    const tablist = root.querySelector('[role="tablist"]');
    if (tablist) {
        tablist.addEventListener("keydown", (e) => {
            const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
            if (!tabs.length) return;
            const idx = tabs.indexOf(document.activeElement);
            if (idx === -1) return;
            let next = -1;
            if (e.key === "ArrowRight") next = (idx + 1) % tabs.length;
            else if (e.key === "ArrowLeft") next = (idx - 1 + tabs.length) % tabs.length;
            else if (e.key === "Home") next = 0;
            else if (e.key === "End") next = tabs.length - 1;
            if (next !== -1) {
                e.preventDefault();
                const nextTab = tabs[next].dataset.gtab;
                _pauseGuideVideos(root);
                state.guideTab = nextTab;
                renderUserGuidesView(root, state, data, onBack);
                root.querySelector(`#guideTab_${nextTab}`)?.focus();
            }
        });
    }

    root.querySelectorAll("[data-gtab]").forEach((btn) => {
        btn.addEventListener("click", () => {
            _pauseGuideVideos(root);
            state.guideTab = btn.dataset.gtab;
            renderUserGuidesView(root, state, data, onBack);
        });
    });

    root.querySelectorAll("[data-ios-m]").forEach((btn) => {
        btn.addEventListener("click", () => {
            _pauseGuideVideos(root);
            state.guideIosMethod = btn.dataset.iosM;
            renderUserGuidesView(root, state, data, onBack);
            root.querySelector(`[data-ios-m="${state.guideIosMethod}"]`)?.focus();
        });
    });
    root.querySelectorAll("[data-and-m]").forEach((btn) => {
        btn.addEventListener("click", () => {
            _pauseGuideVideos(root);
            state.guideAndroidMethod = btn.dataset.andM;
            renderUserGuidesView(root, state, data, onBack);
            root.querySelector(`[data-and-m="${state.guideAndroidMethod}"]`)?.focus();
        });
    });
    root.querySelectorAll("[data-desk-m]").forEach((btn) => {
        btn.addEventListener("click", () => {
            _pauseGuideVideos(root);
            state.guideDesktopMethod = btn.dataset.deskM;
            renderUserGuidesView(root, state, data, onBack);
            root.querySelector(`[data-desk-m="${state.guideDesktopMethod}"]`)?.focus();
        });
    });

    root.querySelectorAll("[data-ext]").forEach((btn) => {
        btn.addEventListener("click", () => openExternalLink(btn.dataset.ext));
    });

    root.querySelectorAll("[data-copy]").forEach((btn) => {
        btn.addEventListener("click", () => copyText(btn.dataset.copy, "Скопировано"));
    });

    root.querySelectorAll("[data-shot]").forEach((btn) => {
        btn.addEventListener("click", () => _openImageModal(btn.dataset.shot, btn.dataset.shotTitle));
    });
}
