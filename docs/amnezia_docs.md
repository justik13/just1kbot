# 📚 AMNEZIA WG 2.0 — ТЕХНИЧЕСКИЙ СПРАВОЧНИК

## 🚫 ТЕКУЩАЯ ПОЛИТИКА ПОДДЕРЖКИ ПРОТОКОЛОВ

В текущей реализации проекта `just1kbot` поддерживается только **AmneziaWG 2.0 (`amneziawg2`)**, управляемый через серверный API `kyoresuas/amnezia-api`. Обычный WireGuard не используется.

| Протокол / Клиент | Статус в проекте | Пояснение |
|---|:---:|---|
| **AmneziaWG 2.0** (`amneziawg2`) | ✅ **PRODUCTION** | Единственный текущий рабочий протокол бота. Полная поддержка обфускации (`Jc`, `Jmin-Jmax`, `S1-S4`, `H1-H4`, `I1-I5`). |
| **AmneziaWG 3.0** (`amneziawg3`) | ⏳ **ROADMAP** | Запланирован к интеграции по мере готовности self-hosted сервера/образов. |
| **Xray** (`vless`, `reality`, `hy2`) | ⏳ **ROADMAP** | Запланирован в будущих релизах для диверсификации транспорта. |
| **Чистый WireGuard** | ❌ **НЕ ИСПОЛЬЗУЕТСЯ** | Не используется в текущей реализации проекта. |
| **AmneziaWG 1.0 / 1.5** | ❌ **УСТАРЕЛ** | Предыдущие версии протокола, заменены на AWG 2.0. |
| **OpenVPN, IKEv2, Cloak** | ❌ **НЕ ИСПОЛЬЗУЕТСЯ** | Не поддерживаются текущим серверным API. |

**Текущее рабочее значение поля `protocol` в API и базе:** `"amneziawg2"`.

---

## 📦 1. ФОРМАТЫ КОНФИГУРАЦИИ И СОВМЕСТИМОСТЬ С КЛИЕНТАМИ

| Формат | Расширение | Совместимость с приложениями | Содержимое |
|---|---|---|---|
| **AmneziaVPN native** | `.vpn` | **AmneziaVPN** (официальный универсальный клиент) | Полный JSON с `containers`, `awg`, `last_config` |
| **AmneziaWG / WireGuard conf** | `.conf` | **AmneziaWG** (нативное приложение), **AmneziaVPN** (импорт файла), **INCY** (мобильные) | Текстовый WireGuard INI + AWG 2.0 параметры |
| **vpn:// URI** | — (строка) | **Только AmneziaVPN** (вставка ключа из буфера/QR) | `base64url(4-byte BE length + zlib(JSON))` |

