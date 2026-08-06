#!/bin/bash
# Декодирует URL-encoded переменные в .env для использования как raw значений

python3 -c "
import urllib.parse
import sys

with open('.env', 'r') as f:
    lines = f.readlines()

with open('.env', 'w') as f:
    for line in lines:
        if '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            if 'PASSWORD' in key and 'URL_DECODED' not in key:
                decoded = urllib.parse.unquote(value.strip())
                f.write(f'{key}_URL_DECODED={decoded}\n')
        f.write(line)
"
