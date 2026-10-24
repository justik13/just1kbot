"""Domain texts for runtime/notifications.py."""
from __future__ import annotations

BALANCE_TOPUP_CREDITED = """✅ <b>Баланс пополнен на +{amount} ₽!</b>

💰 Баланс: <b>{real_balance} ₽</b>
🎁 Бонусный баланс: <b>{bonus_balance} ₽</b>{resume_hint}{welcome_bonus}"""

BALANCE_TOPUP_RESUME_HINT = """

💡 Нажмите кнопку ниже, чтобы завершить начатую операцию."""

BALANCE_TOPUP_WELCOME_BONUS = """

🎁 <b>Вам начислен приветственный бонус +{bonus} ₽ за первое пополнение по приглашению!</b>"""

NOTIFY_1D = """<p>⏳ <b>Напоминание о подписке</b></p>
<blockquote>До окончания вашей подписки остался <b>1 день</b>. Продлите доступ, чтобы ваши устройства не отключились.</blockquote>
<tg-button-row align="center">
  <tg-button type="callback_data" style="success" data="payment_quick_renew">💳 Продлить доступ</tg-button>
  <tg-button type="callback_data" data="dismiss_notification">✅ Прочитано</tg-button>
</tg-button-row>"""

NOTIFY_2H = """<p>⚠️ <b>Подписка скоро закончится!</b></p>
<blockquote>До окончания подписки осталось <b>2 часа</b>. Продлите доступ прямо сейчас, чтобы связь не прервалась.</blockquote>
<tg-button-row align="center">
  <tg-button type="callback_data" style="success" data="payment_quick_renew">💳 Продлить доступ</tg-button>
  <tg-button type="callback_data" data="dismiss_notification">✅ Прочитано</tg-button>
</tg-button-row>"""

NOTIFY_3D = """<p>⏳ <b>Напоминание о подписке</b></p>
<blockquote>До окончания вашей подписки осталось <b>3 дня</b>. Продлите доступ заранее, чтобы не потерять подключение.</blockquote>
<tg-button-row align="center">
  <tg-button type="callback_data" style="success" data="payment_quick_renew">💳 Продлить доступ</tg-button>
  <tg-button type="callback_data" data="dismiss_notification">✅ Прочитано</tg-button>
</tg-button-row>"""

NOTIFY_DEVICES_DELETED = """<p>⚠️ <b>Устройства удалены</b></p>
<blockquote>Ваши устройства были удалены из-за истечения подписки. Продлите доступ, чтобы создать новые.</blockquote>
<tg-button-row align="center">
  <tg-button type="callback_data" style="success" data="menu_buy">💳 Оформить доступ</tg-button>
  <tg-button type="callback_data" data="dismiss_notification">✅ Прочитано</tg-button>
</tg-button-row>"""

NOTIFY_EXPIRED = """<p>🔴 <b>Подписка закончилась!</b></p>
<blockquote>Доступ к серверам приостановлен. До полного удаления конфигураций осталось: <b>{countdown}</b>. Продлите подписку, чтобы сохранить настройки.</blockquote>
<tg-button-row align="center">
  <tg-button type="callback_data" style="success" data="menu_buy">💳 Продлить подписку</tg-button>
  <tg-button type="callback_data" data="dismiss_notification">✅ Прочитано</tg-button>
</tg-button-row>"""

NOTIFY_GRACE_12H = """<p>🚨 <b>Скоро удаление устройств!</b></p>
<blockquote>Осталось менее <b>12 часов</b> до безвозвратного удаления конфигураций. Продлите подписку прямо сейчас, чтобы не настраивать всё заново.</blockquote>
<tg-button-row align="center">
  <tg-button type="callback_data" style="success" data="menu_buy">💳 Продлить подписку</tg-button>
  <tg-button type="callback_data" data="dismiss_notification">✅ Прочитано</tg-button>
</tg-button-row>"""



REFERRAL_ONBOARDING_NOTIFICATION = """<p>👤 <b>Новый пользователь!</b></p>
<blockquote>По вашей ссылке зарегистрировался новый пользователь. Как только он пополнит баланс, вам будет начислено вознаграждение на бонусный счёт.</blockquote>
<tg-button-row align="center">
  <tg-button type="callback_data" style="primary" data="menu_referral">👥 Партнёрская программа</tg-button>
  <tg-button type="callback_data" data="dismiss_notification">✅ Прочитано</tg-button>
</tg-button-row>"""

REFERRAL_BONUS_ACCREDITED = """<p>🎉 <b>Партнёрское вознаграждение!</b></p>
<blockquote>Ваш приглашённый пользователь пополнил баланс. Вам зачислено <b>+{bonus} ₽</b> бонусов на баланс.</blockquote>
<tg-button-row align="center">
  <tg-button type="callback_data" style="primary" data="menu_balance">💰 Мой баланс</tg-button>
  <tg-button type="callback_data" data="dismiss_notification">✅ Прочитано</tg-button>
</tg-button-row>"""

