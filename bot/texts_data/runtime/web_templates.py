
UI_WEB_TEMPLATES_VAR_TIMERSECONDS_3_VAR_TIMEREL_156 = """
        var timerSeconds = 3;
        var timerElement = document.getElementById("timer");
        var countdownElement = document.getElementById("countdown-text");
        var triggered = false;

        function getVpnUri() {
            var el = document.getElementById("устройство-key");
            return el ? el.value : "";
        }

        function openVpnApp() {
            var uri = getVpnUri();
            if (uri) {
                window.location.href = uri;
            }
        }

        function copyVpnKey() {
            var устройствоKeyEl = document.getElementById("устройство-key");
            var feedback = document.getElementById("copy-feedback");
            var text = устройствоKeyEl ? устройствоKeyEl.value : "";

            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(function() {
                    feedback.innerText = "✅ Ключ скопирован в буфер обмена!";
                    feedback.style.display = "block";
                }).catch(function() {
                    fallbackCopy(устройствоKeyEl, feedback);
                });
            } else {
                fallbackCopy(устройствоKeyEl, feedback);
            }
        }

        function fallbackCopy(устройствоKeyEl, feedback) {
            if (устройствоKeyEl) {
                устройствоKeyEl.removeAttribute("hidden");
                устройствоKeyEl.focus();
                устройствоKeyEl.select();
            }
            feedback.innerText = "📋 Выделите и скопируйте полный ключ вручную, затем вставьте его в AmneziaУстройство.";
            feedback.style.display = "block";
        }

        var interval = setInterval(function() {
            timerSeconds--;
            if (timerElement) {
                timerElement.innerText = timerSeconds;
            }
            if (timerSeconds <= 0) {
                clearInterval(interval);
                if (countdownElement) {
                    countdownElement.style.display = "none";
                }
                if (!triggered) {
                    triggered = true;
                    openVpnApp();
                }
            }
        }, 1000);
"""
UI_WEB_TEMPLATES_PODKLYUCHENIE_K_AMNEZIAVPN_239 = """    <title>Подключение к AmneziaУстройство</title>
"""
UI_WEB_TEMPLATES_EKSPERIMENTALNAYA_FUNKTSIYA_244 = """        <div class="badge">🧪 Экспериментальная функция</div>
"""
UI_WEB_TEMPLATES_AVTOMATICHESKOE_OTKRYTIE_CHERE_246 = """        <div class="countdown" id="countdown-text">Автоматическое открытие через <b id="timer">3</b> сек...</div>
"""
UI_WEB_TEMPLATES_POPROBOVAT_OTKRYT_V_AMNEZIAVPN_249 = """            🚀 Попробовать открыть в AmneziaУстройство
"""
UI_WEB_TEMPLATES_SKOPIROVAT_POLNYY_KLYUCH_253 = """            📋 Скопировать полный ключ
"""
UI_WEB_TEMPLATES_INSTRUKTSIYA_ESLI_PRILOZHENIE__261 = """            💡 <b>Инструкция:</b> Если приложение не открылось автоматически (например, на ПК в Windows или при переходе из стороннего браузера), нажмите кнопку <b>«Скопировать полный ключ»</b> выше, затем откройте AmneziaУстройство и выберите <b>«Добавить подключение» → «Вставить ключ из буфера»</b>.
"""
UI_WEB_TEMPLATES_SSYLKA_USTARELA_278 = """    <title>Ссылка устарела</title>
"""
UI_WEB_TEMPLATES_SROK_DEYSTVIYA_ISTEK_283 = """        <div class="badge" style="background: rgba(239, 68, 68, 0.15); color: #f87171;">⏳ Срок действия истёк</div>
"""
UI_WEB_TEMPLATES_SSYLKA_USTARELA_284 = """        <h1>Ссылка устарела</h1>
"""
UI_WEB_TEMPLATES_SROK_DEYSTVIYA_SSYLKI_15_MINUT_285 = """        <p>Срок действия ссылки (15 минут) истёк в целях безопасности.</p>
"""
UI_WEB_TEMPLATES_VERNITES_V_KARTOCHKU_USTROYSTV_287 = """            Вернитесь в карточку устройства в Telegram-боте и нажмите кнопку <b>«Открыть в Amnezia»</b> ещё раз, чтобы получить свежую ссылку.
"""
UI_WEB_TEMPLATES_OSHIBKA_DOSTUPA_309 = """        <div class="badge" style="background: rgba(239, 68, 68, 0.15); color: #f87171;">⚠️ Ошибка доступа</div>
"""
UI_WEB_TEMPLATES_VERNITES_V_TELEGRAM_BOT_DLYA_P_313 = """            Вернитесь в Telegram-бот для проверки статуса подписки и устройства.
"""
UI_WEB_TEMPLATES_OSHIBKA_SERVERA_328 = """    <title>Ошибка сервера</title>
"""
UI_WEB_TEMPLATES_VREMENNAYA_OSHIBKA_333 = """        <div class="badge" style="background: rgba(239, 68, 68, 0.15); color: #f87171;">⚠️ Временная ошибка</div>
"""
UI_WEB_TEMPLATES_VREMENNAYA_OSHIBKA_SERVERA_334 = """        <h1>Временная ошибка сервера</h1>
"""
UI_WEB_TEMPLATES_NE_UDALOS_SFORMIROVAT_KLYUCH_P_335 = """        <p>Не удалось сформировать ключ подключения. Пожалуйста, попробуйте позже.</p>
"""
UI_WEB_TEMPLATES_ESLI_OSHIBKA_POVTORYAETSYA_OBR_337 = """            Если ошибка повторяется, обратитесь в службу поддержки через Telegram-бота.
"""
UI_WEB_TEMPLATES_404_SSYLKA_NE_NAYDENA_27 = "<head><meta charset='utf-8'><title>404 — Ссылка не найдена</title></head>"
UI_WEB_TEMPLATES_404_SSYLKA_NE_NAYDENA_29 = '<h2>404 — Ссылка не найдена</h2>'
UI_WEB_TEMPLATES_SSYLKA_NA_PODPISKU_NEDEYSTVITE_30 = "<p style='color:#8b949e'>Ссылка на подписку недействительна или устарела.</p>"
UI_WEB_TEMPLATES_SLISHKOM_MNOGO_ZAPROSOV_37 = "<head><meta charset='utf-8'><title>Слишком много запросов</title></head>"
UI_WEB_TEMPLATES_SLISHKOM_MNOGO_ZAPROSOV_39 = '<h2>Слишком много запросов</h2>'
UI_WEB_TEMPLATES_POZHALUYSTA_PODOZHDITE_NEMNOGO_40 = "<p style='color:#8b949e'>Пожалуйста, подождите немного и повторите попытку.</p>"
UI_WEB_TEMPLATES_PODPISKA_NE_AKTIVNA_INCY_BOX_S_53 = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
  <title>Подписка не активна — INCY</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: #0f1117;
      color: #e6edf3;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 24px;
      text-align: center;
    }}
    .card {{
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 16px;
      padding: 32px 24px;
      max-width: 440px;
      width: 100%;
      box-shadow: 0 8px 24px rgba(0,0,0,0.5);
    }}
    .icon {{
      font-size: 48px;
      margin-bottom: 16px;
    }}
    h1 {{
      font-size: 20px;
      font-weight: 600;
      margin-bottom: 12px;
      color: #ffffff;
    }}
    p {{
      font-size: 14px;
      color: #8b949e;
      line-height: 1.5;
      margin-bottom: 24px;
    }}
    .btn {{
      display: flex;
      align-items: center;
      justify-content: center;
      width: 100%;
      padding: 14px 20px;
      border-radius: 12px;
      font-size: 15px;
      font-weight: 600;
      text-decoration: none;
      cursor: pointer;
      border: none;
      transition: background-color 0.2s, transform 0.1s;
      margin-bottom: 12px;
    }}
    .btn:active {{ transform: scale(0.98); }}
    .btn-primary {{
      background-color: #1f6feb;
      color: #ffffff;
    }}
    .btn-primary:hover {{
      background-color: #388bfd;
    }}
    .btn-secondary {{
      background-color: #21262d;
      color: #c9d1d9;
      border: 1px solid #30363d;
    }}
    .btn-secondary:hover {{
      background-color: #30363d;
    }}
    .toast {{
      display: none;
      background-color: #238636;
      color: white;
      padding: 10px 18px;
      border-radius: 8px;
      font-size: 13px;
      margin-top: 12px;
      animation: fadeIn 0.3s;
    }}
    @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(-6px); }} to {{ opacity: 1; transform: translateY(0); }} }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">⚠️</div>
    <h1>Подписка не активна</h1>
    <p>Срок действия вашей подписки истёк или она приостановлена. Для продления тарифа вернитесь в бот или свяжитесь с поддержкой.</p>
    
    <a class="btn btn-primary" href="{escaped_support_url}">💬 Связаться с поддержкой</a>
    <button class="btn btn-secondary" onclick="copyLink()">📋 Скопировать ссылку на подписку</button>
    <div id="toast" class="toast">✓ Ссылка скопирована в буфер обмена</div>
  </div>

  <script>
    const subUrl = {js_sub_url};
    function copyLink() {{
      if (navigator.clipboard && window.isSecureContext) {{
        navigator.clipboard.writeText(subUrl).then(showToast);
      }} else {{
        const textArea = document.createElement("textarea");
        textArea.value = subUrl;
        textArea.style.position = "fixed";
        textArea.style.left = "-999999px";
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        try {{
          document.execCommand('copy');
          showToast();
        }} catch (err) {{}}
        document.body.removeChild(textArea);
      }}
    }}
    function showToast() {{
      const toast = document.getElementById("toast");
      toast.style.display = "block";
      setTimeout(() => {{ toast.style.display = "none"; }}, 3000);
    }}
  </script>
