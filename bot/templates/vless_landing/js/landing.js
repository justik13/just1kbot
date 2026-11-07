        const subUrl = window.location.origin + window.location.pathname.replace(/\/$/, "");

        // Populate dynamic deep links and visible address text
        const subUrlTextEl = document.getElementById("subUrlText");
        if (subUrlTextEl) subUrlTextEl.textContent = subUrl;

        const deepLinkHapp = document.getElementById("deepLinkHapp");
        if (deepLinkHapp) deepLinkHapp.href = "happ://add/" + subUrl;

        const deepLinkIncy = document.getElementById("deepLinkIncy");
        if (deepLinkIncy) deepLinkIncy.href = "incy://add/" + subUrl;

        function updateActiveClient(client) {
            const btnHapp = document.getElementById("deepLinkHapp");
            const btnIncy = document.getElementById("deepLinkIncy");
            if (!btnHapp || !btnIncy) return;

            if (client === "happ") {
                btnHapp.classList.add("btn-primary");
                btnHapp.classList.remove("btn-secondary");
                btnIncy.classList.add("btn-secondary");
                btnIncy.classList.remove("btn-primary");
            } else {
                btnIncy.classList.add("btn-primary");
                btnIncy.classList.remove("btn-secondary");
                btnHapp.classList.add("btn-secondary");
                btnHapp.classList.remove("btn-primary");
            }
        }

        let activePlatform = "ios";

        function switchPlatformTab(platform) {
            activePlatform = platform;
            const platforms = ["ios", "android", "windows", "macos", "github"];
            platforms.forEach(function(p) {
                const panel = document.getElementById("tab-" + p);
                const btnId = "tabBtn" + p.charAt(0).toUpperCase() + p.slice(1);
                const btn = document.getElementById(btnId);
                if (panel) panel.style.display = (p === platform) ? "block" : "none";
                if (btn) {
                    if (p === platform) {
                        btn.classList.add("active");
                    } else {
                        btn.classList.remove("active");
                    }
                }
            });
            if (platform === "windows" || platform === "macos") {
                updateActiveClient("happ");
            } else if (platform === "ios" || platform === "android") {
                updateActiveClient("incy");
            }
        }

        // Auto-detect device
        (function initDevice() {
            const ua = navigator.userAgent || "";
            const isMac = /(Macintosh)|(MacIntel)/i.test(ua);
            const isTouch = ("ontouchend" in document) || (navigator.maxTouchPoints > 0);
            const isIOS = /(iPad|iPhone|iPod)/i.test(ua) || (isMac && isTouch);

            if (isIOS && !window.MSStream) {
                switchPlatformTab("ios");
            } else if (/Android/i.test(ua)) {
                switchPlatformTab("android");
            } else if (isMac) {
                switchPlatformTab("macos");
            } else if (/Windows|Win32|Win64/i.test(ua)) {
                switchPlatformTab("windows");
            } else if (/Linux/i.test(ua) && !/Android/i.test(ua)) {
                switchPlatformTab("github");
            } else {
                switchPlatformTab("ios");
            }
        })();

        let copyResetTimer = null;

        function showCopyToast(msg, isError) {
            const toast = document.getElementById("copyToast");
            if (!toast) return;
            toast.textContent = msg;
            if (isError) {
                toast.classList.add("error");
            } else {
                toast.classList.remove("error");
            }
            toast.classList.add("visible");
            if (copyResetTimer) clearTimeout(copyResetTimer);
            copyResetTimer = setTimeout(function() {
                toast.classList.remove("visible");
                toast.classList.remove("error");
                const label = document.getElementById("copyBtnLabel");
                const btn = document.getElementById("copyUrlBtn");
                if (label) label.textContent = "Скопировать";
                if (btn) btn.classList.remove("copied");
            }, 2200);
        }

        function fallbackCopyText(text) {
            try {
                const ta = document.createElement("textarea");
                ta.value = text;
                ta.style.position = "fixed";
                ta.style.left = "-9999px";
                ta.style.top = "-9999px";
                ta.style.opacity = "0";
                ta.setAttribute("readonly", "");
                document.body.appendChild(ta);
                ta.focus();
                ta.select();
                const ok = document.execCommand("copy");
                document.body.removeChild(ta);
                return ok;
            } catch (e) {
                return false;
            }
        }

        function copyLink() {
            const btn = document.getElementById("copyUrlBtn");
            const label = document.getElementById("copyBtnLabel");

            function onCopySuccess() {
                if (label) label.textContent = "Скопировано";
                if (btn) btn.classList.add("copied");
                showCopyToast("Адрес подключения скопирован в буфер", false);
            }

            function onCopyFail() {
                showCopyToast("Не удалось скопировать. Выделите адрес вручную", true);
            }

            if (navigator.clipboard && navigator.clipboard.writeText) {
                try {
                    navigator.clipboard.writeText(subUrl).then(onCopySuccess).catch(function() {
                        if (fallbackCopyText(subUrl)) {
                            onCopySuccess();
                        } else {
                            onCopyFail();
                        }
                    });
                } catch (e) {
                    if (fallbackCopyText(subUrl)) {
                        onCopySuccess();
                    } else {
                        onCopyFail();
                    }
                }
            } else {
                if (fallbackCopyText(subUrl)) {
                    onCopySuccess();
                } else {
                    onCopyFail();
                }
            }
        }

        function toggleSupportForm() {
            const accordion = document.getElementById("supportAccordion");
            const btn = document.getElementById("supportToggleBtn");
            const container = document.getElementById("supportFormContainer");
            if (!accordion || !btn) return;
            const isOpen = accordion.classList.contains("open");
            if (isOpen) {
                accordion.classList.remove("open");
                btn.setAttribute("aria-expanded", "false");
                if (container) container.setAttribute("aria-hidden", "true");
            } else {
                accordion.classList.add("open");
                btn.setAttribute("aria-expanded", "true");
                if (container) container.setAttribute("aria-hidden", "false");
                const input = document.getElementById("supportContact");
                if (input) {
                    if (window.requestAnimationFrame) {
                        requestAnimationFrame(function() { input.focus(); });
                    } else {
                        input.focus();
                    }
                }
            }
        }

        async function submitSupportReport(event) {
            event.preventDefault();
            const contactInput = document.getElementById("supportContact");
            const messageInput = document.getElementById("supportMessage");
            const submitBtn = document.getElementById("supportSubmitBtn");
            const statusMsg = document.getElementById("supportStatusMsg");
            if (!contactInput || !messageInput || !submitBtn || !statusMsg) return;

            const contact = contactInput.value.trim();
            const message = messageInput.value.trim();
            if (contact.length < 3) {
                statusMsg.textContent = "Укажите контакт для связи (минимум 3 символа)";
                statusMsg.className = "support-status-msg error";
                statusMsg.style.display = "block";
                return;
            }
            if (message.length < 5) {
                statusMsg.textContent = "Опишите вопрос подробнее (минимум 5 символов)";
                statusMsg.className = "support-status-msg error";
                statusMsg.style.display = "block";
                return;
            }

            submitBtn.disabled = true;
            submitBtn.innerHTML = '<div class="support-spinner"></div><span>Отправка...</span>';
            statusMsg.style.display = "none";

            try {
                const reportUrl = window.location.pathname.replace(/\/$/, "") + "/report";
                const response = await fetch(reportUrl, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ contact: contact, message: message })
                });

                const data = await response.json().catch(() => ({}));
                if (response.ok && data.ok) {
                    statusMsg.innerHTML = '<span>Контакты отправлены. Мы скоро свяжемся с вами!</span><br><button type="button" class="support-reset-btn" onclick="resetSupportForm()">Отправить ещё сообщение</button>';
                    statusMsg.className = "support-status-msg success";
                    statusMsg.style.display = "block";
                    contactInput.disabled = true;
                    messageInput.disabled = true;
                    submitBtn.style.display = "none";
                } else {
                    const errText = data.error || "Не удалось отправить. Попробуйте позже.";
                    statusMsg.textContent = errText;
                    statusMsg.className = "support-status-msg error";
                    statusMsg.style.display = "block";
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = '<span>Отправить</span>';
                }
            } catch (err) {
                statusMsg.textContent = "Ошибка сети при отправке. Пожалуйста, проверьте соединение.";
                statusMsg.className = "support-status-msg error";
                statusMsg.style.display = "block";
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<span>Отправить</span>';
            }
        }

        function resetSupportForm() {
            const contactInput = document.getElementById("supportContact");
            const messageInput = document.getElementById("supportMessage");
            const submitBtn = document.getElementById("supportSubmitBtn");
            const statusMsg = document.getElementById("supportStatusMsg");
            if (contactInput) {
                contactInput.disabled = false;
                contactInput.value = "";
            }
            if (messageInput) {
                messageInput.disabled = false;
                messageInput.value = "";
            }
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.style.display = "";
                submitBtn.innerHTML = '<span>Отправить</span>';
            }
            if (statusMsg) {
                statusMsg.style.display = "none";
                statusMsg.innerHTML = "";
            }
            if (contactInput) contactInput.focus();
        }
    
        function openGuidesTab(platform) {
            const main = document.getElementById("mainView");
            const guides = document.getElementById("guidesView");
            if (main) main.classList.add("view-hidden");
            if (guides) guides.classList.remove("view-hidden");
            if (platform) switchGuideTab(platform);
            window.location.hash = "guides";
            window.scrollTo({ top: 0, behavior: "smooth" });
        }

        function openGuidesCurrentTab() {
            let guideTab = "ios";
            if (activePlatform === "android") {
                guideTab = "android";
            } else if (activePlatform === "windows" || activePlatform === "macos" || activePlatform === "github") {
                guideTab = "desktop";
            }
            openGuidesTab(guideTab);
            if (activePlatform === "macos") {
                switchDesktopMethod("mac");
            } else if (activePlatform === "windows") {
                switchDesktopMethod("win");
            } else if (activePlatform === "github") {
                switchDesktopMethod("linux");
            }
        }

        function closeGuidesTab() {
            const main = document.getElementById("mainView");
            const guides = document.getElementById("guidesView");
            if (guides) guides.classList.add("view-hidden");
            if (main) main.classList.remove("view-hidden");
            if (window.location.hash === "#guides") {
                history.replaceState(null, document.title, window.location.pathname + window.location.search);
            }
            window.scrollTo({ top: 0, behavior: "smooth" });
        }

        function switchGuideTab(tab) {
            const tabs = ["ios", "android", "desktop", "faq"];
            tabs.forEach(function(t) {
                const btn = document.getElementById("guideNavBtn" + t.charAt(0).toUpperCase() + t.slice(1));
                const sec = document.getElementById("guideSection" + t.charAt(0).toUpperCase() + t.slice(1));
                const isActive = (t === tab);
                if (btn) {
                    btn.classList.toggle("active", isActive);
                    btn.setAttribute("aria-selected", isActive ? "true" : "false");
                }
                if (sec) sec.style.display = isActive ? "block" : "none";
            });
        }

        function switchIosMethod(method) {
            const methods = {
                "tf": { btn: "tabBtnIosTf", sec: "iosSubGuideTf" },
                "region": { btn: "tabBtnIosRegion", sec: "iosSubGuideRegion" },
                "newid": { btn: "tabBtnIosNewId", sec: "iosSubGuideNewId" }
            };
            Object.keys(methods).forEach(function(k) {
                const b = document.getElementById(methods[k].btn);
                const s = document.getElementById(methods[k].sec);
                const isActive = (k === method);
                if (b) {
                    b.classList.toggle("active", isActive);
                    b.setAttribute("aria-selected", isActive ? "true" : "false");
                }
                if (s) s.style.display = isActive ? "block" : "none";
            });
            const step1Num = document.getElementById("iosCommonStep1Num");
            const step2Num = document.getElementById("iosCommonStep2Num");
            if (step1Num && step2Num) {
                if (method === "region") {
                    step1Num.textContent = "5";
                    step2Num.textContent = "6";
                } else {
                    step1Num.textContent = "3";
                    step2Num.textContent = "4";
                }
            }
        }

        function switchAndroidMethod(method) {
            const btnPlay = document.getElementById("tabBtnAndroidPlay");
            const btnApk = document.getElementById("tabBtnAndroidApk");
            const secPlay = document.getElementById("androidSubPlay");
            const secApk = document.getElementById("androidSubApk");
            const isPlay = (method === "play");
            if (btnPlay) {
                btnPlay.classList.toggle("active", isPlay);
                btnPlay.setAttribute("aria-selected", isPlay ? "true" : "false");
            }
            if (btnApk) {
                btnApk.classList.toggle("active", !isPlay);
                btnApk.setAttribute("aria-selected", !isPlay ? "true" : "false");
            }
            if (secPlay) secPlay.style.display = isPlay ? "block" : "none";
            if (secApk) secApk.style.display = !isPlay ? "block" : "none";
        }

        function switchDesktopMethod(method) {
            const btnWin = document.getElementById("tabBtnDesktopWin");
            const btnMac = document.getElementById("tabBtnDesktopMac");
            const btnLinux = document.getElementById("tabBtnDesktopLinux");
            const secWin = document.getElementById("desktopSubWin");
            const secMac = document.getElementById("desktopSubMac");
            const secLinux = document.getElementById("desktopSubLinux");
            const isWin = (method === "win");
            const isMac = (method === "mac");
            const isLinux = (method === "linux");
            if (btnWin) {
                btnWin.classList.toggle("active", isWin);
                btnWin.setAttribute("aria-selected", isWin ? "true" : "false");
            }
            if (btnMac) {
                btnMac.classList.toggle("active", isMac);
                btnMac.setAttribute("aria-selected", isMac ? "true" : "false");
            }
            if (btnLinux) {
                btnLinux.classList.toggle("active", isLinux);
                btnLinux.setAttribute("aria-selected", isLinux ? "true" : "false");
            }
            if (secWin) secWin.style.display = isWin ? "block" : "none";
            if (secMac) secMac.style.display = isMac ? "block" : "none";
            if (secLinux) secLinux.style.display = isLinux ? "block" : "none";
        }

        function copyGuideField(text, btn) {
            if (!btn || btn.classList.contains("copied") || btn.classList.contains("copy-failed")) return;
            const originalHtml = btn.getAttribute("data-original-html") || btn.innerHTML;
            btn.setAttribute("data-original-html", originalHtml);
            btn.classList.add("copied");

            function onSuccess() {
                btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:11px;height:11px;"><polyline points="20 6 9 17 4 12"/></svg><span class="copy-lbl">Скопировано</span>';
                setTimeout(function() {
                    btn.classList.remove("copied");
                    btn.innerHTML = btn.getAttribute("data-original-html") || originalHtml;
                    btn.removeAttribute("data-original-html");
                }, 1400);
            }

            function onFail() {
                btn.classList.remove("copied");
                btn.classList.add("copy-failed");
                btn.innerHTML = '<span class="copy-lbl" style="color:var(--error);">Ошибка</span>';
                setTimeout(function() {
                    btn.classList.remove("copy-failed");
                    btn.innerHTML = btn.getAttribute("data-original-html") || originalHtml;
                    btn.removeAttribute("data-original-html");
                }, 1400);
            }

            try {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(text).then(onSuccess).catch(function() {
                        if (fallbackCopyText(text)) {
                            onSuccess();
                        } else {
                            onFail();
                        }
                    });
                } else {
                    if (fallbackCopyText(text)) {
                        onSuccess();
                    } else {
                        onFail();
                    }
                }
            } catch (e) {
                if (fallbackCopyText(text)) {
                    onSuccess();
                } else {
                    onFail();
                }
            }
        }

        function toggleGuideFaq(btn) {
            const item = btn.closest(".guide-faq-item");
            if (item) {
                const isOpen = item.classList.toggle("open");
                btn.setAttribute("aria-expanded", isOpen ? "true" : "false");
            }
        }

        function markImgLoaded(img) {
            if (!img) return;
            img.classList.add("loaded");
            const w = img.closest(".guide-screenshot-wrapper");
            if (w) w.classList.add("img-loaded");
        }

        let lastFocusedElementBeforeLightbox = null;

        function openImageLightbox(imgSrc, imgAlt) {
            const lightbox = document.getElementById("imageLightbox");
            const img = document.getElementById("lightboxImg");
            const caption = document.getElementById("lightboxCaption");
            if (!lightbox || !img) return;
            lastFocusedElementBeforeLightbox = document.activeElement;
            img.src = imgSrc;
            img.alt = imgAlt || "";
            img.classList.remove("zoomed");
            if (caption) caption.textContent = imgAlt || "Скриншот настройки";
            if (typeof lightbox.showModal === "function") {
                lightbox.showModal();
            } else {
                lightbox.setAttribute("open", "");
            }
            document.body.style.overflow = "hidden";
            const closeBtn = lightbox.querySelector(".lightbox-close-btn");
            if (closeBtn) closeBtn.focus();
        }

        function closeImageLightbox() {
            const lightbox = document.getElementById("imageLightbox");
            if (!lightbox) return;
            if (typeof lightbox.close === "function") {
                lightbox.close();
            } else {
                lightbox.removeAttribute("open");
            }
            document.body.style.overflow = "";
            if (lastFocusedElementBeforeLightbox && typeof lastFocusedElementBeforeLightbox.focus === "function") {
                lastFocusedElementBeforeLightbox.focus();
            }
        }

        function toggleLightboxZoom(event) {
            if (event) event.stopPropagation();
            const img = document.getElementById("lightboxImg");
            if (img) img.classList.toggle("zoomed");
        }

        function onLightboxBackdropClick(event) {
            if (event.target && (
                event.target.id === "lightboxImg" ||
                event.target.closest(".lightbox-img-wrap") ||
                event.target.closest(".lightbox-caption") ||
                event.target.closest(".lightbox-tip") ||
                event.target.closest(".lightbox-close-btn")
            )) {
                return;
            }
            closeImageLightbox();
        }

        const lightboxModal = document.getElementById("imageLightbox");
        if (lightboxModal) {
            const onLightboxDismiss = function() {
                document.body.style.overflow = "";
                const img = document.getElementById("lightboxImg");
                if (img) img.classList.remove("zoomed");
            };
            lightboxModal.addEventListener("close", onLightboxDismiss);
            lightboxModal.addEventListener("cancel", onLightboxDismiss);
        }

        document.addEventListener("keydown", function(e) {
            if (e.key === "Escape") closeImageLightbox();
        });

        document.addEventListener("click", function(e) {
            const img = e.target.closest(".guide-real-screenshot") || (e.target.closest(".guide-screenshot-wrapper") ? e.target.closest(".guide-screenshot-wrapper").querySelector("img") : null);
            if (img && img.src) {
                openImageLightbox(img.src, img.alt);
            }
        });

        window.addEventListener("hashchange", function() {
            if (window.location.hash === "#guides") {
                openGuidesCurrentTab();
            } else if (!window.location.hash) {
                closeGuidesTab();
            }
        });

        if (window.location.hash === "#guides") {
            openGuidesCurrentTab();
        }

        // Initialize any cached images that loaded before DOMContentLoaded
        document.querySelectorAll(".guide-real-screenshot").forEach(function(img) {
            if (img.complete && img.naturalWidth > 0) {
                markImgLoaded(img);
            }
        });
