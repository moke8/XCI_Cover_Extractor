#!/usr/bin/env python3
"""Nintendo 3DS - 平台模块"""

import struct
import re
from pathlib import Path

PLATFORM_TITLE = "Nintendo 3DS"
CONFIG_FILENAME = "3ds_config.json"
TGDB_PLATFORM_ID = 4912

COLLECTION_DEFAULTS = {
    'collection': 'Nintendo 3DS',
    'shortname': '3ds',
    'extensions': '3ds cia cci',
}

MEDIA_UNIT = 0x200


# ===== 3DS ROM 解析 =====

def _read_ncch_info(f, ncch_offset):
    """从 NCCH header 提取 product_code 和 title_id"""
    f.seek(ncch_offset + 0x100)
    magic = f.read(4)
    if magic != b'NCCH':
        return None, None
    f.seek(ncch_offset + 0x108)
    title_id_bytes = f.read(8)
    title_id = title_id_bytes[::-1].hex().upper().lstrip('0') or '0'
    f.seek(ncch_offset + 0x150)
    product_code_raw = f.read(16)
    product_code = product_code_raw.split(b'\x00', 1)[0].decode('ascii', errors='replace').strip()
    return product_code, title_id


def extract_3ds_info(file_path, lang_code='en', log=print):
    """从 3DS ROM (.3ds/.cia) 提取游戏唯一 ID"""
    p = Path(file_path)
    suffix = p.suffix.lower()
    try:
        with open(file_path, 'rb') as f:
            if suffix in ('.3ds', '.cci'):
                f.seek(0x100)
                ncsd_magic = f.read(4)
                if ncsd_magic != b'NCSD':
                    log("[游戏解析] 跳过非有效 NCSD 文件")
                    return None
                f.seek(0x120)
                part0_data = f.read(8)
                part0_offset = struct.unpack('<I', part0_data[0:4])[0] * MEDIA_UNIT
                product_code, title_id = _read_ncch_info(f, part0_offset)
                if not product_code:
                    log("[游戏解析] 无法读取 NCCH header")
                    return None
                title = product_code
            elif suffix == '.cia':
                f.seek(0)
                cia_header = f.read(0x20)
                if len(cia_header) < 0x20:
                    return None
                header_size = struct.unpack('<I', cia_header[0x00:0x04])[0]
                cert_size = struct.unpack('<I', cia_header[0x08:0x0C])[0]
                ticket_size = struct.unpack('<I', cia_header[0x0C:0x10])[0]
                tmd_size = struct.unpack('<I', cia_header[0x10:0x14])[0]
                def align64(x):
                    return (x + 63) & ~63
                tmd_offset = align64(header_size) + align64(cert_size) + align64(ticket_size)
                f.seek(tmd_offset + 0x4C)
                title_id_bytes = f.read(8)
                if len(title_id_bytes) < 8:
                    return None
                title_id = title_id_bytes.hex().upper().lstrip('0') or '0'
                content_offset = tmd_offset + align64(tmd_size)
                product_code, _ = _read_ncch_info(f, content_offset)
                if not product_code:
                    product_code = title_id
            else:
                return None

        clean_name = re.sub(r'\s*[\[\(][^\]\)]*[\]\)]', '', p.stem).strip()
        title = clean_name or p.stem

        return {
            'title': title,
            'title_en': title,
            'product_code': product_code,
            'title_id': title_id,
            'publisher': '',
            'filename': p.name,
        }
    except Exception as e:
        log(f"[游戏解析] 3DS 解析错误: {e}")
        return None
