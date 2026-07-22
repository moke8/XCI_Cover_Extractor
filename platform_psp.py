#!/usr/bin/env python3
"""PlayStation Portable (PSP) - 平台模块"""

import struct
import zlib
from pathlib import Path

from scrape import _has_cjk

PLATFORM_TITLE = "PlayStation Portable"
CONFIG_FILENAME = "psp_config.json"
TGDB_PLATFORM_ID = 13

COLLECTION_DEFAULTS = {
    'collection': 'PlayStation Portable',
    'shortname': 'psp',
    'extensions': 'iso cso pbp',
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


# ===== ISO 9660 最小解析 =====

def _iso_read_file(f, path):
    parts = [p for p in path.split('/') if p]
    if not parts:
        return None
    f.seek(16 * SECTOR_SIZE)
    pvd = f.read(SECTOR_SIZE)
    if len(pvd) < 882 or pvd[0:1] != b'\x01' or pvd[1:6] != b'CD001':
        return None
    root_rec = pvd[156:156 + 34]
    dir_lba = struct.unpack_from('<I', root_rec, 2)[0]
    dir_size = struct.unpack_from('<I', root_rec, 10)[0]

    for depth, part in enumerate(parts):
        is_last = (depth == len(parts) - 1)
        f.seek(dir_lba * SECTOR_SIZE)
        dir_data = f.read(dir_size)
        found = False
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
            name = dir_data[pos + 33:pos + 33 + name_len].decode('ascii', errors='ignore')
            if ';' in name:
                name = name.split(';')[0]
            name = name.rstrip('.')
            entry_lba = struct.unpack_from('<I', dir_data, pos + 2)[0]
            entry_size = struct.unpack_from('<I', dir_data, pos + 10)[0]
            flags = dir_data[pos + 25]
            is_dir = bool(flags & 0x02)
            if name.upper() == part.upper():
                if is_last and not is_dir:
                    f.seek(entry_lba * SECTOR_SIZE)
                    return f.read(entry_size)
                elif not is_last and is_dir:
                    dir_lba = entry_lba
                    dir_size = entry_size
                    found = True
                    break
            pos += rec_len
        if not found and not is_last:
            return None
    return None


# ===== CSO 解压 =====

class _CSOReader:
    def __init__(self, f, header_size, block_size, index_shift, indices,
                 num_blocks, uncompressed_size):
        self._f = f
        self._header_size = header_size
        self._block_size = block_size
        self._index_shift = index_shift
        self._indices = indices
        self._num_blocks = num_blocks
        self._uncompressed_size = uncompressed_size
        self._cache = {}
        self.pos = 0

    def seek(self, offset):
        self.pos = offset

    def read(self, size):
        result = bytearray()
        remaining = size
        pos = self.pos
        while remaining > 0 and pos < self._uncompressed_size:
            block_num = pos // self._block_size
            block_offset = pos % self._block_size
            if block_num not in self._cache:
                self._cache[block_num] = self._decompress_block(block_num)
            block_data = self._cache[block_num]
            to_read = min(remaining, len(block_data) - block_offset)
            if to_read <= 0:
                break
            result.extend(block_data[block_offset:block_offset + to_read])
            pos += to_read
            remaining -= to_read
        self.pos = pos
        return bytes(result)

    def _decompress_block(self, block_num):
        if block_num >= self._num_blocks:
            return b''
        idx = self._indices[block_num]
        idx_next = self._indices[block_num + 1]
        is_plain = bool(idx & 0x80000000)
        offset = (idx & 0x7FFFFFFF) << self._index_shift
        next_offset = (idx_next & 0x7FFFFFFF) << self._index_shift
        length = next_offset - offset
        if length <= 0:
            return b'\x00' * self._block_size
        self._f.seek(offset)
        data = self._f.read(length)
        if is_plain or length >= self._block_size:
            return data
        try:
            return zlib.decompress(data, -15)
        except zlib.error:
            return data


def _make_cso_reader(f):
    f.seek(0)
    header = f.read(24)
    if len(header) < 24 or header[0:4] != b'CISO':
        return None
    header_size = struct.unpack_from('<I', header, 4)[0]
    uncompressed_size = struct.unpack_from('<Q', header, 8)[0]
    block_size = struct.unpack_from('<I', header, 16)[0]
    index_shift = header[21]
    if block_size == 0:
        return None
    num_blocks = (uncompressed_size + block_size - 1) // block_size
    f.seek(header_size)
    index_data = f.read((num_blocks + 1) * 4)
    if len(index_data) < (num_blocks + 1) * 4:
        return None
    indices = struct.unpack(f'<{num_blocks + 1}I', index_data)
    return _CSOReader(f, header_size, block_size, index_shift, indices,
                      num_blocks, uncompressed_size)


# ===== PBP 解析 =====

def _extract_from_pbp(pbp_path, lang_code, log):
    try:
        with open(pbp_path, 'rb') as f:
            header = f.read(0x28)
            if len(header) < 0x28 or header[0:4] != b'\x00PBP':
                log("  [跳过] 非有效 PBP 文件")
                return None
            offsets = struct.unpack_from('<8I', header, 8)
            sfo_off = offsets[0]
            icon_off = offsets[1]
            icon1_off = offsets[2]
            sfo_size = icon_off - sfo_off
            icon_size = icon1_off - icon_off
            sfo_data = None
            if sfo_size > 0:
                f.seek(sfo_off)
                sfo_data = f.read(sfo_size)
            icon_data = None
            if icon_size > 0:
                f.seek(icon_off)
                icon_data = f.read(icon_size)
        return sfo_data, icon_data
    except Exception as e:
        log(f"  [失败] PBP 解析错误: {e}")
        return None


# ===== PSP 提取 =====

def extract_psp_info(psp_path, lang_code='en', log=print):
    ext = Path(psp_path).suffix.lower()
    sfo_data = None
    icon_data = None

    if ext == '.pbp':
        result = _extract_from_pbp(psp_path, lang_code, log)
        if result is None:
            return None
        sfo_data, icon_data = result
    elif ext == '.iso':
        try:
            with open(psp_path, 'rb') as f:
                sfo_data = _iso_read_file(f, 'PSP_GAME/PARAM.SFO')
                if sfo_data:
                    icon_data = _iso_read_file(f, 'PSP_GAME/ICON0.PNG')
        except Exception as e:
            log(f"  [失败] ISO 读取错误: {e}")
            return None
    elif ext == '.cso':
        try:
            with open(psp_path, 'rb') as f:
                reader = _make_cso_reader(f)
                if not reader:
                    log("  [跳过] 非有效 CSO 文件")
                    return None
                sfo_data = _iso_read_file(reader, 'PSP_GAME/PARAM.SFO')
                if sfo_data:
                    icon_data = _iso_read_file(reader, 'PSP_GAME/ICON0.PNG')
        except Exception as e:
            log(f"  [失败] CSO 读取错误: {e}")
            return None
    else:
        return None

    if not sfo_data:
        log("  [跳过] 未找到 PARAM.SFO")
        return None

    sfo = parse_param_sfo(sfo_data)
    if not sfo:
        log("  [跳过] PARAM.SFO 解析失败")
        return None

    title = sfo.get('TITLE', '') or Path(psp_path).stem
    disc_id = sfo.get('DISC_ID', '') or ''

    title_en = title
    if _has_cjk(title) and disc_id:
        title_en = disc_id

    info = {
        'title': title,
        'title_en': title_en,
        'disc_id': disc_id,
        'publisher': '',
        'filename': Path(psp_path).name,
    }
    if icon_data:
        info['icon_data'] = icon_data
    return info