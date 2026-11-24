import { copyText, escapeHtml, openModal } from "../ui.js?v=2.7.7";

function _openExternal(url) {
    if (!url) return;
    const tg = window.Telegram && window.Telegram.WebApp;
    if (tg && typeof tg.openLink === "function") {
        tg.openLink(url);
    } else {
        window.open(url, "_blank", "noopener,noreferrer");
    }
}

function _openImageModal(src, title) {
    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.style.gap = "8px";
    wrap.innerHTML = `<img src="${escapeHtml(src)}" alt="${escapeHtml(title)}" class="guide-modal-img">`;
    openModal(title || "Скриншот", wrap);
}

function _renderIosContent(method) {
    if (method === "region") {
        return `
            <details class="guide-details guide-video-priority">
                <summary class="guide-summary guide-video-summary">
                    <span class="guide-video-play" aria-hidden="true">
                        <svg viewBox="0 0 24 24" fill="currentColor"><polygon points="6 4 20 12 6 20 6 4"/></svg>
                    </span>
                    <span class="guide-video-info">
                        <span class="guide-video-title-row">
                            <span class="guide-video-title">Видеоинструкция: смена региона за 2 минуты</span>
                            <span class="guide-video-tag">Рекомендуем</span>
                        </span>
                        <span class="guide-video-meta">38 сек · 2,0 МБ · наглядный показ без банковской карты</span>
                    </span>
                    <span class="disclosure-pill"><span>Смотреть</span><svg class="guide-video-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></span>
                </summary>
                <div class="guide-body">
                    <video class="guide-video" controls playsinline preload="none" poster="/app/assets/guides/ios_02_kazakhstan.jpg">
                        <source src="/app/assets/guides/happ_guide.mp4" type="video/mp4">
                    </video>
                </div>
            </details>
            <div class="notice-box">
                <span>В App Store Казахстана интерфейс на русском языке, а карта для скачивания не требуется. После установки можно сразу вернуть регион РФ.</span>
            </div>
            <div class="notice-box notice-warn">
                <span><b>Условия смены:</b> на балансе Apple ID не должно быть остатка (0 ₽), действующих подписок Apple, незавершённых заказов и семейного доступа.</span>
            </div>
            <div class="guide-step-card">
                <div class="guide-step-head"><span class="guide-step-num">1</span><span>Откройте меню смены страны в App Store</span></div>
                <p class="muted">Откройте <b>App Store</b> → иконка профиля в правом верхнем углу → первая строка (имя и Apple ID) → <b>Страна/регион</b> → <b>Изменить страну или регион</b>.</p>
                <button type="button" class="guide-shot-btn" data-shot="/app/assets/guides/ios_01_profile.jpg" data-shot-title="Профиль App Store">
                    <img src="/app/assets/guides/ios_01_profile.jpg" alt="Профиль App Store" loading="lazy">
                    <span class="guide-shot-hint">Увеличить</span>
                </button>
            </div>
            <div class="guide-step-card">
                <div class="guide-step-head"><span class="guide-step-num">2</span><span>Выберите Казахстан и примите условия</span></div>
                <p class="muted">Найдите в списке <b>Казахстан</b> и нажмите <b>Принять</b> дважды.</p>
                <button type="button" class="guide-shot-btn" data-shot="/app/assets/guides/ios_02_kazakhstan.jpg" data-shot-title="Выбор страны Казахстан">
                    <img src="/app/assets/guides/ios_02_kazakhstan.jpg" alt="Выбор страны Казахстан" loading="lazy">
                    <span class="guide-shot-hint">Увеличить</span>
                </button>
            </div>
            <div class="guide-step-card">
                <div class="guide-step-head"><span class="guide-step-num">3</span><span>Заполните оплату и адрес</span></div>
                <p class="muted">Способ оплаты выберите <b>Нет</b>. Поля адреса можно заполнить любыми данными (например, Алматы):</p>
                <div class="guide-kv-list">
                    <div class="guide-kv-row"><span>Улица</span><span class="guide-kv-val"><code>ул. Абая, 1</code><button type="button" class="btn btn-sm guide-kv-copy" data-copy="ул. Абая, 1">Копировать</button></span></div>
                    <div class="guide-kv-row"><span>Город</span><span class="guide-kv-val"><code>Алматы</code><button type="button" class="btn btn-sm guide-kv-copy" data-copy="Алматы">Копировать</button></span></div>
                    <div class="guide-kv-row"><span>Индекс</span><span class="guide-kv-val"><code>050000</code><button type="button" class="btn btn-sm guide-kv-copy" data-copy="050000">Копировать</button></span></div>
                    <div class="guide-kv-row"><span>Телефон</span><span class="guide-kv-val"><code>(701) 123-4567</code><button type="button" class="btn btn-sm guide-kv-copy" data-copy="7011234567">Копировать</button></span></div>
                </div>
                <button type="button" class="guide-shot-btn" data-shot="/app/assets/guides/ios_03_payment.jpg" data-shot-title="Способ оплаты и адрес">
                    <img src="/app/assets/guides/ios_03_payment.jpg" alt="Способ оплаты и адрес" loading="lazy">
                    <span class="guide-shot-hint">Увеличить</span>
                </button>
            </div>
            <div class="guide-step-card">
                <div class="guide-step-head"><span class="guide-step-num">4</span><span>Установите INCY или Happ</span></div>
                <p class="muted">После смены региона скачайте клиент из App Store и верните регион РФ при желании.</p>
                <button type="button" class="guide-shot-btn" data-shot="/app/assets/guides/ios_04_appstore.jpg" data-shot-title="Установка из App Store">
                    <img src="/app/assets/guides/ios_04_appstore.jpg" alt="Установка из App Store" loading="lazy">
                    <span class="guide-shot-hint">Увеличить</span>
                </button>
                <div class="connect-pair">
                    <button type="button" class="btn btn-sm" data-ext="https://apps.apple.com/app/incy/id6756943388">App Store: INCY</button>
                    <button type="button" class="btn btn-sm" data-ext="https://apps.apple.com/app/happ-proxy-utility/id6504287215">App Store: Happ</button>
                </div>
            </div>
        `;
    }
    if (method === "newid") {
        return `
            <div class="notice-box">
                <span>Запасной вариант, если на основном Apple ID есть активные подписки Apple или семейный доступ.</span>
            </div>
            <div class="guide-step-card">
                <div class="guide-step-head"><span class="guide-step-num">1</span><span>Создайте Apple ID с регионом Казахстан</span></div>
                <p class="muted">Откройте account.apple.com и зарегистрируйте новый аккаунт, выбрав страну Казахстан и резервную почту.</p>
                <button type="button" class="btn btn-sm" data-ext="https://account.apple.com/account">Открыть account.apple.com</button>
            </div>
            <div class="guide-step-card">
                <div class="guide-step-head"><span class="guide-step-num">2</span><span>Войдите только в App Store</span></div>
                <p class="muted">В настройках телефона iCloud оставьте прежним. Откройте <b>App Store</b> → иконка профиля → <b>Выйти</b> в самом низу и войдите новым аккаунтом.</p>
            </div>
            <div class="guide-step-card">
                <div class="guide-step-head"><span class="guide-step-num">3</span><span>Скачайте приложение и верните основной аккаунт</span></div>
                <div class="connect-pair">
                    <button type="button" class="btn btn-sm" data-ext="https://apps.apple.com/app/incy/id6756943388">App Store: INCY</button>
                    <button type="button" class="btn btn-sm" data-ext="https://apps.apple.com/app/happ-proxy-utility/id6504287215">App Store: Happ</button>
                </div>
            </div>
        `;
    }
    return `
        <div class="notice-box">
            <span>TestFlight доступен в российском App Store (iOS 16+) и не требует смены региона.</span>
        </div>
        <div class="guide-step-card">
            <div class="guide-step-head"><span class="guide-step-num">1</span><span>Установите Apple TestFlight</span></div>
            <p class="muted">Скачайте официальное приложение TestFlight из российского App Store.</p>
            <button type="button" class="btn btn-sm" data-ext="https://apps.apple.com/app/testflight/id899247664">Установить TestFlight</button>
        </div>
        <div class="guide-step-card">
            <div class="guide-step-head"><span class="guide-step-num">2</span><span>Откройте приглашение и установите клиент</span></div>
            <p class="muted">В открывшемся TestFlight нажмите <b>Принять</b> (Accept), затем <b>Установить</b> (Install).</p>
            <div class="connect-pair">
                <button type="button" class="btn btn-primary btn-sm" data-ext="https://testflight.apple.com/join/dFw66h3a">INCY (Рекомендуется)</button>
                <button type="button" class="btn btn-sm" data-ext="https://testflight.apple.com/join/XMls6Ckd">Happ</button>
            </div>
            <button type="button" class="guide-shot-btn" data-shot="/app/assets/guides/ios_testflight.jpg" data-shot-title="Установка в TestFlight">
                <img src="/app/assets/guides/ios_testflight.jpg" alt="Установка в TestFlight" loading="lazy">
                <span class="guide-shot-hint">Увеличить</span>
            </button>
            <p class="muted" style="font-size:12px;">Если TestFlight пишет «Этот бета-тест заполнен», переключитесь выше на способ «Казахстан».</p>
        </div>
    `;
}

