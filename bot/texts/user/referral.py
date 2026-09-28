"""Domain texts for user/referral.py."""
from __future__ import annotations

def mask_telegram_id(telegram_id: int | str) -> str:
    """Mask Telegram ID showing stars in the middle, e.g. 8141287721 -> 814***21."""
    s = str(telegram_id).strip()
    if len(s) <= 4:
        return f"{s[:1]}***{s[-1:]}" if len(s) > 1 else "***"
    return f"{s[:3]}***{s[-2:]}"


BTN_REFERRAL_LEADERBOARD = "🏆 Топ лидеров"

REFERRAL_DISCOUNT_BADGE = "\n🎁 Скидка 25% на первый заказ: <b>-{discount} ₽</b>"

REFERRAL_LEADERBOARD_EMPTY = """<i>Рейтинг пока формируется.

Приглашайте друзей по вашей ссылке и станьте первым в списке лидеров!</i>"""

REFERRAL_LEADERBOARD_ITEM = "{pos}. {medal}{user} — <b>{count}</b> {noun}"

REFERRAL_LEADERBOARD_MEDALS: dict[int, str] = {1: "🥇 ", 2: "🥈 ", 3: "🥉 "}

REFERRAL_LEADERBOARD_NOT_RANKED = "\n➖➖➖➖➖➖➖➖\nВаше место: <b>пока не в рейтинге</b> (0 активных друзей)"

REFERRAL_LEADERBOARD_TITLE = "🏆 <b>Топ лидеров реферальной программы</b>\n"

REFERRAL_LEADERBOARD_USER_OTHER = "<code>{masked}</code>"

REFERRAL_LEADERBOARD_USER_YOU = "<b>{masked}</b> (Вы)"

REFERRAL_LEADERBOARD_YOUR_RANK = "\n➖➖➖➖➖➖➖➖\nВаше место в рейтинге: <b>#{rank}</b> ({count} {noun})"

REFERRAL_LIST_EMPTY = """<i>Список рефералов пока пуст.</i>

Пригласите друзей по вашей ссылке, чтобы они появились здесь."""

REFERRAL_LIST_FOOTER = """
Всего приглашено: {count}"""

REFERRAL_LIST_HEADER = """👥 <b>Ваши рефералы</b>
"""

REFERRAL_LIST_ITEM_FORMAT = "\n{idx}. <b>{user}</b> ({date})"

REFERRAL_SHARE_TEXT = "🎁 Приглашаю в just1kbot! Получи скидку 25% на первую покупку по моей ссылке:"

REFERRAL_TEXT_BALANCE = """🤝 <b>Реферальная программа</b>

💰 Бонусный баланс: <b>{bonus_balance} ₽</b>
🎖 Ваш уровень: <b>{tier_name} ({rate_pct}%)</b>
👥 Активных друзей: <b>{active_count}</b> (всего приглашено: {invited_count}){tier_progress_line}

🎁 <b>Условия программы:</b>
• Вы получаете <b>от 15% до 30%</b> с каждой оплаты приглашённых друзей на бонусный баланс.
• <b>Лестница уровней:</b>
  ▫️ 0–4 друга — <b>15%</b> (Старт)
  ▫️ 5–9 друзей — <b>20%</b> (Активист)
  ▫️ 10–14 друзей — <b>25%</b> (Мастер)
  ▫️ 15+ друзей — <b>30%</b> (Амбассадор)
• Ваш друг получает <b>скидку 25%</b> на первую покупку любого тарифа по вашей ссылке.

<i>💡 Бонусы автоматически используются для оплаты и продления подписки.</i>

🔗 <b>Ваша ссылка для приглашения:</b>
<code>{referral_link}</code>{inviter_line}"""

REFERRAL_TIER_PROGRESS_NEXT = "\n🚀 До уровня <b>{next_tier_name} ({next_rate_pct}%)</b>: ещё {needed_count}"

REFERRAL_TIER_PROGRESS_MAX = "\n🏆 <b>Достигнут максимальный уровень!</b>"

REFERRAL_TOPUP_NOTIFY_TEMPLATE = '🎉 <b>Ваш реферал пополнил баланс!</b>\n\nВам зачислено <b>+{amount} ₽</b> бонусов на баланс.'
