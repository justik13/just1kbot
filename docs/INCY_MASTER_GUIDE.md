# INCY: Техническое руководство по архитектуре, протоколам и кастомизации

> **Назначение документа:**  
> Инженерный справочник по возможностям, протоколам, форматам подписок, кастомизации интерфейса, защите от DPI, криптографии ссылок и архитектурным концептам клиентского приложения **INCY** (`llc.itdev.incy`).  
> Документ составлен на основе официальной документации `docs.incy.cc`, репозиториев `INCY-DEV` на GitHub, данных баг-трекера `feedback.incy.cc` и подтвержденных криптографических тест-векторов.

---

# Часть I. Спецификация и возможности клиента INCY

## 1. Архитектура и поддерживаемые платформы

**INCY** — кроссплатформенное клиентское приложение для защищенных сетевых соединений. Построено на базе двух независимых сетевых ядер:
1. **`xray-core`** (v26.x) — полнофункциональный стек для протоколов семейства VLESS, VMess, Trojan, Shadowsocks и Hysteria 2.
2. **`amneziawg-go v3.1`** — нативное ядро для протоколов WireGuard и AmneziaWG (версий 1.0, 2.0, 3.0 и 3.1).

### Поддерживаемые платформы

| Платформа | Источник | Особенности реализации |
| :--- | :--- | :--- |
| **iOS / iPadOS** | App Store (`id6756943388`) | Нативное приложение, поддержка On-Demand, Shortcuts (Команды Siri), системные виджеты. Лимит памяти 50 МБ (iOS Network Extension). |
| **macOS** | App Store / DMG | Нативная сборка под Apple Silicon (M1–M4) и Intel. |
| **tvOS (Apple TV)** | App Store | Нативная версия для Apple TV с Zero-Knowledge переносом подписки через TV Relay. |
| **Android** | Google Play / APK | Поддержка Per-App Split Tunneling, работа в фоне без сервисов Google. |
| **Android TV** | Google Play / APK | Оптимизированный интерфейс для пультов ДУ, интеграция с TV Relay. |
| **Windows** | GitHub Releases | Версии x64 и ARM64 (Installer + Portable ZIP), сворачивание в системный трей. |
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
  1. Ссылки вида `amneziawg://<base64-conf>#Название`.
  2. Сырой текстовый `.conf` файл в теле подписки.
  3. JSON-контейнер с несколькими серверами:
     ```json
     {"type": "amneziawg", "servers": [{"name": "Германия", "config": "..."}]}
     ```

* **Поддерживаемые параметры обфускации:**
  * Классические: `Jc`, `Jmin`, `Jmax`, `S1`–`S4`, `H1`–`H4`, `I1`–`I5`.
  * Версии AWG 3.0 и 3.1:
    * `HeaderProtectionKey` — симметричный ключ защиты заголовков пакетов (base64 или hex);
    * `ContentPaddingAddition` — рандомизированный паддинг содержимого пакетов;
    * `RekeyAfterTime`, `RekeyTimeout`, `RejectAfterTime` — гибкий тайминг пересогласования ключей;
    * `RandomTrailers` — добавление случайных данных в хвост пакета;
    * `DisableCookies` — отключение механизма cookie WireGuard.

* **Статус для Desktop (Windows, macOS, Linux):**
  На данный момент настольные версии INCY парсят `.conf` как стандартный WireGuard. Полноценная интеграция ядра AmneziaWG на Desktop находится в активной разработке разработчиками INCY (тикет `cmt6vrigr00097b2m4xpmetna`).

---

## 3. Справочник HTTP-заголовков подписки

При запросе подписки клиент INCY отправляет HTTP GET запрос на эндпоинт сервера. Управление клиентом, интерфейсом и защитой осуществляется через HTTP-заголовки ответа.

> **Правило приоритетов:**  
> Значения из HTTP-заголовков имеют наивысший приоритет. Если заголовок отсутствует, клиент считывает параметры из строк тела ответа вида `#заголовок: значение`.

### 3.1. Сводная таблица параметров

