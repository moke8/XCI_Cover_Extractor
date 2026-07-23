#!/usr/bin/env python3
"""Nintendo DS (NDS) - 平台模块"""

import struct
from pathlib import Path

from PySide6.QtCore import Qt, QBuffer, QIODevice
from PySide6.QtGui import QImage, QColor

PLATFORM_TITLE = "Nintendo DS"
CONFIG_FILENAME = "nds_config.json"
TGDB_PLATFORM_ID = 8

COLLECTION_DEFAULTS = {
    'collection': 'Nintendo DS',
    'shortname': 'nds',
    'extensions': 'nds',
}

NDS_TITLE_OFFSETS = {
    'ja': 0x0240,
    'en': 0x0340,
    'fr': 0x0440,
    'de': 0x0540,
    'it': 0x0640,
    'es': 0x0740,
}


# ===== NDS ROM 解析 =====

def decode_nds_icon(bitmap_data, palette_data):
    palette = []
    for i in range(16):
        color16 = struct.unpack_from('<H', palette_data, i * 2)[0]
        r = (color16 & 0x1F) << 3
        g = ((color16 >> 5) & 0x1F) << 3
        b = ((color16 >> 10) & 0x1F) << 3
        a = 0 if i == 0 else 255
        palette.append((r, g, b, a))

    img = QImage(32, 32, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    for tile_y in range(4):
        for tile_x in range(4):
            tile_index = tile_y * 4 + tile_x
            tile_offset = tile_index * 32
            for row in range(8):
                for col in range(0, 8, 2):
                    byte_idx = tile_offset + row * 4 + col // 2
                    byte_val = bitmap_data[byte_idx]
                    px_x = tile_x * 8 + col
                    px_y = tile_y * 8 + row
                    idx_lo = byte_val & 0x0F
                    idx_hi = (byte_val >> 4) & 0x0F
                    r, g, b, a = palette[idx_lo]
                    img.setPixelColor(px_x, px_y, QColor(r, g, b, a))
                    r, g, b, a = palette[idx_hi]
                    img.setPixelColor(px_x + 1, px_y, QColor(r, g, b, a))

    scaled = img.scaled(128, 128, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    scaled.save(buf, "PNG")
    return bytes(buf.data())


def parse_nds_titles(icon_title_data, lang_code='en'):
    titles = {}
    publishers = {}
    for lang, offset in NDS_TITLE_OFFSETS.items():
        end = offset + 256
        if end > len(icon_title_data):
            continue
        raw = icon_title_data[offset:end]
        text = raw.decode('utf-16-le', errors='ignore').split('\x00')[0].strip()
        if not text:
            continue
        lines = text.split('\n')
        if len(lines) >= 2:
            pub = lines[-1].strip().lstrip('©').strip()
            title_lines = lines[:-1]
            titles[lang] = ' '.join(l.strip() for l in title_lines if l.strip())
            if pub:
                publishers[lang] = pub
        else:
            titles[lang] = lines[0].strip()

    lang_prefix = lang_code.split('_')[0].lower()
    if lang_prefix == 'zh':
        selected = titles.get('ja') or titles.get('en')
    else:
        selected = titles.get(lang_prefix) or titles.get('en')
    if not selected:
        selected = next(iter(titles.values()), None)

    publisher = (publishers.get('en') or publishers.get('ja')
                 or next(iter(publishers.values()), None))

    return {
        'selected': selected, 'en': titles.get('en'),
        'publisher': publisher, **titles,
    }


def extract_nds_info(nds_path, lang_code='en', log=print):
    try:
        with open(nds_path, 'rb') as f:
            header = f.read(0x200)
            if len(header) < 0x200:
                log("[游戏解析] NDS 文件头不完整")
                return None

            game_title = header[0x000:0x00C].decode(
                'ascii', errors='ignore').strip('\x00')
            game_code = header[0x00C:0x010].decode(
                'ascii', errors='ignore').strip('\x00')

            icon_offset = struct.unpack_from('<I', header, 0x068)[0]
            if icon_offset == 0:
                log("[游戏解析] NDS ROM 无图标数据")
                return None

            f.seek(icon_offset)
            icon_title_data = f.read(0x0840)
            if len(icon_title_data) < 0x0840:
                log("[游戏解析] NDS 图标数据不完整")
                return None

        bitmap_data = icon_title_data[0x0020:0x0020 + 512]
        palette_data = icon_title_data[0x0220:0x0220 + 32]
        icon_data = decode_nds_icon(bitmap_data, palette_data)

        titles = parse_nds_titles(icon_title_data, lang_code)
        title = titles.get('selected') or game_title or Path(nds_path).stem
        title_en = titles.get('en') or title

        return {
            'title': title,
            'title_en': title_en,
            'publisher': titles.get('publisher'),
            'game_code': game_code,
            'filename': Path(nds_path).name,
            'icon_data': icon_data,
        }
    except Exception as e:
        log(f"[游戏解析] NDS 解析错误: {e}")
        return None
