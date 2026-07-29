# Ручное восстановление production PostgreSQL

> **Аварийная ручная процедура.** Этот runbook не является автоматизацией
> production cutover. Все команды и весь порядок действий сначала проверяют на
> staging или на изолированной копии production. Если любой шаг имеет
> неоднозначный результат, остановитесь и разберите состояние вручную.

## Правила безопасности

- Запланируйте maintenance window и заранее назначьте ответственного оператора.
- Не вставляйте реальные пароли, приватные ключи, bot token, платёжные credentials
  или другие секреты в команды, логи, тикеты и shell history. Используйте
  root-only файлы конфигурации и безопасно переданные environment variables.
- Не используйте `dropdb` для текущей production database.
- Не переименовывайте и не удаляйте старую production database автоматически.
- Не заменяйте production `.env` автоматически.
- Сохраните текущий `DB_ENCRYPTION_KEY`: данные из backup должны расшифровываться
  тем же ключом. Если ключ из backup и текущий ключ несовместимы, остановитесь и
  планируйте отдельную ручную процедуру.
- Не продолжайте после ошибки strict verifier или restore rehearsal.
- Старую database удаляют только отдельным ручным решением после подтверждённого
  периода стабильной работы.

## 1. Подготовка maintenance window

До начала убедитесь, что доступны:

- выбранный encrypted backup artifact `*.tar.age`;
- соответствующий sidecar `*.tar.age.sha256`;
- безопасный regular-файл `AGE_IDENTITY_FILE`;
- текущий production `.env`;
- текущий `DB_ENCRYPTION_KEY`;
- доступ к PostgreSQL maintenance database и systemd;
- достаточно дискового пространства для rehearsal, новой database и ещё одного
  encrypted production backup.

Запишите безопасные идентификаторы операции: время UTC, basename выбранного
artifact, его SHA-256, текущий git revision и имя старой production database. Не
записывайте connection URL или значения секретов.

## 2. Проверка выбранного backup

Сначала запустите strict verifier. Artifact и matching `.sha256` должны находиться
рядом и иметь безопасные permissions.

```bash
AGE_IDENTITY_FILE=/secure/identity.txt \
/usr/local/bin/verify_backup.sh \
/root/backups/just1kbot/<artifact>.tar.age
```

Затем выполните восстановление только в отдельную rehearsal database:

```bash
AGE_IDENTITY_FILE=/secure/identity.txt \
/usr/local/bin/restore_rehearsal.sh \
/root/backups/just1kbot/<artifact>.tar.age
```

**Не продолжайте**, если verifier или rehearsal завершились ненулевым exit code,
если rehearsal database не была безопасно очищена либо если результат проверки
неоднозначен.

## 3. Свежий backup текущей production database

До остановки сервиса создайте свежий encrypted backup текущей production database,
используя настроенный age recipient:

```bash
sudo --preserve-env=BACKUP_AGE_RECIPIENT \
/usr/local/bin/just1kbot-backup.sh
```

Запишите basename и SHA-256 созданного artifact. Проверьте **этот точный artifact**
тем же strict verifier, а затем restore rehearsal:

```bash
AGE_IDENTITY_FILE=/secure/identity.txt \
/usr/local/bin/verify_backup.sh \
/root/backups/just1kbot/<fresh-artifact>.tar.age

AGE_IDENTITY_FILE=/secure/identity.txt \
/usr/local/bin/restore_rehearsal.sh \
/root/backups/just1kbot/<fresh-artifact>.tar.age
```

При любой ошибке остановите процедуру. Не переходите к maintenance window без
двух проверенных encrypted backups: выбранного recovery backup и свежего backup
текущей production database.

## 4. Остановка приложения

Остановите bot service и отдельно подтвердите inactive state:

```bash
sudo systemctl stop just1kbot
sudo systemctl is-active just1kbot
```

Ожидаемый результат второй команды — `inactive`. Дополнительно убедитесь, что не
осталось процесса бота. Если service или процесс не остановился, не выполняйте
никаких изменений database.

