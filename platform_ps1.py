#!/usr/bin/env python3
"""PlayStation 1 (PS1) - 平台模块"""

import struct
import re
from pathlib import Path

from scrape import _has_cjk

PLATFORM_TITLE = "PlayStation 1"
CONFIG_FILENAME = "ps1_config.json"
TGDB_PLATFORM_ID = 10

COLLECTION_DEFAULTS = {
    'collection': 'PlayStation 1',
    'shortname': 'ps1',
    'extensions': 'chd pbp bin iso',
}

SECTOR_SIZE = 2048


# ===== PARAM.SFO 解析 =====

def parse_param_sfo(data):
    if len(data) < 20 or data[0:4] != b'\x00PSF':
        return {}
    _version, key_table_off, data_table_off, num_entries = struct.unpack_from(
        '<IIII', data, 4)
    result = {}
    for i in range(num_entries):
        off = 0x14 + i * 0x10
        if off + 0x10 > len(data):
            break
        key_off, param_fmt, param_len, _param_max, data_off = struct.unpack_from(
            '<HHIII', data, off)
        key_end = data.index(b'\x00', key_table_off + key_off)
        key = data[key_table_off + key_off:key_end].decode('utf-8', errors='ignore')
        val_start = data_table_off + data_off
        val_bytes = data[val_start:val_start + param_len]
        if param_fmt == 0x0204:
            result[key] = val_bytes.rstrip(b'\x00').decode('utf-8', errors='ignore')
        elif param_fmt == 0x0404 and len(val_bytes) >= 4:
            result[key] = struct.unpack('<I', val_bytes[:4])[0]
    return result


# ===== PS1 ROM 解析 =====

def extract_ps1_info(file_path, lang_code='en', log=print):
    """从 PS1 ROM 提取游戏信息"""
    p = Path(file_path)
    suffix = p.suffix.lower()
    try:
        if suffix == '.pbp':
            return _extract_from_pbp(file_path, log)
        elif suffix == '.chd':
            return _extract_from_chd(file_path, log)
        elif suffix in ('.bin', '.iso'):
            return _extract_from_disc(file_path, log)
        else:
            return None
    except Exception as e:
        log(f"[游戏解析] PS1 解析错误: {e}")
        return None


def _extract_from_pbp(file_path, log):
    """从 PBP 文件提取 PS1 游戏信息 (PARAM.SFO)"""
    with open(file_path, 'rb') as f:
        header = f.read(0x28)
        if len(header) < 0x28 or header[0:4] != b'\x00PBP':
            log("[游戏解析] 跳过非有效 PBP 文件")
            return None
        offsets = struct.unpack_from('<8I', header, 8)
        sfo_off = offsets[0]
        icon_off = offsets[1]
        sfo_size = icon_off - sfo_off
        if sfo_size <= 0:
            log("[游戏解析] PBP 中未找到 PARAM.SFO")
            return None
        f.seek(sfo_off)
        sfo_data = f.read(sfo_size)

    sfo = parse_param_sfo(sfo_data)
    if not sfo:
        log("[游戏解析] PARAM.SFO 解析失败")
        return None

    title = sfo.get('TITLE', '') or Path(file_path).stem
    disc_id = sfo.get('DISC_ID', '') or ''
    title_en = title
    if _has_cjk(title) and disc_id:
        title_en = disc_id

    return {
        'title': title,
        'title_en': title_en,
        'disc_id': disc_id,
        'publisher': '',
        'filename': Path(file_path).name,
    }


def _extract_from_chd(file_path, log):
    """从 CHD 文件提取 PS1 游戏序列号"""
    p = Path(file_path)
    try:
        import chdimage
        serial = _read_chd_serial(file_path, chdimage)
        if serial:
            clean_name = re.sub(r'\s*[\[\(][^\]\)]*[\]\)]', '', p.stem).strip()
            clean_name = re.sub(
                r'\s*(汉化版|中文版|日版|美版|欧版|繁体|简体)', '', clean_name).strip()
            title = clean_name or p.stem
            return {
                'title': title,
                'title_en': serial,
                'disc_id': serial,
                'publisher': '',
                'filename': p.name,
            }
    except ImportError:
        pass
    except Exception as e:
        log(f"[游戏解析] CHD 解析失败，回退文件名: {e}")
    clean_name = re.sub(r'\s*[\[\(][^\]\)]*[\]\)]', '', p.stem).strip()
    clean_name = re.sub(
        r'\s*(汉化版|中文版|日版|美版|欧版|繁体|简体)', '', clean_name).strip()
    title = clean_name or p.stem
    return {
        'title': title,
        'title_en': title,
        'disc_id': '',
        'publisher': '',
        'filename': p.name,
    }


