# 📚 AMNEZIA WG (2.0 / 3.0 / 3.1) — ТЕХНИЧЕСКИЙ СПРАВОЧНИК

## 🚫 ТЕКУЩАЯ ПОЛИТИКА ПОДДЕРЖКИ ПРОТОКОЛОВ

В архитектуре проекта `just1kbot` поддерживается семейство современных протоколов **AmneziaWG 2.0+** (`AMNEZIA_PROTOCOLS`), управляемых через нативный серверный микросервис `amnezia-api` (`scripts/amnezia_api/`) и утилиты автоматизации узлов `just1knode`.

Чистый WireGuard (`wg`) и устаревшие версии AmneziaWG ниже 2.0 категорически не используются и удалены из кодовой базы.

| Протокол / Версия | Код в системе (`Server.protocol`) | Статус в проекте | Технические особенности и обфускация |
|---|:---:|:---:|---|
| **AmneziaWG 2.0** | `amneziawg2` | ✅ **PRODUCTION** | Базовая обфускация: мусорные пакеты (`Jc`, `Jmin-Jmax`), фиксированные размеры пакетов (`S1-S4`), кастомные заголовки (`H1-H4`), префиксы CPS (`I1-I5`). |
| **AmneziaWG 3.0** | `amneziawg3` | ✅ **PRODUCTION / SUPPORTED** | Включает все механизмы AWG 2.0 + симметричное шифрование заголовков пакетов (`HeaderProtectionKey`). Обязательный криптографический инвариант пола паддинга: $S1..S4 \ge 12$. |
| **AmneziaWG 3.1** | `amneziawg3.1` | ✅ **PRODUCTION / SUPPORTED** | Актуальный стандарт self-hosted Amnezia. Добавляет рандомизированные концевики (`RandomTrailers = on`), защиту от активного зондирования (`DisableCookies = on`), настраиваемые интервалы хэндшейков (`RekeyAfterTime`, `RekeyTimeout`, `RejectAfterTime`, `KeepaliveTimeout`, `MaxHandshakeAttempts`). |
| **Xray** (`vless` over XHTTP) | `xray` | ✅ **PRODUCTION (Белый Интернет)** | Изолированный контур «Белый Интернет» (клиент INCY через Yandex Cloud CDN). Физически и логически отделён от контура AmneziaWG. |
| **Чистый WireGuard (`wg`)** | — | ❌ **ЗАПРЕЩЕН / НЕ ПОДДЕРЖИВАЕТСЯ** | Не имеет механизмов маскировки рукопожатий и блокируется ТСПУ/DPI сигнатурным анализом. |
| **AmneziaWG 1.0 / 1.5 (`amnezia-awg`, `wg0.conf`)** | — | ❌ **ПОЛНОСТЬЮ УДАЛЕН** | Устаревшие форматы и файлы удалены из бота и скриптов нод. |

---

## 🏗️ КЛЮЧЕВОЙ АРХИТЕКТУРНЫЙ ИНВАРИАНТ: РАЗДЕЛЕНИЕ КОНТЕЙНЕРА И ПРОТОКОЛА

Фундаментальный принцип устройства серверной инфраструктуры проекта:

1. **Имя Docker-контейнера СТРОГО единообразно**:
   * Upstream-установщик AmneziaVPN (режим self-hosted Docker) для ВСЕХ версий AWG (2.0, 3.0, 3.1) создает контейнер с неизменным именем:
     ```text
     amnezia-awg2
     ```
   * В коде проекта это закреплено константой `AMNEZIA_DOCKER_CONTAINER = "amnezia-awg2"`.
   * Конфигурационный файл ядра внутри контейнера всегда располагается по пути:
     ```text
     /opt/amnezia/awg/awg0.conf
     ```
   * Сетевой интерфейс ядра Linux всегда называется: `awg0`.

2. **Версия протокола СТРОГО динамическая**:
   * В базе данных (`Server.protocol`), API-контрактах и очередях операций (`ApiOperation.payload.protocol`) версия фиксируется адресно:
     * `"amneziawg2"` — при наличии только параметров AWG 2.0;
     * `"amneziawg3"` — при наличии `HeaderProtectionKey`;
     * `"amneziawg3.1"` — при наличии `HeaderProtectionKey` + `RandomTrailers` / `DisableCookies`.
   * Определение версии выполняется автоматически функцией `detect_awg_version()` при опросе ноды.

