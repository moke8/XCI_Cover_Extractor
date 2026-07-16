#!/usr/bin/env python3
"""Nintendo DS (NDS) - 平台模块"""

import struct
import threading
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QCheckBox, QTextEdit, QScrollArea, QFrame, QFileDialog, QMessageBox,
)
from PySide6.QtCore import Qt, Signal, QThread, QBuffer, QIODevice
from PySide6.QtGui import QImage, QColor

from game_cover_extractor import (
    FlowLayout, GameCard, GameDetailDialog,
    load_showcase_games, sanitize_filename, collect_game_files,
    write_pegasus_meta, write_gamelist_xml, _has_cjk,
)
from datasource import set_proxy, google_translate, get_datasource, _http_get_bytes

PLATFORM_TITLE = "Nintendo DS"
CONFIG_FILENAME = "nds_config.json"
TGDB_PLATFORM_ID = 3

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

    publisher = publishers.get('en') or publishers.get('ja') or next(iter(publishers.values()), None)

    return {'selected': selected, 'en': titles.get('en'), 'publisher': publisher, **titles}


def extract_nds_info(nds_path, lang_code='en', log=print):
    try:
        with open(nds_path, 'rb') as f:
            header = f.read(0x200)
            if len(header) < 0x200:
                log(f"  [跳过] 文件头不完整")
                return None

            game_title = header[0x000:0x00C].decode('ascii', errors='ignore').strip('\x00')
            game_code = header[0x00C:0x010].decode('ascii', errors='ignore').strip('\x00')

            icon_offset = struct.unpack_from('<I', header, 0x068)[0]
            if icon_offset == 0:
                log(f"  [跳过] 无图标数据")
                return None

            f.seek(icon_offset)
            icon_title_data = f.read(0x0840)
            if len(icon_title_data) < 0x0840:
                log(f"  [跳过] 图标数据不完整")
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
        log(f"  [失败] 解析错误: {e}")
        return None


# ===== 批量处理 =====

def batch_extract(nds_dir, generate_meta=False, generate_gamelist=False,
                  online_mode=False, api_key=None, datasource_name='thegamesdb',
                  lang_code='en', google_lang='',
                  translate=False, video=False, thread_count=4,
                  log=print, cancel_event=None):
    import shutil
    from concurrent.futures import ThreadPoolExecutor, as_completed

    game_files, temp_dir = collect_game_files(nds_dir, ('nds',))

    if not game_files:
        log("[错误] 所选目录中没有找到 NDS 文件")
        return

    output_dir = Path(nds_dir) / 'images'
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

    def process_game(nds_path, display_name):
        if cancel_event and cancel_event.is_set():
            return None
        with lock:
            counter[0] += 1
            idx = counter[0]
        log(f"[{idx}/{total}] {display_name}")
        try:
            info = extract_nds_info(str(nds_path), lang_code, log)
            if not info:
                return None
            info['filename'] = display_name
            safe_title = sanitize_filename(info['title'])
            img_data = info.pop('icon_data')
            img_name = f"{safe_title}.png"
            boxart_downloaded = False

            if online_mode and source:
                search_name = info.get('title_en') or info['title']
                online = source.fetch_metadata(
                    search_name, TGDB_PLATFORM_ID,
                    platform_name=PLATFORM_TITLE,
                    include_boxart=True)
                if not online:
                    wiki = get_datasource('wikipedia')
                    if wiki:
                        online = wiki.fetch_metadata(
                            search_name,
                            platform_name=PLATFORM_TITLE)
                if online:
                    boxart_url = online.pop('boxart_url', None)
                    if boxart_url:
                        boxart_data = _http_get_bytes(boxart_url)
                        if boxart_data:
                            ext = Path(boxart_url).suffix or '.jpg'
                            img_name = f"{safe_title}{ext}"
                            img_data = boxart_data
                            boxart_downloaded = True
                            log(f"  [封面] 已下载在线封面")
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

            out_path = output_dir / img_name
            with open(out_path, 'wb') as out_f:
                out_f.write(img_data)
            if boxart_downloaded:
                log(f"  [OK] -> images/{img_name}")
            else:
                log(f"  [OK] -> images/{img_name} (ROM图标)")
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
        meta_path = Path(nds_dir) / 'metadata.pegasus.txt'
        write_pegasus_meta(meta_path, meta_results, COLLECTION_DEFAULTS)
        log(f"\nPegasus 元数据已写入: {meta_path}")

    if generate_gamelist and meta_results:
        gl_path = Path(nds_dir) / 'gamelist.xml'
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
        self.dir_input.setPlaceholderText("选择包含 NDS 文件的目录...")
        r1.addWidget(self.dir_input, 1)
        db = QPushButton("浏览")
        db.setFixedWidth(60)
        db.clicked.connect(self._pick_dir)
        r1.addWidget(db)
        cl.addLayout(r1)

        r2 = QHBoxLayout()
        self.meta_check = QCheckBox("Pegasus")
        self.meta_check.setChecked(True)
        r2.addWidget(self.meta_check)
        self.gamelist_check = QCheckBox("gamelist.xml")
        r2.addWidget(self.gamelist_check)
        r2.addStretch()
        self.start_btn = QPushButton("开始提取")
        self.start_btn.setObjectName('startBtn')
        self.start_btn.clicked.connect(self._start_extract)
        r2.addWidget(self.start_btn)
        cl.addLayout(r2)

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
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        nds_dir = self.dir_input.text().strip()
        if not nds_dir or not Path(nds_dir).is_dir():
            QMessageBox.warning(self, "错误", "请选择有效的游戏目录")
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
            nds_dir=nds_dir,
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
        nds_dir = self.dir_input.text().strip()
        if nds_dir and Path(nds_dir).is_dir():
            self._load_showcase(nds_dir)

    def load_config(self, cfg):
        if cfg.get('nds_dir'):
            self.dir_input.setText(cfg['nds_dir'])
        if 'generate_meta' in cfg:
            self.meta_check.setChecked(cfg['generate_meta'])
        if 'generate_gamelist' in cfg:
            self.gamelist_check.setChecked(cfg['generate_gamelist'])
        nds_dir = cfg.get('nds_dir', '')
        if nds_dir and Path(nds_dir).is_dir():
            self._on_dir_changed(nds_dir)

    def save_config(self):
        return {
            'nds_dir': self.dir_input.text().strip(),
            'generate_meta': self.meta_check.isChecked(),
            'generate_gamelist': self.gamelist_check.isChecked(),
        }