REFERRAL_BONUS_ACCREDITED_DETAILED = """<p>🎉 <b>Партнёрское вознаграждение!</b></p>
<table>
  <tr><td>Начислено:</td><td><b>+{bonus} ₽</b> (ставка {rate_pct}%)</td></tr>
  <tr><td>Бонусный баланс:</td><td><b>{bonus_balance} ₽</b></td></tr>
</table>
<blockquote>Бонусы можно использовать для оплаты своих подключений.</blockquote>
<tg-button-row align="center">
  <tg-button type="callback_data" style="primary" data="menu_balance">💰 Мой баланс</tg-button>
  <tg-button type="callback_data" data="dismiss_notification">✅ Прочитано</tg-button>
</tg-button-row>"""

REFERRAL_TIER_UPGRADE_NOTIFICATION = """<p>🎖 <b>Повышение партнерского статуса!</b></p>
<table>
  <tr><td>Новый статус:</td><td><b>{tier_name}</b></td></tr>
  <tr><td>Новая ставка:</td><td><b>{rate_pct}%</b></td></tr>
</table>
<blockquote>Приглашайте больше друзей и получайте ещё больше бонусов.</blockquote>
<tg-button-row align="center">
  <tg-button type="callback_data" style="primary" data="menu_referral">👥 Партнёрская программа</tg-button>
  <tg-button type="callback_data" data="dismiss_notification">✅ Прочитано</tg-button>
</tg-button-row>"""

REFERRAL_BONUS_WITH_UPGRADE_NOTIFICATION = """<p>🎉 <b>Партнёрское вознаграждение и новый уровень!</b></p>
<table>
  <tr><td>Начислено:</td><td><b>+{bonus} ₽</b> (ставка {rate_pct}%)</td></tr>
  <tr><td>Бонусный баланс:</td><td><b>{bonus_balance} ₽</b></td></tr>
  <tr><td>Новый статус:</td><td>🎖 <b>{tier_name}</b> ({new_rate_pct}%)</td></tr>
</table>
<blockquote>Бонусы можно использовать для оплаты своих подключений.</blockquote>
<tg-button-row align="center">
  <tg-button type="callback_data" style="primary" data="menu_balance">💰 Мой баланс</tg-button>
  <tg-button type="callback_data" data="dismiss_notification">✅ Прочитано</tg-button>
</tg-button-row>"""

TIME_DAYS_FORMAT = "{days} дн."

TIME_DAYS_HOURS_FORMAT = "{days} дн. {hours} ч."

TIME_HOURS_MINUTES_FORMAT = "{hours} ч. {minutes} мин."

TIME_SOON_LABEL = "в ближайшее время"

NOTIFY_WI_3D = """<p>⏳ <b>Белый Интернет: осталось 3 дня</b></p>
<blockquote>Подписка действует до {date}. Вы можете продлить её в любой момент.</blockquote>
<tg-button-row align="center">
  <tg-button type="callback_data" style="primary" data="white_internet">⚪️ Белый Интернет</tg-button>
  <tg-button type="callback_data" data="dismiss_notification">✅ Прочитано</tg-button>
</tg-button-row>"""

NOTIFY_WI_1D = """<p>⏳ <b>Белый Интернет: остался 1 день</b></p>
<blockquote>Подписка истекает через {countdown}. Рекомендуем продлить её заранее.</blockquote>
<tg-button-row align="center">
  <tg-button type="callback_data" style="primary" data="white_internet">⚪️ Белый Интернет</tg-button>
  <tg-button type="callback_data" data="dismiss_notification">✅ Прочитано</tg-button>
</tg-button-row>"""

NOTIFY_WI_2H = """<p>⏳ <b>Белый Интернет: осталось менее 2 часов</b></p>
<blockquote>Подписка истекает через {countdown}. Продлите её заранее, чтобы оставаться на связи.</blockquote>
<tg-button-row align="center">
  <tg-button type="callback_data" style="primary" data="white_internet">⚪️ Белый Интернет</tg-button>
  <tg-button type="callback_data" data="dismiss_notification">✅ Прочитано</tg-button>
</tg-button-row>"""

NOTIFY_WI_EXPIRED = """<p>🔴 <b>Подписка «Белый Интернет» истекла</b></p>
<blockquote>Доступ временно приостановлен. Чтобы возобновить подключение, продлите подписку.</blockquote>
<tg-button-row align="center">
  <tg-button type="callback_data" style="primary" data="white_internet">⚪️ Белый Интернет</tg-button>
  <tg-button type="callback_data" data="dismiss_notification">✅ Прочитано</tg-button>
</tg-button-row>"""
BTN_EXTEND_WHITE_INTERNET = "⚪️ Продлить Белый Интернет"
