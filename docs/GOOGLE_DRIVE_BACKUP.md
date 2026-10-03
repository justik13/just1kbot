# Автоматическая выгрузка резервных копий в Google Drive

В Just1kBot реализована безопасная автоматическая выгрузка зашифрованных резервных копий базы данных PostgreSQL в **Google Drive** с использованием утилиты `rclone`.

Для личных аккаунтов Google (включая подписки **Google One / Google Pro**) выгрузка выполняется от имени вашего пользователя через OAuth 2.0 токен, расходуя дисковую квоту вашего аккаунта.

---

## 🔒 Безопасность и архитектурные принципы

1. **Сквозное шифрование (End-to-End Encryption)**:
   * База данных дампится через `pg_dump`, упаковывается в `gzip` и шифруется публичным ключом `age` (`.sql.gz.age`).
   * В Google Drive выгружается **исключительно зашифрованный файл** (`.sql.gz.age`). Исходный незашифрованный файл удаляется до начала выгрузки.
   * Без вашего приватного ключа (`backup_private_key.txt`, который хранится оффлайн) расшифровать резервную копию невозможно даже при полном доступе к Google-диску.
2. **Изоляция в Docker**:
   * Утилита `rclone` работает внутри изолированного контейнера `just1kbot_backup`.
   * Контейнер имеет статус `read_only: true`, сброшенные Linux capabilities (`cap_drop: ALL`) и временную файловую память `tmpfs /tmp` для временных конфигов, которые удаляются сразу после завершения.
   * На хостовую систему не требуется устанавливать сторонние пакеты.
3. **Безопасная ротация (Scoped Cleanup)**:
   * Бэкапы на Google Диске хранятся **14 дней** (настраивается через `GDRIVE_RETENTION_DAYS`).
   * Удаление устаревших копий выполняется строго по маске `--include "just1kbot_*.sql.gz.age"`, что исключает случайное удаление любых других файлов в вашей папке Google Диска.
4. **Контроль ошибок (Fail-Closed)**:
   * Если соединение с Google Drive или авторизация не сработали, скрипт завершается с кодом ошибки (`exit 1`), сигнализируя cron и оператору о проблеме.
   * При этом локальный зашифрованный архив **сохраняется на диске сервера** и не удаляется.

---

## 📋 Пошаговая настройка для личного аккаунта Google (Google Pro / One)

### Шаг 1. Создание папки на Google Диске

