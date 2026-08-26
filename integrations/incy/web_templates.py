import html
import json

SECURITY_HEADERS = {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Content-Security-Policy": (
        "default-src 'none'; "
        "style-src 'unsafe-inline'; "
        "script-src 'unsafe-inline'; "
        "img-src data:; "
        "connect-src 'none'; "
        "form-action 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none';"
    ),
}

NOT_FOUND_HTML = (
    "<!DOCTYPE html>"
    "<html lang='ru'>"
    "<head><meta charset='utf-8'><title>404 — Ссылка не найдена</title></head>"
    "<body style='background:#0f1117;color:#fff;text-align:center;padding:50px;font-family:sans-serif'>"
    "<h2>404 — Ссылка не найдена</h2>"
    "<p style='color:#8b949e'>Ссылка на подписку недействительна или устарела.</p>"
    "</body></html>"
)

TOO_MANY_REQUESTS_HTML = (
    "<!DOCTYPE html>"
    "<html lang='ru'>"
    "<head><meta charset='utf-8'><title>Слишком много запросов</title></head>"
    "<body style='background:#0f1117;color:#fff;text-align:center;padding:50px;font-family:sans-serif'>"
    "<h2>Слишком много запросов</h2>"
    "<p style='color:#8b949e'>Пожалуйста, подождите немного и повторите попытку.</p>"
    "</body></html>"
)


def render_inactive_html(sub_url: str, support_username: str) -> str:
    escaped_support_url = (
        html.escape(f"https://t.me/{support_username}")
        if support_username
        else "#"
    )
    js_sub_url = json.dumps(sub_url)

    return f"""<!DOCTYPE html>
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


def render_open_html(sub_url: str, deep_link: str) -> str:
    escaped_deep_link = html.escape(deep_link)
    js_deep_link = json.dumps(deep_link)
    js_sub_url = json.dumps(sub_url)

    return f"""<!DOCTYPE html>
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
        💻 <b>Пользователям на ПК:</b> для Windows 10/11 (x64) и macOS 14+ используйте <b>AmneziaVPN</b> (ключ или файл), для других версий (Windows 7/8/ARM, macOS 12/13) — <b>AmneziaWG</b> с файлом конфигурации <code>.conf</code> (в Telegram-боте).
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
