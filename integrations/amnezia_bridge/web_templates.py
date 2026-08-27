from bot import texts
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
        "default-src 'none'; "+
        "style-src 'unsafe-inline'; "+
        "script-src 'unsafe-inline'; "+
        "connect-src 'none'; "+
        "img-src 'none'; "+
        "object-src 'none'; "+
        "frame-ancestors 'none'; "+
        "base-uri 'none'; "+
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

BRIDGE_JS = texts.UI_WEB_TEMPLATES_VAR_TIMERSECONDS_3_VAR_TIMEREL_156


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
        "<!DOCTYPE html>\n"+
        "<html lang=\"ru\">\n"+
        "<head>\n"+
        "    <meta charset=\"utf-8\">\n"+
        "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"+
        texts.UI_WEB_TEMPLATES_PODKLYUCHENIE_K_AMNEZIAVPN_239+
        f"    <style>{BASE_CSS}</style>\n"+
        "</head>\n"+
        "<body>\n"+
        "    <div class=\"card\">\n"+
        texts.UI_WEB_TEMPLATES_EKSPERIMENTALNAYA_FUNKTSIYA_244+
        f"        <h1>{header_title}</h1>\n"+
        texts.UI_WEB_TEMPLATES_AVTOMATICHESKOE_OTKRYTIE_CHERE_246+
        "\n"+
        "        <button class=\"btn btn-primary\" id=\"open-btn\" onclick=\"openVpnApp()\">\n"+
        texts.UI_WEB_TEMPLATES_POPROBOVAT_OTKRYT_V_AMNEZIAVPN_249+
        "        </button>\n"+
        "\n"+
        "        <button class=\"btn btn-secondary\" id=\"copy-btn\" onclick=\"copyVpnKey()\">\n"+
        texts.UI_WEB_TEMPLATES_SKOPIROVAT_POLNYY_KLYUCH_253+
        "        </button>\n"+
        "\n"+
        "        <div class=\"feedback\" id=\"copy-feedback\"></div>\n"+
        "\n"+
        f"        <textarea id=\"vpn-key\" class=\"textarea-fallback\" hidden readonly>{safe_vpn_uri}</textarea>\n"+
        "\n"+
        "        <div class=\"hint\">\n"+
        texts.UI_WEB_TEMPLATES_INSTRUKTSIYA_ESLI_PRILOZHENIE__261+
        "        </div>\n"+
        "    </div>\n"+
        "\n"+
        f"    <script>{BRIDGE_JS}</script>\n"+
        "</body>\n"+
        "</html>"
    )


def render_expired_html() -> str:
    return (
        "<!DOCTYPE html>\n"+
        "<html lang=\"ru\">\n"+
        "<head>\n"+
        "    <meta charset=\"utf-8\">\n"+
        "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"+
        texts.UI_WEB_TEMPLATES_SSYLKA_USTARELA_278+
        f"    <style>{BASE_CSS}</style>\n"+
        "</head>\n"+
        "<body>\n"+
        "    <div class=\"card\">\n"+
        texts.UI_WEB_TEMPLATES_SROK_DEYSTVIYA_ISTEK_283+
        texts.UI_WEB_TEMPLATES_SSYLKA_USTARELA_284+
        texts.UI_WEB_TEMPLATES_SROK_DEYSTVIYA_SSYLKI_15_MINUT_285+
        "        <div class=\"hint\" style=\"border-top: none; text-align: center;\">\n"+
        texts.UI_WEB_TEMPLATES_VERNITES_V_KARTOCHKU_USTROYSTV_287+
        "        </div>\n"+
        "    </div>\n"+
        "</body>\n"+
        "</html>"
    )


def render_error_html(title: str, message: str) -> str:
    safe_title = html.escape(title, quote=True)
    safe_message = html.escape(message, quote=True)
    return (
        "<!DOCTYPE html>\n"+
        "<html lang=\"ru\">\n"+
        "<head>\n"+
        "    <meta charset=\"utf-8\">\n"+
        "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"+
        f"    <title>{safe_title}</title>\n"+
        f"    <style>{BASE_CSS}</style>\n"+
        "</head>\n"+
        "<body>\n"+
        "    <div class=\"card\">\n"+
        texts.UI_WEB_TEMPLATES_OSHIBKA_DOSTUPA_309+
        f"        <h1>{safe_title}</h1>\n"+
        f"        <p>{safe_message}</p>\n"+
        "        <div class=\"hint\" style=\"border-top: none; text-align: center;\">\n"+
        texts.UI_WEB_TEMPLATES_VERNITES_V_TELEGRAM_BOT_DLYA_P_313+
        "        </div>\n"+
        "    </div>\n"+
        "</body>\n"+
        "</html>"
    )


def render_500_html() -> str:
    return (
        "<!DOCTYPE html>\n"+
        "<html lang=\"ru\">\n"+
        "<head>\n"+
        "    <meta charset=\"utf-8\">\n"+
        "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"+
        texts.UI_WEB_TEMPLATES_OSHIBKA_SERVERA_328+
        f"    <style>{BASE_CSS}</style>\n"+
        "</head>\n"+
        "<body>\n"+
        "    <div class=\"card\">\n"+
        texts.UI_WEB_TEMPLATES_VREMENNAYA_OSHIBKA_333+
        texts.UI_WEB_TEMPLATES_VREMENNAYA_OSHIBKA_SERVERA_334+
        texts.UI_WEB_TEMPLATES_NE_UDALOS_SFORMIROVAT_KLYUCH_P_335+
        "        <div class=\"hint\" style=\"border-top: none; text-align: center;\">\n"+
        texts.UI_WEB_TEMPLATES_ESLI_OSHIBKA_POVTORYAETSYA_OBR_337+
        "        </div>\n"+
        "    </div>\n"+
        "</body>\n"+
        "</html>"
    )
