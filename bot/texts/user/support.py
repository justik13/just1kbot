"""Domain texts for user/support.py."""
from __future__ import annotations

AMNEZIA_DOCS = "https://storage.googleapis.com/amnezia/docs?m-path=/"
AMNEZIA_DOWNLOAD_MIRROR = "https://storage.googleapis.com/amnezia/amnezia.org?m-path=/downloads"
AMNEZIA_GITHUB_LATEST = "https://github.com/amnezia-vpn/amnezia-client/releases/latest"
AMNEZIA_IOS_RU = "https://storage.googleapis.com/amnezia/docs?m-path=/documentation/instructions/installing-amneziavpn-on-ios/"
AMNEZIA_OFFICIAL_SITE = "https://storage.googleapis.com/amnezia/amnezia.org"
AMNEZIA_SPLIT_TUNNELING = "https://storage.googleapis.com/amnezia/docs?m-path=/documentation/instructions/vpn-split-tunneling/"
AMNEZIA_WIN_INSTALL = "https://storage.googleapis.com/amnezia/docs?m-path=/documentation/instructions/installing-app-on-windows/"
AMNEZIA_WIN_UPDATE = "https://storage.googleapis.com/amnezia/docs?m-path=/documentation/instructions/application-update-on-windows/"

BTN_PRIVACY_POLICY = "🔒 Политика"

BTN_TOS = "📄 Условия сервиса"

FAQ_TEXT = """❓ <b>Частые вопросы (FAQ)</b>

<blockquote expandable>📌 <b>1. Как настроить подключение?</b>
• Откройте раздел «🔌 Подключения».
• Нажмите «🚀 Открыть в приложении INCY» или скопируйте ссылку подписки.
• Для роутеров или ПК: в «⚙️ Управление устройствами» выберите устройство и скачайте файл <code>.conf</code> (или ключ <code>vpn://...</code> и <code>.vpn</code> для AmneziaVPN).</blockquote>

<blockquote expandable>📌 <b>2. Какие приложения использовать для подключения?</b>
• <b>INCY (Рекомендуется)</b> — приложение для подключения по ссылке подписки (iOS, Android).
• <b>AmneziaVPN</b> — поддерживает файлы .vpn и ключи vpn://. Доступно для iOS, Android, Windows, macOS, Linux.
• <b>AmneziaWG</b> — клиент для протокола AmneziaWG (файлы .conf).
• <b>DefaultVPN</b> — альтернативный клиент для iOS (файлы .conf или ключ vpn://).</blockquote>

<blockquote expandable>📌 <b>3. Как проверить работу?</b>
• В приложении статус сменится на «Подключено»;
• В строке состояния появится значок подключения (или 🔑);
• На сайте <code>2ip.io</code> страна изменится на локацию сервера;
• Недоступные ранее сайты и сервисы открываются стабильно.</blockquote>

<blockquote expandable>📌 <b>4. Что делать, если подключение медленное или не работает?</b>
• Переключите локацию (сервер) в приложении.
• Переключитесь между Wi-Fi и мобильной сетью.
• Если возникли вопросы — напишите нам через кнопку «💬 Написать в поддержку».</blockquote>

<blockquote expandable>📌 <b>5. Как продлить доступ или сменить тариф?</b>
• В главном меню откройте <b>«⏳ Моя подписка»</b> или <b>«🚀 Купить доступ»</b>.
• Выберите удобный срок и тариф. При смене тарифа неизрасходованные дни автоматически пересчитываются.</blockquote>

<blockquote expandable>📌 <b>6. На скольких устройствах одновременно работает подписка?</b>
• Количество доступных устройств зависит от выбранного тарифа.
• Актуальный лимит отображается в разделе подписки и в списке подключений.</blockquote>

<blockquote expandable>📌 <b>7. Как получить бонусные дни за приглашенных друзей?</b>
• Откройте «🤝 Пригласить друга» в главном меню.
• Скопируйте ссылку и отправьте друзьям. За каждого приглашенного друга начисляются бонусы при пополнении.</blockquote>

<blockquote expandable>📌 <b>8. Безопасность и приватность</b>
• Трафик шифруется современными протоколами.
• Мы не ведем логи и не сохраняем историю посещений.</blockquote>"""

PRIVACY_POLICY_URL = "https://telegra.ph/Politika-konfidencialnosti-07-23-84"

SUPPORT = "❓ Частые вопросы"

SUPPORT_DOWNLOAD_CLIENT_TEXT = """📥 <b>Скачать клиент Amnezia для подключения</b>

Официальные ссылки для загрузки приложения AmneziaVPN для Windows, Android, iOS, macOS и Linux:

• <b>Прямая ссылка (Зеркало)</b> — загрузка последней сборки клиенту
• <b>GitHub Releases</b> — релизы и бинарные файлы всех версий
• <b>Официальный сайт Amnezia</b> — подробная информация"""

SUPPORT_GREETING_TEMPLATE = "Здравствуйте! Мой ID: {telegram_id}"

SUPPORT_HELP_ROOT_TEXT = """📖 <b>Инструкции и справка AmneziaVPN</b>

Выберите нужную тему ниже:"""

SUPPORT_IOS_INSTRUCTION_TEXT = """🍏 <b>Установка AmneziaVPN на iOS в РФ</b>

Руководство по установке и настройке приложения AmneziaVPN на iPhone и iPad."""

SUPPORT_SPLIT_TUNNELING_TEXT = """🔀 <b>Раздельное туннелирование</b>

Позволяет направлять через защищенное соединение только выбранные сайты и приложения, оставляя остальные ресурсы на прямом подключении."""

SUPPORT_TEXT = """💬 <b>Поддержка</b>

Если у вас возникли вопросы, напишите нашему оператору:

👤 {support_username}

Мы постараемся помочь как можно скорее."""

SUPPORT_WINDOWS_INSTRUCTION_TEXT = """💻 <b>Инструкции для Windows</b>

Руководства по установке и обновлению приложения AmneziaVPN на ПК под управлением Windows:"""

TOS_AGREEMENT_URL = "https://telegra.ph/Polzovatelskoe-soglashenie-07-23-48"
