# Точечные переопределения текстов.
# Применяются после user_texts и admin_texts.

OVERRIDES = {
    "ADMIN_SERVER_URL_PROMPT": (
        "🔗 Введите API URL сервера "
        "(например: https://vpn.example.com:8443):"
    ),

    "ERROR_INVALID_URL": """⚠️ Некорректный формат URL.

URL должен начинаться с <code>https://</code>

Пример: <code>https://vpn.example.com:8443</code>""",
}