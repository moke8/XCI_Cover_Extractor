#!/usr/bin/env python3
"""Nintendo Switch (XCI) - 平台模块"""

import struct
import locale
import threading
from pathlib import Path

from Crypto.Cipher import AES

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QCheckBox, QTextEdit, QScrollArea, QFrame, QFileDialog, QMessageBox,
)
from PySide6.QtCore import Qt, Signal, QThread

from game_cover_extractor import (
    FlowLayout, GameCard, GameDetailDialog,
    load_showcase_games, sanitize_filename, collect_game_files,
    write_pegasus_meta, write_gamelist_xml,
    _has_cjk, _is_traditional,
)
from datasource import set_proxy, google_translate, get_datasource

PLATFORM_TITLE = "Nintendo Switch"
CONFIG_FILENAME = "switch_config.json"
TGDB_PLATFORM_ID = 4971

COLLECTION_DEFAULTS = {
    'collection': 'Nintendo Switch',
    'shortname': 'switch',
    'extensions': 'xci',
}


# ===== 密钥解析 =====

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


# ===== AES 加解密 =====

def _xts_gf_mult(tweak):
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


# ===== XCI/NCA 容器解析 =====

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


# ===== NACP 元数据解析 =====

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


# ===== XCI 提取 =====

def extract_xci_info(xci_path, keys, lang_code='en', log=print):
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


# ===== 批量处理 =====

def batch_extract(xci_dir, keys_path, generate_meta=False, generate_gamelist=False,
                  online_mode=False, api_key=None, datasource_name='thegamesdb',
                  lang_code='en', google_lang='',
                  translate=False, video=False, thread_count=4,
                  log=print, cancel_event=None):
    import shutil
    from concurrent.futures import ThreadPoolExecutor, as_completed

    keys = parse_keys(keys_path)
    game_files, temp_dir = collect_game_files(xci_dir, ('xci',))

    if not game_files:
        log("[错误] 所选目录中没有找到 XCI 文件")
        return

    output_dir = Path(xci_dir) / 'images'
    output_dir.mkdir(exist_ok=True)

    source = get_datasource(datasource_name)
    if source and online_mode and api_key:
        source.initialize(api_key, log)

    total = len(game_files)
    counter = [0]
    lock = threading.Lock()
    success = [0]
    failed = [0]
    meta_results = []

    def process_game(xci_path, display_name):
        if cancel_event and cancel_event.is_set():
            return None
        with lock:
            counter[0] += 1
            idx = counter[0]
        log(f"[{idx}/{total}] {display_name}")
        try:
            info = extract_xci_info(str(xci_path), keys, lang_code, log)
            if not info:
                return None
            info['filename'] = display_name
            safe_title = sanitize_filename(info['title'])
            img_name = f"{safe_title}.jpg"
            out_path = output_dir / img_name
            with open(out_path, 'wb') as out_f:
                out_f.write(info['icon_data'])
            log(f"  [OK] -> images/{img_name}")

            if online_mode and source:
                search_name = info.get('title_en') or info['title']
                online = source.fetch_metadata(
                    search_name, TGDB_PLATFORM_ID,
                    platform_name=PLATFORM_TITLE)
                if not online:
                    wiki = get_datasource('wikipedia')
                    if wiki:
                        online = wiki.fetch_metadata(
                            search_name,
                            platform_name=PLATFORM_TITLE)
                if online:
                    if translate and google_lang:
                        for k in ('description', 'genres'):
                            if online.get(k):
                                online[k] = google_translate(
                                    online[k], google_lang)
                    if video:
                        if not online.get('youtube'):
                            log(f"  [视频] 未找到视频: {display_name}")
                    else:
                        online.pop('youtube', None)
                    info.update(online)
                    log(f"  [在线] 已补全元数据")
                else:
                    log(f"  [在线] 未找到匹配")

            if translate and google_lang and google_lang.startswith('zh'):
                if not _has_cjk(info['title']):
                    translated = google_translate(info['title'], google_lang)
                    if translated and translated != info['title']:
                        info['title'] = translated

            return (info, img_name)
        except Exception as e:
            log(f"  [失败] {e}")
            return None

    try:
        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            futures = {executor.submit(process_game, p, n): n
                       for p, n in game_files}
            for fut in as_completed(futures):
                if cancel_event and cancel_event.is_set():
                    log("\n[取消] 用户已取消操作")
                    break
                try:
                    result = fut.result()
                except Exception:
                    result = None
                if result:
                    meta_results.append(result)
                    success[0] += 1
                else:
                    failed[0] += 1
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    if generate_meta and meta_results:
        meta_path = Path(xci_dir) / 'metadata.pegasus.txt'
        write_pegasus_meta(meta_path, meta_results, COLLECTION_DEFAULTS)
        log(f"\nPegasus 元数据已写入: {meta_path}")

    if generate_gamelist and meta_results:
        gl_path = Path(xci_dir) / 'gamelist.xml'
        write_gamelist_xml(gl_path, meta_results)
        log(f"Anbernic gamelist 已写入: {gl_path}")

    log(f"\n处理完成! 成功: {success[0]}, 失败: {failed[0]}")
    log(f"输出目录: {output_dir}")


