import re
import os

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

print("Missing keys:")
for key in sorted(used_keys):
    if key not in defined_keys:
        print(key)
