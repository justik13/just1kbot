# 📚 INCY APPLICATION & SUBSCRIPTION FORMAT — ПОЛНАЯ ТЕХНИЧЕСКАЯ СПРАВКА

> **ОБЛАСТЬ ДЕЙСТВИЯ:** Документ содержит полную техническую спецификацию протокола подписок, deep links, share-ссылок и HTTP-заголовков приложения **INCY** для разработчиков и интеграторов VPN-сервисов.
> Источники: официальная документация [INCY Developer Documentation](https://docs.incy.cc/) и репозитории [`INCY-DEV`](https://github.com/INCY-DEV).

---

## 🧭 1. ОБЩИЙ ОБЗОР И ПОДДЕРЖКА ПЛАТФОРМ

| Платформа | AmneziaWG (`amneziawg://`, `awg://`) | WireGuard (`wireguard://`, `.conf`) | Xray (VLESS, VMess, Trojan, SS, Hy2) |
|---|:---:|:---:|:---:|
| **iOS** (App Store) | ✅ Полная поддержка | ✅ Полная поддержка | ✅ Полная поддержка |
| **Android** (Google Play) | ✅ Полная поддержка | ✅ Полная поддержка | ✅ Полная поддержка |
| **Desktop** (Windows / macOS / Linux) | ❌ **Не поддерживается** (схемы игнорируются) | ✅ Только чистый `.conf` / `wireguard://` | ✅ Полная поддержка |

### ⚠️ Критическое ограничение десктопного клиента INCY
По официальной спецификации разработчиков INCY:
* В мобильных клиентах (iOS / Android) парсер нативно поддерживает схемы `amneziawg://`, `awg://`, JSON-контейнеры AmneziaWG и параметры обфускации (`Jc`, `S1-S4`, `H1-H4`, `I1-I5`).
* В десктопном клиенте (Windows / macOS / Linux) схемы `amneziawg://` и `awg://` **целенаправленно игнорируются парсером** (список нод остаётся пустым).
* **Правило для клиентов на ПК:** Для подключения к AmneziaWG на компьютерах используется официальное приложение **AmneziaVPN** (импорт ключа `vpn://...` или файла `.conf` / `.vpn`).

---

## 📦 2. ФОРМАТ ТЕЛА ПОДПИСКИ (`GET /sub/{token}`)

Подписка выдаётся по протоколу HTTP/HTTPS в одном из поддерживаемых форматов:

### Формат 1: Base64-закодированные строки (Основной формат `just1kbot`)
Тело HTTP-ответа кодируется стандартным `Base64`, внутри которого находятся строки протокольных ссылок (по одной на строку):
```text
base64(
  amneziawg://<base64url-conf>#🇵🇱 Warsaw — iPhone
  amneziawg://<base64url-conf>#🇩🇪 Frankfurt — MacBook
)
```

### 🔑 ПРАВИЛО BASE64 ДЛЯ AMNEZIAWG URI
В строке `amneziawg://<payload>#tag`:
* `<payload>` **ОБЯЗАТЕЛЬНО кодируется в URL-Safe Base64** (`base64.urlsafe_b64encode` в Python).
* Символы `+` заменяются на `-`, `/` заменяются на `_`.
* Паддинг `=` на конце является опциональным.
* Содержимое `<payload>` — это текстовый файл конфигурации WireGuard / AmneziaWG INI (`[Interface]` + `[Peer]`).

### Формат 2: JSON-контейнер AmneziaWG
```json
{
  "type": "amneziawg",
  "version": 1,
  "servers": [
    { "name": "Warsaw", "config": "<base64url-conf>" },
    { "name": "Frankfurt", "config": "<base64url-conf>" }
  ]
}
```

### Формат 3: Сырой `.conf` в теле (Single Server)
Если тело подписки содержит текстовый `[Interface]` с `PrivateKey`, клиент распознаёт его как одиночный сервер.

---

## 🌐 3. СТАНДАРТНЫЕ HTTP-ЗАГОЛОВКИ ПОДПИСКИ (СЕРВЕР → КЛИЕНТ)

HTTP-заголовки имеют наивысший приоритет при настройке параметров подписки в приложении INCY:

| Заголовок | Формат значения | Назначение в приложении |
| :--- | :--- | :--- |
| `profile-title` | `string` или `base64:...` | Отображаемое название подписки в списке (до 25 символов). |
| `profile-description` | `string` или `base64:...` | Описание подписки, отображаемое в карточке профиля. |
| `subscription-userinfo` | `upload=X; download=Y; total=Z; expire=T` | Статистика трафика и срок действия. `expire` — Unix timestamp (сек). При `subscription-userinfo: 0` виджет скрывается. |
| `profile-update-interval` | целое число (часы, напр. `6`) | Интервал фонового автообновления подписки. |
| `support-url` | URL (напр. `https://t.me/support_bot`) | Ссылка на техподдержку. Для ссылок Telegram клиент автоматически отображает логотип TG. |
| `support-email` | email | Добавляет кнопку связи по электронной почте. |
| `profile-web-page-url` | URL | Ссылка на личный кабинет / веб-сайт провайдера. |
| `announce` | `string` или `base64:...` | Текстовый баннер объявления в карточке подписки (до 200 символов, до 5 строк). |
| `announce-url` | URL | Ссылка для перехода при клике по объявлению. |
| `sort-order` | `none` \| `ping` \| `name` | Порядок сортировки серверов по умолчанию. |
| `hide-url` | `1` \| `0` | Запрещает пользователю копировать и экспортировать исходный URL подписки. |
| `hide-check` | `1` \| `0` | Скрывает кнопку «Проверить подключение» на главном экране. |

> **💡 Поддержка UTF-8 / Base64 в заголовках:**
> Для безопасной передачи кириллицы в HTTP-заголовках используется префикс `base64:`:
> `profile-description: base64:0JTQvtGB0YLRg9C/INC6IHByZW1pdW0g0YHQtdGA0LLQtdGA0LDQvA==`

---

## 🔗 4. DEEP LINKS ПРИЛОЖЕНИЯ INCY

INCY регистрирует схему `incy://` для управления состоянием и быстрого импорта:

### 4.1 Управление состоянием VPN
* `incy://connect` или `incy://open` — включить VPN;
* `incy://disconnect` или `incy://close` — отключить VPN;
* `incy://toggle` — переключить состояние (вкл/выкл);
* `incy://status` — открыть приложение и показать статус.

### 4.2 Импорт подписок и конфигураций
* `incy://import/{url_или_base64}` — универсальный импорт с автоопределением типа (подписка, единичный сервер, WireGuard `.conf`);
* `incy://add/{url}` — прямой импорт подписки по URL;
* `incy://crypt1/{payload}` — зашифрованная/обфусцированная ссылка (AES-256-GCM) для защиты от блокировок сканерами мессенджеров.

### 4.3 Управление маршрутизацией
* `incy://autorouting/onadd/{url}` — импорт профиля маршрутизации с автообновлением по URL;
* `incy://routing/onadd/{base64}` — импорт и активация статического JSON-профиля маршрутизации;
* `incy://routing/off` — полное отключение встроенной маршрутизации.

---

## 📡 5. SHARE LINKS (СИНТАКСИС ПРОТОКОЛЬНЫХ ССЫЛОК)

### WireGuard
```text
wireguard://<secretKey>@<host>:<port>?publickey=<KEY>&address=<ADDR>&mtu=1280&reserved=1,22,33#<ServerName>?serverDescription=<base64>
```

### AmneziaWG
```text
amneziawg://<base64url_conf>#<ServerName>?serverDescription=<base64>
awg://<base64url_conf>#<ServerName>?serverDescription=<base64>
```

### VLESS (Xray)
```text
vless://<uuid>@<host>:<port>?encryption=none&flow=xtls-rprx-vision&security=reality&sni=<sni>&pbk=<public_key>&sid=<short_id>&fp=chrome&type=tcp#<ServerName>
```

### Hysteria2
```text
hysteria2://<password>@<host>:<port>?sni=<sni>&insecure=0#<ServerName>
hy2://<password>@<host>:<port>?sni=<sni>#<ServerName>
```

---

## 🔄 6. ЗАГОЛОВКИ ЗАПРОСА КЛИЕНТА (КЛИЕНТ → СЕРВЕР)

При каждом обновлении подписки клиент INCY передаёт:
* `User-Agent`: `INCY/<version>/<platform>` (например, `INCY/3.4.2/windows 11 Dalvik/21.0.8+9-LTS` или `INCY/3.4.2/iOS`);
* `Accept`: `*/*`;
* `Accept-Language`: язык системы (например, `ru-RU`);
* `x-app-version`: версия клиента;
* `x-client`: `INCY`;
* `x-hwid` (или `X-Device-ID` на Android): анонимизированный идентификатор устройства для контроля лимитов.