function _renderAndroidContent(method) {
    if (method === "apk") {
        return `
            <div class="notice-box">
                <span>Для устройств без Google Play (Huawei / Honor) или если приложение недоступно в магазине.</span>
            </div>
            <div class="guide-step-card">
                <div class="guide-step-head"><span class="guide-step-num">1</span><span>Скачайте официальный APK с GitHub</span></div>
                <p class="muted">Если браузер предупредит о файле, нажмите <b>Всё равно скачать</b> и затем <b>Установить</b>.</p>
                <div class="connect-pair">
                    <button type="button" class="btn btn-primary btn-sm" data-ext="https://github.com/INCY-DEV/incy-platforms/releases/download/latest/Incy.apk">Incy.apk</button>
                    <button type="button" class="btn btn-sm" data-ext="https://github.com/Happ-proxy/happ-android/releases/latest/download/Happ.apk">Happ.apk</button>
                </div>
                <button type="button" class="guide-shot-btn" data-shot="/app/assets/guides/android_apk_warning.jpg" data-shot-title="Предупреждение при установке APK">
                    <img src="/app/assets/guides/android_apk_warning.jpg" alt="Предупреждение при установке APK" loading="lazy">
                    <span class="guide-shot-hint">Увеличить</span>
                </button>
            </div>
            <div class="guide-step-card">
                <div class="guide-step-head"><span class="guide-step-num">2</span><span>Подключите профиль</span></div>
                <p class="muted">Вернитесь на вкладку «Доступ» и нажмите кнопку «Подключить в INCY» или «Открыть в Happ», либо вставьте скопированную ссылку в приложении через «+».</p>
            </div>
        `;
    }
    return `
        <div class="notice-box">
            <span>Приложение Happ временно недоступно в Google Play РФ. Для быстрой установки из магазина используйте INCY.</span>
        </div>
        <div class="guide-step-card">
            <div class="guide-step-head"><span class="guide-step-num">1</span><span>Установите INCY из Google Play</span></div>
            <button type="button" class="btn btn-primary btn-sm" data-ext="https://play.google.com/store/apps/details?id=llc.itdev.incy">Открыть Google Play</button>
        </div>
        <div class="guide-step-card">
            <div class="guide-step-head"><span class="guide-step-num">2</span><span>Добавьте профиль</span></div>
            <p class="muted">Вернитесь на вкладку «Доступ» и нажмите «Подключить в INCY». В открывшемся приложении включите главный переключатель.</p>
        </div>
    `;
}

