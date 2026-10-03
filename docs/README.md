# 📚 JUST1KBOT DOCUMENTATION HUB

Добро пожаловать в централизованный каталог технической документации проекта **JUST1KBOT**.
Материалы основаны на архитектуре и кодовой базе проекта, а также на официальных спецификациях протоколов и клиентских приложений.

---

## 📑 Каталог документов

### 1. [AmneziaWG 2.0 Technical Reference](amnezia_docs.md)
* **Назначение:** Спецификация используемого протокола AmneziaWG 2.0 (`amneziawg2`), форматы файлов `.conf` и `.vpn`, схема URI `vpn://...`, обфускационные параметры (`Jc`, `Jmin`, `Jmax`, `S1-S4`, `H1-H4`, `I1-I5`), особенности интеграции с нативным микросервисом `amnezia-api` и чеклист валидации.

### 2. [White Internet («Белый Интернет») Master Guide](WL/WHITELIST_MASTER_GUIDE.md)
* **Назначение:** Полный инженерно-технический справочник по работе в условиях жестких белых списков (Default-Drop). Архитектура VLESS XHTTP over Yandex Cloud CDN, Bodiless GET Uplink (защита от 413 ошибок на CDN), Nginx Zero Buffering, VLESS-Vision туннелирование Origin ➔ Exit, протокол INCY, а также детальный технический аудит и анализ применимости транспорта XDRIVE (`network: "xdrive"` из Xray-core v26.9.30).

### 3. [INCY Client Master Guide](INCY_MASTER_GUIDE.md)
* **Назначение:** Руководство по интеграции и работе с клиентским приложением INCY (платформы iOS, Android, macOS, Windows). Форматы конфигураций (Full Xray JSON, URI с `extra`, HTTP Subscription Feed, `incy://` deep links), оптимизация памяти Network Extension на iOS и обход платформенных ограничений.

### 4. [Billing Architecture Manifesto](BILLING_ARCHITECTURE_MANIFESTO.md)
* **Назначение:** Архитектурные принципы и манифест биллинговой системы проекта (уроки PR #275 и #276). Трехуровневая модель (Провайдеры ➔ Заказы ➔ Выдача подписок), безопасность балансов пользователей, предотвращение оверинжиниринга и прямое подтверждение оплат.

### 5. [Сетевая безопасность, ТСПУ и модель угроз](NETWORK_SECURITY_AND_TSPU.md)
* **Назначение:** Модель угроз ТСПУ/РКН, механизмы пассивной и активной сетевой фильтрации (Active Probing / DPI Spiders), архитектура Zero-Signature (строгое закрытие портов, сброс прямых IP-проб), защита управляющих портов через UFW и реестр исследовательских ресурсов (NTC Party, Net4People, Zapret).

### 6. [Автоматическая выгрузка бэкапов в Google Drive](GOOGLE_DRIVE_BACKUP.md)
* **Назначение:** Инструкция по безопасной автоматической выгрузке зашифрованных резервных копий базы данных в Google Drive через официальный Service Account и rclone в изолированном контейнере backup.

### 7. Внешние источники, репозитории и реестры исследований
* **[docs/main_source.txt](main_source.txt):** Официальные репозитории, апстримы используемых ядер (Xray, AmneziaWG, sing-box) и документация облачных провайдеров.
* **[docs/research.txt](research.txt):** Узкоспециализированные технические исследования, RFC, бенчмарки, аналитические разборы DPI/ТСПУ (net4people #668, Хабр #1044396, постквантовый TLS, SelfSteal SNI).
* **[docs/projects.txt](projects.txt):** Сторонние проекты, форки и идеи для справки.

---

## 🛠 Быстрые команды для разработки

```bash
# Запуск полного набора тестов
pytest tests/ -v

# Статический анализ кода
python -m ruff check bot config database services utils alembic scripts tests

# Проверка компиляции
python -m compileall -q bot config database services utils alembic scripts tests
```