---

## 📦 1. ФОРМАТЫ КОНФИГУРАЦИИ И КЛИЕНТСКАЯ СОВМЕСТИМОСТЬ

| Формат | Расширение | Совместимость с приложениями | Внутреннее устройство |
|---|:---:|---|---|
| **AmneziaVPN native** | `.vpn` | **AmneziaVPN** (официальный универсальный клиент) | Валидный JSON с объектами `containers`, `awg`, `last_config` |
| **AmneziaWG conf** | `.conf` | **AmneziaWG** (нативное приложение), **AmneziaVPN** (импорт файла), роутеры (OpenWrt / Keenetic) | Текстовый WireGuard INI с директивами AWG 2.0 / 3.x |
| **vpn:// URI** | — (строка) | **AmneziaVPN**, **DefaultVPN** (вставка ключа из буфера / сканирование QR) | `vpn://` + Base64URL(4 байта длины BE + zlib(JSON)) |

### ⚠️ Правила совместимости:
1. **`vpn://` URI**: работает в универсальном клиенте **AmneziaVPN** и в **DefaultVPN**. Легковесный клиент AmneziaWG не поддерживает импорт URI `vpn://` (требует `.conf`).
2. **Файл `.conf` универсален**: открывается нативным клиентом AmneziaWG, универсальным AmneziaVPN (через «Подключиться по файлу»), а также роутерами с пакетом `kmod-amneziawg`.
3. **Файл `.vpn`**: предназначен строго для AmneziaVPN. Содержит метаданные сервера и JSON-контейнеры.
4. **В интерфейсе бота**:
   * Моноширинный блок с ключом `vpn://` выдается сразу в карточке устройства (нажмите для копирования).
   * По кнопке **«🔄 Другой способ подключения»** бот отправляет файлы `device.vpn` и `device.conf`.

---

## 📱 2. ПОДДЕРЖИВАЕМЫЕ КЛИЕНТСКИЕ ПРИЛОЖЕНИЯ

### 1. AmneziaVPN (основной клиент)
* **Платформы:** Windows 10/11, macOS 14+, Linux, Android, iOS.
* **Импорт:** Ключ `vpn://...`, файл `.vpn`, файл `.conf`.
* **Поддержка версий:** AWG 2.0, AWG 3.0, AWG 3.1.
* **Особенность:** В официальном GUI AmneziaVPN присутствует известный косметический баг периодического обнуления счетчика переданных байт в интерфейсе приложения (например, сброс со 110 ГБ до 300 КБ). Данный сброс происходит исключительно локально в UI и не влияет на серверный учет трафика.

