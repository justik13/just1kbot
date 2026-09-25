# INCY: Техническое руководство по архитектуре, протоколам и кастомизации

> **Назначение документа:**  
> Инженерный справочник по возможностям, протоколам, форматам подписок, кастомизации интерфейса, защите от DPI, криптографии ссылок и архитектурным концептам клиентского приложения **INCY** (`llc.itdev.incy`).  
> Документ составлен и верифицирован на основе официальной документации [docs.incy.cc](https://docs.incy.cc), репозиториев разработчиков на GitHub ([`INCY-DEV/incy-docs`](https://github.com/INCY-DEV/incy-docs), [`INCY-DEV/incy-link-encoder`](https://github.com/INCY-DEV/incy-link-encoder)), данных баг-трекера `feedback.incy.cc` и подтверждённых криптографических тест-векторов.

---

# Часть I. Спецификация и возможности клиента INCY

## 1. Архитектура и поддерживаемые платформы

**INCY** — кроссплатформенное клиентское приложение для защищённых сетевых соединений. Построено на базе двух независимых сетевых ядер:
1. **`xray-core`** (v26.x) — полнофункциональный стек для протоколов семейства VLESS, VMess, Trojan, Shadowsocks и Hysteria 2.
2. **`amneziawg-go v3.1`** — нативное ядро для протоколов WireGuard и AmneziaWG (версий 1.0, 2.0, 3.0 и 3.1).

### Поддерживаемые платформы

| Платформа | Источник | Особенности реализации |
| :--- | :--- | :--- |
| **iOS / iPadOS** | App Store (`id6756943388`) | Нативное приложение, поддержка On-Demand, Shortcuts (Команды Siri), системные виджеты. Лимит памяти 50 МБ (iOS Network Extension). Хранение HWID в Keychain. |
| **macOS** | App Store / DMG | Нативная сборка под Apple Silicon (M1–M4) и Intel. Поддержка системного трея. |
| **tvOS (Apple TV)** | App Store | Нативная версия для Apple TV с Zero-Knowledge переносом подписки через TV Relay. |
| **Android** | Google Play / APK | Поддержка Per-App Split Tunneling, работа в фоне без сервисов Google. Хранение HWID в EncryptedSharedPreferences. |
| **Android TV** | Google Play / APK | Оптимизированный интерфейс для пультов ДУ, интеграция с TV Relay. |
| **Windows** | GitHub Releases | Версии x64 и ARM64 (Installer + Portable ZIP), сворачивание в системный трей. Per-app на уровне процесса в режиме TUN. |
| **Linux** | GitHub Releases | Пакеты DEB, RPM, Arch Linux PKG и Portable binary. |

---

## 2. Поддерживаемые протоколы и транспорты

### 2.1. Семейство Xray
* **VLESS:**
  * Транспорт **XHTTP** (методы `GET`, `POST`, `OPTIONS`, режимы передачи `packet-up` и `stream-up`, кастомные паддинги `xPaddingHeader`, тюнинг задержек);
  * Транспорт **Reality** (с XTLS Vision);
  * Транспорты **WebSocket** и **gRPC** с поддержкой TLS.
* **Hysteria 2:** Мультипортовый режим (`mport`, port hopping), TLS-обфускация (salamander через `finalmask`).
* **Классические прокси:** VMess, Trojan, Shadowsocks (SIP002 и AEAD 2022), SOCKS5, HTTP.

### 2.2. Семейство WireGuard и AmneziaWG (AWG 3.x)
INCY включает нативное ядро `amneziawg-go v3.1` на мобильных платформах (iOS и Android).

* **Форматы передачи:**
  1. Ссылки вида `amneziawg://<base64-conf>#Название` или короткий алиас `awg://<base64-conf>#Название`.
  2. Сырой текстовый `.conf` файл в теле подписки (распознаётся по секциям `[Interface]` и `[Peer]`).
  3. JSON-контейнер с несколькими серверами:
     ```json
     {
       "type": "amneziawg",
       "version": 1,
       "servers": [
         { "name": "Германия", "config": "<base64url-conf>" },
         { "name": "Нидерланды", "config": "<base64url-conf>" }
       ]
     }
     ```
  4. Включение AmneziaWG-контейнера элементом в JSON-массив рядом с полными Xray-конфигами.

* **Поддерживаемые параметры обфускации:**
  * Классические: `Jc`, `Jmin`, `Jmax`, `S1`–`S4`, `H1`–`H4`, `I1`–`I5`.
  * Версии AWG 3.0 и 3.1:
    * `HeaderProtectionKey` — симметричный ключ защиты заголовков пакетов (base64 или hex, требует `S1`–`S4` $\ge$ 12);
    * `ContentPaddingAddition` — рандомизированный паддинг содержимого пакетов (число или диапазон `min-max`);
    * `RekeyAfterTime`, `RekeyTimeout`, `RejectAfterTime` — гибкий тайминг пересогласования ключей;
    * `KeepaliveTimeout`, `MaxHandshakeAttempts` — тонкий тюнинг соединения;
    * `RandomTrailers` — добавление случайных данных в хвост пакета;
    * `DisableCookies` — отключение механизма cookie WireGuard;
    * Поддержка секции `[Device]` наравне с `[Interface]`.

* **Статус для Desktop (Windows, macOS, Linux):**
  На данный момент настольные версии INCY парсят `.conf` как стандартный WireGuard. Схемы `amneziawg://` и JSON-контейнеры AWG на Desktop пока игнорируются (поддержка ядра AmneziaWG на Desktop находится в разработке у команды INCY).

---

## 3. Справочник параметров подписки и HTTP-заголовков

При запросе подписки клиент INCY отправляет HTTP GET запрос на эндпоинт сервера (в нашем проекте: `/sub/wl/{token}`). Управление клиентом, интерфейсом и защитой осуществляется через HTTP-заголовки ответа.

> **Правило приоритетов источников:**  
> 1. HTTP-заголовки ответа имеют наивысший приоритет.  
> 2. Строки тела ответа вида `#заголовок: значение` служат fallback-значениями, если HTTP-заголовок не был передан (актуально для раздачи через статический nginx).  
> 3. Параметры внутри share-ссылок (например, `#name?serverDescription=...`) управляют индивидуальными узлами.

### 3.1. Клиентские заголовки запроса (HWID и телеметрия)

При каждом обращении к эндпоинту подписки клиент INCY передаёт набор идентификационных заголовков:
* `x-hwid` (или `X-HWID`, `X-Device-Id`): аппаратный идентификатор устройства в формате UUID `8-4-4-4-12` (верхний регистр, 36 символов).
* `x-device-os`: операционная система клиента (`android`, `ios`, `windows`, `linux`, `darwin`).
* `x-ver-os`: версия операционной системы.
* `x-device-model`: аппаратная модель устройства (например, `Pixel 8`, `iPhone15,2`).

Сервер Just1kbot использует `x-hwid` для атомарной регистрации устройства в базе данных и строгого контроля лимита подключений.

---

### 3.2. Возможности, доступные БЕЗ Premium (100% автономно)

| Параметр / HTTP-заголовок | Формат | Поддержка в Body (`#`) | Влияние на клиент INCY |
| :--- | :--- | :---: | :--- |
| **`Profile-Title`** | текст / `base64:...` (до 25 симв.) | ✅ | Название подписки в шапке главной карточки (крупный шрифт слева от стрелки `▼`). |
| **`Profile-Description`** | текст / `base64:...` (до 50 симв.) | — | **Служебное описание подписки.** Отображается в шторке выбора профилей по нажатию на стрелку `▼`, а также в меню «Свойства подписки» (`···`). На главной карточке не выводится для сохранения компактности дашборда. |
| **`Announce`** | текст / `base64:...` (до 200 симв.) | ✅ | **Текстовое объявление** на главном экране прямо над кнопками быстрых действий (сообщения о техработах, статусе сети, акциях). |
| **`Announce-Url`** | URL | ✅ | Кликабельная ссылка для объявления `Announce`. При нажатии на текст объявления клиент открывает указанный URL (пост в Telegram, страницу новостей). |
| **`Support-Url`** | URL | ✅ | Кнопка «Поддержка» в карточке профиля. Для ссылок `t.me/...` INCY **автоматически отображает нативную иконку Telegram**. |
| **`Support-Email`** | Email | ✅ | Кнопка «Email» в карточке профиля (запасной контакт службы поддержки). |
| **`Profile-Web-Page-Url`** | URL (алиас: `homepage`) | ✅ | Кнопка перехода на канал бота или сайт сервиса. |
| **`Profile-Update-Interval`** | число (часы: `1`, `3`, `6`, `12`, `24`) | ✅ | Интервал фонового автообновления списка серверов клиентом (по умолчанию `6`). |
| **`Sort-Order`** | `none` / `ping` / `name` | — | Порядок сортировки серверов: `none` (порядок от сервера), `ping` (по скорости), `name` (по алфавиту). На iOS/Android действует локально для профиля, на Desktop — глобально. |
| **`Subscription-Userinfo`** | `upload=...; download=...; total=...; expire=...` | — | Информер трафика и срока действия. **При `total=0` шкала расхода скрывается, остаётся только счётчик дней.** Значения `expire` > 32 000 000 000 конвертируются из миллисекунд. |
| **`Hide-Url`** | `1` / `true` / `yes` | ✅ | **Защита от утечки ссылки:** скрывает URL под точками (`••••`), блокирует кнопки Share, Copy URL, QR-код и выгрузку в резервные копии. |
| **`Hide-Check`** | `1` / `true` / `yes` | ✅ (Android/iOS) | **Скрытие кнопки «Проверить»** на главном экране (предотвращает ложные ошибки пинга при работе через защищённые CDN). На Desktop читается только из HTTP-заголовка. |
| **`No-Limit-Enabled`** | `1` / `true` | — | **Оптимизация памяти iOS:** переводит процесс Network Extension в режим удержания в системном лимите 50 МБ, предотвращая аварийное завершение процесса ядром iOS. |
| **`serverDescription`** | `?serverDescription=base64(...)` | ✅ (в URL) | Текстовый бейдж под сервером в списке локаций (до 30 символов). |
| **Иконки флагов стран** | Эмодзи флага (🇷🇺, 🇩🇪, 🇪🇪 и др.) в `#` | ✅ (в URL) | Автоматический рендеринг круглой графической иконки флага вместо стандартного текстового эмодзи. |
| **`Fragmentation-Enable`** | `1` / `0` | — | Принудительное включение TCP ClientHello фрагментации для защиты от анализа пакетов. |
| **`Fragmentation-Packets`** | `tlshello` / `1-3` / `all` | — | Пакеты, на которые накладывается фрагментация. |
| **`Fragmentation-Length`** | `min-max` (напр. `10-30`) | — | Диапазон длины фрагмента TCP в байтах. |
| **`Fragmentation-Interval`**| `min-max` (напр. `5-15`) | — | Задержка между фрагментами в миллисекундах. |
| **`Server-Address-Resolve-Enable`** | `1` / `0` | — | Предварительный резолв домена сервера через DoH перед подключением. |
| **`Server-Address-Resolve-Dns-Domain`** | URL (напр. DoH Яндекса) | — | DoH-эндпоинт (`/dns-query`) для защиты от локальной подмены DNS. |
| **`Server-Address-Resolve-Dns-Ip`** | IP (напр. `77.88.8.8`) | — | Статический bootstrap IP DNS-сервера. |
| **`Noises-Enable`** / `Noises-Type` | `1` / `0`, `rand`/`hex` | — | Отправка шумовых UDP-пакетов перед handshake (для AWG / Hysteria 2). |
| **`Autorouting`** | URL | ✅ | Ссылка на автообновляемый JSON-профиль маршрутизации (`incy://autorouting/onadd/...`). |
| **`Routing`** | base64 / URL / `off` | ✅ | Статический профиль маршрутизации (`off` полностью выключает встроенный роутинг). |
| **`Per-App-Proxy-Enable`** | `1` / `0` | — | Включение раздельного туннелирования приложений (управляется заголовками только на Android). |
| **`Per-App-Proxy-Mode`** | `bypass` / `proxy` | — | Режим: `bypass` (все кроме списка) или `proxy` (только выбранные). |
| **`Per-App-Proxy-List`** | CSV / URL / `base64:...` | — | Список package names приложений через запятую или ссылка на текстовый файл. |

---

### 3.3. Возможности, СТРОГО ТРЕБУЮЩИЕ Premium INCY

Следующие функции активируются **исключительно** через платную подписку в облачной панели `web.incy-panel.com` и зашифрованный эндпоинт `GET /api/subscription/config`:

1. **Цветные графические баннеры (`banner-*`):**
   * Заголовки `banner-text`, `banner-button-text`, `banner-button-url`, `banner-bg-color`, `banner-button-color`.
   * *Прямая цитата из документации INCY:*  
     > *«Слать баннер через заголовки могут только премиум-провайдеры: для не-премиум подписок `banner-*` заголовки игнорируются.»*
   * Для автономных (бесплатных) подписок вместо графического баннера используется заголовок **`Announce`** (текстовое объявление над кнопками).
2. **Фильтрация видимости серверов по типу сети (Wi-Fi vs Mobile):**
   * Метки `only Wi-Fi` и `only Mobile` в названии сервера (`#...`). Работают только при активном Premium у подписки. Без Premium серверы показываются во всех сетях.
3. **Lite Mode и кастомный маппинг системных иконок (`icon-presets`):**  
   Переопределение иконок слотов (`botIconKey`, `channelIconKey`, `supportIconKey`) и стиля карточки.
4. **Фирменная цветовая тема (`theme`):**  
   Кастомные цвета фона приложения и градиенты кнопок.
5. **Графический логотип провайдера (`logoUrl`):**  
   Рендеринг векторного SVG/PNG логотипа в шапке клиента.
6. **Принудительный стиль переключателя (`forceConnectionStyle`):**  
   Замена круглой кнопки на компактный тумблер.
7. **Push-уведомления через серверы INCY:**  
   Массовые пуши на мобильные устройства пользователей.
8. **Админ-доступ по HWID (`adminHwids`):**  
   Редактирование конфигураций серверов прямо из интерфейса клиента на доверенных устройствах.

---

## 4. Кастомизация и оформление интерфейса

### 4.1. Оформление шапки профиля и объявлений
Заголовки `Profile-Title`, `Profile-Description` и `Announce` формируют текстовое оформление. При передаче не-ASCII символов (кириллица, пробелы, спецсимволы, эмодзи) рекомендуется использовать префикс `base64:`:

```http
Profile-Title: base64:4pyAIEp1c3Qxa2JvdA==
Profile-Description: base64:0JHRi9GB0YLRgNGL0LUg0YHQtdGA0LLQtdGA0Ys=
Announce: base64:0J/Qu9Cw0L3QvtCy0YvQtSDRgtC10YXRgNCw0LHQvtGC0YsgMTU6MDA=
Announce-Url: https://t.me/just1k13news/123
```

* Поле `Profile-Title` рендерится как главный заголовок карточки.
* Поле `Announce` выводится строкой над кнопками быстрых действий. Если задан `Announce-Url`, клик по тексту открывает ссылку.
* Поле `Profile-Description` отображается при раскрытии списка подписок по стрелке `▼` и в меню «Свойства подписки» (`···`).

### 4.2. Кастомизация карточек серверов
Список серверов форматируется из названий подключений:

1. **Флаги стран и локации:**  
   Если в названии сервера (после `#` в URL) первым символом идёт эмодзи флага (🇷🇺, 🇩🇪, 🇪🇪, 🇳🇱), INCY автоматически рендерит круглую векторную иконку флага в списке серверов.
2. **Параметр `serverDescription` (Информационный бейдж):**  
   Отображает аккуратный бейдж под названием сервера (до 30 символов).  
   *Синтаксис в share-ссылке:* параметр добавляется после названия через разделитель `?`:
   ```text
   vless://UUID@cdn.domain.com:443?params#🇷🇺 Россия?serverDescription=base64(UTF-8)
   ```
   *Принцип чистоты:* если бейдж не задан администратором, ссылка генерируется без `?serverDescription=`, предотвращая навязанные технические тексты.

---

## 5. Криптография ссылок подписки (`crypt1`)

### 5.1. Спецификация wire-format
Формат `crypt1` представляет собой обфусцированный контейнер, скрывающий URL подписки от автоматических сканеров, антивирусов и спам-фильтров:
* **Алгоритм:** `AES-256-GCM` (authenticated encryption).
* **Структура полезной нагрузки:**
  ```text
  IV (12 байт) || Ciphertext || Auth Tag (16 байт)
  ```
* **Wire format:**
  ```text
  incy://crypt1/<base64url(iv[12] || ciphertext || tag[16])>
  ```
* **Payload (JSON):** Компактный UTF-8 с обязательной алфавитной сортировкой ключей (`sortedCompactJson`):
  ```json
  {"n":"Provider Name","url":"https://cdn.domain.com/sub/wl/token","v":1}
  ```
* **Параметры ключа:**
  * Ключ K1: `f6d40ea0c8a8899d7c682d09ba0d4165dfe2b3dd45e6bb3e25cb233cf00c2462`
  * SHA-256 фингерпринт: `b6bf708471cc90043232967660aade86a50b4e57929db2e53c5fa34db624c08c`
  * Официальный NPM-пакет: [`@incy/link-encoder`](https://www.npmjs.com/package/@incy/link-encoder)
  * Репозиторий реализаций (TypeScript, Python, Go, PHP): [`INCY-DEV/incy-link-encoder`](https://github.com/INCY-DEV/incy-link-encoder)
  * Браузерный онлайн-генератор: [incy.cc/encrypt](https://incy.cc/encrypt)

### 5.2. Поведение флага `importedViaCrypt1`
При импорте ссылки формата `incy://crypt1/...` приложение сохраняет внутренний флаг `importedViaCrypt1 = true`. При любом последующем экспорте (Copy URL, Share, QR) приложение генерирует ссылку **строго в формате `crypt1`**, не раскрывая оригинальный URL подписки в открытом виде.

### 5.3. Реализация модуля шифрования на Python

```python
"""INCY crypt1 link encoder & decoder.

Verified against official test vectors of @incy/link-encoder v1.3.0.
Fingerprint: b6bf708471cc90043232967660aade86a50b4e57929db2e53c5fa34db624c08c
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

INCY_K1_KEY = bytes.fromhex(
    "f6d40ea0c8a8899d7c682d09ba0d4165dfe2b3dd45e6bb3e25cb233cf00c2462"
)
LINK_PREFIX = "incy://crypt1/"


def encode_incy_crypt1(subscription_url: str, provider_name: str = "Provider") -> str:
    """Шифрует URL подписки в deep-link incy://crypt1/... с алфавитной сортировкой ключей."""
    if not subscription_url:
        raise ValueError("subscription_url must not be empty")

    payload: dict[str, Any] = {
        "url": subscription_url,
        "v": 1,
    }
    if provider_name:
        payload["n"] = provider_name[:128]

    # Сортировка ключей строго по алфавиту для идентичности байтов
    sorted_keys = sorted(payload.keys())
    parts = [json.dumps(k) + ":" + json.dumps(payload[k], ensure_ascii=False) for k in sorted_keys]
    joined_json = "{" + ",".join(parts) + "}"
    plaintext = joined_json.encode("utf-8")

    iv = os.urandom(12)
    aesgcm = AESGCM(INCY_K1_KEY)
    ct_tag = aesgcm.encrypt(iv, plaintext, None)

    wire = iv + ct_tag
    b64url = base64.urlsafe_b64encode(wire).decode("ascii").rstrip("=")
    return f"{LINK_PREFIX}{b64url}"


def decode_incy_crypt1(crypt1_link: str) -> dict[str, Any]:
    """Декодирует incy://crypt1/... обратно в словарь с 'url' и опциональным 'n'."""
    if not crypt1_link.startswith(LINK_PREFIX):
        raise ValueError(f"Expected prefix {LINK_PREFIX}")

    raw = crypt1_link[len(LINK_PREFIX):].strip().rstrip("/")
    padding = "=" * ((4 - len(raw) % 4) % 4)
    wire = base64.urlsafe_b64decode(raw + padding)

    if len(wire) < 12 + 16 + 1:
        raise ValueError("Payload too short")

    iv = wire[:12]
    ct_tag = wire[12:]

    aesgcm = AESGCM(INCY_K1_KEY)
    decrypted = aesgcm.decrypt(iv, ct_tag, None)
    return json.loads(decrypted.decode("utf-8"))
```

---

## 6. Технология Smart TV (TV Relay)

INCY предлагает нативное решение для переноса конфигураций на Apple TV и Android TV без ручного ввода текста пультом:

```text
[Телевизор (INCY TV)]                     [Телефон пользователя (INCY)]
         |                                              |
         |-- 1. Генерация 8-значного кода (CPace) ----->| (код на экране ТВ)
         |                                              |-- 2. Ввод кода в INCY Mobile
         |                                              |-- 3. Шифрование профиля
         |<========== 4. E2E передача подписки =========|
         |         (через check.incytv.com / Bonjour)   |
         |                                              |
         |-- 5. Мгновенная активация подключения        |
```

### Архитектура протокола
* **Zero-Knowledge обмен:** Протокол основан на CPace (RFC 9496) поверх группы Ristretto255.
* **Каналы передачи:**
  1. *Внешний relay:* `wss://check.incytv.com/ws` (открытый репозиторий: `INCY-DEV/incy-tv-relay`). Сервер видит только эфемерные хеши, не имея доступа к URL подписки.
  2. *Локальный Bonjour:* Если устройства находятся в одной Wi-Fi сети, передача идёт напрямую без интернета.

---

# Часть II. Архитектурная интеграция в Just1kbot

### 1. Что уже реализовано в проекте

В боте Just1kbot полностью реализована поддержка расширенной кастомизации подписок INCY:

1. **Управление оформлением через админ-панель бота** ([`bot/handlers/admin/servers/incy_routes.py`](file:///d:/project/ag/just1kbot/bot/handlers/admin/servers/incy_routes.py)):
   * **Название профиля (`Profile-Title`)** и **описание (`Profile-Description`)**.
   * **Текстовое объявление (`Announce`)** и **ссылка объявления (`Announce-Url`)** — администратор в 1 клик выводит оповещения для пользователей прямо на главный экран клиента.
   * **Ссылки на канал (`Profile-Web-Page-Url`)** и **поддержку (`Support-Url`)** с автоматическим распознаванием ссылок Telegram.
   * **Скрытие кнопки проверки (`Hide-Check: 1`)** — исключает ложные ошибки пинга при работе через Cloud CDN.
   * **Интервал фонового обновления (`Profile-Update-Interval`)** — по умолчанию 6 часов.
   * **Защита от утечки ссылки (`Hide-Url: 1`)** — блокирует копирование, шеринг и QR-код для неавторизованных устройств.
   * **Оптимизация памяти iOS (`No-Limit-Enabled: 1`)** — удерживает фоновый процесс в лимите 50 МБ.
2. **Индивидуальное управление узлами:**
   * Кастомное имя отображения Origin-шлюза (`origin_tag`) и опциональный бейдж (`origin_badge`).
   * Индивидуальные имена релеев (`relay_names[code]`) и бейджи (`relay_badges[code]`).
   * Автоматическая конвертация эмодзи флагов (🇷🇺, 🇩🇪, 🇪🇪, 🇳🇱) в векторные значки стран.
   * Передача бейджей узлов через `?serverDescription=base64(...)`.
3. **Учёт и аутентификация устройств (`x-hwid`)** ([`bot/handlers/white_internet_web.py`](file:///d:/project/ag/just1kbot/bot/handlers/white_internet_web.py)):
   * Регистронезависимая обработка заголовков `X-Hwid`, `X-HWID`, `X-Device-Id`, `X-Device-ID`.
   * Атомарная регистрация HWID в базе данных и контроль аппаратного лимита активных устройств.

---

### 2. Пул перспективных расширений (БЕЗ Premium)

| Функция | Куда добавляется | Какая польза сервису |
| :--- | :--- | :--- |
| **📋 Выдача `crypt1` + 1-клик копирование** | Хэндлер выдачи подписки в боте (`InlineKeyboardButtonCopyText`) | Пользователь копирует ссылку подписки в формате `incy://crypt1/...` в один клик; защита от анализа ссылок спам-фильтрами Telegram. |
| **🛡️ Анти-DPI TCP фрагментация** | Эндпоинт `/sub/wl/{token}` (заголовки `fragmentation-*`) | Автоматический обход блокировок TLS Handshake на ТСПУ для абонентов мобильных операторов РФ. |
| **🌐 DoH Резолв домена CDN** | Эндпоинт `/sub/wl/{token}` (`server-address-resolve-*`) | Защита от блокировок по DNS со стороны региональных провайдеров. |
| **📧 Запасной контакт поддержки** | Заголовок `support-email` в `/sub/wl/{token}` | Выводит в карточке подписки кнопку «Email» на случай недоступности Telegram. |
