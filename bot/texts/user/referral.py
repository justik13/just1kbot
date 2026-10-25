"""Domain texts for user/referral.py."""
from __future__ import annotations

def mask_telegram_id(telegram_id: int | str) -> str:
    """Mask Telegram ID showing stars in the middle, e.g. 8141287721 -> 814***21."""
    s = str(telegram_id).strip()
    if len(s) <= 4:
        return f"{s[:1]}***{s[-1:]}" if len(s) > 1 else "***"
    return f"{s[:3]}***{s[-2:]}"


BTN_LEADERBOARD_ANONYMOUS_OFF = "🌐 Анонимность: ВЫКЛ"
BTN_LEADERBOARD_ANONYMOUS_ON = "🔒 Анонимность: ВКЛ"
BTN_REFERRAL_LEADERBOARD = "🏆 Топ-5 лидеров"
LEADERBOARD_ANONYMITY_TOGGLED = "Режим отображения в рейтинге изменен"

REFERRAL_DISCOUNT_BADGE = "\n🎁 Скидка 25% по пригласительной ссылке: <b>-{discount} ₽</b> (вместо <s>{original_price} ₽</s>)\n<i>Скидка действует на первый заказ тарифа. Пополнение баланса на неё не влияет. Продление будет по обычной цене ({original_price} ₽).</i>"

REFERRAL_LEADERBOARD_EMPTY = """<i>В топе пока пусто.

Пригласите пользователей по ссылке, чтобы занять первое место.</i>"""

REFERRAL_LEADERBOARD_ITEM = "{pos}. {medal}{user} — <b>{count}</b> {noun}"

REFERRAL_LEADERBOARD_MEDALS: dict[int, str] = {1: "🥇 ", 2: "🥈 ", 3: "🥉 "}

REFERRAL_LEADERBOARD_NOT_RANKED = "\n➖➖➖➖➖➖➖➖\nВаше место: <b>вне рейтинга</b> (нет активных приглашённых)"

REFERRAL_LEADERBOARD_TITLE = "🏆 <b>Топ-5 лидеров</b>\n"

REFERRAL_LEADERBOARD_USER_OPEN = "<b>{name}</b>"

REFERRAL_LEADERBOARD_USER_OPEN_YOU = "<b>{name}</b> (Вы)"

REFERRAL_LEADERBOARD_USER_OTHER = "<code>{masked}</code>"

REFERRAL_LEADERBOARD_USER_YOU = "<code>{masked}</code> (Вы)"

REFERRAL_LEADERBOARD_YOUR_RANK = "\n➖➖➖➖➖➖➖➖\nВаше место: <b>#{rank}</b> ({count} {noun})"

REFERRAL_LIST_EMPTY = """<i>Список рефералов пока пуст.</i>

Пригласите пользователей по вашей ссылке, чтобы они появились здесь."""

REFERRAL_LIST_FOOTER = """
Всего приглашено: {count}"""

REFERRAL_LIST_HEADER = """👥 <b>Ваши рефералы</b>
"""

REFERRAL_LIST_ITEM_FORMAT = "\n{idx}. <b>{user}</b> ({date})"

REFERRAL_MAIN_RANK_NOT_RANKED = "вне рейтинга"

REFERRAL_MAIN_RANK_RANKED = "#{rank}"

REFERRAL_SHARE_TEXT = "🎁 Приглашаю в just1kbot! Получи скидку 25% на первый тариф по моей ссылке:"

REFERRAL_TEXT_BALANCE = """<p>🤝 <b>Реферальная программа</b></p>
<table>
  <tr><td>Бонусный баланс:</td><td><b>{bonus_balance} ₽ 🎁</b></td></tr>
  <tr><td>Ваш статус:</td><td><b>{tier_name} ({rate_pct}%)</b></td></tr>
  <tr><td>Приглашённых:</td><td><b>{active_count}</b> (всего {invited_count})</td></tr>
  <tr><td>Место в рейтинге:</td><td><b>{rank_line}</b></td></tr>
  {tier_progress_line}
</table>

<blockquote expandable>🎁 <b>Условия программы:</b>
• Вы получаете <b>от 15% до 30%</b> с каждой оплаты приглашённых пользователей на бонусный баланс.
• <b>Лестница статусов:</b>
  ▫️ 0–2 приглашённых — <b>15% (Standard)</b>
  ▫️ 3–6 приглашённых — <b>20% (Silver)</b>
  ▫️ 7–14 приглашённых — <b>25% (Gold)</b>
  ▫️ 15+ приглашённых — <b>30% (Platinum)</b>
• Приглашённый получает <b>скидку 25%</b> на первый тариф по вашей ссылке (пополнение баланса на скидку не влияет).

<i>Бонусы списываются при оплате и продлении подписки.</i></blockquote>

<p>🔗 <b>Ваша ссылка для приглашения:</b></p>
<code>{referral_link}</code>
<p><i>(нажмите на ссылку, чтобы скопировать)</i></p>{inviter_line}
<tg-button-row align="center">
  <tg-button type="url" style="primary" url="{share_url}">{share_btn_text}</tg-button>
</tg-button-row>
<tg-button-row align="center">
  <tg-button type="callback_data" style="primary" data="referrals_list">👥 Мои рефералы ({invited_count})</tg-button>
  <tg-button type="callback_data" style="primary" data="referral_leaderboard">🏆 Топ-5 лидеров</tg-button>
</tg-button-row>"""
REFERRAL_TIER_PROGRESS_NEXT = "<tr><td>До статуса {next_tier_name} ({next_rate_pct}%):</td><td><b>ещё {needed_count}</b></td></tr>"

REFERRAL_TIER_PROGRESS_MAX = "<tr><td>Прогресс:</td><td><b>🏆 Максимальный</b></td></tr>"
REFERRAL_PAYMENTS_TABLE_TEMPLATE = "<table><tr><th>Статус</th><th>Дата</th><th>Сумма</th></tr>{rows}</table>"
REFERRAL_LEADERBOARD_TABLE_TEMPLATE = "<table><tr><th>#</th><th>Участник</th><th>Приглашено</th></tr>{rows}</table>"

