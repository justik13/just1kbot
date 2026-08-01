# Обновление production из GitHub

После установки версии с этим updater дальнейшее обновление выполняется одной командой:

```bash
sudo bash /opt/just1kbot/deploy.sh update
```

Для неинтерактивного запуска:

```bash
sudo bash /opt/just1kbot/deploy.sh update --yes
```

Проверить наличие новой версии без deploy:

```bash
sudo bash /opt/just1kbot/deploy.sh update --check
```

Посмотреть план transactional deploy:

```bash
sudo bash /opt/just1kbot/deploy.sh update --dry-run
```

## Как это работает

Updater не выполняет `git pull` в `/opt/just1kbot` и не превращает live-каталог в рабочий Git checkout.

Он выполняет следующие действия:

1. Использует только фиксированный repository `https://github.com/justik13/projectx.git`.
2. Получает только `refs/heads/main` в новый временный каталог под `/var/lib/just1kbot/source-releases`.
3. Отключает Git hooks, интерактивную авторизацию, `file` и `ext` protocols и пользовательские Git configs.
4. Получает точный commit из `FETCH_HEAD` и делает detached checkout этого SHA.
5. Выполняет `git fsck`, проверяет чистый index и обязательные deployment-файлы.
6. Отклоняет submodules, tracked symlinks, control characters в tracked paths и symlink в checkout.
7. Делает checkout root-owned и недоступным для записи group/other.
8. Записывает `.release-version` с точным source commit.
9. Запускает существующий application/operational transactional deploy из опубликованного release-каталога.
10. При ошибке сохраняет source release для диагностики; production rollback выполняет deploy transaction.

В live-каталог копируется `.release-version`, поэтому следующая команда `update --check` может сравнить установленный SHA с текущим `main`. При rollback возвращается metadata предыдущего application release.

## Ограничения

- Updater устанавливает только `main`; произвольные URL и ветки не поддерживаются.
- GitHub Actions не перепроверяются самим production-сервером. В `main` следует сливать только PR с проверенным exact-head CI.
- Самый первый переход на версию, содержащую updater, нужно выполнить обычным deploy из отдельного checkout. После этого можно запускать update прямо через `/opt/just1kbot/deploy.sh`.
- Обновление кода не заменяет production restore/cutover PostgreSQL и не выполняет автоматический downgrade базы.
