# 🌐 White Internet («Белый Интернет») Architecture & Operations

## 1. Обзор архитектуры

«Белый Интернет» — это высокоустойчивая система обхода жестких белых списков (White Lists) и блокировок ТСПУ/РКН на базе протокола **VLESS XHTTP (SplitHTTP) over CDN** и трансграничных туннелей **VLESS Vision (TLS или REALITY)**.

```
[ Клиент в РФ (INCY / Happ / v2rayN / NekoBox) ]
       │  (1) HTTPS запрос к CDN (SNI: cdn.just1k.best)
       │      Методы: GET (downlink), OPTIONS (uplink)
       │      Обфускация: X-Cache tokenish padding (queryInHeader)
       ▼
[ Yandex Cloud CDN (Edge в РФ) ]
       │  (2) Доверенный внутрироссийский CDN-трафик
       ▼
[ Origin Сервер (РФ / Москва) ]
       │  (3) Nginx переводит OPTIONS -> POST с Zero-Buffering (^~ location)
       │      Xray Inbound (127.0.0.1:8003/8004...)
       │      Локальный fallback / Российские ресурсы
       │  (4) Зарубежный трафик -> VLESS Vision (TLS к relay.just1k.best или REALITY TCP)
       ▼
[ Relay Сервер (Зарубежье: Германия 🇩🇪 / Нидерланды 🇳🇱 / etc.) ]
       │  (5) Прямой выход в открытый интернет
       ▼
[ Свободный интернет (YouTube, Instagram, etc.) ]
```

---

## 2. Ключевые протоколы и технические решения

### 2.1. XHTTP (SplitHTTP) и трансляция методов
* **Проблема CDN:** Российские CDN (Yandex Cloud CDN) разрешают клиентам только методы `GET, HEAD, OPTIONS`. Метод `POST` режется или блокируется.
* **Решение:** Клиент отправляет аплоад-пакеты методом `OPTIONS` с заголовками `X-Cache`. Nginx на Origin-сервере транслирует метод на лету с модификатором `^~`:
  ```nginx
  map $request_method $xhttp_proxy_method {
      OPTIONS POST;
      default $request_method;
  }
  location ^~ /w_secret/de {
      proxy_method $xhttp_proxy_method;
      proxy_pass http://127.0.0.1:8004;
      proxy_buffering off;
      proxy_request_buffering off;
      proxy_read_timeout 3600s;
      proxy_send_timeout 3600s;
  }
  ```

### 2.2. Защита межсерверного туннеля (Двойной транспорт: TLS / REALITY)
* **Доменный TLS (Let's Encrypt):** Используется для релеев с выделенным доменным именем (например, `relay.just1k.best` на сервере `justik`).
* **VLESS REALITY:** Используется для бессертификатных нод на прямых IP с маскировкой под TLS (например, `www.google.com`).
* Поддерживается алгоритм оптимизации потока `xtls-rprx-vision`.
* Порт туннеля (по умолчанию 10443) защищен UFW-фаерволом и открыт строго для IP-адреса Origin-сервера.

### 2.3. Учет квот и биллинг (Grant Ledger)
* Каждый пользователь имеет подписку `WhiteInternetSubscription` и цепочку грантов `WhiteInternetQuotaGrant` (Base 50 ГБ + накопительные пакеты продления).
* Воркер `white_internet_traffic` опрашивает агент `xray-api` по безопасному TLS-каналу (порт `8444`).
* Семантика **Anomaly Guard**, **Generation CAS** и **Stats Reset Rebase** предотвращает ложные списания при перезапусках ядра Xray и сбросе счетчиков.

---

## 3. Управление узлами через `just1knode` (Zero-Collateral-Damage)

### 3.1. Установка Relay-узла (Зарубежный выход)
```bash
# Интерактивная установка Relay
sudo just1knode install relay

# Или с аргументами:
sudo just1knode install relay 10443 "<IP_ORIGIN_В_РФ>" "www.google.com"
```
После завершения скрипт выдаст готовую команду для добавления реле на Origin-сервере.

### 3.2. Установка Origin-узла (РФ / Москва)
```bash
# Интерактивная установка Origin
sudo just1knode install origin

# Или с аргументами:
sudo just1knode install origin "origin.yourdomain.com" "admin@yourdomain.com" "<API_KEY>" "/w_secret"
```

### 3.3. Управление Relay-узлами на Origin
```bash
# Добавление Relay с доменным TLS (например, нода justik):
sudo just1knode relay add "Германия" "94.249.239.236" 10443 "<UUID>" "de" "tls" "" "" "relay.just1k.best"

# Добавление Relay с REALITY:
sudo just1knode relay add "Швеция" "<RELAY_IP>" 10443 "<UUID>" "se" "reality" "<PUBKEY>" "<SHORT_ID>" "www.google.com"

# Просмотр списка активных релеев
sudo just1knode relay list

# Удаление Relay
sudo just1knode relay remove de
```

### 3.4. Диагностика и статус
```bash
# Комплексная самодиагностика узла (DNS, SSL, Xray, gRPC/Relay, Nginx, UFW)
sudo just1knode doctor

# Статус служб и количество активных клиентов
sudo just1knode status

# Безопасное тестирование API агента
curl -k -H "X-API-Key: $(grep XRAY_API_KEY /etc/xray-api/config.env | cut -d= -f2)" https://127.0.0.1:8444/v1/health
```

---

## 4. Конфигурация Telegram-бота (`.env`)

```env
# Публичный CDN домен
WHITE_INTERNET_CDN_DOMAIN=cdn.just1k.best

# Префикс пути для HTTP подписки (по умолчанию: /sub/wl)
WHITE_INTERNET_SUB_PATH_PREFIX=/sub/wl
```
