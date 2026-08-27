from bot import texts
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
        "default-src 'none'; "+
        "style-src 'unsafe-inline'; "+
        "script-src 'unsafe-inline'; "+
        "img-src data:; "+
        "connect-src 'none'; "+
        "form-action 'none'; "+
        "base-uri 'none'; "+
        "frame-ancestors 'none';"
    ),
}

NOT_FOUND_HTML = (
    "<!DOCTYPE html>"+
    "<html lang='ru'>"+
    texts.UI_WEB_TEMPLATES_404_SSYLKA_NE_NAYDENA_27+
    "<body style='background:#0f1117;color:#fff;text-align:center;padding:50px;font-family:sans-serif'>"+
    texts.UI_WEB_TEMPLATES_404_SSYLKA_NE_NAYDENA_29+
    texts.UI_WEB_TEMPLATES_SSYLKA_NA_PODPISKU_NEDEYSTVITE_30+
    "</body></html>"
)

TOO_MANY_REQUESTS_HTML = (
    "<!DOCTYPE html>"+
    "<html lang='ru'>"+
    texts.UI_WEB_TEMPLATES_SLISHKOM_MNOGO_ZAPROSOV_37+
    "<body style='background:#0f1117;color:#fff;text-align:center;padding:50px;font-family:sans-serif'>"+
    texts.UI_WEB_TEMPLATES_SLISHKOM_MNOGO_ZAPROSOV_39+
    texts.UI_WEB_TEMPLATES_POZHALUYSTA_PODOZHDITE_NEMNOGO_40+
    "</body></html>"
)


def render_inactive_html(sub_url: str, support_username: str) -> str:
    escaped_support_url = (
        html.escape(f"https://t.me/{support_username}")
        if support_username
        else "#"
    )
    js_sub_url = json.dumps(sub_url)

    return texts.UI_WEB_TEMPLATES_PODPISKA_NE_AKTIVNA_INCY_BOX_S_53.format(escaped_support_url=escaped_support_url, js_sub_url=js_sub_url)


def render_open_html(sub_url: str, deep_link: str) -> str:
    escaped_deep_link = html.escape(deep_link)
    js_deep_link = json.dumps(deep_link)
    js_sub_url = json.dumps(sub_url)

    return texts.UI_WEB_TEMPLATES_PODKLYUCHENIE_K_INCY_BOX_SIZIN_188.format(escaped_deep_link=escaped_deep_link, js_deep_link=js_deep_link, js_sub_url=js_sub_url)
