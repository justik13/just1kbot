# 🌐 White Internet («Белый Интернет») Architecture & Operations

## 1. Обзор архитектуры

«Белый Интернет» — это система обхода жестких белых списков (White Lists) и блокировок ТСПУ/РКН на базе протокола **XHTTP (SplitHTTP) over CDN** и трансграничных туннелей **VLESS Vision**.

```
[ Клиент в РФ (Happ / INCY / v2rayN / Amnezia) ]
       │  (1) HTTPS запрос к CDN (SNI: cdn.just1k.best)
       │      Методы: GET (downlink), OPTIONS (uplink)
       │      Обфускация: X-Cache tokenish padding
       ▼
[ Yandex Cloud CDN (Edge в РФ) ]
       │  (2) Доверенный внутрироссийский CDN-трафик
       ▼
[ Origin Сервер (РФ / Москва) ]
       │  (3) Nginx переводит OPTIONS -> POST
       │      Xray Inbound (127.0.0.1:8003/8004)
       │      Российские ресурсы (.ru / банки) -> напрямую через Яндекс DNS
       │  (4) Зарубежный трафик -> VLESS Vision over TLS (порт 10443)
       ▼
[ Exit Сервер (Германия 🇩🇪 / Нидерланды 🇳🇱) ]
       │  (5) Прямой выход в открытый интернет
       ▼
[ Свободный интернет (YouTube, Instagram, etc.) ]
```

---

## 2. Ключевые протоколы и технические решения

### 2.1. XHTTP (SplitHTTP) и трансляция методов
* **Проблема CDN:** Российские CDN (Yandex Cloud CDN) разрешают клиентам только методы `GET, HEAD, OPTIONS`. Метод `POST` режется или блокируется.
* **Решение:** Клиент отправляет аплоад-пакеты методом `OPTIONS` с заголовками `X-Cache`. Nginx на Origin-сервере транслирует метод на лету:
  ```nginx
  map $request_method $just1k_xhttp_proxy_method {
      default $request_method;
      OPTIONS POST;
  }
  location /stream/v1/de {
      proxy_method $just1k_xhttp_proxy_method;
      proxy_pass http://127.0.0.1:8003;
      proxy_buffering off;
      proxy_request_buffering off;
  }
  ```

### 2.2. Учет квот и биллинг (Grant Ledger)
* Каждый пользователь имеет подписку `WhiteInternetSubscription` и цепочку грантов `WhiteInternetQuotaGrant`.
* Воркер `white_internet_traffic` опрашивает агент `xray-api` по безопасному TLS-каналу (порт `8444`).
* Семантика **Anomaly Guard** и **Generation CAS** предотвращает ложные списания при перезапусках ядра Xray на нодах.

---

## 3. Управление узлами через `just1knode`

### 3.1. Установка Exit-узла (Германия / Нидерланды)
```bash
just1knode install xray-exit \
  --origin-ip "<IP_ОРИДЖИНА_МОСКВА>" \
  --domain "exit-de.yourdomain.com" \
  --uuid "<UUID_VLESS>" \
  --email "admin@yourdomain.com"
```

### 3.2. Установка Origin-узла (РФ / Москва)
```bash
just1knode install xray-origin \
  --domain "origin.yourdomain.com" \
  --bot-ip "<IP_БОТА>" \
  --exit-de-host "exit-de.yourdomain.com" \
  --exit-de-uuid "<UUID_EXIT_DE>" \
  --exit-nl-host "exit-nl.yourdomain.com" \
  --exit-nl-uuid "<UUID_EXIT_NL>" \
  --path-de "/stream/v1/de" \
  --path-nl "/stream/v1/nl" \
  --email "admin@yourdomain.com"
```

### 3.3. Диагностика и статус
```bash
# Проверка статуса служб и конфигурации
just1knode doctor

# Быстрое тестирование локального API
curl -H "X-API-Key: $(grep XRAY_API_KEY /etc/xray-api/config.env | cut -d= -f2)" http://127.0.0.1:8444/v1/health
```

---

## 4. Конфигурация Telegram-бота (`.env`)

```env
# Публичный CDN домен
WHITE_INTERNET_CDN_DOMAIN=cdn.just1k.best

# URL-пути XHTTP (должны совпадать с установкой на Origin-ноде)
WHITE_INTERNET_XHTTP_PATH_DE=/stream/v1/de
WHITE_INTERNET_XHTTP_PATH_NL=/stream/v1/nl
```
