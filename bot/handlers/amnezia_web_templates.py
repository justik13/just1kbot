import html

AMNEZIA_SECURITY_HEADERS = {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "X-Permitted-Cross-Domain-Policies": "none",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'none'; "
        "style-src 'unsafe-inline'; "
        "script-src 'unsafe-inline'; "
        "connect-src 'none'; "
        "img-src 'none'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'none'; "
        "form-action 'none';"
    ),
}

BASE_CSS = """
        :root {
            --bg: #0f172a;
            --card-bg: #1e293b;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --accent: #3b82f6;
            --accent-hover: #2563eb;
            --accent-glow: rgba(59, 130, 246, 0.3);
            --border: #334155;
            --badge-bg: rgba(245, 158, 11, 0.15);
            --badge-text: #fbbf24;
            --success: #10b981;
            --error: #ef4444;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text-primary);
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            padding: 20px;
        }
        .card {
            background-color: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 32px 24px;
            max-width: 440px;
            width: 100%;
            text-align: center;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5), 0 8px 10px -6px rgba(0, 0, 0, 0.5);
        }
        .badge {
            display: inline-block;
            background-color: var(--badge-bg);
            color: var(--badge-text);
            padding: 4px 12px;
            border-radius: 9999px;
            font-size: 13px;
            font-weight: 600;
            margin-bottom: 20px;
        }
        h1 {
            font-size: 22px;
            font-weight: 700;
            margin-bottom: 12px;
            line-height: 1.3;
        }
        p {
            color: var(--text-secondary);
            font-size: 15px;
            line-height: 1.5;
            margin-bottom: 24px;
        }
        .btn {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            width: 100%;
            padding: 14px 20px;
            border-radius: 12px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            border: none;
            transition: all 0.2s ease;
            text-decoration: none;
            margin-bottom: 12px;
        }
        .btn-primary {
            background-color: var(--accent);
            color: white;
            box-shadow: 0 4px 14px 0 var(--accent-glow);
        }
        .btn-primary:hover {
            background-color: var(--accent-hover);
        }
        .btn-secondary {
            background-color: var(--border);
            color: var(--text-primary);
        }
        .btn-secondary:hover {
            background-color: #475569;
        }
        .countdown {
            font-size: 14px;
            color: var(--text-secondary);
            margin-bottom: 16px;
        }
        .feedback {
            display: none;
            font-size: 14px;
            color: var(--success);
            margin-top: 12px;
            padding: 8px 12px;
            background: rgba(16, 185, 129, 0.1);
            border-radius: 8px;
            word-break: break-word;
        }
        .textarea-fallback {
            width: 100%;
            height: 110px;
            background: #0f172a;
            border: 1px solid var(--border);
            border-radius: 8px;
            color: var(--text-primary);
            font-family: monospace;
            font-size: 12px;
            padding: 8px;
            margin-top: 12px;
            resize: none;
            word-break: break-all;
        }
        .hint {
            font-size: 13px;
            color: var(--text-secondary);
            margin-top: 20px;
            border-top: 1px solid var(--border);
            padding-top: 16px;
            text-align: left;
        }
"""

