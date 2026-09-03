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

### 2.3. Учет квот, докупка и биллинг (Traffic & Anti-Abuse Policy)
* **Модель пула:** Учет ведется по схеме `base_traffic_bytes` (50 ГБ) + `extra_traffic_bytes` (докуп). Неиспользованная база сгорает при продлении, докуп переносится (подробнее: `WL/TRAFFIC_POLICY_AND_ANTIABUSE_SPEC.md`).
* **Анти-абуз:** Hard Cap остатка (150 ГБ), докупка только при активной подписке (`now < expires_at`), Grace Period 7 дней при просрочке.
* Воркер `white_internet_traffic` опрашивает агент `xray-api` по безопасному TLS-каналу (порт `8444`).
* Семантика **Anomaly Guard**, **Generation CAS** и **Stats Reset Rebase** предотвращает ложные списания при перезапусках ядра Xray и сбросе счетчиков.

---

## 3. Управление узлами через `just1knode` (Zero-Collateral-Damage)

### 3.1. Интерактивное меню управления узлами (`just1knode`)
На любом сервере достаточно запустить команду без параметров:
```bash
sudo just1knode
```
Скрипт автоматически определит текущее состояние машины (не настроен / Origin / Relay) и отобразит контекстное меню с пошаговым опросником.

* **Установка Relay (Зарубежье):**
  В меню выбрать пункт `[2] Установить Relay узел`. Скрипт запросит порт туннеля (по умолчанию: 10443) и IP-адрес Origin-сервера в РФ для настройки защитного UFW-фаервола. При наличии AmneziaWG скрипт работает в режиме Zero-Collateral и не затрагивает порт 51820/udp.
* **Установка Origin (РФ / Москва):**
  В меню выбрать пункт `[1] Установить Origin узел`. Скрипт запросит домен Origin, домен Yandex Cloud CDN, Email для SSL и IP Telegram-бота.
* **Управление Relay-узлами на Origin:**
  В меню Origin доступен пункт `[1] Управление Relay-узлами` (добавление, удаление, просмотр списка подключенных мостов).

### 3.2. Диагностика и статус
```bash
# Комплексная самодиагностика узла (DNS, SSL, Xray, gRPC/Relay, Nginx, UFW)
sudo just1knode doctor

# Статус служб и количество активных клиентов
sudo just1knode status
```

---

## 4. Добавление сервера в Telegram-боте (/admin)

Ручное редактирование `.env` **НЕ ТРЕБУЕТСЯ**.

1. В Telegram-боте отправьте команду `/admin` ➔ **Серверы** ➔ **➕ Добавить сервер**.
2. Укажите название (`🇷🇺 Москва Origin`), флаг (`🇷🇺`), API URL (`https://origin.yourdomain.com:8444`) и API-ключ, выданный скриптом.
3. Бот автоматически запросит агент `xray-api`, заберет `cdn_domain`, секретные префиксы и список подключенных релеев и сохранит их в базе данных. Веб-выдача подписок `/sub/wl/{token}` начнет работать сразу.
