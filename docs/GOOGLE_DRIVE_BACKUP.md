# Автоматическая выгрузка резервных копий в Google Drive

В Just1kBot реализована безопасная автоматическая выгрузка зашифрованных резервных копий базы данных PostgreSQL в **Google Drive** с использованием официального сервисного аккаунта Google Cloud (Google Service Account) и утилиты `rclone`.

---

## 🔒 Безопасность и архитектурные принципы

1. **Сквозное шифрование (End-to-End Encryption)**:
   * База данных сначала дампится через `pg_dump`, упаковывается в `gzip` и шифруется публичным ключом `age` (`.sql.gz.age`).
   * В Google Drive выгружается **исключительно зашифрованный файл** (`.sql.gz.age`). Исходный незашифрованный файл удаляется до начала выгрузки.
   * Без вашего приватного ключа (`backup_private_key.txt`, который хранится оффлайн) расшифровать резервную копию невозможно даже при полном доступе к Google-диску.
2. **Изоляция в Docker**:
   * Утилита `rclone` работает внутри изолированного контейнера `just1kbot_backup`.
   * Контейнер имеет статус `read_only: true`, сброшенные Linux capabilities (`cap_drop: ALL`) и временную файловую систему `tmpfs` для конфигов, которые удаляются сразу после завершения.
   * На хостовую систему не требуется устанавливать сторонние пакеты.
3. **Автоматическая ротация**:
   * Бэкапы на Google Диске хранятся **14 дней** (настраивается через `GDRIVE_RETENTION_DAYS`), после чего старые архивы автоматически удаляются `rclone`.

---

## 📋 Пошаговая инструкция по настройке

### Шаг 1. Создание Service Account в Google Cloud Console

1. Перейдите в [Google Cloud Console](https://console.cloud.google.com/) под своим Google-аккаунтом.
2. Создайте новый проект (или выберите существующий), например `Just1kBot Backup`.
3. В строке поиска найдите **Google Drive API** и нажмите кнопку **Enable** (Включить).
4. Перейдите в меню: **IAM & Admin** ➔ **Service Accounts** (Сервисные аккаунты).
5. Нажмите **Create Service Account**:
   * Назовите его, например, `backup-bot`.
   * Нажмите **Create and Continue**, затем **Done** (роли в самом Google Cloud назначать не требуется).
6. В списке нажмите на созданный сервисный аккаунт и перейдите на вкладку **Keys** (Ключи).
7. Нажмите **Add Key** ➔ **Create new key** ➔ выберите формат **JSON** ➔ нажмите **Create**.
   * На ваш компьютер скачается JSON-файл с приватным ключом сервисного аккаунта.
8. Скопируйте email сервисного аккаунта (например: `backup-bot@just1kbot-backup.iam.gserviceaccount.com`).

---

### Шаг 2. Создание папки на Google Диске и выдача прав

1. Откройте [Google Drive](https://drive.google.com/).
2. Создайте отдельную папку для резервных копий (например, `Just1kBot Backups`).
3. Нажмите на созданную папку правой кнопкой мыши ➔ **Поделиться (Share)**.
4. В поле ввода вставьте email сервисного аккаунта из Шага 1 и назначьте ему роль **«Редактор» (Editor)**. Снимите галочку «Уведомить пользователей» и нажмите **Поделиться**.
5. Зайдите внутрь этой папки и посмотрите в адресную строку браузера:
   ```text
   https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz123456
   ```
6. Скопируйте **ID папки** — это строка символов после `/folders/` (в примере выше: `1AbCdEfGhIjKlMnOpQrStUvWxYz123456`).

---

### Шаг 3. Размещение ключа на сервере

1. Загрузите скачанный JSON-файл на сервер в папку проекта `backups/gdrive_sa.json`:
   ```bash
   # Пример через scp с локального компьютера:
   scp path/to/downloaded-key.json user@server_ip:/opt/just1kbot/backups/gdrive_sa.json
   ```
2. Установите строгие права доступа (только для владельца):
   ```bash
   chmod 600 /opt/just1kbot/backups/gdrive_sa.json
   ```

---

### Шаг 4. Настройка `.env`

В файле `.env` в корне проекта добавьте или обновите параметры:

```bash
GDRIVE_BACKUP_ENABLED=true
GDRIVE_FOLDER_ID='1AbCdEfGhIjKlMnOpQrStUvWxYz123456'
GDRIVE_SA_FILE='backups/gdrive_sa.json'
GDRIVE_RETENTION_DAYS=14
```

---

### Шаг 5. Проверка работы

1. Запустите системную диагностику:
   ```bash
   just1kbot doctor
   ```
   В выводе должна появиться строка:
   ```text
   [OK] Google Drive бэкап: настроен (папка ID: 1AbCdEfGhIj..., ключ: gdrive_sa.json) (OK)
   ```

2. Выполните тестовое создание резервной копии:
   ```bash
   just1kbot backup
   ```
   В логах вы увидите процесс создания дампа, шифрования и выгрузки:
   ```text
   Создание backup...
   Шифрование backup (atomic write)...
   Загрузка encrypted backup в Google Drive (rclone)...
   Google Drive upload завершён: just1kbot_20261003_120000.sql.gz.age
   Очистка устаревших бэкапов в Google Drive (старше 14 дн.)...
   Backup создан: /backups/just1kbot_20261003_120000.sql.gz.age
   ```

3. Проверьте папку на Google Диске: в ней появится зашифрованный файл `.sql.gz.age`.

---

## ⏰ Расписание автоматических бэкапов

Штатный планировщик `cron` сервера настроен на ежедневный запуск в **02:00**:
```bash
0 2 * * * flock -n /tmp/just1kbot-backup.lock sh -c 'cd /opt/just1kbot && docker compose --profile tools run --rm backup >> /opt/just1kbot/backups/backup.log 2>&1'
```
Каждую ночь в 02:00 зашифрованная копия автоматически выгружается в Google Drive, а старые файлы (старше 14 дней) удаляются. Логи процесса записываются в `backups/backup.log`.