BRIDGE_JS = """
        var timerSeconds = 3;
        var timerElement = document.getElementById("timer");
        var countdownElement = document.getElementById("countdown-text");
        var triggered = false;

        function getVpnUri() {
            var el = document.getElementById("vpn-key");
            return el ? el.value : "";
        }

        function openVpnApp() {
            var uri = getVpnUri();
            if (uri) {
                window.location.href = uri;
            }
        }

        function copyVpnKey() {
            var vpnKeyEl = document.getElementById("vpn-key");
            var feedback = document.getElementById("copy-feedback");
            var text = vpnKeyEl ? vpnKeyEl.value : "";

            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(function() {
                    feedback.innerText = "✅ Ключ скопирован в буфер обмена!";
                    feedback.style.display = "block";
                }).catch(function() {
                    fallbackCopy(vpnKeyEl, feedback);
                });
            } else {
                fallbackCopy(vpnKeyEl, feedback);
            }
        }

        function fallbackCopy(vpnKeyEl, feedback) {
            if (vpnKeyEl) {
                vpnKeyEl.removeAttribute("hidden");
                vpnKeyEl.focus();
                vpnKeyEl.select();
            }
            feedback.innerText = "📋 Выделите и скопируйте полный ключ вручную, затем вставьте его в AmneziaVPN.";
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


def render_amnezia_bridge_html(
    vpn_uri: str,
    server_name: str,
    device_name: str,
    country_flag: str = "",
) -> str:
    safe_server = html.escape(server_name, quote=True)
    safe_device = html.escape(device_name, quote=True)
    safe_flag = html.escape(country_flag, quote=True)
    safe_vpn_uri = html.escape(vpn_uri, quote=True)

    header_title = f"{safe_flag} {safe_server} — {safe_device}".strip() if safe_flag else f"{safe_server} — {safe_device}"

    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"ru\">\n"
        "<head>\n"
        "    <meta charset=\"utf-8\">\n"
        "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        "    <title>Подключение к AmneziaVPN</title>\n"
        f"    <style>{BASE_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        "    <div class=\"card\">\n"
        "        <div class=\"badge\">🧪 Экспериментальная функция</div>\n"
        f"        <h1>{header_title}</h1>\n"
        "        <div class=\"countdown\" id=\"countdown-text\">Автоматическое открытие через <b id=\"timer\">3</b> сек...</div>\n"
        "\n"
        "        <button class=\"btn btn-primary\" id=\"open-btn\" onclick=\"openVpnApp()\">\n"
        "            🚀 Попробовать открыть в AmneziaVPN\n"
        "        </button>\n"
        "\n"
        "        <button class=\"btn btn-secondary\" id=\"copy-btn\" onclick=\"copyVpnKey()\">\n"
        "            📋 Скопировать полный ключ\n"
        "        </button>\n"
        "\n"
        "        <div class=\"feedback\" id=\"copy-feedback\"></div>\n"
        "\n"
        f"        <textarea id=\"vpn-key\" class=\"textarea-fallback\" hidden readonly>{safe_vpn_uri}</textarea>\n"
        "\n"
        "        <div class=\"hint\">\n"
        "            💡 <b>Инструкция:</b> Если приложение не открылось автоматически, скопируйте полный ключ с помощью кнопки выше и вставьте его в поле подключения AmneziaVPN.\n"
        "        </div>\n"
        "    </div>\n"
        "\n"
        f"    <script>{BRIDGE_JS}</script>\n"
        "</body>\n"
        "</html>"
    )


def render_expired_html() -> str:
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"ru\">\n"
        "<head>\n"
        "    <meta charset=\"utf-8\">\n"
        "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        "    <title>Ссылка устарела</title>\n"
        f"    <style>{BASE_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        "    <div class=\"card\">\n"
        "        <div class=\"badge\" style=\"background: rgba(239, 68, 68, 0.15); color: #f87171;\">⏳ Срок действия истёк</div>\n"
        "        <h1>Ссылка устарела</h1>\n"
        "        <p>Срок действия ссылки (15 минут) истёк в целях безопасности.</p>\n"
        "        <div class=\"hint\" style=\"border-top: none; text-align: center;\">\n"
        "            Вернитесь в карточку устройства в Telegram-боте и нажмите кнопку <b>«Открыть в Amnezia»</b> ещё раз, чтобы получить свежую ссылку.\n"
        "        </div>\n"
        "    </div>\n"
        "</body>\n"
        "</html>"
    )


def render_error_html(title: str, message: str) -> str:
    safe_title = html.escape(title, quote=True)
    safe_message = html.escape(message, quote=True)
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"ru\">\n"
        "<head>\n"
        "    <meta charset=\"utf-8\">\n"
        "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        f"    <title>{safe_title}</title>\n"
        f"    <style>{BASE_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        "    <div class=\"card\">\n"
        "        <div class=\"badge\" style=\"background: rgba(239, 68, 68, 0.15); color: #f87171;\">⚠️ Ошибка доступа</div>\n"
        f"        <h1>{safe_title}</h1>\n"
        f"        <p>{safe_message}</p>\n"
        "        <div class=\"hint\" style=\"border-top: none; text-align: center;\">\n"
        "            Вернитесь в Telegram-бот для проверки статуса подписки и устройства.\n"
        "        </div>\n"
        "    </div>\n"
        "</body>\n"
        "</html>"
    )


def render_500_html() -> str:
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"ru\">\n"
        "<head>\n"
        "    <meta charset=\"utf-8\">\n"
        "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        "    <title>Ошибка сервера</title>\n"
        f"    <style>{BASE_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        "    <div class=\"card\">\n"
        "        <div class=\"badge\" style=\"background: rgba(239, 68, 68, 0.15); color: #f87171;\">⚠️ Временная ошибка</div>\n"
        "        <h1>Временная ошибка сервера</h1>\n"
        "        <p>Не удалось сформировать ключ подключения. Пожалуйста, попробуйте позже.</p>\n"
        "        <div class=\"hint\" style=\"border-top: none; text-align: center;\">\n"
        "            Если ошибка повторяется, обратитесь в службу поддержки через Telegram-бота.\n"
        "        </div>\n"
        "    </div>\n"
        "</body>\n"
        "</html>"
    )
