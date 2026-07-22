#!/usr/bin/env python3
"""从 No-Intro GBA DAT XML 提取 serial → game_name 映射，生成 Python 字典文件"""

import xml.etree.ElementTree as ET

INPUT = "Nintendo - Game Boy Advance.xml"
OUTPUT = "gba_game_db.py"

with open(INPUT, 'r', encoding='utf-8') as f:
    content = f.read()
if content.startswith('<?xml'):
    content = content.split('?>', 1)[1]
content = f'<root>{content}</root>'
root = ET.fromstring(content)

serial_map = {}
for game in root.iter('game'):
    name = game.get('name', '')
    if not name:
        continue
    for f in game.iter('file'):
        serial = f.get('serial', '')
        if serial and len(serial) == 4 and serial not in serial_map:
            serial_map[serial] = name

print(f"共提取 {len(serial_map)} 个 serial → name 映射")

with open(OUTPUT, 'w', encoding='utf-8') as out:
    out.write('#!/usr/bin/env python3\n')
    out.write('"""GBA game_code(serial) → 完整游戏名 映射表 (自动生成, 勿手动编辑)"""\n\n')
    out.write(f'# 来源: No-Intro "Nintendo - Game Boy Advance" DAT\n')
    out.write(f'# 条目数: {len(serial_map)}\n\n')
    out.write('GBA_GAME_DB = {\n')
    for serial, name in sorted(serial_map.items()):
        escaped = name.replace('\\', '\\\\').replace("'", "\\'")
        out.write(f"    '{serial}': '{escaped}',\n")
    out.write('}\n')

print(f"已写入 {OUTPUT}")