### 2. DefaultVPN (легковесный клиент для iOS)
* **Платформы:** iOS 17+.
* **Репозиторий:** [`amnezia-vpn/DefaultVPN`](https://github.com/amnezia-vpn/DefaultVPN) / [App Store](https://apps.apple.com/app/defaultvpn/id6744725017)
* **Импорт:** Ключ `vpn://...` или файл `.conf`.
* **Назначение:** Быстрый нативный клиент от команды Amnezia с прямой поддержкой AWG и Xray Reality.

### 3. AmneziaWG (нативный легковесный клиент)
* **Платформы:** Windows (включая Windows 7/8, 32-bit, ARM64), macOS, iOS, Android, роутеры OpenWrt/Keenetic.
* **Импорт:** **Только `.conf` файлы** или QR-код с содержимым `.conf`.
* **Назначение:** Минималистичный системный клиент на базе драйвера TUN/WireGuard без дополнительных обвязок.

---

## ⚙️ 3. СПЕЦИФИКАЦИЯ ПАРАМЕТРОВ ОБФУСКАЦИИ (AWG 2.0 / 3.0 / 3.1)

### 3.1. Базовые параметры AmneziaWG 2.0
Параметры размещаются в секции `[Interface]` конфигурационного файла:

* **`Jc` (Junk packet count)**: Количество мусорных пакетов перед инициализацией соединения.
  * Допустимый диапазон: `0 <= Jc <= 128`.
  * Значение `Jc = 0` разрешено upstream-спецификацией `amneziawg-go` и полностью отключает генерацию мусорных пакетов.
* **`Jmin`, `Jmax` (Junk packet size range)**: Минимальный и максимальный размер мусорного пакета в байтах.
  * Диапазон: `0 <= Jmin <= Jmax <= 1280`.
* **`S1`, `S2`, `S3`, `S4` (Padding sizes)**:
  * `S1`: размер паддинга сообщения инициализации (`initiation packet`).
  * `S2`: размер паддинга сообщения ответа (`response packet`).
  * `S3`: размер паддинга cookie-пакета.
  * `S4`: размер паддинга пакетов данных.
* **`H1`, `H2`, `H3`, `H4` (Header identifiers)**:
  * Заголовки пакетов для замены стандартных WireGuard-типов (1, 2, 3, 4).
  * Могут задаваться как одиночными целыми числами (`H1 = 1234567890`), так и диапазонами (`H1 = 169154911-1234371153`).
  * В кодовой базе всегда сохраняются и передаются как `str` без принудительного приведения к `int`.
* **`I1`..`I5` (Custom Packet Sequences / CPS)**:
  * Кастомные последовательности пакетов для имитации протоколов (например, DNS, TLS ClientHello).
  * Формат: `<r 2><b 0x8580...>`.
  * Регистронезависимы. Если не используются, директивы опускаются.

---

### 3.2. Дополнительные параметры AmneziaWG 3.0 и 3.1

#### 1. `HeaderProtectionKey` (Симметричная защита заголовков — AWG 3.0+)
* 32-байтный ключ в кодировке Base64 (длина 44 символа с `=` на конце).
* Используется для симметричного потокового шифрования ChaCha20 заголовков пакетов, полностью скрывая сигнатуру типов WireGuard от пассивного и активного DPI.

#### 2. Криптографический инвариант пола S-padding ($S1..S4 \ge 12$)
* **Источник в коде**: `amneziawg-go v3.0.1` (`device/send.go` и `device/uapi.go`), Linux Kernel module (`src/netlink.c`), `Any-Tech-ARCHITECT`.
* **Математическая суть инварианта**:
  В механизме Header Protection алгоритм ChaCha20 требует 12-байтный одноразовый вектор инициализации (Nonce). Реализация AmneziaWG конструирует этот нонс непосредственно из префикса паддинга:
  $$\text{nonce} := \text{crypt}[:\text{HeaderCipherNonceSize}] \quad (\text{где HeaderCipherNonceSize} = 12 \text{ байт})$$
  Если хотя бы один из параметров $S1, S2, S3, S4 < 12$, буфер нонса перекрывает тело полезной нагрузки пакета.
* **Поведение ядра**:
  При попытке передать в ядро конфигурацию с `HeaderProtectionKey` и $S < 12$, ядро Linux и `amneziawg-go` немедленно отклоняют конфигурацию с системной ошибкой `-EINVAL`:
  ```text
  "S%d must be more then %d to use headerProtection"
  ```
* **Правило валидатора проекта**:
  При наличии `HeaderProtectionKey` валидаторы `app.py` и бота строго проверяют условие $\min(S1, S2, S3, S4) \ge 12$.

#### 3. `RandomTrailers` (Рандомизация концевиков — AWG 3.1)
* Значение в `.conf`: `RandomTrailers = on` (в JSON допустимо `"1"` или `"on"`).
* Добавляет к пакетам данных переменные хвостовые байты случайной длины, предотвращая fingerprinting соединения по корреляции длин пакетов MTU.

#### 4. `DisableCookies` (Блокировка активного зондирования — AWG 3.1)
* Значение в `.conf`: `DisableCookies = on`.
* Отключает отправку стандартных WireGuard mac2 cookie-пакетов при перегрузке, нейтрализуя активные сетевые сканеры (active probes), зондирующие сервер характерными ответами WireGuard.

#### 5. Параметры времени и стабильности сессии (AWG 3.1)
Задаются диапазонами для предотвращения тайминг-атак DPI:
* `RekeyAfterTime`: интервал планового пересогласования ключей (например, `100-120` сек).
* `RekeyTimeout`: таймаут ожидания ответа на рекей (например, `3-7` сек).
* `RejectAfterTime`: максимальное время удержания сессии без подтверждения (например, `150-180` сек).
* `KeepaliveTimeout`: интервал поддержания соединения (например, `5-15` сек).
* `MaxHandshakeAttempts`: число повторных попыток хэндшейка (например, `15-20`).

---

## 📄 4. ЭТАЛОННЫЕ ПРИМЕРЫ КОНФИГУРАЦИЙ

### 4.1. Эталонный `.conf` для AWG 3.1 (реальный сервер `just1knode`)

```ini
[Interface]
Address = 10.8.1.5/32
DNS = 1.1.1.1, 1.0.0.1
MTU = 1280
PrivateKey = aHtwGE7ajuHsJCIzjyagdbXPnoPk8PpJ4kGAs2vv0G0=
ListenPort = 40336

Jc = 4
Jmin = 10
Jmax = 50
S1 = 12
S2 = 12
S3 = 12
S4 = 12
H1 = 1
H2 = 2
H3 = 3
H4 = 4
HeaderProtectionKey = w4tUyn8Wn/npv1yb9DinRhjtlC2JF+dIvIglBBBlTzU=
RekeyAfterTime = 100-120
RekeyTimeout = 3-7
RejectAfterTime = 150-180
KeepaliveTimeout = 5-15
MaxHandshakeAttempts = 15-20
RandomTrailers = on
DisableCookies = on

[Peer]
PublicKey = YC9bNJlf48m4jx4iDyDey6O02kYdHvwTEziT3IzvqC8=
PresharedKey = 0jEf5SzdQzBaldFifpSwaLyeWZZqzAWhHi9FbIGJ6wM=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 168.222.254.191:40336
PersistentKeepalive = 25
```

---

### 4.2. Эталонный `.vpn` (Native JSON AmneziaVPN) для AWG 3.1

```json
{
  "containers": [
    {
      "container": "amnezia-awg2",
      "awg": {
        "protocol_version": "3.1",
        "port": 40336,
        "transport_proto": "udp",
        "Jc": "4",
        "Jmin": "10",
        "Jmax": "50",
        "S1": "12",
        "S2": "12",
        "S3": "12",
        "S4": "12",
        "H1": "1",
        "H2": "2",
        "H3": "3",
        "H4": "4",
        "HeaderProtectionKey": "w4tUyn8Wn/npv1yb9DinRhjtlC2JF+dIvIglBBBlTzU=",
        "RandomTrailers": "on",
        "DisableCookies": "on",
        "RekeyAfterTime": "100-120",
        "RekeyTimeout": "3-7",
        "RejectAfterTime": "150-180",
        "KeepaliveTimeout": "5-15",
        "MaxHandshakeAttempts": "15-20",
        "last_config": "{\"H1\":\"1\",\"H2\":\"2\",\"H3\":\"3\",\"H4\":\"4\",\"HeaderProtectionKey\":\"w4tUyn8Wn/npv1yb9DinRhjtlC2JF+dIvIglBBBlTzU=\",\"Jc\":\"4\",\"Jmin\":\"10\",\"Jmax\":\"50\",\"RandomTrailers\":\"on\",\"DisableCookies\":\"on\",\"S1\":\"12\",\"S2\":\"12\",\"S3\":\"12\",\"S4\":\"12\",\"allowed_ips\":[\"0.0.0.0/0\",\"::/0\"],\"client_ip\":\"10.8.1.5\",\"client_priv_key\":\"...\",\"client_pub_key\":\"...\",\"config\":\"[Interface]\\nAddress = 10.8.1.5/32\\n...\",\"hostName\":\"168.222.254.191\",\"mtu\":\"1280\",\"port\":40336,\"psk_key\":\"...\",\"server_pub_key\":\"YC9bNJlf48m4jx4iDyDey6O02kYdHvwTEziT3IzvqC8=\"}"
      }
    }
  ],
  "defaultContainer": "amnezia-awg2",
  "description": "AmneziaWG Server",
  "dns1": "1.1.1.1",
  "dns2": "1.0.0.1",
  "hostName": "168.222.254.191"
}
```

> **Архитектурная деталь `last_config`:**
> Поле `awg.last_config` — это строго **экранированная строка JSON** (`string`), содержащая готовый текст `config` и плоский словарь параметров. Десериализуется клиентами через `json.loads()`.

---

## 🔐 5. ДЕКОДИРОВАНИЕ И КОДИРОВАНИЕ `vpn://` URI

```python
import base64
import json
import struct
import zlib

def decode_vpn_uri(uri: str) -> dict:
    """Декодирование Amnezia vpn:// URI в JSON-документ."""
    payload = uri[6:]  # Удаление префикса vpn://
    b64 = payload.replace("-", "+").replace("_", "/")
    b64 += "=" * ((4 - len(b64) % 4) % 4)
    data = base64.b64decode(b64)
    
    orig_len = struct.unpack(">I", data[:4])[0]
    json_bytes = zlib.decompress(data[4:])
    if len(json_bytes) != orig_len:
        raise ValueError(f"Длина не совпадает: {len(json_bytes)} != {orig_len}")
    return json.loads(json_bytes.decode("utf-8"))

def encode_vpn_uri(config_dict: dict) -> str:
    """Кодирование JSON-конфигурации в Amnezia vpn:// URI."""
    json_bytes = json.dumps(config_dict, ensure_ascii=False).encode("utf-8")
    header = struct.pack(">I", len(json_bytes))
    compressed = zlib.compress(json_bytes, level=8)
    payload = header + compressed
    b64 = (
        base64.urlsafe_b64encode(payload)
        .decode("ascii")
        .rstrip("=")
        .replace("+", "-")
        .replace("/", "_")
    )
    return f"vpn://{b64}"
```

---

## 🖥️ 6. СЕРВЕРНЫЙ МИКРОСЕРВИС `amnezia-api` И НОДА `just1knode`

### 6.1. Архитектура микросервиса (`scripts/amnezia_api/app.py`)
Микросервис на базе **FastAPI** развёртывается на каждом сервере AmneziaWG в директории `/opt/amnezia-api` и управляется systemd-службой `amnezia-api.service`:

* **Изоляция транзакций**: Все операции изменения конфигурации сериализуются через асинхронный мьютекс `state_lock = asyncio.Lock()`.
* **Прямое взаимодействие с ядром**:
  * Добавление пира: `docker exec -i amnezia-awg2 awg set awg0 peer <pub> preshared-key /dev/stdin allowed-ips <ip>` (PSK передаётся безопасно через stdin, не попадая в `ps` процессную таблицу).
  * Удаление пира: `docker exec amnezia-awg2 awg set awg0 peer <pub> remove`.
  * Чтение состояния и счетчиков трафика: `docker exec amnezia-awg2 awg show awg0 dump`.
* **Синхронизация состояния**: Изменения атомарно записываются в `/opt/amnezia/awg/awg0.conf` и в локальную базу метаданных `/opt/amnezia/awg/clientsTable.json`.

### 6.2. Ключевые REST API эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/server` | Метаданные сервера, порт, публичный ключ, `protocol` (основной) и `protocols` (поддерживаемые). |
| `GET` | `/server/load` | Нагрузка системы (CPU, RAM, диск, uptime, число активных пиров). |
| `POST` | `/clients` | Регистрация нового пира (`clientName`, `protocol`, `expiresAt`). Возвращает `.conf` и `vpn://`. |
| `PATCH` | `/clients` | Обновление статуса пира (`active`/`disabled`) и времени окончания подписки. |
| `DELETE` | `/clients` | Удаление пира из ядра и файла конфигурации по `clientId` / `clientName`. |
| `GET` | `/server/backup` | Полный экспорт состояния ноды (`awg0.conf`, `clientsTable.json`, PSK). |
| `POST` | `/server/backup` | Восстановление сервера из бэкапа с валидацией `[Interface]` и транзакционным откатом при сбое. |

---

## 🔗 7. ИСТОЧНИКИ И СПРАВОЧНЫЕ МАТЕРИАЛЫ

### Официальные репозитории:
* [AmneziaVPN Client (Desktop / Mobile)](https://github.com/amnezia-vpn/amnezia-client)
* [AmneziaWG Go Engine (`amneziawg-go`)](https://github.com/amnezia-vpn/amneziawg-go)
* [AmneziaWG Linux Kernel Module](https://github.com/amnezia-vpn/amneziawg-linux-kernel-module)
* [AmneziaWG Tools (`awg`, `awg-quick`)](https://github.com/amnezia-vpn/amneziawg-tools)
* [DefaultVPN (iOS Client)](https://github.com/amnezia-vpn/DefaultVPN)
* [Amnezia Documentation Portal](https://docs.amnezia.org/)

### Архитектурные валидаторы:
* [Any-Tech-ARCHITECT (AmneziaWG Parameter Generator)](https://github.com/Vadim-Khristenko/Any-Tech-ARCHITECT)
* [just1kbot amnezia_api](../scripts/amnezia_api/) — серверный микросервис интеграции проекта.