def _read_chd_sector(img, chdimage, lba):
    """读取 CHD 中指定 LBA 的用户数据"""
    m = (lba + 150) // (60 * 75)
    s = ((lba + 150) // 75) % 60
    f = (lba + 150) % 75
    img.set_location(chdimage.MsfIndex(m, s, f))
    raw = img.copy_current_sector()
    return raw[24:24 + 2048]


def _read_chd_serial(file_path, chdimage):
    """从 CHD 文件的 ISO 文件系统中读取 SYSTEM.CNF 序列号"""
    img = chdimage.open(str(file_path))
    pvd = _read_chd_sector(img, chdimage, 16)
    if pvd[1:6] != b'CD001':
        return None
    root_rec = pvd[156:156 + 34]
    dir_lba = struct.unpack_from('<I', root_rec, 2)[0]
    dir_data = _read_chd_sector(img, chdimage, dir_lba)
    pos = 0
    while pos < len(dir_data):
        rec_len = dir_data[pos]
        if rec_len == 0:
            break
        if pos + 33 > len(dir_data):
            break
        name_len = dir_data[pos + 32]
        name = dir_data[pos + 33:pos + 33 + name_len].decode('ascii', errors='ignore')
        if ';' in name:
            name = name.split(';')[0]
        entry_lba = struct.unpack_from('<I', dir_data, pos + 2)[0]
        entry_size = struct.unpack_from('<I', dir_data, pos + 10)[0]
        if name.upper() == 'SYSTEM.CNF':
            cnf = _read_chd_sector(img, chdimage, entry_lba)[:entry_size]
            return _parse_system_cnf(cnf)
        pos += rec_len
    return None


def _extract_from_disc(file_path, log):
    """从 BIN/ISO 文件提取 PS1 游戏序列号 (SYSTEM.CNF)"""
    p = Path(file_path)
    try:
        with open(file_path, 'rb') as f:
            serial = _read_system_cnf_serial(f)
            if serial:
                return {
                    'title': p.stem,
                    'title_en': serial,
                    'disc_id': serial,
                    'publisher': '',
                    'filename': p.name,
                }
    except Exception:
        pass
    clean_name = re.sub(r'\s*[\[\(][^\]\)]*[\]\)]', '', p.stem).strip()
    return {
        'title': clean_name or p.stem,
        'title_en': clean_name or p.stem,
        'disc_id': '',
        'publisher': '',
        'filename': p.name,
    }


def _read_system_cnf_serial(f):
    """尝试从 ISO 9660 文件系统读取 SYSTEM.CNF 中的序列号"""
    try:
        f.seek(16 * SECTOR_SIZE)
        pvd = f.read(SECTOR_SIZE)
        if len(pvd) < 882 or pvd[0:1] != b'\x01' or pvd[1:6] != b'CD001':
            return None
        root_rec = pvd[156:156 + 34]
        dir_lba = struct.unpack_from('<I', root_rec, 2)[0]
        dir_size = struct.unpack_from('<I', root_rec, 10)[0]
        f.seek(dir_lba * SECTOR_SIZE)
        dir_data = f.read(dir_size)
        pos = 0
        while pos < len(dir_data):
            rec_len = dir_data[pos]
            if rec_len == 0:
                pos = ((pos // SECTOR_SIZE) + 1) * SECTOR_SIZE
                if pos >= len(dir_data):
                    break
                continue
            if pos + rec_len > len(dir_data):
                break
            name_len = dir_data[pos + 32]
            name = dir_data[pos + 33:pos + 33 + name_len].decode(
                'ascii', errors='ignore')
            if ';' in name:
                name = name.split(';')[0]
            name = name.rstrip('.')
            entry_lba = struct.unpack_from('<I', dir_data, pos + 2)[0]
            entry_size = struct.unpack_from('<I', dir_data, pos + 10)[0]
            if name.upper() == 'SYSTEM.CNF':
                f.seek(entry_lba * SECTOR_SIZE)
                cnf_data = f.read(min(entry_size, 1024))
                return _parse_system_cnf(cnf_data)
            pos += rec_len
    except Exception:
        pass
    return None


def _parse_system_cnf(data):
    """从 SYSTEM.CNF 内容解析游戏序列号"""
    try:
        text = data.decode('ascii', errors='ignore')
        for line in text.split('\n'):
            if 'BOOT' in line.upper() and '=' in line:
                parts = line.split('=', 1)[1].strip()
                match = re.search(
                    r'([A-Z]{4})[_\-](\d{3})\.?(\d{2,3})', parts.upper())
                if match:
                    return f"{match.group(1)}-{match.group(2)}{match.group(3)}"
    except Exception:
        pass
    return None