1. Откройте [Google Drive](https://drive.google.com/).
2. Создайте папку для бэкапов (например, `Just1kBot Backups`).
3. Зайдите внутрь папки и посмотрите в адресную строку браузера:
   ```text
   https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz123456
   ```
4. Скопируйте **ID папки** — это строка символов после `/folders/` (в примере: `1AbCdEfGhIjKlMnOpQrStUvWxYz123456`).

---

### Шаг 2. Однократное получение OAuth токена (на вашем ПК)

> [!NOTE]
> В течение 2026 года Google выводит из эксплуатации общий (shared) Client ID rclone. Создание собственного Client ID в Google Cloud Console занимает 2 минуты, гарантирует отсутствие rate limit и обеспечивает непрерывную работу бэкапов.

1. **Создание Client ID в Google Cloud Console**:
   * Перейдите в [Google Cloud Console](https://console.developers.google.com/).
   * Создайте новый проект (например, `Just1kBot-Backup`).
   * Перейдите в **APIs & Services** ➔ **Enabled APIs & Services** ➔ нажмите **+ ENABLE APIS AND SERVICES**.
   * Найдите **Google Drive API** и нажмите **Enable**.
   * В левом меню откройте **OAuth consent screen** (Экран согласия OAuth):
     * User Type: выберите **External** ➔ нажмите **Create**.
     * Укажите App name (`Just1kBot`), ваш email в полях поддержки и контактов разработчика ➔ нажмите **Save and Continue**.
     * В разделе **Scopes** добавьте scope `https://www.googleapis.com/auth/drive` ➔ нажмите **Save and Continue**.
     * В разделе **Test users** нажмите **+ ADD USERS**, укажите ваш личный Google адрес (`@gmail.com`) ➔ нажмите **Save and Continue**.
   * В левом меню откройте **Credentials** (Учетные данные):
     * Нажмите **+ CREATE CREDENTIALS** ➔ **OAuth client ID**.
     * Application type: выберите **Desktop app** (Классическое приложение).
     * Нажмите **Create**.
     * Скопируйте полученные **Client ID** и **Client Secret**.

2. **Авторизация через rclone**:
   * Установите `rclone` на ваш компьютер (**Windows**: `winget install Rclone.Rclone`, **macOS**: `brew install rclone`, **Linux**: `sudo apt install rclone`).
   * Запустите авторизацию в терминале с вашими ключами:
     ```bash
     rclone authorize "drive" "ВАШ_CLIENT_ID" "ВАШ_CLIENT_SECRET"
     ```
     *(Либо просто `rclone authorize "drive"`, если используете быстрый режим)*.
   * В браузере откроется окно авторизации Google ➔ войдите под своим Google Pro аккаунтом и нажмите **«Разрешить» (Allow)**.
   * В терминале появится строка токена:
     ```json
     {"access_token":"ya29.a0...","token_type":"Bearer","refresh_token":"1//04...","expiry":"..."}
     ```
     Скопируйте этот JSON-токен целиком (вместе с фигурными скобками).

---

### Шаг 3. Передача авторизации на сервер

Вы можете выбрать один из двух удобных способов:

#### Способ А. Через файл `backups/rclone.conf` (Рекомендуется — максимальная изоляция)
Папка `./backups/` смонтирована **только** в контейнер бэкапа и изолирована от бота. Создайте файл `backups/rclone.conf` на сервере:
```ini
[gdrive]
type = drive
scope = drive
token = {"access_token":"ya29...","token_type":"Bearer","refresh_token":"1//04...","expiry":"..."}
root_folder_id = 1AbCdEfGhIjKlMnOpQrStUvWxYz123456
```
Установите права доступа:
```bash
chmod 600 /opt/just1kbot/backups/rclone.conf
```

#### Способ Б. Через `.env` (Base64 — без создания файлов)
Закодируйте скопированный JSON-токен в Base64 на своем компьютере:
* **Windows (PowerShell)**:
  ```powershell
  [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes('ВАШ_JSON_ТОКЕН'))
  ```
* **macOS**:
  ```bash
  echo -n 'ВАШ_JSON_ТОКЕН' | base64
  ```
* **Linux**:
  ```bash
  echo -n 'ВАШ_JSON_ТОКЕН' | base64 -w 0
  ```
Полученную строку вставьте в `.env` сервера как `GDRIVE_TOKEN_BASE64`.

---

### Шаг 4. Настройка `.env`

В файле `.env` укажите:

```bash
GDRIVE_BACKUP_ENABLED=true
GDRIVE_FOLDER_ID='1AbCdEfGhIjKlMnOpQrStUvWxYz123456'
# Если используете Способ Б (Base64 токен в .env):
GDRIVE_TOKEN_BASE64='eyJhbGciOi...'
GDRIVE_RETENTION_DAYS=14
```

---

### Шаг 5. Проверка работы

1. Запустите системную диагностику:
   ```bash
   just1kbot doctor
   ```
   В выводе появится:
   ```text
   [OK] Google Drive бэкап: настроен (папка ID: 1AbCdEfGhIj..., авторизация: ...) (OK)
   ```

2. Выполните тестовое создание резервной копии:
   ```bash
   just1kbot backup
   ```
   В выводе вы увидите процесс:
   ```text
   Создание backup...
   Шифрование backup (atomic write)...
   Загрузка encrypted backup в Google Drive (rclone)...
   Google Drive upload успешно завершён: just1kbot_20261003_120000.sql.gz.age
   Очистка устаревших бэкапов в Google Drive (старше 14 дн.)...
   Backup создан: /backups/just1kbot_20261003_120000.sql.gz.age
   ```

3. Откройте папку на Google Диске: в ней появится зашифрованный файл `.sql.gz.age`.

---

## ⏰ Расписание автоматических бэкапов

Штатный планировщик `cron` сервера настроен на ежедневный запуск в **02:00**:
```bash
0 2 * * * flock -n /tmp/just1kbot-backup.lock sh -c 'cd /opt/just1kbot && docker compose --profile tools run --rm backup >> /opt/just1kbot/backups/backup.log 2>&1'
```
Каждую ночь в 02:00 зашифрованная копия автоматически выгружается в Google Drive, а старые архивы старше 14 дней удаляются. Логи процесса записываются в `backups/backup.log`.
