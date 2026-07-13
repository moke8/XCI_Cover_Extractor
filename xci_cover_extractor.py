#!/usr/bin/env python3
"""XCI Cover Extractor - 从 Nintendo Switch XCI 文件中提取游戏封面和元数据"""

import re
import struct
import json
import sys
import locale
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import urlencode, quote

try:
    from Crypto.Cipher import AES
except ImportError:
    print("请先安装依赖: pip install -r requirements.txt")
    exit(1)


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


def parse_keys(keys_path):
    keys = {}
    with open(keys_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(';') or '=' not in line:
                continue
            name, value = line.split('=', 1)
            keys[name.strip().lower()] = bytes.fromhex(value.strip())
    return keys


def _xts_gf_mult(tweak):
    """GF(2^128) 乘法，用于 XTS tweak 更新"""
    out = bytearray(16)
    carry = 0
    for i in range(16):
        new_carry = (tweak[i] >> 7) & 1
        out[i] = ((tweak[i] << 1) | carry) & 0xFF
        carry = new_carry
    if carry:
        out[0] ^= 0x87
    return out


def aes_xts_decrypt(data, key, sector, sector_size=0x200):
    """手动实现 AES-128-XTS 解密 (Nintendo 大端序 tweak)"""
    key1 = key[:16]
    key2 = key[16:]
    ecb1 = AES.new(key1, AES.MODE_ECB)
    ecb2 = AES.new(key2, AES.MODE_ECB)
    out = bytearray()
    num_sectors = len(data) // sector_size
    for i in range(num_sectors):
        tweak_plain = struct.pack('>QQ', 0, sector + i)
        tweak = bytearray(ecb2.encrypt(tweak_plain))
        sector_data = data[i * sector_size:(i + 1) * sector_size]
        num_blocks = sector_size // 16
        for j in range(num_blocks):
            block = bytearray(sector_data[j * 16:(j + 1) * 16])
            for k in range(16):
                block[k] ^= tweak[k]
            dec = bytearray(ecb1.decrypt(bytes(block)))
            for k in range(16):
                dec[k] ^= tweak[k]
            out.extend(dec)
            tweak = _xts_gf_mult(tweak)
    return bytes(out)


def aes_ctr_decrypt(data, key, nonce):
    cipher = AES.new(key, AES.MODE_CTR, nonce=b'', initial_value=nonce)
    return cipher.decrypt(data)


class HFS0:
    def __init__(self, f, offset):
        f.seek(offset)
        magic = f.read(4)
        if magic != b'HFS0':
            raise ValueError(f"Invalid HFS0 magic at 0x{offset:X}")
        num_files, string_table_size, _ = struct.unpack('<III', f.read(12))
        self.entries = []
        for _ in range(num_files):
            entry_offset, entry_size, string_offset, hashed_size, _, sha256 = \
                struct.unpack('<QQII8s32s', f.read(64))
            self.entries.append({
                'offset': entry_offset,
                'size': entry_size,
                'string_offset': string_offset,
            })
        string_table_start = f.tell()
        string_table = f.read(string_table_size)
        self.data_offset = string_table_start + string_table_size
        for entry in self.entries:
            end = string_table.index(b'\x00', entry['string_offset'])
            entry['name'] = string_table[entry['string_offset']:end].decode()
            entry['abs_offset'] = self.data_offset + entry['offset']


def decrypt_nca_header(f, nca_offset, keys):
    header_key = keys.get('header_key')
    if not header_key or len(header_key) != 32:
        raise ValueError("prod.keys 中缺少有效的 header_key")
    f.seek(nca_offset)
    header_data = f.read(0xC00)
    return aes_xts_decrypt(header_data, header_key, 0, 0x200)


def parse_nca_header(decrypted_header):
    magic = decrypted_header[0x200:0x204]
    if magic not in (b'NCA3', b'NCA2'):
        return None
    content_type = decrypted_header[0x205]
    key_generation_old = decrypted_header[0x206]
    key_area_key_index = decrypted_header[0x207]
    key_generation = decrypted_header[0x220]
    key_gen = max(key_generation_old, key_generation)
    if key_gen > 0:
        key_gen -= 1
    sections = []
    for i in range(4):
        off = 0x240 + i * 0x10
        start = struct.unpack_from('<I', decrypted_header, off)[0]
        end = struct.unpack_from('<I', decrypted_header, off + 4)[0]
        if start and end:
            fs_header_off = 0x400 + i * 0x200
            section_ctr = b'\x00' * 8
            if fs_header_off + 0x148 <= len(decrypted_header):
                section_ctr = decrypted_header[fs_header_off + 0x140:fs_header_off + 0x148]
            sections.append({
                'index': i,
                'start': start * 0x200,
                'end': end * 0x200,
                'size': (end - start) * 0x200,
                'section_ctr': section_ctr,
            })
    romfs_offset_in_section = 0
    if sections:
        sec = sections[0]
        fs_off = 0x400 + sec['index'] * 0x200
        if fs_off + 0x90 <= len(decrypted_header):
            ivfc_magic = decrypted_header[fs_off + 0x08:fs_off + 0x0C]
            if ivfc_magic == b'IVFC':
                num_levels = struct.unpack_from('<I', decrypted_header, fs_off + 0x08 + 0x0C)[0]
                for lvl in range(num_levels - 1, -1, -1):
                    level_off = fs_off + 0x08 + 0x10 + lvl * 0x18
                    if level_off + 16 > len(decrypted_header):
                        continue
                    lv_offset = struct.unpack_from('<Q', decrypted_header, level_off)[0]
                    lv_size = struct.unpack_from('<Q', decrypted_header, level_off + 8)[0]
                    if lv_size > 0:
                        romfs_offset_in_section = lv_offset
                        break
    return {
        'content_type': content_type,
        'key_gen': key_gen,
        'key_index': key_area_key_index,
        'sections': sections,
        'romfs_offset': romfs_offset_in_section,
        'key_area': decrypted_header[0x300:0x340],
    }


def get_section_decrypt_key(nca_info, keys):
    key_gen = nca_info['key_gen']
    key_index = nca_info['key_index']
    key_names = ['key_area_key_application', 'key_area_key_ocean', 'key_area_key_system']
    if key_index >= len(key_names):
        key_index = 0
    kak_name = f"{key_names[key_index]}_{key_gen:02x}"
    kak = keys.get(kak_name)
    if not kak:
        return None
    encrypted_key = nca_info['key_area'][2*16:3*16]
    cipher = AES.new(kak, AES.MODE_ECB)
    return cipher.decrypt(encrypted_key)


def decrypt_section_ctr(f, nca_offset, section, key, offset_in_section=0, size=None):
    section_offset = nca_offset + section['start']
    read_size = size if size else (section['size'] - offset_in_section)
    f.seek(section_offset + offset_in_section)
    data = f.read(read_size)
    section_ctr = section.get('section_ctr', b'\x00' * 8)
    upper_ctr = bytes(section_ctr[7 - j] for j in range(8))
    abs_offset = section['start'] + offset_in_section
    block_index = abs_offset >> 4
    ctr = upper_ctr + struct.pack('>Q', block_index)
    return aes_ctr_decrypt(data, key, ctr)


def parse_romfs(data):
    file_meta_off = struct.unpack_from('<Q', data, 0x38)[0]
    file_meta_size = struct.unpack_from('<Q', data, 0x40)[0]
    data_off = struct.unpack_from('<Q', data, 0x48)[0]
    files = {}
    pos = file_meta_off
    while pos < file_meta_off + file_meta_size:
        file_offset = struct.unpack_from('<Q', data, pos + 8)[0]
        file_size = struct.unpack_from('<Q', data, pos + 16)[0]
        name_len = struct.unpack_from('<I', data, pos + 28)[0]
        name = data[pos + 32:pos + 32 + name_len].decode('utf-8', errors='ignore')
        files[name] = (data_off + file_offset, file_size)
        entry_size = (32 + name_len + 3) & ~3
        pos += entry_size
    return files


NACP_LANG_MAP = {
    'ja': [0], 'en': [1, 12], 'fr': [2, 13], 'de': [3], 'it': [4],
    'es': [5, 14], 'nl': [6], 'pt': [7], 'ru': [8], 'ko': [9],
    'zh': [11, 10],
}


def _get_nacp_lang_priority():
    try:
        lang = locale.getdefaultlocale()[0] or ''
    except Exception:
        lang = ''
    prefix = lang.split('_')[0].lower()
    priority = NACP_LANG_MAP.get(prefix, [])
    all_indices = list(range(16))
    return priority + [i for i in all_indices if i not in priority]


def _has_cjk(text):
    return any('一' <= c <= '鿿' for c in text)


_TC_CHARS = set(
    '優記點開關學東與號說麗買義書實總這從進時間問題長們線還過處對電動員請種業經連結個機數產無裡發準現環樂選項認設據應體歡視頁練網幣節讓際計畫區觀覺單歷歸調變標響廣達歲當積類會島語車輕齊廳觸創職護編譯'
    '騎夢魘級瑪歐驚寶劍傳險戰鬥義務構圖館絡話輛運遊錄訊號該還結頭圍廠廢廚龍鳳鑰鍵鐵銀釘針腦臟藝術蘭藥獲獎獵猶瑣爾燈燒營獻獨狀滅漲測決減準溫視離種積穩競節篇範築縣織繼續縮繩約紀純紅納級終組細織續華萬著薩處裝製複觀詢試詳認誤課談調請論證識議護變讓議豐貝負財販貧貨質購貿費貼資賊賓賞賢賣賦質賬賴賺購贈贊趙輕載較輝輩輪轉辦辭辯達遙適選遷邊邏還進遠違連過運過適選遲遺鄰醫醬釋鑒鋼錢錯鍋鍵鏡鐘關隊階隨險隱雙雜離難電靈靜響頂預領頻題顏額願類顯飛養餘駐駕驅驗體'
)


def _is_traditional(text):
    return any(c in _TC_CHARS for c in text)


def parse_nacp(nacp_data, lang_code='en'):
    titles = []
    publishers = []
    for i in range(16):
        base = i * 0x300
        if base + 0x300 > len(nacp_data):
            break
        t = nacp_data[base:base + 0x200]
        p = nacp_data[base + 0x200:base + 0x300]
        null_t = t.find(b'\x00')
        title = t[:null_t].decode('utf-8', errors='ignore').strip() if null_t > 0 else ''
        null_p = p.find(b'\x00')
        pub = p[:null_p].decode('utf-8', errors='ignore').strip() if null_p > 0 else ''
        titles.append(title)
        publishers.append(pub)

    lang_prefix = lang_code.split('_')[0].lower()
    priority = NACP_LANG_MAP.get(lang_prefix, [])
    all_indices = priority + [i for i in range(16) if i not in priority]

    title = None
    publisher = None
    for i in all_indices:
        if titles[i] and not title:
            title = titles[i]
        if publishers[i] and not publisher:
            publisher = publishers[i]
        if title and publisher:
            break

    if lang_prefix == 'zh':
        is_sc = lang_code in ('zh_CN', 'zh_SG')
        best_sc = None
        best_tc = None
        for t in titles:
            if t and _has_cjk(t):
                if _is_traditional(t):
                    if not best_tc:
                        best_tc = t
                else:
                    if not best_sc:
                        best_sc = t
        if is_sc:
            title = best_sc or best_tc or title
        else:
            title = best_tc or best_sc or title

    title_en = None
    for i in range(16):
        if titles[i] and titles[i].isascii():
            title_en = titles[i]
            break

    return {'title': title, 'publisher': publisher, 'title_en': title_en}


def sanitize_filename(name):
    illegal = '<>:"/\\|?*'
    for c in illegal:
        name = name.replace(c, '_')
    return name.strip().strip('.')


def extract_xci_info(xci_path, keys, lang_code='en', log=print):
    """从 XCI 提取封面和元数据，返回 dict 或 None"""
    with open(xci_path, 'rb') as f:
        f.seek(0x100)
        if f.read(4) != b'HEAD':
            log(f"  [跳过] 非有效 XCI 文件")
            return None

        f.seek(0x130)
        hfs0_offset = struct.unpack('<Q', f.read(8))[0]
        root_hfs0 = HFS0(f, hfs0_offset)

        secure_entry = None
        for entry in root_hfs0.entries:
            if 'secure' in entry['name'].lower():
                secure_entry = entry
                break
        if not secure_entry:
            log(f"  [跳过] 未找到 secure 分区")
            return None

        secure_hfs0 = HFS0(f, secure_entry['abs_offset'])

        control_nca_entry = None
        control_nca_info = None
        nca_count = 0
        for entry in secure_hfs0.entries:
            if not entry['name'].lower().endswith('.nca'):
                continue
            nca_count += 1
            try:
                dec = decrypt_nca_header(f, entry['abs_offset'], keys)
                info = parse_nca_header(dec)
                if info is None:
                    continue
                if info['content_type'] == 2:
                    control_nca_entry = entry
                    control_nca_info = info
                    break
            except Exception:
                continue

        if not control_nca_info:
            log(f"  [跳过] 未找到 Control NCA (共扫描 {nca_count} 个 NCA)")
            return None

        section_key = get_section_decrypt_key(control_nca_info, keys)
        if not section_key:
            log(f"  [跳过] 缺少对应的 key_area_key")
            return None

        if not control_nca_info['sections']:
            log(f"  [跳过] Control NCA 无有效 section")
            return None

        section = control_nca_info['sections'][0]
        nca_offset = control_nca_entry['abs_offset']
        romfs_off = control_nca_info.get('romfs_offset', 0)
        max_read = min(section['size'] - romfs_off, 0x800000)
        decrypted = decrypt_section_ctr(f, nca_offset, section, section_key, romfs_off, max_read)

        romfs_data = decrypted
        if len(romfs_data) > 8:
            header_val = struct.unpack_from('<Q', romfs_data, 0)[0]
            if header_val != 0x50:
                for soff in range(0, min(len(romfs_data), 0x100000), 0x200):
                    if soff + 0x50 > len(romfs_data):
                        break
                    val = struct.unpack_from('<Q', romfs_data, soff)[0]
                    if val == 0x50:
                        romfs_data = romfs_data[soff:]
                        break

        try:
            files = parse_romfs(romfs_data)
        except Exception as e:
            log(f"  [跳过] RomFS 解析失败: {e}")
            return None

        icon_data = None
        for icon_name in ['icon_AmericanEnglish.dat', 'icon_Japanese.dat',
                          'icon_BritishEnglish.dat', 'icon_CanadianFrench.dat']:
            if icon_name in files:
                off, size = files[icon_name]
                icon_data = romfs_data[off:off + size]
                break

        if not icon_data or len(icon_data) < 100:
            log(f"  [跳过] 未找到图标文件")
            return None

        meta = {'title': None, 'publisher': None}
        if 'control.nacp' in files:
            nacp_off, nacp_size = files['control.nacp']
            nacp_data = romfs_data[nacp_off:nacp_off + nacp_size]
            meta = parse_nacp(nacp_data, lang_code)

        title = meta['title'] or Path(xci_path).stem
        title_en = meta.get('title_en') or title
        return {
            'title': title,
            'title_en': title_en,
            'publisher': meta.get('publisher'),
            'filename': Path(xci_path).name,
            'icon_data': icon_data,
        }


# ===== 在线元数据 =====

_proxy_handler = None


def set_proxy(proxy_url):
    global _proxy_handler
    if proxy_url:
        from urllib.request import ProxyHandler, build_opener, install_opener
        _proxy_handler = build_opener(ProxyHandler({
            'http': proxy_url, 'https': proxy_url}))
        install_opener(_proxy_handler)


def _http_get_json(url):
    try:
        req = Request(url, headers={"User-Agent": "XCI-Cover-Extractor/1.0"})
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


TGDB_BASE = "https://api.thegamesdb.net/v1"
TGDB_SWITCH_PLATFORM = 4971


def _tgdb_request(endpoint, params):
    url = f"{TGDB_BASE}/{endpoint}?{urlencode(params)}"
    return _http_get_json(url)


def fetch_genres_map(api_key):
    data = _tgdb_request("Genres", {"apikey": api_key})
    if not data or "data" not in data:
        return {}
    genres = data["data"].get("genres", {})
    return {int(k): v.get("name", "") for k, v in genres.items()}


def fetch_publishers_map(api_key):
    data = _tgdb_request("Publishers", {"apikey": api_key})
    if not data or "data" not in data:
        return {}
    pubs = data["data"].get("publishers", {})
    return {int(k): v.get("name", "") for k, v in pubs.items()}


def fetch_game_metadata(title, api_key, genres_map, publishers_map):
    params = {
        "apikey": api_key,
        "name": title,
        "filter[platform]": TGDB_SWITCH_PLATFORM,
        "fields": "players,genres,overview,rating,publishers",
    }
    data = _tgdb_request("Games/ByGameName", params)
    if not data or "data" not in data:
        return None
    games = data["data"].get("games", [])
    if not games:
        return None
    game = games[0]
    result = {}
    if game.get("overview"):
        result["description"] = game["overview"]
    if game.get("release_date"):
        result["release"] = game["release_date"]
    if game.get("players"):
        result["players"] = str(game["players"])
    if game.get("rating"):
        result["rating"] = game["rating"]
    genre_ids = game.get("genres") or []
    if genre_ids and genres_map:
        names = [genres_map[gid] for gid in genre_ids if gid in genres_map]
        if names:
            result["genres"] = ", ".join(names)
    pub_ids = game.get("publishers") or []
    if pub_ids and publishers_map:
        names = [publishers_map[pid] for pid in pub_ids if pid in publishers_map]
        if names:
            result["publisher_online"] = ", ".join(names)
    return result if result else None


# ===== Wikipedia 免费元数据 =====

def _wiki_request(lang, endpoint, params):
    base = f"https://{lang}.wikipedia.org"
    url = f"{base}/{endpoint}"
    if params:
        url += "?" + urlencode(params)
    return _http_get_json(url)


def fetch_wikipedia_metadata(title):
    for lang in ('zh', 'en'):
        search_term = f"{title} Nintendo Switch" if lang == 'en' else title
        params = {
            "action": "query", "list": "search", "format": "json",
            "srsearch": search_term, "srlimit": "3",
        }
        data = _wiki_request(lang, "w/api.php", params)
        if not data or "query" not in data:
            continue
        results = data["query"].get("search", [])
        if not results:
            continue
        page_title = results[0]["title"]
        summary = _wiki_request(
            lang, f"api/rest_v1/page/summary/{quote(page_title, safe='')}", None)
        if summary and summary.get("extract"):
            return {"description": summary["extract"]}
    return None


# ===== Google 翻译 =====

def google_translate(text, google_lang_code):
    if not text or google_lang_code.startswith('en'):
        return text
    url = (
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl=en&tl={google_lang_code}&dt=t"
        f"&q={quote(text[:4000])}"
    )
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        if data and isinstance(data, list) and data[0]:
            return ''.join(seg[0] for seg in data[0] if seg[0])
    except Exception:
        pass
    return text


# ===== Pegasus Metadata =====

def parse_pegasus_meta(meta_path):
    """解析已有的 metadata.pegasus.txt，返回 (collection_lines, games_list)
    games_list: [{'lines': [...], 'file': 'xxx.xci'}]
    """
    if not meta_path.exists():
        return [], []
    text = meta_path.read_text(encoding='utf-8')
    blocks = re.split(r'\n(?=game:)', text)
    collection_lines = []
    games = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if block.startswith('game:'):
            file_match = re.search(r'^file:\s*(.+)$', block, re.MULTILINE)
            filename = file_match.group(1).strip() if file_match else None
            games.append({'lines': block, 'file': filename})
        else:
            collection_lines.append(block)
    return collection_lines, games


def build_game_entry(info, image_filename):
    """构建一个 game 条目的文本"""
    lines = [f"game: {info['title']}"]
    lines.append(f"file: {info['filename']}")
    if info.get('publisher'):
        lines.append(f"developer: {info['publisher']}")
    if info.get('genres'):
        lines.append(f"genre: {info['genres']}")
    if info.get('players'):
        lines.append(f"players: {info['players']}")
    if info.get('release'):
        lines.append(f"release: {info['release']}")
    if info.get('rating'):
        lines.append(f"rating: {info['rating']}")
    if info.get('description'):
        desc = info['description'].replace('\n', ' ').replace('\r', '')
        lines.append(f"description: {desc}")
    lines.append(f"assets.boxFront: images/{image_filename}")
    return '\n'.join(lines)


def backup_file(path):
    """备份文件：.bak, .bak1, .bak2 ..."""
    if not path.exists():
        return
    bak = Path(str(path) + '.bak')
    if not bak.exists():
        import shutil
        shutil.copy2(path, bak)
        return
    i = 1
    while True:
        bak = Path(str(path) + f'.bak{i}')
        if not bak.exists():
            import shutil
            shutil.copy2(path, bak)
            return
        i += 1


def write_pegasus_meta(meta_path, xci_dir, results):
    """写入或更新 metadata.pegasus.txt"""
    backup_file(meta_path)
    collection_lines, existing_games = parse_pegasus_meta(meta_path)

    if not collection_lines:
        collection_lines = [
            "collection: Nintendo Switch\n"
            "shortname: switch\n"
            "extensions: xci\n"
            f"launch: {{file.path}}"
        ]

    file_to_index = {}
    for i, g in enumerate(existing_games):
        if g['file']:
            file_to_index[g['file']] = i

    for info, image_filename in results:
        entry_text = build_game_entry(info, image_filename)
        fname = info['filename']
        if fname in file_to_index:
            existing_games[file_to_index[fname]]['lines'] = entry_text
        else:
            existing_games.append({'lines': entry_text, 'file': fname})

    with open(meta_path, 'w', encoding='utf-8') as f:
        for coll in collection_lines:
            f.write(coll.strip() + '\n')
        f.write('\n')
        for g in existing_games:
            f.write('\n' + g['lines'].strip() + '\n')


# ===== Anbernic gamelist.xml =====

def _xml_escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def write_gamelist_xml(gamelist_path, results):
    """写入或更新 Anbernic/EmulationStation gamelist.xml"""
    backup_file(gamelist_path)
    existing = {}
    if gamelist_path.exists():
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(gamelist_path)
            for game_el in tree.findall('game'):
                path_el = game_el.find('path')
                if path_el is not None and path_el.text:
                    existing[path_el.text] = game_el
        except Exception:
            pass

    import xml.etree.ElementTree as ET
    root = ET.Element('gameList')

    for game_el in existing.values():
        root.append(game_el)

    for info, image_filename in results:
        game_path = f"./{info['filename']}"
        if game_path in existing:
            game_el = existing[game_path]
        else:
            game_el = ET.SubElement(root, 'game')

        def _set(tag, val):
            if not val:
                return
            el = game_el.find(tag)
            if el is None:
                el = ET.SubElement(game_el, tag)
            el.text = str(val)

        _set('path', game_path)
        _set('name', info.get('title', ''))
        _set('image', f"./images/{image_filename}")
        if info.get('description'):
            _set('desc', info['description'])
        if info.get('publisher'):
            _set('developer', info['publisher'])
            _set('publisher', info['publisher'])
        if info.get('genres'):
            _set('genre', info['genres'])
        if info.get('players'):
            _set('players', info['players'])
        if info.get('release'):
            date_str = info['release'].replace('-', '') + 'T000000'
            _set('releasedate', date_str)
        if info.get('rating'):
            try:
                r = float(info['rating'].rstrip('%')) / 100.0
                _set('rating', f"{r:.2f}")
            except (ValueError, AttributeError):
                pass

    tree = ET.ElementTree(root)
    ET.indent(tree, space='  ')
    tree.write(str(gamelist_path), encoding='utf-8', xml_declaration=True)


# ===== 批量处理 =====

def batch_extract(xci_dir, keys_path, generate_meta=False, generate_gamelist=False,
                  online_mode=False, api_key=None, lang_code='en', google_lang='',
                  translate=False, log=print):
    keys = parse_keys(keys_path)
    xci_files = list(Path(xci_dir).glob('*.xci')) + list(Path(xci_dir).glob('*.XCI'))
    seen = set()
    unique = []
    for p in xci_files:
        if p.name.lower() not in seen:
            seen.add(p.name.lower())
            unique.append(p)
    xci_files = unique

    if not xci_files:
        log("[错误] 所选目录中没有找到 XCI 文件")
        return

    output_dir = Path(xci_dir) / 'images'
    output_dir.mkdir(exist_ok=True)

    genres_map = {}
    publishers_map = {}
    if api_key:
        log("正在获取 TheGamesDB 元数据映射表...")
        genres_map = fetch_genres_map(api_key)
        publishers_map = fetch_publishers_map(api_key)
        if genres_map:
            log(f"  已加载 {len(genres_map)} 个类型标签")
        else:
            log("  [警告] 无法获取类型标签映射")

    success = 0
    failed = 0
    meta_results = []

    for i, xci_path in enumerate(xci_files):
        log(f"[{i+1}/{len(xci_files)}] {xci_path.name}")
        try:
            info = extract_xci_info(str(xci_path), keys, lang_code, log)
            if info:
                safe_title = sanitize_filename(info['title'])
                img_name = f"{safe_title}.jpg"
                out_path = output_dir / img_name
                with open(out_path, 'wb') as out_f:
                    out_f.write(info['icon_data'])
                log(f"  [OK] -> images/{img_name}")
                if (generate_meta or generate_gamelist) and online_mode:
                    search_name = info.get('title_en') or info['title']
                    if api_key:
                        online = fetch_game_metadata(
                            search_name, api_key, genres_map, publishers_map)
                    else:
                        online = fetch_wikipedia_metadata(search_name)
                    if online:
                        if translate and google_lang:
                            for k in ('description', 'genres'):
                                if online.get(k):
                                    online[k] = google_translate(
                                        online[k], google_lang)
                        info.update(online)
                        log(f"  [在线] 已补全元数据")
                    else:
                        log(f"  [在线] 未找到匹配")
                meta_results.append((info, img_name))
                success += 1
            else:
                failed += 1
        except Exception as e:
            log(f"  [失败] {e}")
            failed += 1

    if generate_meta and meta_results:
        meta_path = Path(xci_dir) / 'metadata.pegasus.txt'
        write_pegasus_meta(meta_path, xci_dir, meta_results)
        log(f"\nPegasus 元数据已写入: {meta_path}")

    if generate_gamelist and meta_results:
        gl_path = Path(xci_dir) / 'gamelist.xml'
        write_gamelist_xml(gl_path, meta_results)
        log(f"Anbernic gamelist 已写入: {gl_path}")

    log(f"\n处理完成! 成功: {success}, 失败: {failed}")
    log(f"输出目录: {output_dir}")


# ===== GUI =====

_APP_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
CONFIG_FILE = _APP_DIR / 'xci_extractor_config.json'


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("XCI 封面提取工具")
        self.resizable(True, True)
        self.minsize(560, 480)
        self._build_ui()
        self._load_config()
        self._running = False

    def _build_ui(self):
        pad = {'padx': 8, 'pady': 4}
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill='both', expand=True)

        # 游戏目录
        ttk.Label(frm, text="游戏目录 *").grid(row=0, column=0, sticky='w', **pad)
        self.dir_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.dir_var, width=40).grid(row=0, column=1, sticky='ew', **pad)
        ttk.Button(frm, text="浏览", command=self._pick_dir).grid(row=0, column=2, **pad)

        # Keys 文件
        ttk.Label(frm, text="prod.keys *").grid(row=1, column=0, sticky='w', **pad)
        self.keys_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.keys_var, width=40).grid(row=1, column=1, sticky='ew', **pad)
        ttk.Button(frm, text="浏览", command=self._pick_keys).grid(row=1, column=2, **pad)

        # 语言
        ttk.Label(frm, text="语言").grid(row=2, column=0, sticky='w', **pad)
        self.lang_var = tk.StringVar()
        lang_names = [l[0] for l in LANGUAGES]
        cb = ttk.Combobox(frm, textvariable=self.lang_var, values=lang_names, state='readonly', width=20)
        cb.grid(row=2, column=1, sticky='w', **pad)
        cb.current(_get_default_lang_index())

        # 分割线
        ttk.Separator(frm, orient='horizontal').grid(row=3, column=0, columnspan=3, sticky='ew', pady=8)

        # 选项
        self.meta_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="生成 Pegasus Frontend 元数据", variable=self.meta_var).grid(
            row=4, column=0, columnspan=3, sticky='w', **pad)

        self.gamelist_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="生成 Anbernic gamelist.xml", variable=self.gamelist_var).grid(
            row=5, column=0, columnspan=3, sticky='w', **pad)

        self.online_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="在线补全元数据 (Wikipedia/TheGamesDB)", variable=self.online_var).grid(
            row=6, column=0, columnspan=3, sticky='w', **pad)

        self.translate_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="翻译在线元数据到所选语言 (Google Translate)", variable=self.translate_var).grid(
            row=7, column=0, columnspan=3, sticky='w', **pad)

        # 代理
        ttk.Label(frm, text="代理 (可选)").grid(row=8, column=0, sticky='w', **pad)
        self.proxy_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.proxy_var, width=40).grid(row=8, column=1, sticky='ew', **pad)
        ttk.Label(frm, text="如 http://127.0.0.1:7890", foreground='gray').grid(row=8, column=2, sticky='w', **pad)

        # API Key
        ttk.Label(frm, text="API Key (可选)").grid(row=9, column=0, sticky='w', **pad)
        self.apikey_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.apikey_var, width=40).grid(row=9, column=1, sticky='ew', **pad)
        ttk.Label(frm, text="TheGamesDB", foreground='gray').grid(row=9, column=2, sticky='w', **pad)

        # 开始按钮
        self.start_btn = ttk.Button(frm, text="开始提取", command=self._start)
        self.start_btn.grid(row=10, column=0, columnspan=3, pady=12)

        # 日志区域
        ttk.Label(frm, text="日志:").grid(row=11, column=0, sticky='w', **pad)
        self.log_text = tk.Text(frm, height=12, width=70, state='disabled', bg='#1e1e1e', fg='#cccccc')
        self.log_text.grid(row=12, column=0, columnspan=3, sticky='nsew', **pad)
        frm.rowconfigure(12, weight=1)
        frm.columnconfigure(1, weight=1)

    def _pick_dir(self):
        p = filedialog.askdirectory(title="选择 XCI 文件所在目录")
        if p:
            self.dir_var.set(p)

    def _pick_keys(self):
        p = filedialog.askopenfilename(
            title="选择 prod.keys 文件",
            filetypes=[("Keys files", "*.keys"), ("All files", "*.*")])
        if p:
            self.keys_var.set(p)

    def _load_config(self):
        if not CONFIG_FILE.exists():
            return
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
            if cfg.get('xci_dir'):
                self.dir_var.set(cfg['xci_dir'])
            if cfg.get('keys_path'):
                self.keys_var.set(cfg['keys_path'])
            if cfg.get('language'):
                self.lang_var.set(cfg['language'])
            if 'proxy' in cfg:
                self.proxy_var.set(cfg['proxy'])
            if 'api_key' in cfg:
                self.apikey_var.set(cfg['api_key'])
            if 'generate_meta' in cfg:
                self.meta_var.set(cfg['generate_meta'])
            if 'generate_gamelist' in cfg:
                self.gamelist_var.set(cfg['generate_gamelist'])
            if 'online_mode' in cfg:
                self.online_var.set(cfg['online_mode'])
            if 'translate' in cfg:
                self.translate_var.set(cfg['translate'])
        except Exception:
            pass

    def _save_config(self):
        cfg = {
            'xci_dir': self.dir_var.get().strip(),
            'keys_path': self.keys_var.get().strip(),
            'language': self.lang_var.get(),
            'proxy': self.proxy_var.get().strip(),
            'api_key': self.apikey_var.get().strip(),
            'generate_meta': self.meta_var.get(),
            'generate_gamelist': self.gamelist_var.get(),
            'online_mode': self.online_var.get(),
            'translate': self.translate_var.get(),
        }
        try:
            CONFIG_FILE.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2),
                encoding='utf-8')
        except Exception:
            pass

    def _log(self, msg):
        self.log_text.config(state='normal')
        self.log_text.insert('end', msg + '\n')
        self.log_text.see('end')
        self.log_text.config(state='disabled')
        self.update_idletasks()

    def _start(self):
        xci_dir = self.dir_var.get().strip()
        keys_path = self.keys_var.get().strip()
        if not xci_dir or not Path(xci_dir).is_dir():
            messagebox.showerror("错误", "请选择有效的游戏目录")
            return
        if not keys_path or not Path(keys_path).is_file():
            messagebox.showerror("错误", "请选择有效的 prod.keys 文件")
            return
        if self._running:
            return
        self._running = True
        self.start_btn.config(state='disabled')
        self.log_text.config(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.config(state='disabled')

        proxy = self.proxy_var.get().strip()
        if proxy:
            set_proxy(proxy)
            self._log(f"已设置代理: {proxy}")

        lang_idx = next(
            (i for i, l in enumerate(LANGUAGES) if l[0] == self.lang_var.get()), 1)
        lang_code = LANGUAGES[lang_idx][1]
        google_lang = LANGUAGES[lang_idx][2]

        params = dict(
            xci_dir=xci_dir,
            keys_path=keys_path,
            generate_meta=self.meta_var.get(),
            generate_gamelist=self.gamelist_var.get(),
            online_mode=self.online_var.get(),
            api_key=self.apikey_var.get().strip() or None,
            lang_code=lang_code,
            google_lang=google_lang,
            translate=self.translate_var.get(),
            log=self._log_threadsafe,
        )
        self._save_config()
        threading.Thread(target=self._run, args=(params,), daemon=True).start()

    def _log_threadsafe(self, msg):
        self.after(0, self._log, msg)

    def _run(self, params):
        try:
            batch_extract(**params)
        except Exception as e:
            self._log_threadsafe(f"\n[错误] {e}")
        finally:
            self.after(0, self._on_done)

    def _on_done(self):
        self._running = False
        self.start_btn.config(state='normal')
        messagebox.showinfo("完成", "处理完成，请查看日志输出")


if __name__ == '__main__':
    App().mainloop()
