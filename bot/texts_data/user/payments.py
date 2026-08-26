"""Domain copy catalogue for: user/payments.py"""

TEXTS = {
    'BALANCE_ENTRY_LABELS': {   'admin_adjustment': 'Корректировка',
    'chargeback_debit': 'Банковский спор',
    'payment_credit': 'Пополнение',
    'purchase_debit': 'Покупка тарифа',
    'purchase_reversal': 'Возврат покупки',
    'refund_debit': 'Возврат через ЮKassa'},
    'BALANCE_HISTORY_EMPTY': '<i>Операций пока нет.</i>',
    'BALANCE_HISTORY_TITLE': """🧾 <b>История операций</b>

{history}""",
    'BUTTON_OPEN_PAYMENT': '💳 Перейти к оплате',
    'PAYMENT_LINK_READY': """💳 <b>Ссылка на оплату готова!</b>

Сумма: <b>{amount} ₽</b>
Текущий баланс: <b>{balance} ₽</b>

Нажмите кнопку ниже, чтобы перейти к оплате.""",
    'ERROR_PAYMENT_SERVICE': '❌ Ошибка платёжной системы. Попробуйте позже.',
    'HISTORY_EMPTY': '<i>История пуста. У вас пока не было пополнений.</i>',
    'HISTORY_HEADER': """🧾 <b>История пополнений</b>
""",
    'HISTORY_LIMIT_NOTE': """
<i>Показаны последние 10 из {count} пополнений</i>""",
    'PAYMENT_ACTIVE_CHANGE_QUOTE_EXISTS': '⚠️ У вас уже создан запрос на смену тарифа. Нажмите назад и выберите его.',
    'PAYMENT_ACTIVE_CHECKOUT_EXISTS': '⚠️ У вас уже есть не завершённая операция покупки. Завершите её или попробуйте чуть позже.',
    'PAYMENT_CHANGE_TARIFF_HEADER': """⚙️ <b>Смена тарифа</b>

Ваш текущий тариф: <b>{tariff_name}</b> (действует до {valid_until})

💡 <b>Правила смены тарифа:</b>
• Оставшаяся стоимость текущей подписки учитывается при переходе.
• 🔼 <b>При увеличении устройств:</b> новые лимиты становятся доступны сразу.
• 🔽 <b>При уменьшении устройств:</b> количество ваших активных устройств не должно превышать лимит нового тарифа (при необходимости удалите лишние устройства в разделе «Устройства»).

Выберите новый тариф ниже:""",
    'PAYMENT_CHANGE_TARIFF_UNAVAILABLE_NO_SUB': """⚠️ <b>Смена тарифа недоступна</b>

Смена тарифа с перерасчётом доступна только при наличии действующей подписки.
У вас сейчас нет активной подписки. Для оформления подписки воспользуйтесь кнопкой ниже.""",
    'PAYMENT_CURRENT_TARIFF_UNKNOWN': """⚠️ Смена тарифа возможна только при действующей подписке.

Перейдите в раздел «Купить подписку» для оформления нового тарифа.""",
    'PAYMENT_DOWNGRADE_BLOCKED_PROFILES': """⚠️ <b>Смена тарифа невозможна</b>

У вас подключено <b>{profiles_count}</b> устройств,
а выбранный тариф поддерживает только <b>{new_limit}</b>.

Чтобы перейти на этот тариф, сначала удалите лишние устройства
в разделе «🔌 Подключения».""",
    'PAYMENT_DOWNGRADE_COOLDOWN_ALERT': 'Смена тарифа на меньший доступна не чаще одного раза в 24 часа. Попробуйте позже.',
    'PAYMENT_DURATION_HEADER': """⏱ <b>На какой срок открываем доступ?</b>
""",
    'PAYMENT_HUB_HEADER': """⏳ <b>Ваша подписка</b>

🟢 <b>Статус:</b> Активна
📅 <b>Действует до:</b> {valid_until} <i>(осталось {days_left})</i>

💎 <b>Тариф:</b> {tariff_name}
🔌 <b>Устройства:</b> {devices_count} / {device_limit}

Выберите действие:""",
    'PAYMENT_NO_TARIFFS': """💳 В данный момент нет доступных тарифов.

Обратитесь в поддержку для оформления подписки вручную.""",
    'PAYMENT_QUICK_RENEW_HEADER': """🔄 <b>Продление доступа</b>

Ваш текущий тариф: <b>{tariff_name}</b>
Активен до: <i>{valid_until}</i>

Выберите, на сколько продлить:""",
    'PAYMENT_SHOWCASE_HEADER': """🛡 <b>Выберите формат подписки</b>

Выберите тариф, который подходит под ваши задачи.
""",
    'PAYMENT_STATUS_ICONS': {   'cancelled': '❌',
    'completed': '✅',
    'failed': '⚠️',
    'paid_processing': '🔄',
    'pending': '⏳',
    'refunded': '↩️',
    'requires_manual_review': '🧪'},
    'PAYMENT_STATUS_NAMES': {   'cancelled': 'Отменен',
    'completed': 'Выполнен',
    'failed': 'Ошибка',
    'paid_processing': 'Обработка',
    'pending': 'Ожидание',
    'refunded': 'Возврат',
    'requires_manual_review': 'Ручная проверка'},
    'PAYMENT_SUBSCRIPTION_INACTIVE': """⚠️ Смена тарифа с перерасчётом остатка возможна только при действующей подписке.

Ваша подписка неактивна. Перейдите в раздел «Купить подписку» для оформления нового тарифа.""",
    'PAYMENT_USER_NOT_REGISTERED': """⚠️ <b>Профиль не найден</b>

Похоже, вы ещё не зарегистрированы в боте.

Нажмите кнопку ниже, чтобы начать.""",
    'PURCHASE_COMPLETED': 'Тариф куплен',
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L106_1': 'Зарезервировано: <b>{value_0} ₽</b>',
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L108_1': 'Задолженность: <b>{value_0} ₽</b>',
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L111_1': """{value_0}💰 <b>Баланс</b>

""",
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L113_1': """

<b>Последние операции</b>
""",
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L151_1': """💳 <b>Пополнение баланса</b>

Сумма: <b>{value_0} ₽</b>
Текущий баланс: <b>{value_1} ₽</b>

Ссылка ведёт на защищённую страницу ЮKassa.""",
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L172_1': """⏳ <b>Создаём ссылку на пополнение</b>

Сумма: <b>{value_0} ₽</b>
Текущий баланс: <b>{value_1} ₽</b>

Ссылка появится здесь автоматически. Ручная проверка остаётся доступна.""",
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L83_1': 'Операция',
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L84_1': '−',
    'RUNTIME_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L87_1': '• {value_0} · {value_1}: <b>{value_2}{value_3} ₽</b>',
    'RUNTIME_BOT_HANDLERS_PAYMENT_COMMON_L70_1': 'Не удалось надёжно определить текущий тариф. Покупка временно недоступна — обратитесь в поддержку для проверки подписки.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_COMMON_L73_1': 'Безопасная смена тарифа временно недоступна. Продление текущего тарифа продолжает работать.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L106_1': """

Не хватает {value_0} ₽. Минимальное пополнение — {value_1} ₽; после покупки останется {value_2} ₽.""",
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L111_1': """

Не хватает: <b>{value_0} ₽</b>.""",
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L167_1': 'Покупка не выполнена. Деньги не списаны.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L176_1': 'Продление выполнено',
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L316_1': 'Операция устарела. Выберите тариф заново.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L38_1': 'Котировка не найдена. Выберите тариф ещё раз.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L39_1': 'Цена устарела. Выберите тариф ещё раз.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L40_1': 'Эта покупка больше не активна.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L41_1': 'Тариф больше недоступен.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L42_1': 'Цена тарифа изменилась. Проверьте новую цену.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L43_1': 'Цена изменилась. Выберите тариф ещё раз.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L44_1': 'Состояние подписки изменилось. Начните операцию заново.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L45_1': 'На балансе недостаточно средств.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L46_1': 'Покупки временно заблокированы из-за финансового спора.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L47_1': 'Покупки недоступны до погашения задолженности.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L48_1': 'Сначала удалите лишние устройства.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L79_1': 'Не удалось открыть покупку.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L90_1': 'Продление',
    'RUNTIME_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L92_1': """✅ <b>Подтверждение: {value_0}</b>

Тариф: <b>{value_1}</b>
Срок: <b>{value_2} дней</b>
Лимит устройств: <b>{value_3}</b>
Цена: <b>{value_4} ₽</b>

Баланс до покупки: <b>{value_5} ₽</b>
Баланс после покупки: <b>{value_6} ₽</b>""",
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L197_1': 'У вас уже подключён этот тариф. Для добавления дней используйте продление.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L201_1': 'Смена тарифа заблокирована из-за финансового спора.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L204_1': 'Смена тарифа недоступна до погашения задолженности.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L207_1': 'Не удалось надёжно рассчитать остаток подписки. Обратитесь в поддержку.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L216_1': 'Не удалось подготовить смену тарифа. Попробуйте ещё раз.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L233_1': """
⚠️ Не хватает: <b>{value_0} ₽</b>""",
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L274_1': 'Покупки временно заблокированы из-за открытого финансового спора.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L275_1': 'Покупки недоступны до погашения задолженности.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L276_1': 'Для этого варианта используйте раздел «Сменить тариф».',
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L277_1': 'Сначала завершите или отмените смену тарифа.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L282_1': 'Не удалось подготовить покупку. Попробуйте ещё раз.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L292_1': """
⚠️ Не хватает: <b>{value_0} ₽</b>""",
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L297_1': """💳 <b>Оформление заказа</b>

📦 Тариф: <b>{value_0}</b>
⏱ Срок: {value_1} дней
🔌 Устройства: до {value_2}
💰 Цена: <b>{value_3} ₽</b>

Баланс: <b>{value_4} ₽</b>
После покупки: <b>{value_5} ₽</b>{value_6}

Покупка выполняется только после отдельного подтверждения.""",
    'RUNTIME_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L46_1': '{value_0} дн.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L104_1': """

Не хватает {value_0} ₽. Минимальное пополнение — {value_1} ₽; после смены останется {value_2} ₽.""",
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L109_1': """

Не хватает: <b>{value_0} ₽</b>.""",
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L167_1': 'Смена тарифа не выполнена. Деньги не списаны.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L36_1': 'Котировка не найдена. Выберите тариф ещё раз.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L37_1': 'Расчёт устарел. Выберите тариф ещё раз.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L38_1': 'Эта смена тарифа больше не активна.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L39_1': 'Выбранный тариф больше недоступен.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L40_1': 'Цена тарифа изменилась. Проверьте новый расчёт.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L41_1': 'Подписка изменилась. Создайте новый расчёт.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L42_1': 'Экономика подписки изменилась. Создайте новый расчёт.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L43_1': 'Состояние подписки изменилось. Начните заново.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L44_1': 'Не удалось надёжно рассчитать остаток подписки.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L45_1': 'На балансе недостаточно средств.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L46_1': 'Смена тарифа заблокирована из-за финансового спора.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L47_1': 'Смена тарифа недоступна до погашения задолженности.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L48_1': 'Сначала удалите лишние устройства.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L61_1': '{value_0} дн.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L80_1': 'Не удалось открыть смену тарифа.',
    'RUNTIME_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L90_1': """✅ <b>Подтверждение смены тарифа</b>

Новый тариф: <b>{value_0}</b>
Лимит устройств: <b>{value_1}</b>
Срок после конвертации: <b>{value_2}</b>
Доплата: <b>{value_3} ₽</b>

Баланс до операции: <b>{value_4} ₽</b>
Баланс после операции: <b>{value_5} ₽</b>""",
    'RUNTIME_BOT_KEYBOARDS_PAYMENT_L94_1': ' 🔽',
    'RUNTIME_BOT_KEYBOARDS_PAYMENT_L96_1': ' ✅',
    'RUNTIME_BOT_KEYBOARDS_PAYMENT_L98_1': ' 🔼',
    'RUNTIME_SERVICES_WORKERS_PAYMENTS_L139_1': '🧪',
    'RUNTIME_SERVICES_WORKERS_PAYMENTS_L141_1': '⏳',
    'RUNTIME_SERVICES_WORKERS_PAYMENTS_L147_1': '—',
    'TOPUP_ALREADY_FINISHED_ALERT': 'Пополнение уже завершено',
    'TOPUP_CHECKING_ALERT': 'Проверяем…',
    'TOPUP_CONFIRMED_NOTICE': '✅ Оплата подтверждена. Зачисляем деньги на баланс.',
    'TOPUP_CREATING_ALERT': 'Создаём ссылку…',
    'TOPUP_CREDITED_NOTICE': '✅ Баланс пополнен.',
    'TOPUP_CUSTOM_AMOUNT_PROMPT': """Введите сумму пополнения целым числом от {minimum} до {maximum} ₽.
Например: <code>499</code>""",
    'TOPUP_ERROR_BALANCE_LIMIT': 'Сумма превышает допустимый лимит баланса с учётом активных ссылок.',
    'TOPUP_ERROR_BANNED': 'Пополнение недоступно для этого аккаунта.',
    'TOPUP_ERROR_BLOCKED': 'Новые пополнения временно заблокированы. Обратитесь в поддержку.',
    'TOPUP_ERROR_MAXIMUM': 'Максимальная сумма одного пополнения — {maximum} ₽.',
    'TOPUP_ERROR_MINIMUM': 'Минимальная сумма пополнения — {minimum} ₽.',
    'TOPUP_ERROR_UNFINISHED': 'У вас уже есть {limit} незавершённых пополнения. Проверьте их статус или закройте ненужную ссылку.',
    'TOPUP_ERROR_WHOLE_RUBLES': 'Введите сумму целыми рублями без копеек.',
    'TOPUP_HIDE_NOTICE': 'Ссылка закрыта и больше не показывается. Если платёж уже завершён, деньги всё равно поступят на баланс.',
    'TOPUP_INVALID_AMOUNT': 'Введите целую сумму без копеек, пробелов и знаков. Например: <code>499</code>',
    'TOPUP_MISSING_NOTICE': 'Активная ссылка пополнения не найдена.',
    'TOPUP_NOT_FOUND_ALERT': 'Пополнение не найдено',
    'TOPUP_OPERATION_MINIMUM': 'Для выбранной операции нужно пополнить минимум на <b>{minimum} ₽</b>.',
    'TOPUP_PROVIDER_CANCELLED_NOTICE': 'Пополнение отменено платёжным провайдером.',
    'TOPUP_SAVED_NOTICE': 'Ссылка сохранена. Вы можете вернуться к пополнению позже.',
    'UI_BOT_HANDLERS_PAYMENT_BALANCE_ROUTES_L246_1': 'Аккаунт не найден',
    'UI_BOT_HANDLERS_PAYMENT_COMMON_L125_1': '🔄 Продлить подписку',
    'UI_BOT_HANDLERS_PAYMENT_COMMON_L128_1': '⚙️ Сменить тариф',
    'UI_BOT_HANDLERS_PAYMENT_COMMON_L131_1': '🏠 В главное меню',
    'UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L135_1': 'Некорректная покупка',
    'UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L147_1': 'Проводим покупку…',
    'UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L180_1': """🎉 <b>{value_0}</b>

Тариф: <b>{value_1}</b>
Срок: {value_2} дней
Списано: <b>{value_3} ₽</b>
💰 Баланс: <b>{value_4} ₽</b>
🎁 Бонусный баланс: <b>{value_5} ₽</b>""",
    'UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L209_1': 'Создаём ссылку…',
    'UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L218_1': 'Котировка устарела',
    'UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L251_1': 'Котировка устарела',
    'UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L263_1': 'Введите целую сумму от <b>{value_0} ₽</b> до 5000 ₽.',
    'UI_BOT_HANDLERS_PAYMENT_PURCHASE_ROUTES_L300_1': 'Операция устарела. Выберите тариф заново.',
    'UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L119_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L125_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L165_1': 'У вас уже подключён этот тариф. Для добавления дней используйте продление.',
    'UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L245_1': """💱 <b>Смена тарифа</b>

Новый тариф: <b>{value_0}</b>
Лимит устройств: <b>{value_1}</b>
Срок после конвертации: <b>{value_2}</b>
Доплата: <b>{value_3} ₽</b>

Баланс: <b>{value_4} ₽</b>
После смены: <b>{value_5} ₽</b>{value_6}

Остаточная стоимость подписки используется только в этом расчёте и не зачисляется на свободный баланс.""",
    'UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L458_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L464_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_PAYMENT_SHOWCASE_ROUTES_L50_1': '🚀 Начать',
    'UI_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L133_1': 'Некорректная операция',
    'UI_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L145_1': 'Меняем тариф…',
    'UI_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L180_1': """🎉 <b>Тариф изменён</b>

Новый тариф: <b>{value_0}</b>
Срок: {value_1}
Списано: <b>{value_2} ₽</b>
💰 Баланс: <b>{value_3} ₽</b>
🎁 Бонусный баланс: <b>{value_4} ₽</b>""",
    'UI_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L210_1': 'Создаём ссылку…',
    'UI_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L219_1': 'Котировка устарела',
    'UI_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L252_1': 'Котировка устарела',
    'UI_BOT_HANDLERS_PAYMENT_TARIFF_CHANGE_ROUTES_L264_1': 'Введите целую сумму от <b>{value_0} ₽</b> до 5000 ₽.',
    'UI_BOT_KEYBOARDS_PAYMENT_L113_1': '🔌 Подключить устройство',
    'UI_BOT_KEYBOARDS_PAYMENT_L116_1': '⏳ К подписке',
    'UI_BOT_KEYBOARDS_PAYMENT_L150_1': '{value_0} ₽',
    'UI_BOT_KEYBOARDS_PAYMENT_L206_1': '💰 Купить с баланса',
    'UI_BOT_KEYBOARDS_PAYMENT_L219_1': '✅ Подтвердить покупку',
    'UI_BOT_KEYBOARDS_PAYMENT_L232_1': '💱 Сменить тариф с баланса',
    'UI_BOT_KEYBOARDS_PAYMENT_L245_1': '✅ Подтвердить смену тарифа',
    'UI_BOT_KEYBOARDS_PAYMENT_L256_1': '🔄 Перейти к продлению',
    'UI_BOT_KEYBOARDS_PAYMENT_L269_1': 'Пополнить на {value_0} ₽',
    'UI_BOT_KEYBOARDS_PAYMENT_L273_1': 'Указать другую сумму',
    'UI_BOT_KEYBOARDS_PAYMENT_L286_1': 'Пополнить на {value_0} ₽',
    'UI_BOT_KEYBOARDS_PAYMENT_L290_1': 'Указать другую сумму',
    'UI_BOT_KEYBOARDS_PAYMENT_L304_1': 'Вернуться к покупке',
    'UI_BOT_KEYBOARDS_PAYMENT_L307_1': '💰 К балансу',
    'UI_BOT_KEYBOARDS_PAYMENT_L52_1': '← К выбору тарифа',
    'WORD_PURCHASE': 'Покупка',
    'BTN_ISTORIYA_POPOLNENIJ': '🧾 История пополнений',
    'BTN_ISTORIYA_OPERATSIJ': '📊 История операций',
    'BTN_NAZAD': '◀️ Назад',
    'BTN_VPERED': 'Вперед ▶️',
    'BTN_ZAKRYT_NEZAVERSHYONNYE_SSYLKI': '❌ Закрыть незавершённые ссылки',
    'BTN_PROCHITANO': '✅ Прочитано',
}