# ===== 工作线程 =====

class ExtractWorker(QThread):
    log_signal = Signal(str)
    finished_signal = Signal()

    def __init__(self, params):
        super().__init__()
        self.params = params
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        try:
            batch_extract(**self.params, log=self.log_signal.emit,
                          cancel_event=self.cancel_event)
        except Exception as e:
            self.log_signal.emit(f"\n[错误] {e}")
        finally:
            self.finished_signal.emit()


# ===== 平台 Tab =====

class PlatformTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(16, 16, 16, 16)
        main.setSpacing(12)

        ctrl = QFrame()
        ctrl.setObjectName('controlPanel')
        cl = QVBoxLayout(ctrl)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)

        r1 = QHBoxLayout()
        lbl1 = QLabel("游戏目录")
        lbl1.setFixedWidth(65)
        r1.addWidget(lbl1)
        self.dir_input = QLineEdit()
        self.dir_input.setPlaceholderText("选择包含 XCI 文件的目录...")
        r1.addWidget(self.dir_input, 1)
        db = QPushButton("浏览")
        db.setFixedWidth(60)
        db.clicked.connect(self._pick_dir)
        r1.addWidget(db)
        cl.addLayout(r1)

        r2 = QHBoxLayout()
        lbl2 = QLabel("prod.keys")
        lbl2.setFixedWidth(65)
        r2.addWidget(lbl2)
        self.keys_input = QLineEdit()
        self.keys_input.setPlaceholderText("选择 prod.keys 密钥文件...")
        r2.addWidget(self.keys_input, 1)
        kb = QPushButton("浏览")
        kb.setFixedWidth(60)
        kb.clicked.connect(self._pick_keys)
        r2.addWidget(kb)
        cl.addLayout(r2)

        r3 = QHBoxLayout()
        self.meta_check = QCheckBox("Pegasus")
        self.meta_check.setChecked(True)
        r3.addWidget(self.meta_check)
        self.gamelist_check = QCheckBox("gamelist.xml")
        r3.addWidget(self.gamelist_check)
        r3.addStretch()
        self.start_btn = QPushButton("开始提取")
        self.start_btn.setObjectName('startBtn')
        self.start_btn.clicked.connect(self._start_extract)
        r3.addWidget(self.start_btn)
        cl.addLayout(r3)

        main.addWidget(ctrl)

        hdr = QHBoxLayout()
        sl = QLabel("游戏展柜")
        sl.setStyleSheet('font-size: 16px; font-weight: bold; color: #e6edf3;')
        hdr.addWidget(sl)
        self.showcase_status = QLabel("加载游戏目录后显示")
        self.showcase_status.setStyleSheet('color: #484f58; font-size: 12px;')
        hdr.addWidget(self.showcase_status)
        hdr.addStretch()
        main.addLayout(hdr)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.showcase_widget = QWidget()
        self.showcase_widget.setStyleSheet('background: transparent;')
        self.showcase_layout = FlowLayout(self.showcase_widget, spacing=16)
        self.showcase_layout.setContentsMargins(4, 4, 4, 4)
        self.scroll_area.setWidget(self.showcase_widget)
        main.addWidget(self.scroll_area, 1)

        self.log_toggle = QPushButton("▶ 日志")
        self.log_toggle.setObjectName('logToggle')
        self.log_toggle.clicked.connect(self._toggle_log)
        main.addWidget(self.log_toggle)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(160)
        self.log_text.setVisible(False)
        main.addWidget(self.log_text)

    def _toggle_log(self):
        vis = not self.log_text.isVisible()
        self.log_text.setVisible(vis)
        self.log_toggle.setText(f"{'▼' if vis else '▶'} 日志")

    def _pick_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择游戏目录")
        if path:
            self.dir_input.setText(path)
            self._on_dir_changed(path)

    def _pick_keys(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 prod.keys", "", "Keys files (*.keys);;All files (*.*)")
        if path:
            self.keys_input.setText(path)

    def _on_dir_changed(self, directory):
        base = Path(directory)
        has_pegasus = (base / 'metadata.pegasus.txt').exists()
        has_gamelist = (base / 'gamelist.xml').exists()
        if has_pegasus or has_gamelist:
            self.meta_check.setChecked(has_pegasus)
            self.gamelist_check.setChecked(has_gamelist)
        self._load_showcase(directory)

    def _load_showcase(self, directory):
        self.showcase_layout.clear()
        games, has_pegasus, has_gamelist = load_showcase_games(directory)
        if not games:
            if not has_pegasus and not has_gamelist:
                self.showcase_status.setText("未找到 metadata.pegasus.txt 或 gamelist.xml")
            else:
                self.showcase_status.setText("元数据文件中没有游戏条目")
            return
        sources = []
        if has_pegasus:
            sources.append('metadata.pegasus.txt')
        if has_gamelist:
            sources.append('gamelist.xml')
        self.showcase_status.setText(
            f"已加载 {len(games)} 款游戏 (来自 {', '.join(sources)})")
        for game in games:
            card = GameCard(game, self.showcase_widget)
            card.clicked.connect(self._show_detail)
            self.showcase_layout.addWidget(card)

    def _show_detail(self, game):
        GameDetailDialog(game, self).exec()

    def _log(self, msg):
        self.log_text.append(msg)
        if not self.log_text.isVisible():
            self.log_text.setVisible(True)
            self.log_toggle.setText("▼ 日志")

    def _start_extract(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self.start_btn.setEnabled(False)
            self.start_btn.setText("取消中...")
            return
        xci_dir = self.dir_input.text().strip()
        keys_path = self.keys_input.text().strip()
        if not xci_dir or not Path(xci_dir).is_dir():
            QMessageBox.warning(self, "错误", "请选择有效的游戏目录")
            return
        if not keys_path or not Path(keys_path).is_file():
            QMessageBox.warning(self, "错误", "请选择有效的 prod.keys 文件")
            return
        self.start_btn.setText("取消")
        self.start_btn.setStyleSheet(
            'background: #da3633; border: none; color: white; '
            'font-weight: 600; padding: 10px 28px; font-size: 14px;')
        self.log_text.clear()
        mw = self.window()
        g = mw.get_global_settings()
        if g['proxy']:
            set_proxy(g['proxy'])
            self._log(f"已设置代理: {g['proxy']}")
        params = dict(
            xci_dir=xci_dir, keys_path=keys_path,
            generate_meta=self.meta_check.isChecked(),
            generate_gamelist=self.gamelist_check.isChecked(),
            online_mode=g['online_mode'],
            api_key=g['api_key'] or None,
            datasource_name=g.get('datasource', 'thegamesdb'),
            lang_code=g['lang_code'], google_lang=g['google_lang'],
            translate=g['translate'],
            video=g.get('video', False),
            thread_count=g.get('thread_count', 4),
        )
        mw.save_config()
        self._worker = ExtractWorker(params)
        self._worker.log_signal.connect(self._log)
        self._worker.finished_signal.connect(self._on_done)
        self._worker.start()

    def _on_done(self):
        self.start_btn.setText("开始提取")
        self.start_btn.setStyleSheet('')
        self.start_btn.setEnabled(True)
        QMessageBox.information(self, "完成", "处理完成，请查看日志输出")
        xci_dir = self.dir_input.text().strip()
        if xci_dir and Path(xci_dir).is_dir():
            self._load_showcase(xci_dir)

    def load_config(self, cfg):
        if cfg.get('xci_dir'):
            self.dir_input.setText(cfg['xci_dir'])
        if cfg.get('keys_path'):
            self.keys_input.setText(cfg['keys_path'])
        if 'generate_meta' in cfg:
            self.meta_check.setChecked(cfg['generate_meta'])
        if 'generate_gamelist' in cfg:
            self.gamelist_check.setChecked(cfg['generate_gamelist'])
        xci_dir = cfg.get('xci_dir', '')
        if xci_dir and Path(xci_dir).is_dir():
            self._on_dir_changed(xci_dir)

    def save_config(self):
        return {
            'xci_dir': self.dir_input.text().strip(),
            'keys_path': self.keys_input.text().strip(),
            'generate_meta': self.meta_check.isChecked(),
            'generate_gamelist': self.gamelist_check.isChecked(),
        }
