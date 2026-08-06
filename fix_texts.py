import re
import os
import json

used_keys = set()
for root, _, files in os.walk("d:/just1kbot/bot"):
    for file in files:
        if file.endswith(".py"):
            with open(os.path.join(root, file), "r", encoding="utf-8") as f:
                content = f.read()
                used_keys.update(re.findall(r"texts\.([A-Z0-9_]+)", content))

defined_keys = set()
for file in os.listdir("d:/just1kbot/bot/texts_data"):
    if file.endswith(".py"):
        with open(os.path.join("d:/just1kbot/bot/texts_data", file), "r", encoding="utf-8") as f:
            content = f.read()
            defined_keys.update(re.findall(r"'([A-Z0-9_]+)'\s*:", content))
            defined_keys.update(re.findall(r'"([A-Z0-9_]+)"\s*:', content))
            defined_keys.update(re.findall(r'([A-Z0-9_]+)\s*=', content))

missing_keys = sorted([k for k in used_keys if k not in defined_keys])

with open("d:/just1kbot/bot/texts_data/overrides.py", "a", encoding="utf-8") as f:
    for key in missing_keys:
        if key == "PAYMENT_STATUS_NAMES":
            f.write("\nOVERRIDES['PAYMENT_STATUS_NAMES'] = {'completed': 'Выполнен', 'cancelled': 'Отменен', 'failed': 'Ошибка', 'refunded': 'Возврат', 'requires_manual_review': 'Ручная проверка', 'pending': 'Ожидание', 'paid_processing': 'Обработка'}\n")
        else:
            f.write(f"\nOVERRIDES['{key}'] = '[MISSING TEXT: {key}]'\n")

print(f"Fixed {len(missing_keys)} texts!")