## 5. Восстановление в новую database

Сгенерируйте новое безопасное имя, например
`just1kbot_manual_restore_YYYYMMDDHHMMSS`. Не используйте имя текущей production
DB. Из verified artifact получите dump только утверждённым verifier/rehearsal
процессом в private root-only workspace; не распаковывайте `config.env` в общий
каталог и не выводите его содержимое.

Создайте новую database с согласованными owner, encoding и locale. Восстановите
проверенный custom dump только в неё:

```bash
pg_restore \
  --exit-on-error \
  --no-owner \
  --no-acl \
  --dbname="just1kbot_manual_restore_YYYYMMDDHHMMSS" \
  /secure/private-workspace/dump.custom
```

Точные `createdb`, owner, locale и connection параметры сначала подтвердите на
staging. Оператор обязан проверить, что `--dbname` указывает на новую database, а
не на текущую production database.

## 6. Проверка новой database и migrations

До переключения выполните read-only проверки новой database:

- существует ровно одна ожидаемая строка `alembic_version`;
- доступны таблицы пользователей;
- доступны payment tables и durable payment queues;
- открывается новое подключение;
- выполняются простые read-only `SELECT`;
- критические encrypted поля читаются с текущим `DB_ENCRYPTION_KEY`;
- отсутствуют очевидные нарушения внешних ключей.

Не запускайте Alembic против старой production database. Если восстановленная
новая database имеет старую revision, сформируйте отдельный `DATABASE_URL`, ещё
раз проверьте имя новой database и примените migrations **только к ней**:

```bash
DATABASE_URL='postgresql+asyncpg://<credentials>@<host>:<port>/just1kbot_manual_restore_YYYYMMDDHHMMSS' \
alembic upgrade head
```

Не помещайте реальный URL с паролем в shell history. После migration повторите все
read-only проверки и подтвердите exact Alembic head.

## 7. Ручное переключение

Не меняйте `.env` автоматически и не заменяйте `DB_ENCRYPTION_KEY`. После ручной
проверки измените **только** database name/URL в production configuration так,
чтобы приложение подключалось к новой database. Сохраните защищённую копию
предыдущего `DATABASE_URL`, необходимую для rollback.

Старая production database остаётся на месте под прежним именем. Этот runbook не
предписывает автоматический rename, swap или удаление databases.

## 8. Запуск и health validation

Запустите service:

```bash
sudo systemctl start just1kbot
sudo systemctl is-active just1kbot
```

В ограниченном временном окне проверьте:

- systemd сообщает `active`;
- heartbeat свежий и продолжает обновляться;
- штатный healthcheck успешен;
- payment queues доступны для безопасного чтения;
- нет немедленного crash loop;
- read-only запросы выполняются через production application configuration.

Не выполняйте тестовый платёж, не создавайте VPN peer и не вызывайте внешние API
только ради этой проверки.

## 9. Ручной rollback при проблеме

Если новая database или приложение не проходят health validation:

1. Остановите service и подтвердите inactive state.
2. Верните сохранённый старый `DATABASE_URL`; не меняйте остальные `.env` values.
3. Снова запустите service.
4. Подтвердите systemd active, свежий heartbeat, штатный healthcheck, чтение
   payment queues и отсутствие crash loop.
5. Сохраните новую database для диагностики; не удаляйте её в ходе rollback.

Если старый service не восстанавливается, не выполняйте циклические restart и не
утверждайте, что rollback успешен. Зафиксируйте безопасное состояние и продолжите
ручную диагностику.

## 10. Хранение после восстановления

В день восстановления не удаляйте:

- старую production database;
- выбранный encrypted recovery backup и его `.sha256`;
- свежий encrypted backup прежней production database и его `.sha256`;
- новую восстановленную database, если был rollback.

Удаление старой database разрешается только отдельным ручным решением после
достаточного периода стабильной работы, повторной проверки backups и подтверждения
того, что rollback к ней больше не требуется.
