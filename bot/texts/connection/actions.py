"""Domain texts for connection/actions.py."""
from __future__ import annotations

CONNECTION_ACTIONS_DEVICE_DELETE_IDET_AVTOMATICHESKOE_VOSSTANOV = "⚠️ Идёт автоматическое восстановление или действие недоступно."

CONNECTION_ACTIONS_DEVICE_RENAME_DEVICE_S_IMENEM_UZHE_SUSHC = """⚠️ Устройство с именем «<b>{safe_new_name}</b>» уже существует.

Пожалуйста, введите другое имя:"""

CONNECTION_ACTIONS_DEVICE_RENAME_DEVICE_UZHE_UDALYAETSYA_S = "🗑 Устройство уже удаляется с сервера."

CONNECTION_ACTIONS_DEVICE_RENAME_IDET_AVTOMATICHESKOE_VOSSTANOV = "⚠️ Идёт автоматическое восстановление после сбоя. Попробуйте позже."

CONNECTION_ACTIONS_DEVICE_RENAME_IMYA_NE_MOZHET_BYT_PUSTYM_VVED = "⚠️ Имя не может быть пустым. Введите корректное имя:"

CONNECTION_ACTIONS_DEVICE_RENAME_IMYA_SLISHKOM_DLINNOE_IZ_16_SI = """⚠️ Имя слишком длинное ({len_cleaned_base} из 16 символов).

Пожалуйста, введите имя покороче (максимум 16 символов):"""

CONNECTION_ACTIONS_DEVICE_RENAME_IMYA_SODERZHIT_NEDOPUSTIMYE_SI = """⚠️ Имя содержит недопустимые символы.

Разрешены только буквы, цифры, пробелы, дефисы, подчёркивания и знак #.

Попробуйте ещё раз:"""