</body>
</html>"""
UI_WEB_TEMPLATES_PODKLYUCHENIE_K_INCY_BOX_SIZIN_188 = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
  <title>Подключение к INCY</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: #0f1117;
      color: #e6edf3;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 24px;
      text-align: center;
    }}
    .card {{
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 16px;
      padding: 32px 24px;
      max-width: 440px;
      width: 100%;
      box-shadow: 0 8px 24px rgba(0,0,0,0.5);
    }}
    .icon {{
      font-size: 48px;
      margin-bottom: 16px;
    }}
    h1 {{
      font-size: 20px;
      font-weight: 600;
      margin-bottom: 12px;
      color: #ffffff;
    }}
    p {{
      font-size: 14px;
      color: #8b949e;
      line-height: 1.5;
      margin-bottom: 24px;
    }}
    .btn {{
      display: flex;
      align-items: center;
      justify-content: center;
      width: 100%;
      padding: 14px 20px;
      border-radius: 12px;
      font-size: 15px;
      font-weight: 600;
      text-decoration: none;
      cursor: pointer;
      border: none;
      transition: background-color 0.2s, transform 0.1s;
      margin-bottom: 12px;
    }}
    .btn:active {{ transform: scale(0.98); }}
    .btn-primary {{
      background-color: #238636;
      color: #ffffff;
    }}
    .btn-primary:hover {{
      background-color: #2ea043;
    }}
    .btn-secondary {{
      background-color: #21262d;
      color: #c9d1d9;
      border: 1px solid #30363d;
    }}
    .btn-secondary:hover {{
      background-color: #30363d;
    }}
    .divider {{
      border-top: 1px solid #30363d;
      margin: 20px 0;
    }}
    .download-section {{
      font-size: 13px;
      color: #8b949e;
    }}
    .download-links {{
      display: flex;
      justify-content: center;
      gap: 12px;
      margin-top: 10px;
      flex-wrap: wrap;
    }}
    .download-links a {{
      color: #58a6ff;
      text-decoration: none;
      font-weight: 500;
    }}
    .download-links a:hover {{ text-decoration: underline; }}
    .toast {{
      display: none;
      background-color: #238636;
      color: white;
      padding: 10px 18px;
      border-radius: 8px;
      font-size: 13px;
      margin-top: 12px;
      animation: fadeIn 0.3s;
    }}
    @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(-6px); }} to {{ opacity: 1; transform: translateY(0); }} }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">🚀</div>
    <h1>Подключение к INCY</h1>
    <p>Нажмите кнопку ниже, чтобы открыть и импортировать подписку в приложении INCY:</p>
    
    <a class="btn btn-primary" href="{escaped_deep_link}">📱 Открыть в приложении INCY</a>
    <button class="btn btn-secondary" onclick="copyLink()">📋 Скопировать ссылку на подписку</button>
    <div id="toast" class="toast">✓ Ссылка скопирована в буфер обмена</div>

    <div class="divider"></div>

    <div class="download-section">
      <span>Приложение ещё не установлено?</span>
      <div class="download-links">
        <a href="https://apps.apple.com/app/incy/id6756943388" target="_blank" rel="noopener">iOS (App Store)</a>
        <span>•</span>
        <a href="https://play.google.com/store/apps/details?id=llc.itdev.incy" target="_blank" rel="noopener">Android (Google Play)</a>
        <span>•</span>
        <a href="https://github.com/INCY-DEV/incy-platforms" target="_blank" rel="noopener">GitHub</a>
        <span>•</span>
        <a href="https://incy.cc/" target="_blank" rel="noopener">Сайт INCY</a>
      </div>
      <p style="font-size: 12px; color: #8b949e; margin-top: 14px; line-height: 1.4;">
        💻 <b>Пользователям на ПК:</b> для Windows 10/11 (x64) и macOS 14+ используйте <b>AmneziaУстройство</b> (ключ или файл), для других версий (Windows 7/8/ARM, macOS 12/13) — <b>AmneziaWG</b> с файлом конфигурации <code>.conf</code> (в Telegram-боте).
      </p>
    </div>
  </div>

  <script>
    const deepLink = {js_deep_link};
    const subUrl = {js_sub_url};
    
    try {{
      window.location.href = deepLink;
    }} catch (e) {{}}

    function copyLink() {{
      if (navigator.clipboard && window.isSecureContext) {{
        navigator.clipboard.writeText(subUrl).then(showToast);
      }} else {{
        const textArea = document.createElement("textarea");
        textArea.value = subUrl;
        textArea.style.position = "fixed";
        textArea.style.left = "-999999px";
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        try {{
          document.execCommand('copy');
          showToast();
        }} catch (err) {{}}
        document.body.removeChild(textArea);
      }}
    }}

    function showToast() {{
      const toast = document.getElementById("toast");
      toast.style.display = "block";
      setTimeout(() => {{ toast.style.display = "none"; }}, 3000);
    }}
  </script>
</body>
</html>"""
