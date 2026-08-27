from bot import texts

def get_tariff_display_name(device_limit: int) -> str:
    if device_limit <= 2:
        return texts.UI_TARIFF_NAMES_BAZOVYY_4
    elif device_limit <= 5:
        return texts.UI_TARIFF_NAMES_SEMEYNYY_6
    else:
        return "🚀 Pro"


def get_tariff_group_name(device_limit: int) -> str:
    if device_limit <= 2:
        return texts.UI_TARIFF_NAMES_BAZOVYY_2_USTR_13
    elif device_limit <= 5:
        return texts.UI_TARIFF_NAMES_SEMEYNYY_5_USTR_15
    else:
        return texts.UI_TARIFF_NAMES_PRO_USTR_17.format(device_limit=device_limit)
