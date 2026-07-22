#!/usr/bin/env python3
"""Sega Dreamcast (DC) - 平台模块"""

import struct
import re
import zlib
import lzma
from pathlib import Path

PLATFORM_TITLE = "Sega Dreamcast"
CONFIG_FILENAME = "dc_config.json"
TGDB_PLATFORM_ID = 16

COLLECTION_DEFAULTS = {
    'collection': 'Sega Dreamcast',
    'shortname': 'dc',
    'extensions': 'chd cdi gdi',
}


# ===== DC ROM 解析 =====

def _find_ip_bin(data):
    """在数据中搜索 Dreamcast IP.BIN 头，返回 (product_id, title)"""
    idx = data.find(b'SEGA SEGAKATANA')
    if idx < 0:
        idx = data.find(b'SEGA ENTERPRISES')
    if idx < 0:
        return None, None
    ip = data[idx:idx + 0x100]
    if len(ip) < 0x100:
        return None, None
    product = ip[0x40:0x50].decode('ascii', errors='replace').strip()
    title = ip[0x80:0x100].decode('ascii', errors='replace').strip()
    return product, title


def _extract_from_chd(file_path, log):
    """从 CHD 文件解析 IP.BIN 获取 product_id"""
    p = Path(file_path)
    try:
        product, title = _chd_read_ip_bin(file_path)
        if product:
            product_id = product.split()[0] if product else ''
            if not title or len(title) < 2:
                title = _clean_filename(p.stem)
            return {
                'title': title,
                'title_en': title,
                'product_id': product_id,
                'publisher': '',
                'filename': p.name,
            }
    except Exception as e:
        log(f"  [CHD] 解析失败: {e}")
    return _extract_from_filename(file_path, log)


def _clean_filename(stem):
    """清理文件名用作标题"""
    clean = re.sub(r'\s*[\[\(][^\]\)]*[\]\)]', '', stem).strip()
    clean = re.sub(r'\s*(汉化版|中文版|日版|美版|欧版|繁体|简体)', '', clean).strip()
    return clean or stem


def _chd_read_ip_bin(file_path):
    """解析 CHD v5 文件，解压 hunk 0 获取 IP.BIN"""
    with open(file_path, 'rb') as f:
        tag = f.read(8)
        if tag != b'MComprHD':
            return None, None
        header_len = struct.unpack('>I', f.read(4))[0]
        version = struct.unpack('>I', f.read(4))[0]
        if version != 5:
            return None, None
        f.seek(0)
        hdr = f.read(124)
        codecs = []
        for i in range(4):
            c = hdr[16 + i * 4:20 + i * 4]
            if c == b'\x00\x00\x00\x00':
                break
            codecs.append(c.decode('ascii', errors='replace'))
        hunk_bytes = struct.unpack('>I', hdr[56:60])[0]
        map_offset = struct.unpack('>Q', hdr[40:48])[0]
        f.seek(map_offset)
        map_header = f.read(16)
        firstoffs = int.from_bytes(map_header[4:10], 'big')
        frames = hunk_bytes // 2448
        if frames == 0:
            return None, None
        expected_base = frames * 2048
        f.seek(firstoffs)
        raw = f.read(min(hunk_bytes, 20000))
        if len(raw) < 10:
            return None, None
        complen_base = (raw[1] << 8) | raw[2]
        if complen_base > hunk_bytes or complen_base < 1:
            return None, None
        base_data = raw[3:3 + complen_base]
        sector_data = _chd_decompress_base(base_data, codecs, expected_base)
        if sector_data:
            product, title = _find_ip_bin(sector_data)
            if product:
                return product, title
        return None, None


def _chd_decompress_base(data, codecs, expected_size):
    """尝试解压 CHD CD codec 的 base 数据"""
    for codec in codecs:
        if codec == 'cdlz':
            alone_hdr = (b'\x5d' + struct.pack('<I', 65536)
                         + struct.pack('<Q', 0xFFFFFFFFFFFFFFFF))
            try:
                dec = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
                result = dec.decompress(alone_hdr + data, max_length=expected_size)
                if len(result) >= expected_size:
                    return result
            except lzma.LZMAError:
                pass
        elif codec == 'cdzl':
            try:
                result = zlib.decompress(data, -15)
                if len(result) >= expected_size:
                    return result
            except zlib.error:
                pass
            try:
                result = zlib.decompress(data)
                if len(result) >= expected_size:
                    return result
            except zlib.error:
                pass
    return None


def _extract_from_filename(file_path, log):
    """从文件名提取标题作为后备"""
    p = Path(file_path)
    title = _clean_filename(p.stem)
    return {
        'title': title,
        'title_en': title,
        'publisher': '',
        'filename': p.name,
    }


def extract_dc_info(file_path, lang_code='en', log=print):
    """从 DC ROM 文件提取信息"""
    p = Path(file_path)
    ext = p.suffix.lower()
    if ext == '.chd':
        return _extract_from_chd(file_path, log)
    return _extract_from_filename(file_path, log)
