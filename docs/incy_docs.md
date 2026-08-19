# 📚 INCY APPLICATION & SUBSCRIPTION FORMAT — ТЕХНИЧЕСКИЙ СПРАВОЧНИК

> **ПОЛИТИКА ПРОЕКТА ПО ПРОТОКОЛАМ:**
> 1. В боте **JUST1KBOT** используется **ТОЛЬКО AmneziaWG** (чистый стандартный WireGuard не используется из-за блокировок).
> 2. В текущей версии работает **ТОЛЬКО AWG 2.0 (`amneziawg2`)**.
> 3. В планах развития (**Roadmap**): поддержка **AmneziaWG 3.0 (`amneziawg3`)** и стека **Xray** (VLESS-Reality / Hysteria 2).

---

## 🧭 1. ОБЩИЙ ОБЗОР И ПОДДЕРЖКА ПЛАТФОРМ

| Платформа | AmneziaWG 2.0 (`amneziawg://`, `awg://`) | Чистый WireGuard (стандартный) | Xray (VLESS, VMess, Trojan, Hy2) |
|---|:---:|:---:|:---:|
| **Статус в боте** | ✅ **Active Production** (`amneziawg2`) | ❌ **Не используется** | ⏳ **Roadmap** (в планах) |
| **iOS** (App Store) | ✅ Полная поддержка | ✅ Поддерживается клиентом | ✅ Полная поддержка |
| **Android** (Google Play) | ✅ Полная поддержка | ✅ Поддерживается клиентом | ✅ Полная поддержка |
| **Desktop** (Win/macOS/Linux) | ❌ **Игнорируется в INCY** (нужен AmneziaVPN) | ✅ Только чистый `.conf` | ✅ Полная поддержка |

### ⚠️ Ограничение десктопного клиента INCY
По спецификации разработчиков INCY:
* В мобильных клиентах (iOS / Android) парсер поддерживает схемы `amneziawg://`, `awg://`, JSON-контейнеры AmneziaWG и параметры обфускации (`Jc`, `S1-S4`, `H1-H4`, `I1-I5`).
* В десктопном клиенте (Windows / macOS / Linux) схемы `amneziawg://` и `awg://` **целенаправленно игнорируются парсером** (список нод остаётся пустым).
* **Рекомендация для ПК:** Для подключения к AmneziaWG на современных компьютерах (Windows 10/11 x64, macOS 14+) используется официальное приложение **AmneziaVPN** (импорт ключа `vpn://...` или файла `.conf` / `.vpn`). Для устаревших систем (Windows 7/8, Windows 32-bit, macOS <14) используется легкий **AmneziaWG** с файлом `.conf`.

---

## 📦 2. ФОРМАТ ТЕЛА ПОДПИСКИ (`GET /sub/{token}`)

Подписка выдаётся по протоколу HTTP/HTTPS в формате Base64-закодированных строк протокольных ссылок (по одной на строку):

```text
base64(
  amneziawg://<base64url-conf>#🇵🇱 Warsaw — iPhone
  amneziawg://<base64url-conf>#🇩🇪 Frankfurt — MacBook
)
```

### 🔑 Правило Base64URL для AmneziaWG URI
В строке `amneziawg://<payload>#tag`:
* `<payload>` **кодируется в URL-Safe Base64** (`base64.urlsafe_b64encode` в Python).
* Символы `+` заменяются на `-`, `/` заменяются на `_`, паддинг `=` на конце опционален.
* Содержимое `<payload>` — это текстовый файл конфигурации WireGuard / AmneziaWG INI (`[Interface]` + `[Peer]`).

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

---

## ⚠️ 5. ИЗВЕСТНЫЕ ОГРАНИЧЕНИЯ И ROADMAP

1. **INCY Upstream Issue #102:** В открытом трекере `INCY-DEV/incy-platforms` зафиксировано поведение, при котором параметры обфускации AWG могут некорректно определяться при импорте через некоторые варианты ссылок/подписок (в отличие от прямой вставки текстового `.conf`). Поэтому в боте предусмотрены альтернативные пути подключения (AmneziaVPN, скачивание файлов конфигурации).
2. **Спецификация AmneziaWG 3.0 в INCY:** В актуальной документации INCY описаны параметры протокола AWG 3.0 (`HeaderProtectionKey`, `ContentPaddingAddition`, `RekeyAfterTime` и др.). Бот `just1kbot` на текущем этапе работает с AWG 2.0 (`amneziawg2`), а поддержка AWG 3.0 запланирована по мере стабилизации self-hosted серверов.

---

## 🔗 6. ИСТОЧНИКИ

* [INCY Developer Documentation](https://docs.incy.cc/)
* [INCY Subscription Format](https://docs.incy.cc/subscription-format/)
* [INCY Deep Links](https://docs.incy.cc/deep-links/)
* [INCY App Management Headers](https://docs.incy.cc/app-management/)
* [INCY-DEV GitHub Organization](https://github.com/INCY-DEV)
* [INCY Link Encoder (Crypt1 / AES-256-GCM)](https://github.com/INCY-DEV/incy-link-encoder)
