# ⚪ БЕЛЫЙ ИНТЕРНЕТ 2026: ТЕХНИЧЕСКИЙ АУДИТ, АНАЛИЗ БЛОКИРОВОК ТСПУ/РКН И СПЕЦИФИКАЦИЯ АРХИТЕКТУРЫ

> **Дата актуализации:** 1 сентября 2026 года  
> **Статус документа:** Единый источник истины (SSOT) по исследованию белых списков, обходу ТСПУ L3/L7 фильтрации, каскадному проксированию VLESS XHTTP через Yandex Cloud CDN и интеграции с клиентами **INCY**, **AmneziaVPN**, **Happ**, **v2rayNG**.

---

## 📑 СОДЕРЖАНИЕ

1. [Анализ цензурной инфраструктуры РФ: Black List vs White List](#1-анализ-цензурной-инфраструктуры-рф-black-list-vs-white-list)
2. [Анатомия фильтрации ТСПУ в режиме «Белых списков» (Default-Drop)](#2-анатомия-фильтрации-тспу-в-режиме-белых-списков-default-drop)
   - 2.1. [Двухуровневый барьер: L3 (CIDR) + L7 (SNI / DPI)](#21-двухуровневый-барьер-l3-cidr--l7-sni--dpi)
   - 2.2. [Тотальная смерть UDP (WireGuard / AmneziaWG / QUIC / DNS)](#22-тотальная-смерть-udp-wireguard--amneziawg--quic--dns)
   - 2.3. [Иерархия белых подсетей в РФ](#23-иерархия-белых-подсетей-в-рф)
3. [Архитектура обхода: VLESS XHTTP через Yandex Cloud CDN](#3-архитектура-обхода-vless-xhttp-через-yandex-cloud-cdn)
   - 3.1. [Сетевая топология каскада Multi-Hop](#31-сетевая-топология-каскада-multi-hop)
   - 3.2. [Метод OPTIONS и обход блокировки POST на CDN (PR #5414)](#32-метод-options-и-обход-блокировки-post-на-cdn-pr-5414)
   - 3.3. [Nginx на Origin: маппинг методов и Zero Buffering](#33-nginx-на-origin-маппинг-методов-и-zero-buffering)
   - 3.4. [Обфускация паддинга и защита от сканеров (Anti-Fingerprinting)](#34-обфускация-паддинга-и-защита-от-сканеров-anti-fingerprinting)
4. [Поддержка Multi-Relay: Масштабируемость локаций](#4-поддержка-multi-relay-масштабируемость-локаций)
5. [Клиентская экосистема](#5-клиентская-экосистема)
   - 5.1. [Основной клиент (Primary #1): INCY (Xray Native)](#51-основной-клиент-primary-1-incy-xray-native)
   - 5.2. [Второй клиент (Secondary #2): AmneziaVPN (Контейнер amnezia-xray)](#52-второй-клиент-secondary-2-amneziavpn-контейнер-amnezia-xray)
   - 5.3. [Happ / v2rayNG / NekoBox (VLESS Extra URI)](#53-happ--v2rayng--nekobox-vless-extra-uri)
6. [Утилита управления серверами: `just1knode`](#6-утилита-управления-серверами-just1knode)
   - 6.1. [Интерактивная консольная панель](#61-интерактивная-консольная-панель)
   - 6.2. [Три режима установки (Amnezia API / Origin / Relay)](#62-три-режима-установки-amnezia-api--origin--relay)
   - 6.3. [Легковесный `xray-api` без 3x-ui и локальная персистентность](#63-легковесный-xray-api-без-3x-ui-и-локальная-персистентность)
7. [Продуктовая модель в Telegram-боте (`just1kbot`)](#7-продуктовая-модель-в-telegram-боте-just1kbot)
8. [Сводный каталог исследований и источников](#8-сводный-каталог-исследований-и-источников)

---

## 1. АНАЛИЗ ЦЕНЗУРНОЙ ИНФРАСТРУКТУРЫ РФ: BLACK LIST VS WHITE LIST

| Параметр | Черные списки (Default-Allow) | Белые списки (Default-Drop / Default-Deny) |
| :--- | :--- | :--- |
| **Принцип работы** | Разрешен весь интернет, кроме записей из единого реестра блокировок РКН | Заблокирован весь мировой интернет (~46 млн IPv4). Разрешены **только ~63 000 белых IP-адресов** |
| **Где применяется** | Проводной интернет (ШПД), Wi-Fi, мобильная связь в штатном режиме | Мобильные операторы (LTE/3G/5G) в периоды ограничений, приграничные регионы, режим ЧС |
| **Доступность ресурсов** | Работают Google, Telegram, App Store, GitHub, Википедия. Блокируются Instagram, Twitter, заблокированные СМИ | Не работает **ни один зарубежный сервис**. Открываются только ресурсы из белого списка: Госуслуги, Яндекс, ВК, Ozon, Сбер |
| **VPN-протоколы** | AmneziaWG (`awg`), VLESS Reality, Shadowsocks-2022 | **Строго VLESS XHTTP over TLS (TCP:443)**. Любой UDP блокируется на 100% |
| **Схема проксирования** | Прямой зарубежный сервер (Германия, Финляндия) | **Обязательный Multi-Hop каскад**: Клиент ➔ Российский CDN (White IP) ➔ Origin (РФ) ➔ Exit (Зарубежье) |

---

## 2. АНАТОМИЯ ФИЛЬТРАЦИИ ТСПУ В РЕЖИМЕ «БЕЛЫХ СПИСКОВ» (DEFAULT-DROP)

### 2.1. Двухуровневый барьер: L3 (CIDR) + L7 (SNI / DPI)

Трафик абонента на оборудовании ТСПУ (развернутом на 2-м сетевом хопе у всех операторов «Большой четверки») проходит жесткую двухэтапную фильтрацию:

```text
       [Клиентский пакет (LTE/5G)]
                   │
                   ▼
  ┌─────────────────────────────────┐
  │  Уровень L3: Фильтр подсетей    │ ──► IP НЕ в Белом Списке? ──► TCP RST / DROP (Пакет уничтожен)
  │       (White CIDR Table)        │
  └─────────────────────────────────┘
                   │ IP разрешен (Yandex, VK, Gosuslugi)
                   ▼
  ┌─────────────────────────────────┐
  │  Уровень L7: ТСПУ DPI Инспекция │ ──► Неразрешенный SNI / аномальный TLS? ──► TCP RST
  │   (SNI + JA3/JA4 + Entropy)     │
  └─────────────────────────────────┘
                   │ SNI валиден + отпечаток браузера легитимен
                   ▼
    [Легитимный узел / Edge CDN РФ]
```

1. **L3 Фильтрация (Network Layer):**
   - Роутеры оператора дропают пакеты на все IP-адреса, не входящие в белый список доверенных CIDR.
   - Любое прямое обращение к зарубежным хостингам (Hetzner, OVH, DigitalOcean, AWS) уничтожается моментально.
2. **L7 Инспекция (Application / DPI):**
   - ТСПУ разбирает `TLS ClientHello`.
   - Проверяется поле `Server Name Indication (SNI)`. Если IP принадлежит Яндексу, но SNI указывает на незарегистрированный или запрещенный домен — TCP-сессия сбрасывается с помощью инжектированного `TCP RST`.
   - Анализируется отпечаток TLS (JA3/JA4/uTLS) и профиль трафика.

---

### 2.2. Тотальная смерть UDP (WireGuard / AmneziaWG / QUIC / DNS)

> [!WARNING]
> В режиме Белых списков протокол **UDP полностью блокируется либо жестко режется**:
> - UDP-порт 53 (традиционный DNS) перехватывается или глушится.
> - UDP-порт 443 (QUIC / HTTP/3) полностью сбрасывается.
> - **AmneziaWG и WireGuard (UDP) физически не работают в белых списках**, так как на L3/L4 операторский фильтр уничтожает весь несогласованный UDP-трафик независимо от параметров обфускации (`Jc`, `S1`, `H1-H4`).
> - **Вывод:** В Белом Интернете протокол AmneziaWG **не используется**. Используется исключительно стек **Xray (VLESS XHTTP поверх TCP/TLS 443)**. AmneziaWG остается только для стандартной подписки (Черные списки).

---

### 2.3. Иерархия белых подсетей в РФ

1. **Tier 1 (Абсолютный приоритет):**
   - Госмессенджер `MAX` (`max.ru`, `complat.ru`), `gosuslugi.ru`, ресурсы `*.gov.ru`.
2. **Tier 2 (Высокий приоритет — базовая инфраструктура):**
   - **Яндекс:** `ya.ru`, `yandex.ru`, `storage.yandex.net`, `yastatic.net`.
   - **Yandex Cloud CDN:** IP-диапазоны `*.yccdn.ru`, `*.gslb.yccdn.ru` (входят в белые списки всех операторов: МТС, Мегафон, Билайн, Т2).
   - **VK:** `vk.com`, `userapi.com`, `vkuser.net`.
3. **Tier 3 (Локальный приоритет):**
   - Маркетплейсы (`ozon.ru`, `wildberries.ru`), Банки (`salutejazz.ru`, ВТБ, Т-Банк).

---

## 3. АРХИТЕКТУРА ОБХОДА: VLESS XHTTP ЧЕРЕЗ YANDEX CLOUD CDN

### 3.1. Сетевая топология каскада Multi-Hop

```text
   [ Клиент (INCY / Amnezia / Happ) ]
                  │
                  │ 1. HTTPS (TLS 443, HTTP/2)
                  │    Метод: OPTIONS (uplinkHTTPMethod)
                  │    SNI: cdn.yourdomain.com
                  │    Header: X-Cache: token... (xPadding)
                  ▼
      [ Yandex Cloud CDN (Edge) ]
   (Белый IP из списка РФ, валидный Let's Encrypt SSL)
                  │
                  │ 2. Проксирование на Origin (TLS 443)
                  │    Host: origin.yourdomain.com
                  │    SNI: origin.yourdomain.com
                  ▼
      [ Origin-сервер: Россия ]
     ┌────────────────────────────────────────────────────────┐
     │ Nginx (Порт 443):                                      │
     │  - SSL Termination (Let's Encrypt)                     │
     │  - Маппинг: OPTIONS -> POST ($xhttp_proxy_method)      │
     │  - Zero Buffering (proxy_buffering off)               │
     │  - Проброс: /stream/de -> 127.0.0.1:8003               │
     │             /stream/nl -> 127.0.0.1:8004               │
     │                                                        │
     │ Xray-core (Inbounds 8003, 8004...):                    │
     │  - Протокол: VLESS (XHTTP transport, packet-up)        │
     │  - Outbounds: VLESS-Vision TLS -> Relays               │
     └────────────────────────────────────────────────────────┘
                  │
                  │ 3. Межсерверные туннели VLESS-Vision (TLS 10443)
                  │    (Неотличимы от легитимного HTTPS)
                  ▼
       ┌───────────────────────────────┐
       ▼                               ▼
 [ Relay 1: Германия (Exit) ]    [ Relay 2: Нидерланды (Exit) ]
 (Xray Inbound: 10443)           (Xray Inbound: 10443)
 (Outbound: Freedom)             (Outbound: Freedom)
       │                               │
       └───────────────┬───────────────┘
                       │ 4. Свободный интернет
                       ▼
      [ YouTube, Telegram, Google, ChatGPT, Instagram ]
```

---

### 3.2. Метод OPTIONS и обход блокировки POST на CDN (PR #5414)

* **Проблема:** Российские CDN (включая Yandex Cloud CDN) по умолчанию блокируют метод HTTP `POST` для динамических путей или включают жесткую буферизацию тела запроса, что делает интерактивный VPN-стриминг невозможным.
* **Решение в Xray-core:** В ядро Xray был добавлен параметр `uplinkHTTPMethod` ([PR #5414](https://github.com/XTLS/Xray-core/pull/5414)).
* **Выбор метода `OPTIONS`:**
  - В Yandex Cloud CDN метод `OPTIONS` разрешен в стандартном профиле веб-приложений (как CORS Preflight).
  - Метод `OPTIONS` не подвергается валидации схемы тела запроса на CDN.
  - ТСПУ полностью игнорирует запросы `OPTIONS`, считая их легитимными служебными браузерными запросами.

---

### 3.3. Nginx на Origin: маппинг методов и Zero Buffering

Nginx на Origin принимает `OPTIONS` от CDN и передает в Xray как `POST`:

```nginx
# /etc/nginx/conf.d/xhttp-method.conf
map $request_method $xhttp_proxy_method {
    default  $request_method;
    OPTIONS  POST;
}
```

```nginx
# Фрагмент /etc/nginx/sites-available/xhttp-origin.conf
server {
    listen 443 ssl http2;
    server_name origin.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/origin.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/origin.yourdomain.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    client_max_body_size 0;
    client_header_buffer_size 64k;
    large_client_header_buffers 8 128k;

    # Служебный эндпоинт проверки
    location = /cdn-check {
        add_header X-CDN-Origin "ok" always;
        add_header X-Origin-Method $request_method always;
        add_header X-Origin-Content-Length $http_content_length always;
        return 204;
    }

    # Локация Relay 1 (Германия)
    location /stream/de {
        proxy_pass http://127.0.0.1:8003;
        proxy_method $xhttp_proxy_method;
        proxy_http_version 1.1;
        proxy_set_header Connection "";

        proxy_pass_request_headers on;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Тотальное отключение буферизации
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

---

### 3.4. Обфускация паддинга и защита от сканеров (Anti-Fingerprinting)

1. **Динамические и случайные пути:**
   - Пути вроде `/api/v3/secure-data` или дефолтные префиксы `/sub/wl/` быстро детектируются.
   - Секретные пути XHTTP на Origin генерируются с высокой энтропией (например, `/d8f7a29e4b/de`).
   - Префикс подписки бота настраивается через переменную окружения `WHITE_INTERNET_SUB_PATH_PREFIX` (например, `/s/w/` или случайный хэш) с криптостойким токеном пользователя.
2. **Параметры обфускации XHTTP:**
   - `xPaddingObfsMode`: `true`
   - `xPaddingKey`: случайный двухсимвольный ключ (например, `"dc"`, `"a1"`)
   - `xPaddingHeader`: `"X-Cache"` (маскировка под стандартный заголовок CDN)
   - `xPaddingMethod`: `"tokenish"`
   - `xPaddingPlacement`: `"queryInHeader"`

---

## 4. ПОДДЕРЖКА MULTI-RELAY: МАСШТАБИРУЕМОСТЬ ЛОКАЦИЙ

Origin-сервер выступает шлюзом и агрегатором: к нему подключается **произвольное количество Relay-серверов** (зарубежных нод выхода).

При генерации подписки для пользователя сервер формирует список всех доступных стран:
- `🇩🇪 Германия (Белый Интернет)` ➔ маршрут: CDN ➔ Origin:8003 ➔ Relay DE
- `🇳🇱 Нидерланды (Белый Интернет)` ➔ маршрут: CDN ➔ Origin:8004 ➔ Relay NL
- `🇸🇪 Швеция (Белый Интернет)` ➔ маршрут: CDN ➔ Origin:8005 ➔ Relay SE

Управление релеями на Origin выполняется через `just1knode`:
```bash
just1knode relay add de 94.249.x.x 10443 "Германия"
just1knode relay add nl 185.146.x.x 10443 "Нидерланды"
just1knode relay list
just1knode relay remove nl
```

---

## 5. КЛИЕНТСКАЯ ЭКОСИСТЕМА

### 5.1. Основной клиент (Primary #1): INCY (Xray Native)

INCY — целевой клиент под iOS, Android, macOS и Windows.

#### 1. Формат HTTP Subscription Feed (`/sub/{token}`)
Сервер бота отдает заголовки управления и виджета трафика:

```http
HTTP/1.1 200 OK
Content-Type: text/plain; charset=utf-8
profile-title: base64:0JHQtdC70YvQtSBT0L/QuNGB0LrQuCAoSnVzdDFrKQ==
profile-description: base64:VkxFU1MgWEhUVFAgWcOhbmRleCBDRE4=
subscription-userinfo: upload=0; download=10737418240; total=53687091200; expire=1788000000
profile-update-interval: 6
support-url: https://t.me/your_support_bot
hide-url: 1
no-limit-enabled: 1

<Base64-закодированный список vless:// конфигураций под все активные Relay>
```

* `subscription-userinfo`: рендерит на главном экране INCY красивый прогресс-бар (использовано / осталось из 50 ГБ) и дату окончания.
* `profile-update-interval: 6`: приложение автоматически обновляет список серверов каждые 6 часов.
* `hide-url: 1`: защищает ссылку подписки от случайного копирования и утечки.

#### 2. Добавление в 1 клик
- В Telegram-боте кнопка **«📋 Скопировать ссылку для INCY»** использует нативный `CopyTextButton`.
- Поддерживается deep-link: `incy://add/https://your-domain.com/s/w/TOKEN`.

---

### 5.2. Второй клиент (Secondary #2): AmneziaVPN (Контейнер `amnezia-xray`)

Для AmneziaVPN генерируется ключ формата `vpn://`, содержащий контейнер `amnezia-xray`:

```json
{
  "containers": [
    {
      "container": "amnezia-xray",
      "xray": {
        "isThirdPartyConfig": true,
        "last_config": "{\"inbounds\":[{\"listen\":\"127.0.0.1\",\"port\":10808,\"protocol\":\"socks\",\"settings\":{\"udp\":true}}],\"outbounds\":[{\"protocol\":\"vless\",\"settings\":{\"vnext\":[{\"address\":\"cdn.yourdomain.com\",\"port\":443,\"users\":[{\"encryption\":\"none\",\"id\":\"USER_UUID\"}]}]},\"streamSettings\":{\"network\":\"xhttp\",\"security\":\"tls\",\"tlsSettings\":{\"alpn\":[\"h2\"],\"serverName\":\"cdn.yourdomain.com\"},\"xhttpSettings\":{\"mode\":\"packet-up\",\"path\":\"/stream/de\",\"scMaxBufferedPosts\":30,\"scMaxEachPostBytes\":1000000,\"scMinPostsIntervalMs\":30,\"uplinkHTTPMethod\":\"OPTIONS\",\"xPaddingHeader\":\"X-Cache\",\"xPaddingKey\":\"dc\",\"xPaddingMethod\":\"tokenish\",\"xPaddingObfsMode\":true,\"xPaddingPlacement\":\"queryInHeader\"}}}]}"
      }
    }
  ],
  "defaultContainer": "amnezia-xray",
  "description": "Белый Интернет (Amnezia)",
  "dns1": "1.1.1.1",
  "dns2": "1.0.0.1",
  "hostName": "cdn.yourdomain.com"
}
```

---

### 5.3. Happ / v2rayNG / NekoBox (VLESS Extra URI)

Поддерживается импорт ссылки подписки или прямых VLESS URI:

```text
vless://USER_UUID@cdn.yourdomain.com:443?encryption=none&security=tls&sni=cdn.yourdomain.com&alpn=h2&fp=chrome&type=xhttp&mode=packet-up&host=cdn.yourdomain.com&path=%2Fstream%2Fde&extra=%7B%22mode%22%3A%22packet-up%22%2C%22scMaxEachPostBytes%22%3A1000000%2C%22scMinPostsIntervalMs%22%3A30%2C%22scMaxBufferedPosts%22%3A30%2C%22xPaddingObfsMode%22%3Atrue%2C%22xPaddingKey%22%3A%22dc%22%2C%22xPaddingHeader%22%3A%22X-Cache%22%2C%22xPaddingMethod%22%3A%22tokenish%22%2C%22xPaddingPlacement%22%3A%22queryInHeader%22%2C%22uplinkHTTPMethod%22%3A%22OPTIONS%22%7D#%F0%9F%87%A9%F0%9F%87%AA%20%D0%93%D0%B5%D1%80%D0%BC%D0%B0%D0%BD%D0%B8%D1%8F%20(%D0%91%D0%B5%D0%BB%D1%8B%D0%B9%20%D0%98%D0%BD%D1%82%D0%B5%D1%80%D0%BD%D0%B5%D1%82)
```

---

## 6. УТИЛИТА УПРАВЛЕНИЯ СЕРВЕРАМИ: `just1knode`

### 6.1. Интерактивная консольная панель

Скрипт `just1knode` — интерактивный CLI-менеджер с псевдографическим меню:

```text
┌─────────────────────────────────────────────────────────────┐
│                 🚀 JUST1KNODE CONTROL PANEL                 │
│              Менеджер серверных узлов Just1kBot             │
└─────────────────────────────────────────────────────────────┘

  [1] 🚀 Установить Amnezia API узел (AmneziaWG 2.0 для обычной подписки)
  [2] 🌐 Установить Origin узел (Белый Интернет — Входной шлюз в РФ)
  [3] 🛡️  Установить Relay узел (Белый Интернет — Зарубежный выход)
  [4] 🔄 Управление Relay-узлами на Origin (Добавить / Удалить / Список)
  [5] 📊 Статус узла и активные клиенты
  [6] 🩺 Комплексная самодиагностика (Doctor: DNS, SSL, Xray, UFW)
  [7] 🔄 Обновление ядра Xray-core
  [0] ❌ Выход
```

---

### 6.2. Три режима установки

1. **`[1]` Amnezia API Node (`amnezia`)**:
   - Клонирует `kyoresuas/amnezia-api` с GitHub.
   - Настраивает Node.js, Nginx SSL на порту `8443`, UFW.
   - Генерирует API-ключ и выводит карточку для добавления в `/admin` бота.
2. **`[2]` White Internet Origin (`origin`)**:
   - Запрашивает Origin-домен, SSL Email, API-ключ.
   - Настраивает Nginx (`OPTIONS ➔ POST`, Zero Buffering), Xray-core и легкий `xray-api`.
   - Настраивает UFW (22, 80, 443, 8444).
3. **`[3]` White Internet Relay (`relay`)**:
   - Устанавливает Xray-core на зарубежном VPS.
   - Настраивает входящий порт `10443` (VLESS-Vision TLS).
   - В UFW **разрешает порт 10443 строго с IP Origin-сервера**.

---

### 6.3. Легковесный `xray-api` без 3x-ui и локальная персистентность

* **Микросервис на Python (`xray-api`)**:
  - Слушает `127.0.0.1:5001` (проксируется через Nginx на `8444` с `X-API-Key`).
  - Управляет пользователями через локальный gRPC Xray (`HandlerService`).
  - Снимает точную статистику трафика (`StatsService`).
* **Локальная персистентность (Zero-Loss State)**:
  - Активные UUID сохраняются в локальный файл `/etc/just1knode/clients.json`.
  - При перезапуске Xray или сервера `xray-api` мгновенно регистрирует всех активных клиентов обратно в память Xray.
  - Никаких 503 ошибок, никаких сложных проверок по `/proc`.

---

## 7. ПРОДУКТОВАЯ МОДЕЛЬ В TELEGRAM-БОТЕ (`just1kbot`)

1. **Раздел «⚪ Белый Интернет»** в главном меню бота.
2. **1 Базовый тариф**:
   - 50 ГБ на 30 дней (250 ₽).
3. **Пакеты докупки трафика**:
   - `+10 ГБ` (40 ₽)
   - `+25 ГБ` (100 ₽)
   - `+50 ГБ` (200 ₽)
   - Трафик мгновенно суммируется с остатком.
4. **Управление жизненным циклом**:
   - Если трафик израсходован (0 ГБ) или срок истек: статус переходит в `EXHAUSTED` / `EXPIRED`, бот отключает пользователя на Origin через API, отправляет уведомление в Telegram с кнопками продления/докупки.
   - При продлении или покупке пакета ГБ доступ мгновенно восстанавливается.

---

## 8. СВОДНЫЙ КАТАЛОГ ИССЛЕДОВАНИЙ И ИСТОЧНИКОВ

1. **Хабр:**
   - [Хабр #1027276: Белые списки, L3/L7 и 6 способов обхода](https://habr.com/ru/articles/1027276/)
   - [Хабр #1014038: DPI IS ALL YOU NEED, ТСПУ и MAX](https://habr.com/ru/articles/1014038/)
   - [Хабр #1007570: Чебурнет 2026, Mesh и NaïveProxy](https://habr.com/ru/articles/1007570/)
2. **GitHub:**
   - [XTLS/Xray-core PR #5414: Add uplinkHTTPMethod support](https://github.com/XTLS/Xray-core/pull/5414)
   - [INCY Developer Documentation](https://docs.incy.cc/)
   - [Amnezia Client & API Repositories](https://github.com/amnezia-vpn)
3. **Форумы и сообщества:**
   - **NTC.party:** Технические треды по ТСПУ, XHTTP обфускации и Nginx Zero Buffering.
   - **4PDA:** Тема 1110469 (Мобильные клиенты и фильтрация операторов РФ).
   - **Reddit:** `r/vpn`, `r/russia`.

---
*Документ составлен и утвержден по состоянию на 1 сентября 2026 года.*
