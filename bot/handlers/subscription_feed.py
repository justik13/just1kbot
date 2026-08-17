import html
import json
import logging
from aiohttp import web

from config.settings import get_settings
from database.connection import session_scope
from services.subscription import SubscriptionService
from services.subscription_feed_service import SubscriptionFeedService
from services.subscription_token_service import (
    MAX_SUBSCRIPTION_TOKEN_LENGTH,
    SubscriptionTokenService,
)

logger = logging.getLogger(__name__)


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


def _render_inactive_html(sub_url: str, bot_username: str) -> str:
    escaped_bot_url = (
        html.escape(f"https://t.me/{bot_username}") if bot_username else "#"
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
    <p>Срок действия вашей подписки истёк или она приостановлена. Для возобновления доступа продлите тариф в Telegram-боте.</p>
    
    <a class="btn btn-primary" href="{escaped_bot_url}">🤖 Продлить в Telegram-боте</a>
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


def _render_open_html(sub_url: str, deep_link: str) -> str:
    escaped_deep_link = html.escape(deep_link)
    js_deep_link = json.dumps(deep_link)
    js_sub_url = json.dumps(sub_url)

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
  <title>Подключение к INCY</title>
  <meta http-equiv="refresh" content="0;url={escaped_deep_link}">
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
    <h1>Открытие приложения INCY...</h1>
    <p>Если приложение не открылось автоматически, нажмите кнопку ниже:</p>
    
    <a class="btn btn-primary" href="{escaped_deep_link}">📱 Открыть в приложении INCY</a>
    <button class="btn btn-secondary" onclick="copyLink()">📋 Скопировать ссылку</button>
    <div id="toast" class="toast">✓ Ссылка скопирована в буфер обмена</div>

    <div class="divider"></div>

    <div class="download-section">
      <span>Приложение ещё не установлено?</span>
      <div class="download-links">
        <a href="https://apps.apple.com/app/incy-vpn/id6475727187" target="_blank" rel="noopener">App Store</a>
        <span>•</span>
        <a href="https://play.google.com/store/apps/details?id=cc.incy.app" target="_blank" rel="noopener">Google Play</a>
        <span>•</span>
        <a href="https://incy.cc/" target="_blank" rel="noopener">Официальный сайт</a>
      </div>
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


async def subscription_feed_handler(request: web.Request) -> web.Response:
    token = request.match_info.get("token", "").strip()
    if not token or len(token) > MAX_SUBSCRIPTION_TOKEN_LENGTH:
        return web.Response(status=404, text="Not Found")

    async with session_scope() as session:
        user = await SubscriptionTokenService.get_user_by_token(session, token)
        if not user:
            return web.Response(status=404, text="Not Found")

        # Security: never log full secret token, log user_id only
        logger.info("Subscription feed requested for user_id=%s", user.id)

        status, headers, body = await SubscriptionFeedService.build_feed(session, user)
        return web.Response(
            status=status,
            text=body,
            headers=headers,
        )


async def subscription_open_handler(request: web.Request) -> web.Response:
    token = request.match_info.get("token", "").strip()
    if not token or len(token) > MAX_SUBSCRIPTION_TOKEN_LENGTH:
        return web.Response(
            status=404,
            text="<!DOCTYPE html><html><body style='background:#0f1117;color:#fff;text-align:center;padding:50px;font-family:sans-serif'><h2>404 — Ссылка не найдена</h2><p style='color:#8b949e'>Ссылка на подписку недействительна или устарела.</p></body></html>",
            headers=SECURITY_HEADERS,
        )

    async with session_scope() as session:
        user = await SubscriptionTokenService.get_user_by_token(session, token)
        if not user:
            return web.Response(
                status=404,
                text="<!DOCTYPE html><html><body style='background:#0f1117;color:#fff;text-align:center;padding:50px;font-family:sans-serif'><h2>404 — Ссылка не найдена</h2><p style='color:#8b949e'>Ссылка на подписку недействительна или устарела.</p></body></html>",
                headers=SECURITY_HEADERS,
            )

        # Security: never log full secret token, log user_id only
        logger.info("Subscription open bridge requested for user_id=%s", user.id)

        settings = get_settings()
        sub_url = f"https://{settings.DOMAIN}/sub/{token}"
        deep_link = f"incy://import/{sub_url}"

        has_access = SubscriptionService.check_vpn_access(user)
        if not has_access:
            bot_username = getattr(settings, "SUPPORT_USERNAME", "")
            html_content = _render_inactive_html(sub_url, bot_username)
        else:
            html_content = _render_open_html(sub_url, deep_link)

        return web.Response(
            status=200,
            text=html_content,
            headers=SECURITY_HEADERS,
        )