function _renderDesktopContent(method) {
    if (method === "mac") {
        return `
            <div class="guide-step-card">
                <div class="guide-step-head"><span class="guide-step-num">1</span><span>Скачайте образ Happ для macOS</span></div>
                <p class="muted">Подходит для процессоров Apple Silicon (M1–M4) и Intel. Перетащите иконку в папку «Программы» (Applications).</p>
                <div class="connect-pair">
                    <button type="button" class="btn btn-primary btn-sm" data-ext="https://github.com/Happ-proxy/happ-desktop/releases/latest/download/Happ.macOS.universal.dmg">Скачать Happ (.dmg)</button>
                    <button type="button" class="btn btn-sm" data-ext="https://github.com/INCY-DEV/incy-platforms/releases">Релизы INCY</button>
                </div>
            </div>
            <div class="guide-step-card">
                <div class="guide-step-head"><span class="guide-step-num">2</span><span>Добавьте ссылку подписки</span></div>
                <p class="muted">Скопируйте ссылку на вкладке «Доступ», откройте Happ, нажмите «+» и выберите «Вставить из буфера».</p>
            </div>
        `;
    }
    if (method === "linux") {
        const cmd = "curl -fsSL https://github.com/Happ-proxy/happ-desktop/releases/latest/download/Happ.linux.x64.AppImage -o Happ.AppImage && chmod +x Happ.AppImage";
        return `
            <div class="guide-step-card">
                <div class="guide-step-head"><span class="guide-step-num">1</span><span>Скачайте Happ для Linux</span></div>
                <p class="muted">Доступны пакеты AppImage, .deb и .rpm на GitHub.</p>
                <div class="sub-url-box" style="font-size:12px;">${escapeHtml(cmd)}</div>
                <div class="connect-pair">
                    <button type="button" class="btn btn-sm" data-copy="${escapeHtml(cmd)}">Копировать команду</button>
                    <button type="button" class="btn btn-sm" data-ext="https://github.com/Happ-proxy/happ-desktop/releases/latest">Релизы GitHub</button>
                </div>
            </div>
        `;
    }
    return `
        <div class="guide-step-card">
            <div class="guide-step-head"><span class="guide-step-num">1</span><span>Установите программу для Windows</span></div>
            <p class="muted">Если появится окно SmartScreen, нажмите «Подробнее» → «Выполнить в любом случае».</p>
            <div class="connect-pair">
                <button type="button" class="btn btn-primary btn-sm" data-ext="https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe">Happ (.exe)</button>
                <button type="button" class="btn btn-sm" data-ext="https://github.com/INCY-DEV/incy-platforms/releases/download/latest/incy-windows-setup.exe">INCY (.exe)</button>
            </div>
        </div>
        <div class="guide-step-card">
            <div class="guide-step-head"><span class="guide-step-num">2</span><span>Добавьте ссылку и выберите режим</span></div>
            <p class="muted">Скопируйте ссылку на вкладке «Доступ», в программе нажмите «+» → «Вставить из буфера». Внизу выберите режим <b>TUN (Рекомендуется)</b> или Системный прокси.</p>
        </div>
    `;
}