| Заголовок | Формат | Описание и влияние на клиент |
| :--- | :--- | :--- |
| `Profile-Title` | текст / `base64:...` | Название профиля (до 25 символов). Для не-ASCII и эмодзи обязателен префикс `base64:`. |
| `Profile-Description` | текст / `base64:...` | Серая подстрока-описание под названием профиля (до 50 символов). |
| `Subscription-Userinfo` | `key=value;...` | Виджет трафика (`upload`, `download`, `total`, `expire`). **Значение `0` полностью скрывает блок трафика в UI**. `expire > 32000000000` интерпретируется как миллисекунды. |
| `Profile-Update-Interval` | число (часы) | Интервал автообновления серверов в фоне (рекомендуется `6` или `12`). |
| `Support-Url` | URL | Кнопка «Поддержка» в карточке профиля. Для ссылок `t.me` отображается иконка Telegram. |
| `Support-Email` | email | Кнопка «Email» в карточке профиля (запасной канал связи). |
| `Profile-Web-Page-Url` | URL | Кнопка «Сайт» / «Кабинет» для перехода в личный кабинет (fallback: `homepage`). |
| `Announce` | текст / `base64:...` | Информационная плашка-объявление вверху карточки профиля (до 5 строк, до 200 символов). |
| `Announce-Url` | URL | Кликабельная ссылка для объявления `Announce`. |
| `Sort-Order` | `none` / `ping` / `name` | Порядок сортировки серверов (на iOS/Android — для профиля, на Desktop — глобально). |
| `Hide-Url` | `1` / `true` / `yes` | **Защита от утечек:** скрывает URL под `••••`, блокирует экспорт, копирование, бэкап и показ QR. |
| `Hide-Check` | `1` / `true` / `yes` | Скрывает кнопку проверки/пинга на главном экране (снижает паразитный трафик). |
| `No-Limit-Enabled` | `1` / `true` | **iOS:** оптимизирует буферы Network Extension, удерживая процесс в лимите памяти 50 МБ. |
| `Banner-Text` | `base64:...` | Текст брендированного баннера удержания (Retention Banner). |
| `Banner-Button-Text` | `base64:...` | Текст кнопки на баннере. |
| `Banner-Button-Url` | URL | Ссылка кнопки баннера. |
| `Banner-Bg-Color` | `#RRGGBB` | HEX-код цвета фона баннера. |
| `Banner-Button-Color` | `#RRGGBB` | HEX-код цвета кнопки баннера. |
| `Autorouting` | URL | Ссылка на автообновляемый JSON-профиль правил маршрутизации. |
| `Routing` | base64 / URL / `off` | Статический профиль маршрутизации. `off` отключает роутинг. |
| `Per-App-Proxy-Enable` | `1` / `0` | Включение раздельного туннелирования приложений (Android). |
| `Per-App-Proxy-Mode` | `bypass` / `proxy` | Режим работы: `bypass` (исключить список) или `proxy` (направлять только список). |
| `Per-App-Proxy-List` | CSV / `base64:...` | Список package names приложений через запятую. |
| `Fragmentation-Enable` | `1` / `0` | Принудительное включение TCP ClientHello фрагментации на клиенте. |
| `Fragmentation-Length` | `min-max` | Длина фрагмента пакета в байтах (например: `10-30`). |
| `Fragmentation-Interval` | `min-max` | Задержка между фрагментами в миллисекундах (например: `5-15`). |
| `Fragmentation-Packets` | `tlshello` / `1-3` / `all` | На какие пакеты накладывать фрагментацию. |
| `Noises-Enable` | `1` / `0` | Отправка шумовых пакетов перед установлением соединения. |
| `Noises-Type` | `rand` / `str` / `hex` | Формат шума (случайные байты, строка или hex). |
| `Noises-Delay` | `min-max` | Диапазон задержки между пакетами шума в мс. |
| `Server-Address-Resolve-Enable` | `1` / `0` | Предварительный резолв домена сервера через DoH перед подключением. |
| `Server-Address-Resolve-Dns-Domain` | URL | Эндпоинт DoH сервера (например: `https://cloudflare-dns.com/dns-query`). |
| `Server-Address-Resolve-Dns-Ip` | IP-адрес | Статический IP адрес DNS-сервера для первоначального DoH резолва. |

