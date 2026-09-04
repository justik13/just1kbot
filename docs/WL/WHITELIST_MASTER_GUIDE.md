# ⚪ БЕЛЫЕ СПИСКИ В РФ: АРХИТЕКТУРА, ТЕОРИЯ, МЕТОДЫ ОБХОДА И ПОЛНОЕ РУКОВОДСТВО ПО VLESS XHTTP ЧЕРЕЗ РОССИЙСКИЕ CDN

> **Единый источник истины (SSOT)** по анализу белых списков, цензурной инфраструктуры РКН/ТСПУ и развертыванию отказоустойчивого каскадного проксирования **VLESS XHTTP + Padding (OPTIONS) через Yandex Cloud CDN**.
>
> ⚡ **СТРАТЕГИЯ КЛИЕНТОВ ДЛЯ РЕЖИМА БЕЛЫХ СПИСКОВ (WL):**
> 1. **Режим Белых Списков — СТРОГО Xray (VLESS XHTTP).** Протокол AmneziaWG в белых списках **НЕ ИСПОЛЬЗУЕТСЯ**, так как весь трафик UDP полностью блокируется или деградирует до 0 кбит/с на оборудовании ТСПУ.
> 2. **Основной клиент (Primary Client #1) — INCY (Xray):** нативная работа с `xray-core`, поддержка Full Xray JSON, HTTP-подписок с автообновлением, управлением через заголовки (`subscription-userinfo`, `profile-title`) и deep links (`incy://add/...`, `incy://crypt1/...`).
> 3. **Второй клиент (Secondary Client #2) — AmneziaVPN Client:** нативная работа через контейнер `amnezia-xray` по ключам формата **`vpn://`** и прямым JSON-файлам.
>
> *Актуальность: 2026 год. Протестировано на Ubuntu 22.04 / 24.04 LTS, Xray-core v26.5.9+, Yandex Cloud CDN.*

---

## 📑 СОДЕРЖАНИЕ

1. [Введение и фундаментальная разница: Черные vs Белые списки](#1-введение-и-фундаментальная-разница-черные-vs-белые-списки)
2. [Анатомия блокировок ТСПУ в режиме «Белых списков»](#2-анатомия-блокировок-тспу-в-режиме-белых-списков)
   - [2.1. Двухуровневая фильтрация L3 (IP/CIDR) + L7 (SNI/DPI)](#21-двухуровневая-фильтрация-l3-ipcidr--l7-snidpi)
   - [2.2. Тотальная смерть UDP (WireGuard, AmneziaWG, QUIC, DNS)](#22-тотальная-смерть-udp-wireguard-amneziawg-quic-dns)
   - [2.3. Иерархия и приоритеты в белых списках](#23-иерархия-и-приоритеты-в-белых-списках)
   - [2.4. Сравнительный анализ 6 способов пробития белых списков](#24-сравнительный-анализ-6-способов-пробития-белых-списков)
3. [Архитектура каскадного проксирования VLESS XHTTP + CDN (Multi-Hop)](#3-архитектура-каскадного-проксирования-vless-xhttp--cdn-multi-hop)
   - [3.1. Полная топология прохождения трафика](#31-полная-топология-прохождения-трафика)
   - [3.2. Почему именно Yandex Cloud CDN?](#32-почему-именно-yandex-cloud-cdn)
   - [3.3. Ключевое открытие: обход блокировки POST через метод OPTIONS (PR #5414)](#33-ключевое-открытие-обход-блокировки-post-через-метод-options-pr-5414)
   - [3.4. Роль Nginx на Origin: маппинг методов и Zero Buffering](#34-роль-nginx-на-origin-маппинг-методов-и-zero-buffering)
4. [Подготовка и планирование инфраструктуры](#4-подготовка-и-планирование-инфраструктуры)
   - [4.1. Сводная таблица параметров и плейсхолдеров](#41-сводная-таблица-параметров-и-плейсхолдеров)
   - [4.2. Настройка DNS-записей (DNS-Only)](#42-настройка-dns-записей-dns-only)
5. [Пошаговое развертывание серверов](#5-пошаговое-развертывание-серверов)
   - [5.1. Настройка Exit-сервера (Германия / Нидерланды / Зарубежье)](#51-настройка-exit-сервера-германия--нидерланды--зарубежье)
   - [5.2. Настройка Origin-сервера (Россия — Москва / Санкт-Петербург)](#52-настройка-origin-сервера-россия--москва--санкт-петербург)
   - [5.3. Специфика РФ: обход блокировки GitHub, настройка DNS и фаервола](#53-специфика-рф-обход-блокировки-github-настройка-dns-и-фаервола)
6. [Настройка Yandex Cloud: Certificate Manager & Cloud CDN](#6-настройка-yandex-cloud-certificate-manager--cloud-cdn)
   - [6.1. Выпуск Let's Encrypt сертификата в Certificate Manager](#61-выпуск-lets-encrypt-сертификата-в-certificate-manager)
   - [6.2. Создание Группы источников](#62-создание-группы-источников)
   - [6.3. Конфигурация параметров CDN-ресурса](#63-конфигурация-параметров-cdn-ресурса)
   - [6.4. Привязка CNAME-записи и включение HTTPS-редиректа](#64-привязка-cname-записи-и-включение-https-редиректа)
7. [Двухклиентская стратегия: INCY (Основной) и AmneziaVPN (Второй)](#7-двухклиентская-стратегия-incy-основной-и-amneziavpn-второй)
   - [7.1. Клиент #1: INCY — интеграция с ядром Xray](#71-клиент-1-incy--интеграция-с-ядром-xray)
   - [7.2. INCY Full Xray JSON: гарантированная доставка параметров XHTTP](#72-incy-full-xray-json-гарантированная-доставка-параметров-xhttp)
   - [7.3. INCY HTTP Subscription Feed (`/sub/{token}`) и App Management Headers](#73-incy-http-subscription-feed-subtoken-и-app-management-headers)
   - [7.4. INCY Deep Links (`incy://add/`, `incy://import/`, `incy://crypt1/`)](#74-incy-deep-links-incyadd-incyimport-incycrypt1)
   - [7.5. Клиент #2: AmneziaVPN — нативные ключи `vpn://` (контейнер `amnezia-xray`)](#75-клиент-2-amneziavpn--нативные-ключи-vpn-контейнер-amnezia-xray)
   - [7.6. Универсальный Python-генератор для обоих клиентов](#76-универсальный-python-генератор-для-обоих-клиентов)
8. [Диагностика, мониторинг и валидация](#8-диагностика-мониторинг-и-валидация)
   - [8.1. Сквозная проверка через `/cdn-check`](#81-сквозная-проверка-через-cdn-check)
   - [8.2. Анализ логов Nginx и Xray на Origin](#82-анализ-логов-nginx-и-xray-на-origin)
   - [8.3. Проверка Exit-сервера](#83-проверка-exit-сервера)
9. [Справочник типовых ошибок (Troubleshooting Guide)](#9-справочник-типовых-ошибок-troubleshooting-guide)
10. [Сводный каталог внешних источников и исследований](#10-сводный-каталог-внешних-источников-и-исследований)

---

## 1. ВВЕДЕНИЕ И ФУНДАМЕНТАЛЬНАЯ РАЗНИЦА: ЧЕРНЫЕ VS БЕЛЫЕ СПИСКИ

Для правильного выбора технологии и протокола необходимо четко понимать текущее разделение режимов фильтрации трафика в Российской Федерации.

| Критерий | Черные списки (Default-Allow) | Белые списки (Default-Drop / Default-Deny) |
| :--- | :--- | :--- |
| **Базовый принцип** | «Разрешено все, что явно не запрещено» | «Запрещено абсолютно все, кроме явно разрешенного» |
| **Среда применения** | Любой проводной (кабельный) интернет, офисные сети, мобильный интернет в спокойных регионах | Мобильный интернет (LTE/3G/5G) при включении режима ограничений, приграничные зоны, режим ЧС |
| **Что открывается без VPN?** | Доступен весь мировой интернет: Google, Telegram, App Store, GitHub, Википедия. Не открываются только ресурсы из реестра РКН (Instagram, Twitter, заблокированные СМИ) | Открываются **только** одобренные госресурсы: `Госуслуги`, `ya.ru`, `vk.com`, `Ozon`, `Rutube`, `Сбербанк`. Ни один зарубежный сервер, включая Google, Telegram, App Store, не доступен |
| **Цель использования VPN** | Обойти блокировку конкретного запрещенного сервиса (YouTube 4K, Discord, Instagram, ChatGPT, игры) на высокой скорости и с низким пингом | Получить хоть какой-то доступ к мировому интернету (текстовые сообщения Telegram, WhatsApp, почта, поиск информации), когда сеть парализована |
| **Применимые протоколы** | AmneziaWG (`awg`), VLESS Reality, Shadowsocks-2022 | **ИСКЛЮЧИТЕЛЬНО VLESS XHTTP (TCP/TLS)**. AmneziaWG (UDP) в этом режиме полностью мертв |
| **Архитектура подключения** | Прямой зарубежный сервер (Германия, Финляндия) | **Обязательный Multi-Hop (каскад)** через белый IP внутри РФ или отечественный CDN (Yandex Cloud CDN) |

---

## 2. АНАТОМИЯ БЛОКИРОВОК ТСПУ В РЕЖИМЕ «БЕЛЫХ СПИСКОВ»

### 2.1. Двухуровневая фильтрация: L3 (IP/CIDR) + L7 (SNI/DPI)

В режиме белых списков фильтрация трафика носит комплексный двухслойный характер. Пакет абонента должен последовательно преодолеть два барьера:

```text
[Клиентский пакет]
       │
       ▼
 [Уровень L3: Фильтр подсетей (CIDR)] ──► IP НЕ в белом списке? ──► TCP RST / DROP (Пакет уничтожен)
       │ (IP разрешен: Yandex / VK / Ozon)
       ▼
 [Уровень L7: ТСПУ DPI (SNI / TLS Handshake)] ──► Домен запрещен или протокол распознан? ──► TCP RST
       │ (SNI разрешен + маскировка под валидный HTTPS)
       ▼
[Целевой узел / CDN]
```

1. **Сетевой уровень (L3):** 
   - Маршрутизаторы оператора применяют правила аппаратной фильтрации на 2-м сетевом хопе.
   - Из 46+ миллионов адресов глобального интернета разрешено прохождение пакетов **только к ~63 000 белых IP-адресов**.
   - Пакет к любому серверу Hetzner, DigitalOcean, OVH или AWS сбрасывается немедленно (генерируется `TCP RST` со стороны ТСПУ), соединение даже не доходит до фазы TLS Handshake.
2. **Прикладной уровень (L7 / DPI):**
   - Если IP-адрес назначения входит в белый список (например, арендован сервер в VK Cloud или Yandex Cloud), к нему подключается модуль глубокой инспекции пакетов (ТСПУ DPI).
   - DPI анализирует расширение `Server Name Indication (SNI)` в пакете `ClientHello`. Если абонент пытается обратиться к серверу с разрешенным IP, но указывает чужой или незарегистрированный SNI, ТСПУ моментально разрывает TCP-сессию.
   - Кроме того, ТСПУ проверяет отпечаток браузера (TLS Fingerprint / JA3 / JA4) и поведенческую энтропию потока.

### 2.2. Тотальная смерть UDP (WireGuard, AmneziaWG, QUIC, DNS)

> [!WARNING]
> В режиме белых списков протокол **UDP полностью блокируется либо жестко деградирует**:
> - UDP-трафик на порт 53 (классический DNS) принудительно перехватывается или сбрасывается.
> - UDP-трафик на порт 443 (протокол QUIC / HTTP/3) режется операторами.
> - **Протоколы AmneziaWG и WireGuard (UDP) физически не могут работать в белых списках**, так как на втором хопе пакеты UDP дропаются эвристиками ТСПУ независимо от обфускации заголовков (`Jc`, `S1`, `H1`).
> - **Вывод:** Режим Белых Списков (WL) строится **СТРОГО БЕЗ AmneziaWG**, исключительно на базе стека **Xray (VLESS XHTTP поверх TCP/TLS 443)**.

### 2.3. Иерархия и приоритеты в белых списках

1. **Уровень 1 (Абсолютный приоритет — работает всегда и везде):**
   - Государственный мессенджер `MAX` (`max.ru` / `complat.ru`). Доступен с любых вышек, даже при тотальном глушении.
2. **Уровень 2 (Высокий приоритет — базовая инфраструктура):**
   - `ya.ru`, `yandex.ru`, сервисы Яндекса (`storage.yandex.net`, `yastatic.net`).
   - `vk.com`, `userapi.com`, `vkuser.net`.
   - `gosuslugi.ru`, порталы госорганов РФ (`*.gov.ru`).
   - Yandex Cloud CDN (`*.yccdn.ru`) и VK Cloud.
3. **Уровень 3 (Переменный приоритет — лотерея между операторами):**
   - Банковские сервисы (Т-Банк, ВТБ, при этом Сбербанк периодически частично отваливается, но работает `salutejazz.ru`).
   - Маркетплейсы (`ozon.ru`, `wildberries.ru`).
   - Разрозненные подсети российских хостинг-провайдеров (Timeweb, Selectel, Beget).

### 2.4. Сравнительный анализ 6 способов пробития белых списков

| Метод | Механика | Плюсы | Минусы | Вердикт |
| :--- | :--- | :--- | :--- | :--- |
| **1. VLESS XHTTP через Yandex Cloud CDN (Каскад)** | Трафик идет на белый IP CDN Яндекса ➔ Origin РФ ➔ Exit Германия | **100% стабильность**, белый IP CDN, легитимный сертификат, обфускация XHTTP | Требует настройки двух серверов и аккаунта YC | 🏆 **ПРОМЫШЛЕННЫЙ ЭТАЛОН (SSOT)** |
| **2. VLESS + Reality на российском VPS** | Аренда VPS в РФ (Timeweb, VK, Yandex), Reality под `vk.com`/`ya.ru` | Высокая скорость, простота настройки (1 сервер) | Риск вылета IP из белого списка, блокировки хостерами за VPN | **Рабочий, но нестабильный вариант** |
| **3. Yandex Serverless Cloud Functions** | Развертывание функции-прокси на `functions.yandexcloud.net` | Бесплатный тариф (1 млн вызовов в месяц), IP гарантированно в БС | Ограничение по таймаутам (не держит долгоживущие TCP), низкая скорость | **Подходит только для легкого серфинга** |
| **4. Yandex API Gateway** | Использование API Gateway как реверс-прокси на скрытый зарубежный VPS | Белый IP Яндекса, простой OpenAPI конфиг | Метод был публично описан на Хабре и попал под точечные блокировки РКН | ⚠️ **Устарел / Частично заблокирован** |
| **5. olcRTC (WebRTC туннели)** | Инкапсуляция TCP/IP в DataChannel сервисов видеозвонков (Telemost, WBStream) | Бесплатно, паразитирует на белых медиа-серверах | Сложность настройки, пре-альфа статус, периодический шейпинг трафика | **Экспериментальный гиковский метод** |
| **6. xDNS (DNS Tunneling)** | Туннелирование данных через UDP:53 запросы на кастомный NS-сервер | Работает при открытом порте 53 | Экстремально низкая скорость (10-50 кбит/с), не подходит для медиа | **Крайний аварийный резерв** |

---

## 3. АРХИТЕКТУРА КАСКАДНОГО ПРОКСИРОВАНИЯ VLESS XHTTP + CDN (MULTI-HOP)

### 3.1. Полная топология прохождения трафика

```text
  [Клиентский уровень]
  ┌───────────────────────────────────────────────────────────┐
  │ ОСНОВНОЙ КЛИЕНТ (#1): INCY (VLESS XHTTP / Full Xray JSON) │
  │ ВТОРОЙ КЛИЕНТ   (#2): AmneziaVPN Client (amnezia-xray)    │
  └───────────────────────────────────────────────────────────┘
                 │
                 │ 1. HTTPS / HTTP/2 (TLS 443)
                 │    Метод: OPTIONS (uplinkHTTPMethod)
                 │    SNI: cdn.YOUR_DOMAIN.COM
                 │    Заголовок обфускации: X-Cache: token...
                 ▼
     [Yandex Cloud CDN (Edge)]
  (IP-адрес в белом списке РФ, ТСПУ не блокирует)
                 │
                 │ 2. Проксирование без изменений (TLS 443)
                 │    Host: origin.YOUR_DOMAIN.COM
                 │    SNI: origin.YOUR_DOMAIN.COM
                 ▼
  [Origin-сервер: Россия (Aeza / Timeweb)]
  ┌───────────────────────────────────────────────────────────┐
  │ Nginx (Порт 443):                                         │
  │  - Терминирует TLS Let's Encrypt                         │
  │  - Маппинг: OPTIONS ➔ POST ($xhttp_proxy_method)          │
  │  - Отключение буферизации (proxy_buffering off)          │
  │  - Проброс на локальный порт 127.0.0.1:8003              │
  │                                                           │
  │ Xray-core (Inbound 8003):                                 │
  │  - Протокол: VLESS (Decryption: none)                     │
  │  - Транспорт: XHTTP (mode: packet-up)                     │
  │  - Outbound: VLESS-Vision TLS ➔ Exit-сервер (Порт 10443) │
  └───────────────────────────────────────────────────────────┘
                 │
                 │ 3. Межсерверный туннель VLESS-Vision (TLS 10443)
                 │    (Трафик из РФ в Зарубежье: неотличим от обычного TLS)
                 ▼
   [Exit-сервер: Зарубежье (Германия)]
  ┌───────────────────────────────────────────────────────────┐
  │ Xray-core (Inbound 10443):                                │
  │  - Принимает VLESS xtls-rprx-vision                       │
  │  - Доступ по UFW разрешен строго с IP Origin-сервера     │
  │                                                           │
  │ Xray-core (Outbound Freedom):                             │
  │  - Прямой выход в мировой интернет                       │
  └───────────────────────────────────────────────────────────┘
                 │
                 │ 4. Свободный доступ
                 ▼
       [Мировой интернет]
 (YouTube, Instagram, ChatGPT, Telegram, etc.)
```

### 3.2. Почему именно Yandex Cloud CDN?

1. **Неприкасаемый L3:** IP-диапазоны CDN Яндекса (`*.gslb.yccdn.ru`) входят в базовые белые списки абсолютно всех операторов сотовой связи в РФ (МТС, Мегафон, Билайн, Т2).
2. **Легитимный L7:** Клиент подключается к CDN с настоящим валидным сертификатом Let's Encrypt, выпущенным через Yandex Certificate Manager. ТСПУ видит идеальное HTTPS-соединение без каких-либо аномалий.
3. **Экономичность:** Трафик внутри РФ через Cloud CDN тарифицируется по минимальным ценам, а начального гранта хватает на месяцы работы.

### 3.3. Ключевое открытие: обход блокировки POST через метод OPTIONS (PR #5414)

Исторически транспорт XHTTP в ядре Xray использовал метод HTTP `POST` для передачи восходящего потока данных (Uplink). Однако российские CDN-сервисы (Yandex Cloud CDN, EdgeCDN) по умолчанию **блокируют метод POST** для предотвращения атак или буферизируют тело запроса, что делает интерактивный VPN-туннель неработоспособным.

- **31 января 2026 года:** В ядро [XTLS/Xray-core был принят PR #5414](https://github.com/XTLS/Xray-core/pull/5414), добавивший параметр `uplinkHTTPMethod`.
- **Финальное решение:** Выбор метода **`OPTIONS`**.
  - В Yandex Cloud CDN метод `OPTIONS` разрешен в стандартном профиле работы веб-приложений.
  - Метод `OPTIONS` не подвергается агрессивной валидации схемы тела запроса на CDN.
  - ТСПУ полностью игнорирует запросы `OPTIONS`, считая их легитимными CORS preflight-запросами браузеров.

### 3.4. Роль Nginx на Origin: маппинг методов и Zero Buffering

1. **Подмена метода на лету:**
   ```nginx
   map $request_method $xhttp_proxy_method {
       default  $request_method;
       OPTIONS  POST;
   }
   ```
   Nginx принимает от CDN запрос `OPTIONS` и передает его в Xray как `POST` через директиву `proxy_method $xhttp_proxy_method;`.
2. **Тотальное отключение буферизации (Zero Buffering):**
   ```nginx
   proxy_buffering off;
   proxy_request_buffering off;
   ```
3. **Увеличение буферов под обфусцированные заголовки:**
   Поскольку XHTTP передает паддинг внутри HTTP-заголовков (`xPaddingPlacement: queryInHeader`), Nginx обязан иметь расширенные буферы:
   ```nginx
   client_header_buffer_size 64k;
   large_client_header_buffers 8 128k;
   ```

---

## 4. ПОДГОТОВКА И ПЛАНИРОВАНИЕ ИНФРАСТРУКТУРЫ

### 4.1. Сводная таблица параметров и плейсхолдеров

| Параметр | Описание | Пример значения | Где используется |
| :--- | :--- | :--- | :--- |
| `YOUR_DOMAIN.COM` | Ваш корневой домен | `example.com` | DNS, SSL |
| `YOUR_ORIGIN_HOST` | Поддомен Origin-сервера в РФ | `origin.example.com` | DNS, Nginx, CDN Origin Host |
| `YOUR_CDN_HOST` | Поддомен CDN (точка входа для клиентов) | `cdn.example.com` | DNS, YC CDN, INCY, Amnezia |
| `YOUR_RELAY_HOST` | Поддомен Exit-сервера за рубежом | `relay.example.com` | DNS, Xray TLS |
| `YOUR_ORIGIN_IP` | Публичный IP Origin-сервера (РФ) | `192.0.2.10` | DNS A-запись, UFW на Exit |
| `YOUR_EXIT_IP` | Публичный IP Exit-сервера (Зарубежье) | `198.51.100.10` | DNS A-запись, Xray Outbound |
| `YOUR_UUID` | Секретный UUID пользователя | `a2b9d4e1-73c5-4812-b964-f3e7b85a1902` | Xray на Origin, Exit, INCY, Amnezia |
| `YOUR_SECRET_PATH` | Секретный URL-эндпоинт XHTTP | `/api/v3/secure-data` | Nginx location, Xray, Клиенты |
| `YOUR_PADDING_KEY` | Двухсимвольный ключ обфускации | `dc` | Xray Settings, Клиенты |
| `YOUR_EMAIL` | Email для выпуска Let's Encrypt | `admin@example.com` | Certbot |

### 4.2. Настройка DNS-записей (DNS-Only)

1. **A-запись `origin`** ➡️ `YOUR_ORIGIN_IP` (например, `192.0.2.10`)
2. **A-запись `relay`** ➡️ `YOUR_EXIT_IP` (например, `198.51.100.10`)
3. **CNAME-запись `_acme-challenge.cdn`** ➡️ проверочная запись из Yandex Certificate Manager
4. **CNAME-запись `cdn`** ➡️ технический домен Yandex CDN вида `*.gslb.yccdn.ru`

---

## 5. ПОШАГОВОЕ РАЗВЕРТЫВАНИЕ СЕРВЕРОВ

### 5.1. Настройка Exit-сервера (Германия / Нидерланды / Зарубежье)

```bash
sudo -i
export RELAY_HOST='relay.YOUR_DOMAIN.COM'
export UUID='YOUR_UUID'
export EMAIL='YOUR_EMAIL'
export ORIGIN_IP='YOUR_ORIGIN_IP'

apt update && apt install -y curl certbot ufw
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install --version 26.5.9

certbot certonly --standalone --non-interactive --agree-tos --email "$EMAIL" -d "$RELAY_HOST"

install -d -m 750 -o root -g nogroup /usr/local/etc/xray/tls
install -m 640 -o root -g nogroup "/etc/letsencrypt/live/${RELAY_HOST}/fullchain.pem" /usr/local/etc/xray/tls/fullchain.pem
install -m 640 -o root -g nogroup "/etc/letsencrypt/live/${RELAY_HOST}/privkey.pem" /usr/local/etc/xray/tls/privkey.pem

cat > /usr/local/etc/xray/config.json <<EOF
{
  "log": { "loglevel": "warning" },
  "inbounds": [
    {
      "tag": "from-origin",
      "listen": "0.0.0.0",
      "port": 10443,
      "protocol": "vless",
      "settings": {
        "users": [{ "id": "${UUID}", "flow": "xtls-rprx-vision" }],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "tcp",
        "security": "tls",
        "tlsSettings": {
          "alpn": ["h2", "http/1.1"],
          "certificates": [
            {
              "certificateFile": "/usr/local/etc/xray/tls/fullchain.pem",
              "keyFile": "/usr/local/etc/xray/tls/privkey.pem"
            }
          ]
        }
      }
    }
  ],
  "outbounds": [{ "tag": "internet", "protocol": "freedom" }]
}
EOF

/usr/local/bin/xray run -test -config /usr/local/etc/xray/config.json
systemctl enable --now xray && systemctl restart xray

# Автообновление SSL в Xray
install -d /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/restart-xray.sh <<'EOF'
#!/bin/sh
set -eu
install -m 640 -o root -g nogroup "${RENEWED_LINEAGE}/fullchain.pem" /usr/local/etc/xray/tls/fullchain.pem
install -m 640 -o root -g nogroup "${RENEWED_LINEAGE}/privkey.pem" /usr/local/etc/xray/tls/privkey.pem
systemctl restart xray
EOF
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/restart-xray.sh

# Защита портов
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow from "$ORIGIN_IP" to any port 10443 proto tcp
ufw --force enable
```

---

### 5.2. Настройка Origin-сервера (Россия — Москва / Санкт-Петербург)

```bash
sudo -i
export ORIGIN_HOST='origin.YOUR_DOMAIN.COM'
export RELAY_HOST='relay.YOUR_DOMAIN.COM'
export RELAY_IP='YOUR_EXIT_IP'
export UUID='YOUR_UUID'
export EMAIL='YOUR_EMAIL'
export XHTTP_PATH='/api/v3/secure-data'
export PADDING_KEY='dc'

# Фикс резолва в РФ
cat > /etc/resolv.conf <<EOF
nameserver 77.88.8.8
nameserver 8.8.8.8
nameserver 1.1.1.1
EOF

apt update && apt install -y nginx certbot curl wget unzip

# Установка Xray через зеркало
wget -O install-release.sh https://gh.ddlc.top/https://raw.githubusercontent.com/XTLS/Xray-install/main/install-release.sh
chmod +x install-release.sh
wget -O Xray-linux-64.zip https://gh.ddlc.top/https://github.com/XTLS/Xray-core/releases/download/v26.5.9/Xray-linux-64.zip
./install-release.sh install --local Xray-linux-64.zip
rm -f install-release.sh Xray-linux-64.zip

# Выпуск SSL для Origin
install -d -m 755 /var/www/acme
rm -f /etc/nginx/sites-enabled/default

cat > /etc/nginx/sites-available/xhttp-origin.conf <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${ORIGIN_HOST};
    location ^~ /.well-known/acme-challenge/ { root /var/www/acme; }
    location / { default_type text/plain; return 200 "origin ready\n"; }
}
EOF

ln -sfn /etc/nginx/sites-available/xhttp-origin.conf /etc/nginx/sites-enabled/xhttp-origin.conf
nginx -t && systemctl enable --now nginx && systemctl reload nginx
certbot certonly --webroot -w /var/www/acme --non-interactive --agree-tos --email "$EMAIL" -d "$ORIGIN_HOST"

# Конфиг Xray на Origin
cat > /usr/local/etc/xray/config.json <<EOF
{
  "log": { "loglevel": "warning" },
  "inbounds": [
    {
      "tag": "from-yandex-cdn",
      "listen": "127.0.0.1",
      "port": 8003,
      "protocol": "vless",
      "settings": {
        "users": [{ "id": "${UUID}" }],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "xhttp",
        "security": "none",
        "xhttpSettings": {
          "mode": "packet-up",
          "path": "${XHTTP_PATH}",
          "xPaddingObfsMode": true,
          "xPaddingKey": "${PADDING_KEY}",
          "xPaddingHeader": "X-Cache",
          "xPaddingMethod": "tokenish",
          "xPaddingPlacement": "queryInHeader"
        }
      }
    }
  ],
  "outbounds": [
    {
      "tag": "to-exit",
      "protocol": "vless",
      "settings": {
        "vnext": [
          {
            "address": "${RELAY_IP}",
            "port": 10443,
            "users": [{ "id": "${UUID}", "encryption": "none", "flow": "xtls-rprx-vision" }]
          }
        ]
      },
      "streamSettings": {
        "network": "tcp",
        "security": "tls",
        "tlsSettings": {
          "serverName": "${RELAY_HOST}",
          "alpn": ["h2", "http/1.1"]
        }
      }
    },
    { "tag": "direct", "protocol": "freedom" },
    { "tag": "block", "protocol": "blackhole" }
  ]
}
EOF

/usr/local/bin/xray run -test -config /usr/local/etc/xray/config.json
systemctl enable --now xray && systemctl restart xray

# Боевой Nginx (OPTIONS -> POST + Zero Buffering)
cat > /etc/nginx/conf.d/xhttp-method.conf <<'EOF'
map $request_method $xhttp_proxy_method {
    default  $request_method;
    OPTIONS  POST;
}
EOF

cat > /etc/nginx/sites-available/xhttp-origin.conf <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${ORIGIN_HOST};
    location ^~ /.well-known/acme-challenge/ { root /var/www/acme; }
    location / { return 301 https://\$host\$request_uri; }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${ORIGIN_HOST};

    ssl_certificate     /etc/letsencrypt/live/${ORIGIN_HOST}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${ORIGIN_HOST}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    client_max_body_size 0;
    client_header_buffer_size 64k;
    large_client_header_buffers 8 128k;

    location = /cdn-check {
        add_header X-CDN-Origin "ok" always;
        add_header X-Origin-Method \$request_method always;
        add_header X-Origin-Content-Length \$http_content_length always;
        return 204;
    }

    location ${XHTTP_PATH} {
        proxy_pass http://127.0.0.1:8003;
        proxy_method \$xhttp_proxy_method;
        proxy_http_version 1.1;
        proxy_set_header Connection "";

        proxy_pass_request_headers on;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location / {
        default_type text/html;
        return 200 "<html><body><h1>Origin Ready</h1></body></html>";
    }
}
EOF

nginx -t && systemctl restart nginx
```

---

## 6. НАСТРОЙКА YANDEX CLOUD: CERTIFICATE MANAGER & CLOUD CDN

1. **Certificate Manager:** Let's Encrypt сертификат на домен `cdn.YOUR_DOMAIN.COM` с проверкой по DNS CNAME.
2. **Группа источников:** Тип «Сервер», Доменное имя: **строго `origin.YOUR_DOMAIN.COM`** (НЕ IP!).
3. **CDN-ресурс:**
   - Домен: `cdn.YOUR_DOMAIN.COM`.
   - Протокол к источнику: HTTPS (443).
   - Заголовок Host и SNI-хост: **строго `origin.YOUR_DOMAIN.COM`**.
   - Сертификат: из Certificate Manager.
   - Кеширование: полностью выключено (CDN и браузер).
   - Методы: **GET, HEAD, OPTIONS** (OPTIONS обязательно!).
   - Сжатие и экранирование источников: выключено.
4. **DNS:** CNAME `cdn` ➡️ технический домен `*.topology.gslb.yccdn.ru`. После проверки переключить «Перенаправление с HTTP на HTTPS».

---

## 7. ДВУХКЛИЕНТСКАЯ СТРАТЕГИЯ: INCY (ОСНОВНОЙ) И AMNEZIAVPN (ВТОРОЙ)

В контуре белых списков (WL) реализовано четкое разделение ролей:
- **Основной клиент (Client #1):** **INCY** (с акцентом на нативную работу с `xray-core`, автообновляемые подписки, информационные виджеты и 1-click deep links).
- **Второй клиент (Client #2):** **AmneziaVPN Client** (надежный кроссплатформенный резерв через нативные контейнерные ключи `vpn://` или прямой JSON).

---

### 7.1. Клиент #1: INCY — интеграция с ядром Xray

Согласно официальной документации [INCY Developer Docs (`ru/dev-docs/full-xray-config.md`)](https://docs.incy.cc), клиент INCY под капотом исполняет официальный **`xray-core`**:
- На iOS / macOS: внутри Network Extension (`PacketTunnelProvider`).
- На Android / Windows / Linux: через нативный системный сервис.

INCY умеет принимать Xray-конфигурации тремя способами:
1. **Full Xray JSON Configuration** (наивысшая надежность).
2. **URL `vless://`** с закодированным параметром `extra`.
3. **HTTP Subscription Feed (`GET /sub/{token}`)** с управляющими заголовками и deep link ссылками `incy://add/...`.

---

### 7.2. INCY Full Xray JSON: гарантированная доставка параметров XHTTP

> [!TIP]
> При использовании **Full Xray JSON** клиент INCY распознает объект с полями `inbounds` и `outbounds` и передает его во внутренний `xray-core` **напрямую без изменений**.
> Это полностью исключает потерю параметров `uplinkHTTPMethod: "OPTIONS"`, заголовков `xPaddingHeader` и режима `packet-up`!

Структура конфигурации для INCY:

```json
{
  "log": {
    "loglevel": "warning"
  },
  "inbounds": [
    {
      "tag": "socks-in",
      "listen": "127.0.0.1",
      "port": 10808,
      "protocol": "socks",
      "settings": {
        "udp": true
      }
    },
    {
      "tag": "http-in",
      "listen": "127.0.0.1",
      "port": 10809,
      "protocol": "http"
    }
  ],
  "outbounds": [
    {
      "tag": "WL-YandexCDN",
      "protocol": "vless",
      "settings": {
        "vnext": [
          {
            "address": "cdn.YOUR_DOMAIN.COM",
            "port": 443,
            "users": [
              {
                "id": "YOUR_UUID",
                "encryption": "none"
              }
            ]
          }
        ]
      },
      "streamSettings": {
        "network": "xhttp",
        "security": "tls",
        "tlsSettings": {
          "serverName": "cdn.YOUR_DOMAIN.COM",
          "alpn": ["h2"]
        },
        "xhttpSettings": {
          "mode": "packet-up",
          "path": "YOUR_SECRET_PATH",
          "scMaxBufferedPosts": 30,
          "scMaxEachPostBytes": 1000000,
          "scMinPostsIntervalMs": 30,
          "uplinkHTTPMethod": "OPTIONS",
          "xPaddingHeader": "X-Cache",
          "xPaddingKey": "YOUR_PADDING_KEY",
          "xPaddingMethod": "tokenish",
          "xPaddingObfsMode": true,
          "xPaddingPlacement": "queryInHeader"
        }
      }
    },
    {
      "tag": "direct",
      "protocol": "freedom"
    }
  ]
}
```

---

### 7.3. INCY HTTP Subscription Feed (`/sub/{token}`) и App Management Headers

При запросе подписки клиентом INCY сервер возвращает статусную информацию и настройки приложения через HTTP-заголовки ответа:

```http
HTTP/1.1 200 OK
Content-Type: text/plain; charset=utf-8
profile-title: base64:0JHQtdC70YvQtSBT0L/QuNGB0LrQuCDQoNCk
profile-description: base64:VkxFU1MgWEhUVFAgWcOhbmRleCBDRE4=
subscription-userinfo: upload=0; download=1073741824; total=107374182400; expire=1788000000
profile-update-interval: 6
support-url: https://t.me/your_support_bot
announce: base64:0KDQtdC20LjQvCDQsdC10LvRi9GFINGB0L/QuNGB0LrQvtCyINCw0LrRgtC40LLQtdC9LiDQmNGB0L/QvtC70YzQt9GD0LnRgtC1IFhSQUku
hide-url: 1

<Base64-закодированное тело: либо Full JSON, либо vless:// URI>
```

- **`profile-title`**: Название профиля в списке серверов INCY (поддерживает UTF-8 через `base64:`).
- **`subscription-userinfo`**: Виджет трафика и срока действия подписки прямо на главном экране INCY.
- **`profile-update-interval`**: Интервал автообновления серверов в часах (например, каждые 6 часов).
- **`hide-url: 1`**: Защищает URL подписки от случайного копирования и утечки пользователем.

---

### 7.4. INCY Deep Links (`incy://add/`, `incy://import/`, `incy://crypt1/`)

Для мгновенного добавления в приложение INCY прямо из Telegram-бота используются ссылки:

1. **Добавление подписки по URL:**
   ```text
   incy://add/https://YOUR_DOMAIN.COM/sub/USER_TOKEN
   ```
2. **Прямой импорт VLESS XHTTP:**
   ```text
   incy://add/vless://YOUR_UUID@cdn.YOUR_DOMAIN.COM:443?encryption=none&security=tls&sni=cdn.YOUR_DOMAIN.COM&alpn=h2&type=xhttp&mode=packet-up&path=YOUR_SECRET_PATH&extra=%7B...%7D#WL-YandexCDN
   ```
3. **Защищенные ссылки (`crypt1`):**
   ```text
   incy://crypt1/<AES-256-GCM-Base64URL-Payload>
   ```
   *Шифрованная ссылка скрывает домен и токен подписки от парсеров и сканеров ссылок в мессенджерах.*

---

### 7.5. Клиент #2: AmneziaVPN — нативные ключи `vpn://` (контейнер `amnezia-xray`)

Клиент **AmneziaVPN** выступает вторым полноценным клиентом. Для него формируется нативный ключ `vpn://`, внутри которого упакован контейнер `amnezia-xray`:

- Контейнер: `amnezia-xray`
- Поле: `isThirdPartyConfig: True`
- Вложенное поле: `last_config` (строка JSON с конфигурацией Xray)
- Общий кодек: Base64 с префиксом `vpn://`

---

### 7.6. Универсальный Python-генератор для обоих клиентов

Этот скрипт объединяет логику генерации конфигураций для обоих клиентов:

```python
import base64
import json
import urllib.parse


class WhitelistKeyGenerator:
    def __init__(
        self,
        domain: str,
        uuid_str: str,
        path: str = "/api/v3/secure-data",
        padding_key: str = "dc",
    ):
        self.domain = domain
        self.uuid = uuid_str
        self.path = path
        self.padding_key = padding_key

    def get_xray_outbound_dict(self) -> dict:
        """Базовый Xray Outbound объект для VLESS XHTTP через CDN."""
        return {
            "protocol": "vless",
            "settings": {
                "vnext": [
                    {
                        "address": self.domain,
                        "port": 443,
                        "users": [{"id": self.uuid, "encryption": "none"}],
                    }
                ]
            },
            "streamSettings": {
                "network": "xhttp",
                "security": "tls",
                "tlsSettings": {"alpn": ["h2"], "serverName": self.domain},
                "xhttpSettings": {
                    "mode": "packet-up",
                    "path": self.path,
                    "scMaxBufferedPosts": 30,
                    "scMaxEachPostBytes": 1000000,
                    "scMinPostsIntervalMs": 30,
                    "uplinkHTTPMethod": "OPTIONS",
                    "xPaddingHeader": "X-Cache",
                    "xPaddingKey": self.padding_key,
                    "xPaddingMethod": "tokenish",
                    "xPaddingObfsMode": True,
                    "xPaddingPlacement": "queryInHeader",
                },
            },
        }

    # ==========================================
    # КЛИЕНТ #1: INCY (ОСНОВНОЙ)
    # ==========================================

    def generate_incy_full_config(self) -> str:
        """Генерирует Full Xray JSON для импорта в INCY (наивысшая надежность)."""
        full_cfg = {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "tag": "socks-in",
                    "listen": "127.0.0.1",
                    "port": 10808,
                    "protocol": "socks",
                    "settings": {"udp": True},
                },
                {
                    "tag": "http-in",
                    "listen": "127.0.0.1",
                    "port": 10809,
                    "protocol": "http",
                },
            ],
            "outbounds": [
                {
                    "tag": "WL-YandexCDN",
                    **self.get_xray_outbound_dict(),
                },
                {"tag": "direct", "protocol": "freedom"},
            ],
        }
        return json.dumps(full_cfg, indent=2)

    def generate_incy_vless_url(self, label: str = "WL-YandexCDN") -> str:
        """Генерирует vless:// ссылку с параметром extra для INCY / Happ / v2rayNG."""
        extra_dict = {
            "mode": "packet-up",
            "scMaxEachPostBytes": 1000000,
            "scMinPostsIntervalMs": 30,
            "scMaxBufferedPosts": 30,
            "xPaddingObfsMode": True,
            "xPaddingKey": self.padding_key,
            "xPaddingHeader": "X-Cache",
            "xPaddingMethod": "tokenish",
            "xPaddingPlacement": "queryInHeader",
            "uplinkHTTPMethod": "OPTIONS",
        }
        extra_encoded = urllib.parse.quote(
            json.dumps(extra_dict, separators=(",", ":"))
        )

        params = {
            "encryption": "none",
            "security": "tls",
            "sni": self.domain,
            "alpn": "h2",
            "fp": "chrome",
            "type": "xhttp",
            "mode": "packet-up",
            "host": self.domain,
            "path": self.path,
        }
        query_string = urllib.parse.urlencode(params)
        return f"vless://{self.uuid}@{self.domain}:443?{query_string}&extra={extra_encoded}#{label}"

    def generate_incy_deep_link(self, sub_url: str) -> str:
        """Генерирует ссылку 1-click импорта для INCY."""
        return f"incy://add/{sub_url}"

    # ==========================================
    # КЛИЕНТ #2: AMNEZIA VPN (ВТОРОЙ)
    # ==========================================

    def generate_amnezia_vpn_key(
        self, description: str = "WL-YandexCDN"
    ) -> str:
        """Генерирует нативный vpn:// ключ (amnezia-xray) для AmneziaVPN."""
        client_xray_cfg = {
            "inbounds": [
                {
                    "listen": "127.0.0.1",
                    "port": 10808,
                    "protocol": "socks",
                    "settings": {"udp": True},
                }
            ],
            "outbounds": [self.get_xray_outbound_dict()],
        }

        last_config_str = json.dumps(client_xray_cfg, indent=2)

        amnezia_payload = {
            "containers": [
                {
                    "container": "amnezia-xray",
                    "xray": {
                        "isThirdPartyConfig": True,
                        "last_config": last_config_str,
                    },
                }
            ],
            "defaultContainer": "amnezia-xray",
            "description": description,
            "dns1": "1.1.1.1",
            "dns2": "1.0.0.1",
            "hostName": self.domain,
        }

        raw_bytes = json.dumps(
            amnezia_payload, separators=(",", ":")
        ).encode("utf-8")
        b64_key = base64.b64encode(raw_bytes).decode("utf-8")
        return f"vpn://{b64_key}"


# Пример использования
if __name__ == "__main__":
    gen = WhitelistKeyGenerator(
        domain="cdn.example.com",
        uuid_str="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
        path="/api/v3/secure-data",
        padding_key="dc",
    )

    print("=== КЛИЕНТ #1: INCY (ОСНОВНОЙ) ===")
    print("\n1. Ссылка VLESS URL:")
    print(gen.generate_incy_vless_url())
    print("\n2. Deep Link для 1-click добавления подписки в INCY:")
    print(
        gen.generate_incy_deep_link(
            "https://example.com/sub/example_token_123"
        )
    )

    print("\n=== КЛИЕНТ #2: AMNEZIA VPN (ВТОРОЙ) ===")
    print("\n1. Нативный ключ vpn://:")
    print(gen.generate_amnezia_vpn_key())
```

---

## 8. ДИАГНОСТИКА, МОНИТОРИНГ И ВАЛИДАЦИЯ

### 8.1. Сквозная проверка через `/cdn-check`

```bash
curl -sS -D - -o /dev/null -X OPTIONS --data-binary 'test' \
  "https://cdn.YOUR_DOMAIN.COM/cdn-check?nocache=$(date +%s)"
```

**Ожидаемый ответ:**
```http
HTTP/2 204
server: nginx
x-cdn-origin: ok
x-origin-method: OPTIONS
x-origin-content-length: 4
```

### 8.2. Анализ логов Nginx и Xray на Origin

1. **Мониторинг запросов INCY и AmneziaVPN:**
   ```bash
   tail -f /var/log/nginx/access.log | grep "/api/v3/secure-data"
   ```
2. **Проверка системного статуса Xray:**
   ```bash
   journalctl -u xray -n 50 -f
   ```

---

## 9. СПРАВОЧНИК ТИПОВЫХ ОШИБОК (TROUBLESHOOTING GUIDE)

| Ошибка | Первопричина | Решение |
| :--- | :--- | :--- |
| **HTTP 405 Method Not Allowed** | В YC CDN выключен метод `OPTIONS` | В панели Cloud CDN ➔ *HTTP-заголовки и методы* ➔ разрешить `OPTIONS` |
| **HTTP 502 / 504 от CDN** | Неверный Host или SSL-ошибка с Origin | В Группе источников указать **домен `origin.YOUR_DOMAIN.COM`**, Host header = `origin.YOUR_DOMAIN.COM` |
| **INCY: соединение не устанавливается** | Отрезаны параметры XHTTP | Использовать **Full Xray JSON** или проверить параметр `extra` в `vless://` |
| **AmneziaVPN не подключается по `vless://`** | Парсер Amnezia удаляет `extra` | Использовать нативный ключ **`vpn://`** с контейнером `amnezia-xray` |
| **HTTP 400 / 414 на Origin** | Заголовок обфускации превышает буфер | В Nginx задать: `client_header_buffer_size 64k; large_client_header_buffers 8 128k;` |

---

## 10. СВОДНЫЙ КАТАЛОГ ВНЕШНИХ ИСТОЧНИКОВ И ИССЛЕДОВАНИЙ

1. **Документация INCY:**
   - [INCY Developer Documentation](https://docs.incy.cc/)
   - [INCY Full Xray Configuration Specification](https://docs.incy.cc/subscription-format/)
   - [INCY Deep Links Guide](https://docs.incy.cc/deep-links/)
2. **Исследования на Хабре:**
   - [Хабр #1027276: Белые списки, L3/L7 и 6 способов обхода](https://habr.com/ru/articles/1027276/)
   - [Хабр #1014038: DPI IS ALL YOU NEED, ТСПУ и MAX](https://habr.com/ru/articles/1014038/)
   - [Хабр #1007570: Чебурнет 2026, Mesh и NaïveProxy](https://habr.com/ru/articles/1007570/)
3. **Репозитории:**
   - [XTLS/Xray-core PR #5414: Добавление uplinkHTTPMethod](https://github.com/XTLS/Xray-core/pull/5414)
   - [INCY-DEV Platforms & Docs](https://github.com/INCY-DEV)
   - [OpenLibreCommunity TWL](https://github.com/openlibrecommunity/twl)
https://habr.com/ru/articles/1027276/
https://habr.com/ru/articles/1007570/
https://habr.com/ru/articles/1014038/
https://github.com/igareck/vpn-configs-for-russia
https://4pda.to/forum/index.php?showtopic=1110469&st=8940