function _renderFaqContent(deviceLimit) {
    const items = [
        {
            q: "Нужно ли отключать при входе в банки или Госуслуги?",
            a: "Нет, не требуется. Настроена выборочная маршрутизация: банки, Госуслуги и российские сервисы открываются напрямую на обычной скорости вашего провайдера.",
        },
        {
            q: "Как вернуть регион App Store обратно на Россию?",
            a: "Сразу после загрузки INCY или Happ откройте App Store → иконка профиля → Имя/Apple ID → «Страна или регион» → «Россия» и способ оплаты «Нет». Приложение останется на устройстве.",
        },
        {
            q: "Что делать, если в TestFlight пишет «Этот бета-тест заполнен»?",
            a: "Выберите способ «Казахстан» во вкладке iOS: смените регион App Store без банковской карты (займёт 2 минуты) и установите приложение напрямую из магазина.",
        },
        {
            q: "Что делать, если пропал доступ или страницы не грузятся?",
            a: "1. Потяните список вниз на главном экране Happ или INCY, чтобы обновить серверы. 2. Выключите и снова включите тумблер. 3. Выберите другую локацию. 4. Проверьте срок подписки на вкладке «Доступ».",
        },
        {
            q: "Что делать, если на Android не включается соединение?",
            a: "Если ранее были установлены другие сетевые приложения, откройте Настройки телефона → Подключения → Сетевые профили и выключите «Постоянное соединение» и «Блокировать соединения» у старых программ.",
        },
        {
            q: "Сколько устройств можно подключить?",
            a: `По вашему текущему тарифу доступно до ${deviceLimit || 2} устройств одновременно. Управлять активными устройствами и освобождать слоты можно на вкладке «Доступ».`,
        },
    ];
    return items
        .map(
            (item, idx) => `
            <details class="guide-details" ${idx === 0 ? "open" : ""}>
                <summary class="guide-summary">
                    <span>${escapeHtml(item.q)}</span>
                    <span class="disclosure-pill"><span>Ответ</span><svg class="guide-video-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></span>
                </summary>
                <div class="guide-body">
                    <p class="muted">${escapeHtml(item.a)}</p>
                </div>
            </details>
        `
        )
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
    if (!state.guideIosMethod) state.guideIosMethod = "tf";
    if (!state.guideAndroidMethod) state.guideAndroidMethod = "play";
    if (!state.guideDesktopMethod) {
        state.guideDesktopMethod = state.platform === "macos" ? "mac" : "win";
    }

    const stdLimit = Number((data && data.standard && data.standard.device_limit) || 2);

    let subTabsHtml = "";
    let contentHtml = "";

    if (state.guideTab === "ios") {
        subTabsHtml = `
            <div class="filter-pills">
                <button type="button" class="pill-btn ${state.guideIosMethod === "tf" ? "active" : ""}" data-ios-m="tf">TestFlight</button>
                <button type="button" class="pill-btn ${state.guideIosMethod === "region" ? "active" : ""}" data-ios-m="region">Казахстан</button>
                <button type="button" class="pill-btn ${state.guideIosMethod === "newid" ? "active" : ""}" data-ios-m="newid">Второй ID</button>
            </div>
        `;
        contentHtml = _renderIosContent(state.guideIosMethod);
    } else if (state.guideTab === "android") {
        subTabsHtml = `
            <div class="filter-pills">
                <button type="button" class="pill-btn ${state.guideAndroidMethod === "play" ? "active" : ""}" data-and-m="play">Google Play</button>
                <button type="button" class="pill-btn ${state.guideAndroidMethod === "apk" ? "active" : ""}" data-and-m="apk">Прямой APK</button>
            </div>
        `;
        contentHtml = _renderAndroidContent(state.guideAndroidMethod);
    } else if (state.guideTab === "desktop") {
        subTabsHtml = `
            <div class="filter-pills">
                <button type="button" class="pill-btn ${state.guideDesktopMethod === "win" ? "active" : ""}" data-desk-m="win">Windows</button>
                <button type="button" class="pill-btn ${state.guideDesktopMethod === "mac" ? "active" : ""}" data-desk-m="mac">macOS</button>
                <button type="button" class="pill-btn ${state.guideDesktopMethod === "linux" ? "active" : ""}" data-desk-m="linux">Linux</button>
            </div>
        `;
        contentHtml = _renderDesktopContent(state.guideDesktopMethod);
    } else {
        contentHtml = _renderFaqContent(stdLimit);
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
            <button type="button" class="seg-btn seg-btn-with-icon ${state.guideTab === "ios" ? "active" : ""}" role="tab" aria-selected="${state.guideTab === "ios" ? "true" : "false"}" data-gtab="ios">
                <svg class="seg-btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="6" y="2" width="12" height="20" rx="2.5"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>
                <span>iOS</span>
            </button>
            <button type="button" class="seg-btn seg-btn-with-icon ${state.guideTab === "android" ? "active" : ""}" role="tab" aria-selected="${state.guideTab === "android" ? "true" : "false"}" data-gtab="android">
                <svg class="seg-btn-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6 18c0 .55.45 1 1 1h1v3.5c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5V19h2v3.5c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5V19h1c.55 0 1-.45 1-1V8H6v10zM3.5 8C2.67 8 2 8.67 2 9.5v7c0 .83.67 1.5 1.5 1.5S5 17.33 5 16.5v-7C5 8.67 4.33 8 3.5 8zm17 0c-.83 0-1.5.67-1.5 1.5v7c0 .83.67 1.5 1.5 1.5s1.5-.67 1.5-1.5v-7c0-.83-.67-1.5-1.5-1.5zm-4.97-4.84l1.3-1.3c.2-.2.2-.51 0-.71-.2-.2-.51-.2-.71 0l-1.48 1.48C13.85 2.23 12.95 2 12 2c-.96 0-1.86.23-2.66.63L7.85 1.15c-.2-.2-.51-.2-.71 0-.2.2-.2.51 0 .71l1.31 1.31C6.97 4.26 6 5.92 6 7.8h12c0-1.88-.97-3.54-2.47-4.64zM9 6c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1zm6 0c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1z"/></svg>
                <span>Android</span>
            </button>
            <button type="button" class="seg-btn seg-btn-with-icon ${state.guideTab === "desktop" ? "active" : ""}" role="tab" aria-selected="${state.guideTab === "desktop" ? "true" : "false"}" data-gtab="desktop">
                <svg class="seg-btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="2"/><line x1="2" y1="20" x2="22" y2="20"/></svg>
                <span>ПК / Mac</span>
            </button>
            <button type="button" class="seg-btn seg-btn-with-icon ${state.guideTab === "faq" ? "active" : ""}" role="tab" aria-selected="${state.guideTab === "faq" ? "true" : "false"}" data-gtab="faq">
                <svg class="seg-btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                <span>Вопросы</span>
            </button>
        </div>

        ${subTabsHtml}

        <div class="field" style="gap:12px;">
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
        });
    });
    root.querySelectorAll("[data-and-m]").forEach((btn) => {
        btn.addEventListener("click", () => {
            _pauseGuideVideos(root);
            state.guideAndroidMethod = btn.dataset.andM;
            renderUserGuidesView(root, state, data, onBack);
        });
    });
    root.querySelectorAll("[data-desk-m]").forEach((btn) => {
        btn.addEventListener("click", () => {
            _pauseGuideVideos(root);
            state.guideDesktopMethod = btn.dataset.deskM;
            renderUserGuidesView(root, state, data, onBack);
        });
    });

    root.querySelectorAll("[data-ext]").forEach((btn) => {
        btn.addEventListener("click", () => _openExternal(btn.dataset.ext));
    });

    root.querySelectorAll("[data-copy]").forEach((btn) => {
        btn.addEventListener("click", () => copyText(btn.dataset.copy, "Команда скопирована"));
    });

    root.querySelectorAll("[data-shot]").forEach((btn) => {
        btn.addEventListener("click", () => _openImageModal(btn.dataset.shot, btn.dataset.shotTitle));
    });
}