---

## 4. Кастомизация и брендинг интерфейса

### 4.1. Оформление шапки профиля
Заголовки `Profile-Title` и `Profile-Description` формируют визуальный стиль карточки. При передаче не-ASCII символов (кириллица, пробелы, спецсимволы, эмодзи) **обязательно** использовать префикс `base64:`:

```http
Profile-Title: base64:SnVzdDFrIE5ldHdvcmsg4pqh
Profile-Description: base64:0J7Qv9GC0LjQvNC40LfQuNGA0L7QstCw0L3QvdGL0Lkg0LrQsNC90LDQuyAoQ0ROKQ==
```
* `SnVzdDFrIE5ldHdvcmsg4pqh` ➔ **Just1k Network ⚡**
* `0J7Qv9GC...` ➔ **Оптимизированный канал (CDN)**

### 4.2. Кастомизация карточек серверов
Список серверов форматируется из названий подключений:

1. **Флаги стран и локации:**  
   Если в названии сервера (после `#` в URL или в поле `tag` JSON outbounds) присутствует эмодзи флага (🇩🇪, 🇫🇮, 🇳🇱, 🇷🇺) или двухбуквенный ISO-код, INCY автоматически рендерит круглую иконку страны в списке.
2. **Параметр `serverDescription` (Информационный бейдж):**  
   INCY поддерживает отображение серого бейджа под каждым сервером (до 30 символов).  
   *Синтаксис в share-ссылке:* параметр добавляется после названия через разделитель `?`:
   ```text
   vless://UUID@cdn.domain.com:443?params#🇩🇪 Франкфурт 01?serverDescription=base64(UTF-8)
   ```
   *Пример:*  
   Для строки `CDN Relay ⚡ 100 Mbps` base64-значение: `Q0ROIFJlbGF5IOKaoSAxMDAgTWJwcw==`.
   ```text
   #🇩🇪 Франкфурт 01?serverDescription=Q0ROIFJlbGF5IOKaoSAxMDAgTWJwcw==
   ```
   В приложении отображается:
   * **🇩🇪 Франкфурт 01** (основной заголовок);
   * `CDN Relay ⚡ 100 Mbps` (серый бейдж под сервером).

### 4.3. Интерактивные баннеры (Retention Banners)
Позволяют выводить в приложении полноразмерный брендированный баннер:

```http
Banner-Text: base64:0J7RgdGC0LDQu9C+0YHRjCAzINC00L3RjyDRgNCw0LHQvtGC0Ysh
Banner-Button-Text: base64:0J/RgNC+0LTQu9C40YLRjA==
Banner-Button-Url: https://t.me/example_bot?start=renew
Banner-Bg-Color: #1e293b
Banner-Button-Color: #3b82f6
```

### 4.4. Lite Mode (Упрощенный интерфейс) и пресеты иконок
INCY поддерживает Lite Mode с набором из 20 системных иконок (`icon-presets`), которые нативно транслируются в SF Symbols (Apple) и Material Icons (Android):

| Группа | Иконки | Описание |
| :--- | :--- | :--- |
| **Бот / Чат** | `send`, `bot`, `chat`, `message`, `mail` | Дефолтные иконки бота (`send`), переписки и обратной связи. |
| **Канал / Новости** | `megaphone`, `bell`, `newspaper`, `rss`, `broadcast` | Рупор объявлений (`megaphone`), уведомления, новости сервиса. |
| **Помощь / Инфо** | `help`, `support`, `lifebuoy`, `info`, `book` | Знак вопроса (`help`), гарнитура поддержки (`support`), FAQ. |
| **Акцентные** | `crown`, `star`, `gem`, `rocket`, `heart` | Премиум-статус (`crown`), избранное, ракета скорости. |

---

## 5. Криптография ссылок подписки (`crypt1`)

### 5.1. Спецификация wire-format
Формат `crypt1` представляет собой обфусцированный контейнер, скрывающий URL подписки от сканеров и поисковых ботов:
* **Алгоритм:** `AES-256-GCM` (authenticated encryption).
* **Структура полезной нагрузки:**
  ```text
  IV (12 байт) || Ciphertext || Auth Tag (16 байт)
  ```
