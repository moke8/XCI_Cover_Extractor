#!/usr/bin/env python3
"""Nintendo GameCube (NGC) - 平台模块"""

import struct
import re
from pathlib import Path

PLATFORM_TITLE = "Nintendo GameCube"
CONFIG_FILENAME = "ngc_config.json"
TGDB_PLATFORM_ID = 2

COLLECTION_DEFAULTS = {
    'collection': 'Nintendo GameCube',
    'shortname': 'gc',
    'extensions': 'iso gcz rvz',
}


# ===== NGC ROM 解析 =====

def _read_disc_header(f, offset=0):
    """读取 GC disc header，返回 (game_id, title)"""
    f.seek(offset)
    header = f.read(0x60)
    if len(header) < 0x60:
        return None, None
    game_id = header[0:6].decode('ascii', errors='replace').strip('\x00')
    title_raw = header[0x20:0x60].split(b'\x00', 1)[0]
    for enc in ('utf-8', 'gbk', 'shift_jis'):
        try:
            title = title_raw.decode(enc).strip()
            if title:
                return game_id, title
        except (UnicodeDecodeError, ValueError):
            continue
    title = title_raw.decode('utf-8', errors='ignore').strip()
    return game_id, title or game_id


def _read_gcz_disc_header(f):
    """读取 GCZ 容器格式，解压第一个块获取 disc header"""
    import zlib
    f.seek(0)
    magic = f.read(4)
    if magic != b'\x01\xc0\x0b\xb1':
        return _read_disc_header(f, 0)
    f.seek(4)
    _sub_type = struct.unpack('<I', f.read(4))[0]
    _compressed_size = struct.unpack('<Q', f.read(8))[0]
    _data_size = struct.unpack('<Q', f.read(8))[0]
    _block_size = struct.unpack('<I', f.read(4))[0]
    num_blocks = struct.unpack('<I', f.read(4))[0]
    data_start = 32 + num_blocks * 8 + num_blocks * 4
    f.seek(32)
    ptr0 = struct.unpack('<Q', f.read(8))[0]
    ptr1 = struct.unpack('<Q', f.read(8))[0]
    block_len = ptr1 - ptr0
    f.seek(data_start + ptr0)
    compressed = f.read(block_len)
    dec = zlib.decompress(compressed)
    game_id = dec[0:6].decode('ascii', errors='replace').strip('\x00')
    title_raw = dec[0x20:0x60].split(b'\x00', 1)[0]
    for enc in ('utf-8', 'gbk', 'shift_jis'):
        try:
            title = title_raw.decode(enc).strip()
            if title:
                return game_id, title
        except (UnicodeDecodeError, ValueError):
            continue
    title = title_raw.decode('utf-8', errors='ignore').strip()
    return game_id, title or game_id


def extract_ngc_info(file_path, lang_code='en', log=print):
    """从 NGC ROM 提取游戏 ID 和标题"""
    p = Path(file_path)
    suffix = p.suffix.lower()
    try:
        with open(file_path, 'rb') as f:
            if suffix == '.iso':
                game_id, title = _read_disc_header(f, 0)
            elif suffix == '.gcz':
                game_id, title = _read_gcz_disc_header(f)
            elif suffix == '.rvz':
                game_id, title = _read_disc_header(f, 0x58)
            else:
                return None

            if not game_id:
                return None
            if not title:
                title = p.stem

        return {
            'title': title,
            'title_en': title,
            'game_id': game_id,
            'publisher': '',
            'filename': p.name,
        }
    except Exception as e:
        log(f"[游戏解析] GameCube 解析错误: {e}")
        return None
