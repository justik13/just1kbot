# 📚 JUST1KBOT DOCUMENTATION HUB

Добро пожаловать в централизованный каталог технической документации проекта **JUST1KBOT**.
Материалы основаны на архитектуре и кодовой базе проекта, а также на официальных спецификациях протоколов и клиентских приложений.

---

## 📑 Каталог документов

### 1. [AmneziaWG 2.0 Technical Reference](amnezia_docs.md)
* **Назначение:** Спецификация используемого протокола AmneziaWG 2.0 (`amneziawg2`), форматы файлов `.conf` и `.vpn`, схема URI `vpn://...`, обфускационные параметры (`Jc`, `Jmin`, `Jmax`, `S1-S4`, `H1-H4`, `I1-I5`), особенности интеграции с `kyoresuas/amnezia-api` и чеклист валидации.

### 2. [INCY Application & Subscription Protocol](incy_docs.md)
* **Назначение:** Спецификация формата подписки INCY, протокольные схемы (`amneziawg://`, `awg://`), правила кодирования Base64URL, стандартные HTTP-заголовки управления (`profile-title`, `subscription-userinfo`, `announce`), deep links (`incy://import/`, `incy://crypt1/`) и кросс-платформенная матрица поддержки (iOS, Android vs Desktop).

### 3. [Architecture & Security Reference](architecture_and_security.md)
* **Назначение:** Архитектура бэкенда, база данных PostgreSQL и модели SQLAlchemy, MultiFernet шифрование данных, защита веб-мостов через HMAC-SHA256, фоновые воркеры уведомлений и платежей, Rate Limiting и руководство по обновлению продакшена через `just1kbot update`.

---

## 🛠 Быстрые команды для разработки

```bash
# Запуск полного набора тестов
python -m unittest discover -s tests -v

# Статический анализ кода
python -m ruff check bot config database services utils alembic scripts tests --select E4,E7,E9,F,B,ASYNC,PLE,PLW,RUF100 --ignore PLW0603,PLW0108 --output-format full

# Проверка компиляции
python -m compileall -q bot config database services utils alembic scripts tests
```
