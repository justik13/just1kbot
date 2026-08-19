# 📚 AMNEZIA WG 2.0 — ТЕХНИЧЕСКИЙ СПРАВОЧНИК

> **ОБЛАСТЬ ДЕЙСТВИЯ:** В проекте используется **ТОЛЬКО AmneziaWG** (чистый стандартный WireGuard не поддерживается из-за DPI-блокировок).
> На текущий момент в продакшене активен **ТОЛЬКО протокол AmneziaWG 2.0** (`amneziawg2`), управляемый через серверный API `kyoresuas/amnezia-api`.
> **Планы развития (Roadmap):**
> 1. Поддержка **AmneziaWG 3.0** (`amneziawg3`) при появлении стабильного self-hosted серверного решения.
> 2. Интеграция протоколов стека **Xray** (VLESS-Reality, Trojan, Shadowsocks, Hysteria 2).

---

## 🚫 ТЕКУЩАЯ ПОЛИТИКА ПОДДЕРЖКИ ПРОТОКОЛОВ

| Протокол / Клиент | Статус в проекте | Пояснение |
|---|:---:|---|
| **AmneziaWG 2.0** (`amneziawg2`) | ✅ **PRODUCTION** | Единственный текущий рабочий протокол бота. Полная поддержка обфускации (`Jc`, `Jmin-Jmax`, `S1-S4`, `H1-H4`, `I1-I5`). |
| **AmneziaWG 3.0** (`amneziawg3`) | ⏳ **ROADMAP** | Запланирован к интеграции по мере готовности self-hosted сервера/образов. |
| **Xray** (`vless`, `reality`, `hy2`) | ⏳ **ROADMAP** | Запланирован в будущих релизах для диверсификации транспорта. |
| **Чистый WireGuard** | ❌ **НЕ ИСПОЛЬЗУЕТСЯ** | Не содержит обфускации, блокируется ТСПУ/DPI на сетях РФ. |
| **AmneziaWG 1.0 / 1.5** | ❌ **УСТАРЕЛ** | Нет полной матрицы обфускации `S3/S4`, диапазонов `H1-H4` и `I1-I5`. |
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

### 2. AmneziaWG (легковесный клиент)
* **Платформы:** Windows (включая Windows 7/8, 32-bit, ARM64), macOS, iOS, Android.
* **Импорт:** **Только `.conf` файлы** (или QR-код с содержимым `.conf`).
* **Назначение:** Альтернативный быстрый клиент для устройств, где не поддерживается или избыточен полный AmneziaVPN.

### Рекомендация интерфейса бота
1. **Быстрый доступ:** Кнопка **«🔑 Показать ключ»** — выводит `vpn://` URI для AmneziaVPN.
2. **Резервный доступ:** Кнопка **«📥 Скачать файлом»** — отправляет:
   - `device.vpn` — для AmneziaVPN;
   - `device.conf` — для AmneziaWG / роутеров / сторонних клиентов.

---

## 📄 3. СТРУКТУРА ФОРМАТА `.vpn` (JSON)

Это декодированное представление того, что возвращает `kyoresuas/amnezia-api` в поле `client.config` (`vpn://...`):

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
        "last_config": "{...JSON-СТРОКА...}"
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
* [Amnezia Developer Portal](https://docs.amnezia.org)
* [Amnezia — Alternative Apps & Native Configs](https://docs.amnezia.org/documentation/alternative-clients/)
* [Amnezia — Supported Configuration Formats](https://docs.amnezia.org/documentation/supported-configuration-formats/)
* [Amnezia — How to Share VPN Access](https://docs.amnezia.org/documentation/instructions/amnezia-hosting-sharing/)
* [Amnezia Client (Desktop/Mobile)](https://github.com/amnezia-vpn/amnezia-client)
* [AmneziaWG Go Engine](https://github.com/amnezia-vpn/amneziawg-go)
* [AmneziaWG Windows Client](https://github.com/amnezia-vpn/amneziawg-windows-client)
* [AmneziaWG Android](https://github.com/amnezia-vpn/amneziawg-android)
* [AmneziaWG Apple (iOS / macOS)](https://github.com/amnezia-vpn/amneziawg-apple)

### Сторонние и сопутствующие ресурсы:
* [kyoresuas/amnezia-api (Fastify REST API для управления AmneziaWG)](https://github.com/kyoresuas/amnezia-api) — стороннее серверное API, используемое ботом.
* [AmneziaWG-Architect (Community Validator)](https://github.com/Vadim-Khristenko/AmneziaWG-Architect) — генератор и валидатор параметров AWG.
