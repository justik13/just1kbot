# 🛡 СПЕЦИФИКАЦИЯ: УЧЕТ ТРАФИКА, ДОКУПКА И АНТИ-АБУЗ (БЕЛЫЙ ИНТЕРНЕТ)

> **Статус документа:** Архитектурный стандарт (SSOT) биллинга трафика «Белого Интернета».  
> **Цель:** Исключение оверинжиниринга, защита от абуза платного CDN-трафика, прозрачный UX для пользователей и клиентов INCY.

---

## 1. Контекст и экономическая модель

В отличие от стандартной подписки (AmneziaWG на собственных серверах), трафик «Белого Интернета» передается по схеме:
$$\text{Клиент} \xrightarrow{\text{XHTTP/OPTIONS}} \textbf{Yandex Cloud CDN (Edge в РФ)} \xrightarrow{} \textbf{Origin (РФ)} \xrightarrow{\text{VLESS Vision}} \textbf{Relay (Европа)}$$

* **Исходящий трафик через Yandex Cloud CDN платный за каждый гигабайт.**
* Безлимит невозможен: неконтролируемое скачивание торрентов приведет к выставлению огромных счетов от облачного провайдера.
* Базовый тариф: **50 ГБ на 30 дней за 250 ₽**.
* Пакеты докупки: **+10 ГБ (40 ₽)**, **+25 ГБ (100 ₽)**, **+50 ГБ (200 ₽)**.

---

## 2. Бизнес-логика: Сгорание vs Перенос (Rollover)

1. **Базовый трафик (50 ГБ):**
   * Это абонентская квота на расчетный 30-дневный период.
   * **Неизрасходованный остаток базы сгорает** в конце 30 дней при продлении подписки.
   * Сгорание базы необходимо, чтобы неактивные пользователи не копили терабайты за счет дешевой абонплаты.

2. **Докупленный трафик (Top-up):**
   * Это объем, купленный за отдельные деньги сверх абонплаты.
   * **Неизрасходованный остаток докупа НЕ сгорает** и переносится на следующий расчетный месяц при своевременном продлении подписки.

3. **Очередность списания (Strict Base-First):**
   * Сначала расходуются базовые 50 ГБ.
   * Докупленный трафик начинает расходоваться **только после полного исчерпания базовых 50 ГБ**.
   * Благодаря этому докупленный трафик сохраняется максимально долго.

---

## 3. Правила защиты от абуза (Anti-Abuse Policy)

| Правило | Значение | Механизм защиты |
| :--- | :--- | :--- |
| **1. Hard Cap (Потолок баланса)** | **150 ГБ** | Суммарный доступный остаток (база + докуп) не может превышать 150 ГБ. Попытка купить пакет сверх лимита отклоняется ботом. |
| **2. Pre-condition докупки** | `now < expires_at` | Докупка доступна **только при активной подписке по сроку**. Если подписка просрочена, купить пакет за 40 ₽ нельзя — доступно только продление за 250 ₽. Докупка не прибавляет дней к сроку. |
| **3. Лимит докупок в месяц** | **Макс. 150 ГБ докупа за 30 дней** | Защита от использования туннеля как торрент-качалки через платный CDN. |
| **4. Grace Period при неоплате** | **7 дней** | При наступлении даты `expires_at` доступ выключается. Докупной остаток «замораживается» на 7 дней. Если за 7 дней подписка не продлена — накопленный докуп сгорает окончательно (`EXPIRED`). |

---

## 4. Упрощенная архитектура хранения (No Overengineering)

### 4.1. Отказ от таблиц-грантов
* ❌ **Запрещено:** Создавать отдельные строки `WhiteInternetQuotaGrant` на каждую покупку, крутить FIFO-циклы в Python и брать блокировки `SELECT ... FOR UPDATE` на пачку дочерних строк.
*  **Стандарт:** Все данные хранятся в строке `white_internet_subscriptions`:
  * `base_traffic_bytes: BigInteger` — базовый лимит текущего периода (50 ГБ).
  * `extra_traffic_bytes: BigInteger` — активный докупной остаток.
  * `traffic_used_bytes: BigInteger` — суммарно израсходовано байт за текущий период.
  * `traffic_limit_bytes: BigInteger` — кэш суммы `base_traffic_bytes + extra_traffic_bytes` (для совместимости).

### 4.2. Алгоритм списания трафика (Воркер)
При получении дельты $\Delta$ из Xray-core:
```python
sub.traffic_used_bytes += delta
total_limit = sub.base_traffic_bytes + sub.extra_traffic_bytes

if sub.traffic_used_bytes >= total_limit:
    sub.status = WhiteInternetStatus.EXHAUSTED
    sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
```

### 4.3. Алгоритм покупки докупа
```python
pack_bytes = pack_gb * 1024 * 1024 * 1024
total_available = max(0, (sub.base_traffic_bytes + sub.extra_traffic_bytes) - sub.traffic_used_bytes)

if total_available + pack_bytes > MAX_CAP_BYTES:  # 150 GiB
    return False, "Превышен максимальный лимит накопления (150 ГБ)."

sub.extra_traffic_bytes += pack_bytes
sub.traffic_limit_bytes = sub.base_traffic_bytes + sub.extra_traffic_bytes
if sub.status == WhiteInternetStatus.EXHAUSTED:
    sub.status = WhiteInternetStatus.ACTIVE
```

### 4.4. Алгоритм продления на 30 дней (Renew)
```python
# 1. Сколько всего суммарно осталось у пользователя:
total_left = max(0, (sub.base_traffic_bytes + sub.extra_traffic_bytes) - sub.traffic_used_bytes)

# 2. Переносится только остаток докупа (база сгорает):
extra_rollover = min(sub.extra_traffic_bytes, total_left)

# 3. Начисляем новый месяц с учетом лимита Cap (150 ГБ):
sub.base_traffic_bytes = 50 * 1024 * 1024 * 1024
sub.extra_traffic_bytes = min(100 * 1024 * 1024 * 1024, extra_rollover)
sub.traffic_used_bytes = 0
sub.traffic_limit_bytes = sub.base_traffic_bytes + sub.extra_traffic_bytes
sub.expires_at = max(sub.expires_at, now) + timedelta(days=30)
sub.status = WhiteInternetStatus.ACTIVE
```

---

## 5. Интеграция с клиентом INCY (`Subscription-Userinfo`)

Для корректного рендеринга нативного виджета в приложении INCY эндпоинт подписки отдает:
```http
Subscription-Userinfo: upload=0; download={traffic_used_bytes}; total={base_traffic_bytes + extra_traffic_bytes}; expire={expires_at_timestamp}
```
* Пользователь всегда видит честный прогресс-бар: `Использовано X из Y ГБ` и дату окончания.
* Никаких скачков или расхождений счетчиков.