### ⚠️ Важные правила совместимости:
1. **`vpn://` URI работает ТОЛЬКО в AmneziaVPN** — отдельное легковесное приложение AmneziaWG не поддерживает key-based импорт `vpn://` (официальная документация [Amnezia Sharing](https://docs.amnezia.org/documentation/instructions/amnezia-hosting-sharing/)).
2. **Файл `.conf` универсален для AWG** — его открывает как легковесный клиент AmneziaWG, так и универсальный клиент AmneziaVPN через меню «Подключиться по файлу конфигурации».
3. **Файл `.vpn` строго для AmneziaVPN** — содержит полный JSON-контейнер настроек сервера.

---

## 📱 2. КЛИЕНТСКИЕ ПРИЛОЖЕНИЯ

### 1. AmneziaVPN (универсальный клиент)
* **Платформы:** Windows 10/11 x64, macOS 14+, Linux, Android, iOS.
* **Импорт:** Ключ `vpn://...` из буфера обмена, файл `.vpn`, файл `.conf`.
* **Назначение:** Рекомендуемый основной клиент для современных настольных и мобильных ОС.

### 2. DefaultVPN (альтернативный клиент для iOS)
* **Платформы:** iOS 17+.
* **Репозиторий:** [`amnezia-vpn/DefaultVPN`](https://github.com/amnezia-vpn/DefaultVPN) / [App Store](https://apps.apple.com/app/defaultvpn/id6744725017)
* **Импорт:** Ключ `vpn://...` или файл конфигурации `.conf`.
* **Назначение:** Легковесное нативное iOS-приложение от команды Amnezia с поддержкой AmneziaWG и XRay Reality.

### 3. AmneziaWG (легковесный клиент)
* **Платформы:** Windows (включая Windows 7/8, 32-bit, ARM64), macOS, iOS, Android.
* **Импорт:** **Только `.conf` файлы** (или QR-код с содержимым `.conf`).
* **Назначение:** Альтернативный быстрый клиент для устройств, где не поддерживается или избыточен полный AmneziaVPN (включая роутеры OpenWrt/Keenetic).

### Рекомендация интерфейса бота
1. **Быстрый доступ:** Кнопка **«🔑 Показать ключ»** — выводит `vpn://` URI для AmneziaVPN.
2. **Резервный доступ:** Кнопка **«📥 Скачать файлом»** — отправляет:
   - `device.vpn` — для AmneziaVPN;
   - `device.conf` — для AmneziaWG / роутеров / сторонних клиентов.

---

## 📄 3. СТРУКТУРА ФОРМАТА `.vpn` (НАТИВНЫЙ JSON AMNEZIA)

Файл с расширением **`.vpn`** — это нативный формат приложения **AmneziaVPN**.
Он представляет собой **валидный JSON-документ (UTF-8)**, содержащий полную конфигурацию сервера, контейнеров и клиента.

### Эталонная JSON-структура `.vpn`:
```json
{
  "containers": [
    {
      "container": "amnesia-awg2",
      "awg": {
        "protocol_version": "2",
        "port": "1234",
        "transport_proto": "udp",
        "Jc": "4",
        "Jmin": "10",
        "Jmax": "50",
        "S1": "79",
        "S2": "115",
        "S3": "5",
        "S4": "1",
        "H1": "169154911-1234371153",
        "H2": "2057051984-2121122945",
        "H3": "2132872968-2133668229",
        "H4": "2136455412-2141801388",
        "I1": "<r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>",
        "I2": "",
        "I3": "",
        "I4": "",
        "I5": "",
        "last_config": "{\"H1\":\"169154911-1234371153\",\"H2\":\"2057051984-2121122945\",\"H3\":\"2132872968-2133668229\",\"H4\":\"2136455412-2141801388\",\"I1\":\"<r 2><b 0x8580...>\",\"Jc\":\"4\",\"Jmin\":\"10\",\"Jmax\":\"50\",\"S1\":\"79\",\"S2\":\"115\",\"S3\":\"5\",\"S4\":\"1\",\"allowed_ips\":[\"0.0.0.0/0\",\"::/0\"],\"client_ip\":\"10.8.1.34\",\"client_priv_key\":\"...\",\"client_pub_key\":\"...\",\"config\":\"[Interface]\\nAddress = 10.8.1.34/32\\n...\",\"hostName\":\"vpn.example.com\",\"mtu\":\"1280\",\"port\":1234,\"psk_key\":\"...\",\"server_pub_key\":\"...\"}"
      }
    }
  ],
  "defaultContainer": "amnesia-awg2",
  "description": "Germany",
  "dns1": "8.8.8.8",
  "dns2": "8.8.4.4",
  "hostName": "vpn.example.com"
}
```

### 3.1 Архитектурная роль и структура `last_config`

Поле `awg.last_config` в спецификации Amnezia — это **экранированная JSON-строка** (`string`), а не вложенный JSON-объект.

При десериализации (`json.loads(last_config)`) получается словарь со следующими ключевыми полями:
* `config` (`str`) — готовый текстовый файл WireGuard/AmneziaWG INI (`[Interface]` + `[Peer]`).
* `client_ip` (`str`) — локальный IP адрес клиента в VPN-сети (например, `10.8.1.34`).
* `client_priv_key` (`str`) — приватный ключ клиента.
* `client_pub_key` (`str`) — публичный ключ клиента.
* `server_pub_key` (`str`) — публичный ключ сервера.
* `psk_key` (`str`) — preshared key (PSK).
* `hostName` (`str`) и `port` (`int`) — адрес и UDP-порт сервера.
* `mtu` (`str`) — размер MTU (в боте кастомизируется до `1280`).
* `allowed_ips` (`list[str]`) — маршруты (`["0.0.0.0/0", "::/0"]`).
* `H1-H4`, `I1-I5`, `Jc`, `Jmin`, `Jmax`, `S1-S4` — параметры обфускации AWG 2.0.

**Как `last_config` используется в боте (`utils/vpn_parser.py`):**
1. **Для `.conf`:** Бот извлекает `last_config["config"]`, кастомизирует `DNS` и `MTU`, и отдает пользователю как готовый `.conf` файл.
2. **Fallback:** Если строка `config` отсутствует, бот собирает INI-конфигурацию заново из отдельных полей `last_config` через `_build_conf_fallback`.
3. **Для `vpn://`:** Бот модифицирует `last_config`, упаковывает его обратно в JSON-строку (`json.dumps(last_config, ensure_ascii=False)`), сжимает весь контейнер через `zlib` и кодирует в `vpn://`.

> **⚠️ КРИТИЧЕСКОЕ РАЗЛИЧИЕ ФОРМАТОВ:**
> * **`.vpn`** — ВСЕГДА **JSON** (`json.dumps(dict, indent=2)`). Если положить туда текст WireGuard INI (`[Interface]`), приложение AmneziaVPN вернёт ошибку парсинга.
> * **`.conf`** — ВСЕГДА **WireGuard INI** (`[Interface]` + `[Peer]`). Если положить туда JSON, нативный клиент AmneziaWG и роутеры вернут ошибку.
> * **`vpn://`** — это закодированный в `Base64URL` сжатый через `zlib` исходный **JSON-документ** (с 4-байтным префиксом длины оригинального JSON в Big-Endian).

---

## ⚙️ 4. СТРУКТУРА ФАЙЛА `.conf` И ПАРАМЕТРЫ ОБФУСКАЦИИ

```ini
[Interface]
Address = 10.8.1.34/32
DNS = 8.8.8.8, 8.8.4.4
MTU = 1280
PrivateKey = uC6xUgdQDF4+fAOiw37ZQCG7XljilDsnBCl7VH7bAl8=

Jc = 4
Jmin = 10
Jmax = 50
S1 = 79
S2 = 115
S3 = 5
S4 = 1
H1 = 169154911-1234371153
H2 = 2057051984-2121122945
H3 = 2132872968-2133668229
H4 = 2136455412-2141801388

I1 = <r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>

[Peer]
PublicKey = bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o=
PresharedKey = PGh2rNsBmWVJC7qpa3fZ1dwB6tLjBUVKsxSZK6pMQRY=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = vpn.example.com:1234
PersistentKeepalive = 25
```

### Спецификация параметров AWG 2.0:
1. **`Jc`, `Jmin`, `Jmax` (Junk packets):** Количество и диапазон размеров мусорных пакетов перед хэндшейком.
2. **`S1`, `S2`, `S3`, `S4` (Packet sizes):** Размеры пакетов инициализации, ответа, cookie и префикса данных.
3. **`H1`, `H2`, `H3`, `H4` (Headers):** Заголовки пакетов. В протоколе `amneziawg-go` допускаются как одиночные значения (`H1 = 1234567890`), так и диапазоны (`H1 = 169154911-1234371153`). Серверный API `kyoresuas/amnezia-api` генерирует диапазоны. В коде всегда сохраняются строками без приведения к `int`.
4. **`I1`..`I5` (Custom Packet Sequences / CPS):** Пакеты инициализации протокола.
   - В коде проекта и официальных клиентах записываются как `I1`, `I2`, `I3`, `I4`, `I5`.
   - Парсеры официальных клиентов (`amneziawg-windows-client`, `amneziawg-android`, `amneziawg-go`) регистронезависимы.
   - *Known compatibility caveat:* В Android AmneziaWG пустые строки `I2 = `, `I3 = ` могут вызывать ошибки импорта QR (upstream issue #56). Если CPS-пакеты не используются сервером, их можно безопасно опускать.

---

## 🔐 5. ДЕКОДИРОВАНИЕ И КОДИРОВАНИЕ `vpn://` URI

```python
import base64
import json
import struct
import zlib

def decode_vpn_uri(uri: str) -> dict:
    payload = uri[6:]  # убрать vpn://
    b64 = payload.replace("-", "+").replace("_", "/")
    b64 += "=" * ((4 - len(b64) % 4) % 4)
    data = base64.b64decode(b64)
    
    orig_len = struct.unpack(">I", data[:4])[0]
    json_bytes = zlib.decompress(data[4:])
    if len(json_bytes) != orig_len:
        raise ValueError(f"Length mismatch: {len(json_bytes)} != {orig_len}")
    return json.loads(json_bytes.decode("utf-8"))

def encode_vpn_uri(config_dict: dict) -> str:
    json_bytes = json.dumps(config_dict, ensure_ascii=False).encode("utf-8")
    header = struct.pack(">I", len(json_bytes))
    compressed = zlib.compress(json_bytes, level=9)
    payload = header + compressed
    b64 = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"vpn://{b64}"
```

---

## 🔗 6. ИСТОЧНИКИ И СПРАВОЧНЫЕ МАТЕРИАЛЫ

### Официальные ресурсы Amnezia:
* [Amnezia Documentation Portal (RU)](https://docs.amnezia.org/ru/documentation)
* [Amnezia — Инструкции по настройке](https://docs.amnezia.org/ru/documentation/instructions/)
* [Amnezia — Альтернативные приложения](https://docs.amnezia.org/ru/documentation/alternative-clients)
* [Amnezia — Поддерживаемые форматы конфигураций](https://docs.amnezia.org/ru/documentation/supported-configuration-formats)
* [Amnezia — Как поделиться VPN-доступом](https://docs.amnezia.org/documentation/instructions/amnezia-hosting-sharing/)
* [Amnezia Client (Desktop/Mobile)](https://github.com/amnezia-vpn/amnezia-client)
* [DefaultVPN (iOS 17+ client)](https://github.com/amnezia-vpn/DefaultVPN)
* [AmneziaWG Go Engine](https://github.com/amnezia-vpn/amneziawg-go)
* [AmneziaWG Windows Client](https://github.com/amnezia-vpn/amneziawg-windows-client)
* [AmneziaWG Android](https://github.com/amnezia-vpn/amneziawg-android)
* [AmneziaWG Apple (iOS / macOS)](https://github.com/amnezia-vpn/amneziawg-apple)

### Сторонние и сопутствующие ресурсы:
* [kyoresuas/amnezia-api (Fastify REST API для управления AmneziaWG)](https://github.com/kyoresuas/amnezia-api) — стороннее серверное API, используемое ботом.
* [AmneziaWG-Architect (Community Validator)](https://github.com/Vadim-Khristenko/AmneziaWG-Architect) — генератор и валидатор параметров AWG.
