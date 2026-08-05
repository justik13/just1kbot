# Production restore/cutover PostgreSQL

Эта процедура восстанавливает проверенный encrypted backup в новую staging-БД,
проверяет её текущим приложением и только затем выполняет короткий production
cutover. Текущая production-БД не удаляется: она сохраняется под rollback-именем
до отдельной команды `restore-finalize`.

## Что автоматизировано

Команда production restore выполняет следующий порядок:

1. Берёт exclusive lock всех deploy/backup/restore/uninstall операций.
2. Проверяет production `.env`, локальный PostgreSQL cluster и настоящий port.
3. Проверяет artifact, sidecar, age identity, manifest и все checksums.
4. Сравнивает `DB_ENCRYPTION_KEY` из backup с текущим production key без вывода
   значения в журнал.
5. Проверяет свободное место для одновременного хранения текущей и восстановленной
   database.
6. Восстанавливает dump в новую `just1kbot_stg_*` database.
7. Применяет текущий `alembic upgrade head` только к staging database.
8. Проверяет единственный Alembic head и критические application/payment tables.
9. Показывает basename, SHA-256, дату backup и staging revision.
10. После точного подтверждения останавливает timers и application writers.
11. Создаёт новый encrypted backup текущей production database и сразу проверяет,
    что он расшифровывается доступным age identity.
12. Запрещает новые подключения к обеим database и завершает оставшиеся sessions.
13. Переименовывает текущую production database в `just1kbot_rb_*`.
14. Переименовывает staging database в `just1kbot_bot` без изменения `.env`.
15. Запускает приложение и ждёт штатный bounded healthcheck.
16. При readiness failure автоматически возвращает предыдущую database, а
    неуспешную восстановленную database сохраняет как `just1kbot_fail_*`.

Старую database нельзя удалить той же командой, которая выполняет cutover.

## Предварительные условия

До restore должны быть установлены и работать:

- версия приложения с production restore engine;
- `/opt/just1kbot/.env` с локальным `DATABASE_URL`;
- exact PostgreSQL cluster, определяемый `pg_lsclusters`;
- `just1kbot.service` и штатный healthcheck;
- encrypted backup tooling и systemd backup service;
- artifact `*.tar.age`, matching `.sha256` и соответствующий private age key.

Artifact и identity должны быть regular files, не symlink. Artifact должен иметь
mode `0600`, как требует strict verifier.

## Проверка без production cutover

Сначала всегда выполните isolated rehearsal:

```bash
sudo AGE_IDENTITY_FILE=/etc/just1kbot/backup.agekey \
  bash /opt/just1kbot/deploy.sh restore-test \
  /var/lib/just1kbot/backups/just1kbot-pg-v1-YYYYMMDDTHHMMSSZ.tar.age
```

Rehearsal создаёт только `just1kbot_rehearsal_*`, проверяет данные и удаляет эту
database. Production database не переименовывается и не удаляется.

## Production restore

Интерактивный запуск:

```bash
sudo AGE_IDENTITY_FILE=/etc/just1kbot/backup.agekey \
  bash /opt/just1kbot/deploy.sh restore-production \
  /var/lib/just1kbot/backups/just1kbot-pg-v1-YYYYMMDDTHHMMSSZ.tar.age
```

До остановки приложения staging restore и migrations уже должны успешно
завершиться. Для начала cutover скрипт потребует фразу с первыми 12 символами
точного SHA-256 artifact.

Неинтерактивный запуск разрешён только с полным ожидаемым SHA-256:

```bash
sudo AGE_IDENTITY_FILE=/secure/backup.agekey \
  bash /opt/just1kbot/deploy.sh restore-production \
  --yes \
  --expected-sha256 '<64 lowercase hex characters>' \
  /secure/just1kbot-pg-v1-YYYYMMDDTHHMMSSZ.tar.age
```

Произвольное `--yes` без exact checksum отклоняется.

## Проверка состояния

После успешного cutover:

```bash
sudo bash /opt/just1kbot/deploy.sh restore-status
```

Статус показывает только безопасные идентификаторы:

- transaction ID;
- active или rolled_back;
- production, rollback и failed database names;
- artifact basename и SHA-256;
- время backup/cutover;
- basename свежего pre-cutover safety backup.

Connection URL, password, private key и `DB_ENCRYPTION_KEY` не выводятся.

## Откат после успешного restore

Пока transaction имеет status `active`, можно вернуть предыдущую database:

```bash
sudo bash /opt/just1kbot/deploy.sh restore-rollback
```

Перед rollback приложение снова останавливается и создаётся свежий encrypted
backup текущей восстановленной database. Затем:

- текущая restored database сохраняется как `just1kbot_fail_*`;
- `just1kbot_rb_*` возвращается под именем `just1kbot_bot`;
- приложение должно пройти healthcheck.

Поздний rollback логически теряет для активного приложения записи, сделанные
после cutover. Эти записи не уничтожаются: restored database остаётся отдельной
`just1kbot_fail_*` до finalize. Решение о переносе отдельных данных принимается
отдельно, без автоматического merge databases.

Неинтерактивный rollback требует exact transaction ID:

```bash
sudo bash /opt/just1kbot/deploy.sh restore-rollback \
  --yes --transaction-id 'YYYYMMDDHHMMSS_PID'
```

## Finalize

После подтверждённого периода стабильной работы и повторной проверки backups:

```bash
sudo bash /opt/just1kbot/deploy.sh restore-finalize
```

Finalize сначала требует успешный production healthcheck. Затем он удаляет только
сохранённую non-production database:

- при status `active` — предыдущую `just1kbot_rb_*`;
- при status `rolled_back` — неуспешную `just1kbot_fail_*`.

Имя `just1kbot_bot` имеет отдельный fail-closed запрет на удаление.

Неинтерактивный finalize также требует exact transaction ID:

```bash
sudo bash /opt/just1kbot/deploy.sh restore-finalize \
  --yes --transaction-id 'YYYYMMDDHHMMSS_PID'
```

После finalize active state атомарно переносится в root-only archive под:

```text
/var/lib/just1kbot/restore-transactions/
```

## Ограничения

- Restore не меняет и не восстанавливает production `.env`.
- Несовпадение `DB_ENCRYPTION_KEY` блокирует операцию.
- Restore работает только с локальной `just1kbot_bot` и ролью `just1kbot`.
- Одновременно разрешена только одна незавершённая restore transaction.
- PostgreSQL cluster не создаётся, не удаляется и не пересоздаётся.
- Restore не объединяет записи из старой и новой databases.
- UFW, Redis, Amnezia API и Let's Encrypt не являются частью database cutover.
- GitHub Actions не заменяют controlled запуск на настоящем VPS; первый production
  restore следует выполнять в maintenance window с доступом к systemd/journal.
