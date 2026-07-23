#!/usr/bin/env python3
"""Game Boy Advance (GBA) - 平台模块"""

import re
from pathlib import Path

from gba_game_db import GBA_GAME_DB

PLATFORM_TITLE = "Game Boy Advance"
CONFIG_FILENAME = "gba_config.json"
TGDB_PLATFORM_ID = 5

COLLECTION_DEFAULTS = {
    'collection': 'Game Boy Advance',
    'shortname': 'gba',
    'extensions': 'gba agb mb bin',
}

GBA_REGION_MAP = {
    'J': 'Japan',
    'E': 'USA',
    'P': 'Europe',
    'D': 'Germany',
    'F': 'France',
    'I': 'Italy',
    'S': 'Spain',
    'K': 'Korea',
    'C': 'China',
}


def _clean_title(raw_name):
    """从 No-Intro 全名中提取干净的游戏标题，去掉区域/语言后缀"""
    name = re.sub(r'\s*\(.*?\)', '', raw_name).strip()
    return name


def extract_gba_info(file_path, lang_code='en', log=print):
    try:
        with open(file_path, 'rb') as f:
            header = f.read(0xC0)
            if len(header) < 0xC0:
                log("[游戏解析] GBA 文件头不完整")
                return None

            game_title = header[0x0A0:0x0AC].decode('ascii', errors='ignore').strip('\x00').strip()
            game_code = header[0x0AC:0x0B0].decode('ascii', errors='ignore').strip('\x00').strip()
            maker_code = header[0x0B0:0x0B2].decode('ascii', errors='ignore').strip('\x00').strip()

            fixed_val = header[0x0B2]
            if fixed_val != 0x96:
                log("[游戏解析] 不是有效的 GBA ROM，固定字节校验失败")
                return None

            db_name = GBA_GAME_DB.get(game_code)
            if db_name:
                title = _clean_title(db_name)
            else:
                title = game_title or Path(file_path).stem

            region_char = game_code[3] if len(game_code) >= 4 else ''
            region = GBA_REGION_MAP.get(region_char, '')

        return {
            'title': title,
            'title_en': title,
            'publisher': maker_code,
            'game_code': game_code,
            'filename': Path(file_path).name,
            'icon_data': None,
            'region': region,
        }
    except Exception as e:
        log(f"[游戏解析] GBA 解析错误: {e}")
        return None