* **Payload (JSON):** Компактный UTF-8 с обязательной алфавитной сортировкой ключей (`sortedCompactJson`):
  ```json
  {"n":"Provider Name","url":"https://cdn.domain.com/sub/token","v":1}
  ```
* **Параметры ключа:**
  * Ключ K1: `f6d40ea0c8a8899d7c682d09ba0d4165dfe2b3dd45e6bb3e25cb233cf00c2462`
  * SHA-256 фингерпринт: `b6bf708471cc90043232967660aade86a50b4e57929db2e53c5fa34db624c08c`

### 5.2. Поведение флага `importedViaCrypt1`
При импорте ссылки формата `incy://crypt1/...` приложение сохраняет внутренний флаг `importedViaCrypt1 = true`. При любом последующем экспорте (Copy URL, Share, QR) приложение генерирует ссылку **строго в формате `crypt1`**, не раскрывая оригинальный URL подписки в открытом виде.

### 5.3. Реализация модуля шифрования на Python

```python
"""INCY crypt1 link encoder & decoder.

Verified against @incy/link-encoder v1.3.0.
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

```
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
  2. *Локальный Bonjour:* Если устройства находятся в одной Wi-Fi сети, передача идет напрямую без интернета.

---

# Часть II. Концептуальные идеи для Just1kbot

> **Примечание:**  
> Данный раздел содержит исключительно идеи и архитектурные концепты для возможного развития проекта. Он не является утвержденным планом работ или обязательством к внедрению. Решения о применимости принимаются владельцем проекта.

### Идея 1: Формат `crypt1` для выдачи ключей
* **Суть:** Выдавать в боте ссылки вида `incy://crypt1/...` вместо открытых HTTPS-адресов.
* **Возможные плюсы:** Спам-фильтры Telegram и внешние сканеры не видят доменных имен CDN и путей подписок; снижается риск случайной утечки токена при пересылке сообщений.

### Идея 2: Брендирование шапки подписки и каналы поддержки
* **Суть:** Добавить в ответ эндпоинта подписки заголовки `Profile-Title: base64:...`, `Profile-Description: base64:...` и `Support-Url`.
* **Возможные плюсы:** В карточке подключения в приложении пользователь видит узнаваемое название сервиса и кнопку «Поддержка», которая в INCY автоматически помечается иконкой Telegram.

### Идея 3: Информативные бейджи серверов
* **Суть:** Формировать ремарки серверов с параметром `?serverDescription=base64(...)`.
* **Возможные плюсы:** Пользователь видит под сервером краткую подстроку (например, `CDN Relay ⚡ 100 Mbps`), что улучшает читаемость списка при наличии нескольких локаций.

### Идея 4: Нативная кнопка копирования в Telegram Bot API
* **Суть:** Использовать инлайн-кнопку с параметром `copy_text`:
  ```python
  InlineKeyboardButton(
      text="📋 Скопировать ключ для INCY",
      copy_text=InlineKeyboardButtonCopyText(text=crypt1_link)
  )
  ```
* **Возможные плюсы:** Моментальное копирование в 1 клик без всплывающих окон браузера. При следующем открытии INCY на телефоне приложение само предлагает импортировать профиль из буфера обмена.

### Идея 5: Справочные подсказки по Smart TV
* **Суть:** В справочных материалах бота кратко описать функцию переноса на ТВ: *«Установите INCY на телевизор, откройте приложение на телефоне и введите 8-значный код с экрана ТВ»*.
* **Возможные плюсы:** Упрощение онбординга пользователей телевизоров без необходимости настраивать сложные протоколы вручную.

### Идея 6: Потенциальная унификация клиентов в будущем
* **Суть:** По мере стабилизации поддержки AmneziaWG на настольных версиях INCY рассмотреть возможность использования одного клиента для обоих тарифов (Стандартный и Белый Интернет).
* **Возможные плюсы:** Единый интерфейс и одна инструкция для всех пользователей.
