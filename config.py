#!/usr/bin/env python3
"""配置管理：路径常量、配置读写、语言列表"""

import json
import sys
import locale
from pathlib import Path


_APP_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
GLOBAL_CONFIG_FILE = _APP_DIR / 'config.json'


def _platform_config_path(filename):
    return _APP_DIR / filename


def load_json_config(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_json_config(path, data):
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass


LANGUAGES = [
    ('日本語', 'ja', 'ja', [0]),
    ('English (US)', 'en', 'en', [1, 12]),
    ('Français', 'fr', 'fr', [2, 13]),
    ('Deutsch', 'de', 'de', [3]),
    ('Italiano', 'it', 'it', [4]),
    ('Español', 'es', 'es', [5, 14]),
    ('Nederlands', 'nl', 'nl', [6]),
    ('Português', 'pt', 'pt', [7]),
    ('Русский', 'ru', 'ru', [8]),
    ('한국어', 'ko', 'ko', [9]),
    ('简体中文', 'zh_CN', 'zh-CN', [11, 10]),
    ('繁體中文', 'zh_TW', 'zh-TW', [10, 11]),
    ('English (UK)', 'en_GB', 'en', [12, 1]),
    ('Français (CA)', 'fr_CA', 'fr', [13, 2]),
    ('Español (LA)', 'es_LA', 'es', [14, 5]),
    ('Português (BR)', 'pt_BR', 'pt', [7]),
]


def _get_default_lang_index():
    try:
        loc = locale.getdefaultlocale()[0] or ''
    except Exception:
        loc = ''
    loc = loc.lower()
    for i, (_, code, _, _) in enumerate(LANGUAGES):
        if code.lower() == loc:
            return i
    prefix = loc.split('_')[0]
    for i, (_, code, _, _) in enumerate(LANGUAGES):
        if code.split('_')[0].lower() == prefix:
            return i
    return 1
