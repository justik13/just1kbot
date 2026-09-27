# 📚 JUST1KBOT DOCUMENTATION HUB

Добро пожаловать в централизованный каталог технической документации проекта **JUST1KBOT**.
Материалы основаны на архитектуре и кодовой базе проекта, а также на официальных спецификациях протоколов и клиентских приложений.

---

## 📑 Каталог документов

### 1. [AmneziaWG 2.0 Technical Reference](amnezia_docs.md)
* **Назначение:** Спецификация используемого протокола AmneziaWG 2.0 (`amneziawg2`), форматы файлов `.conf` и `.vpn`, схема URI `vpn://...`, обфускационные параметры (`Jc`, `Jmin`, `Jmax`, `S1-S4`, `H1-H4`, `I1-I5`), особенности интеграции с нативным микросервисом `amnezia-api` и чеклист валидации.

### 2. [Architecture & Security Reference](architecture_and_security.md)
* **Назначение:** Архитектура бэкенда, база данных PostgreSQL и модели SQLAlchemy, MultiFernet шифрование данных, фоновые воркеры уведомлений и платежей, Rate Limiting и руководство по обновлению продакшена через `just1kbot update`.

### 3. [White Internet («Белый Интернет») Architecture & Operations](white_internet.md)
* **Назначение:** Архитектура XHTTP (SplitHTTP) over CDN, Nginx OPTIONS->POST трансляция, VLESS Vision туннели, биллинг грантов и руководство по установке узлов через `just1knode`.

### 4. [Сетевая безопасность, ТСПУ и модель угроз](NETWORK_SECURITY_AND_TSPU.md)
* **Назначение:** Модель угроз ТСПУ/РКН, механизмы пассивной и активной сетевой фильтрации (Active Probing / DPI Spiders), архитектура Zero-Signature (строгое закрытие портов, сброс прямых IP-проб), защита управляющих портов через UFW и реестр исследовательских ресурсов (NTC Party, Net4People, Zapret).

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
