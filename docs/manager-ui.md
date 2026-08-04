# Just1kBot Manager UI

`sudo just1kbot` без аргументов открывает русскоязычный state-aware интерфейс.
Меню является только визуальным слоем над существующим безопасным control plane:
оно не ослабляет ownership manifest, durable journal, exact-SHA update, backup
verification, restore lifecycle или manifest-driven uninstall.

## Состояния

- `clean` — установка с managed Nginx/TLS, external proxy или read-only dry-run;
- `installed_managed` — логи, ручная проверка обновлений, повторное развёртывание,
  бэкапы/restore, doctor, repair, service status и uninstall;
- `partial_install` — только recovery/rollback и диагностика;
- `legacy_managed` / `residual_managed` — strict migration, repair check,
  диагностика, support bundle и safe uninstall;
- `foreign_collision` / `corrupted_state` / `unknown` — только read-only действия.

## Защита от случайных действий

- GitHub проверяется только после ручного выбора пункта обновления.
- Устанавливается только повторно полученный полный 40-hex SHA; штатное
  подтверждение updater сохраняется.
- Остановка сервиса требует `STOP`.
- Удаление старых бэкапов требует `DELETE BACKUPS` и затрагивает только обычные
  файлы `just1kbot-pg-v1-*.tar.age` внутри `/root/backups/just1kbot`.
- Полный uninstall остаётся под защитой штатной фразы `DELETE JUST1KBOT`.
- При неподтверждённом ownership mutating-пункты не показываются.

## Live-логи

В live-режиме выводятся последние строки и последующие события. Нажатие `q`
останавливает только локальный viewer и возвращает в меню; сервис не меняется.
