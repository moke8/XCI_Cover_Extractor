#!/usr/bin/env python3
"""平台调度：BasePlatformTab 基类、平台注册、collect_game_files、展柜解析"""

import re
import zipfile
import tempfile
from pathlib import Path

from scrape import sanitize_filename


# ===== 文件收集 =====

def collect_game_files(directory, extensions):
    """扫描目录中的游戏文件，包括 ZIP 压缩包。
    extensions: 小写后缀元组，不含点，如 ('xci',) 或 ('nds',)
    返回 (entries, temp_dir)。
    """
    dir_path = Path(directory)
    entries = []
    seen = set()

    for ext in extensions:
        for p in list(dir_path.glob(f'*.{ext}')) + list(dir_path.glob(f'*.{ext.upper()}')):
            if p.name.lower() not in seen:
                seen.add(p.name.lower())
                entries.append((p, p.name))

    temp_dir = None
    for zp in list(dir_path.glob('*.zip')) + list(dir_path.glob('*.ZIP')):
        if zp.name.lower() in seen:
            continue
        try:
            with zipfile.ZipFile(zp) as zf:
                members = [m for m in zf.namelist()
                           if any(m.lower().endswith(f'.{ext}') for ext in extensions)]
                if len(members) != 1:
                    continue
                if temp_dir is None:
                    temp_dir = tempfile.mkdtemp(prefix='game_extract_')
                member_ext = Path(members[0]).suffix
                temp_path = Path(temp_dir) / (zp.stem + member_ext)
                temp_path.write_bytes(zf.read(members[0]))
                seen.add(zp.name.lower())
                entries.append((temp_path, zp.name))
        except Exception:
            pass

    return entries, temp_dir


# ===== 游戏展柜解析 =====

def _resolve_asset(base_dir, rel_path):
    p = base_dir / rel_path
    if p.exists():
        return str(p)
    for ext in ('.jpg', '.png', '.webp', '.jpeg'):
        alt = p.with_suffix(ext)
        if alt.exists():
            return str(alt)
    return str(p)


def _media_candidates(game):
    candidates = []
    for key in ('title', 'developer', 'file'):
        val = game.get(key, '')
        if not val:
            continue
        if key == 'file':
            val = Path(val).stem
        safe = sanitize_filename(val)
        if safe and safe not in candidates:
            candidates.append(safe)
    return candidates


def _find_media_cover(base_dir, game):
    candidates = _media_candidates(game)
    for safe in candidates:
        media_dir = base_dir / 'media' / safe
        if media_dir.is_dir():
            for name in ('boxfront', 'logo'):
                for ext in ('.jpg', '.png', '.webp', '.jpeg'):
                    p = media_dir / f'{name}{ext}'
                    if p.exists():
                        return str(p)
    images_dir = base_dir / 'images'
    if images_dir.is_dir():
        for safe in candidates:
            for ext in ('.jpg', '.png', '.webp', '.jpeg'):
                p = images_dir / f'{safe}{ext}'
                if p.exists():
                    return str(p)
    return None


def _find_media_video(base_dir, game):
    candidates = _media_candidates(game)
    for safe in candidates:
        media_dir = base_dir / 'media' / safe
        if media_dir.is_dir():
            for ext in ('.mp4', '.webm', '.avi', '.mkv'):
                p = media_dir / f'video{ext}'
                if p.exists():
                    return str(p)
    return None


def parse_pegasus_for_showcase(meta_path, base_dir):
    if not meta_path.exists():
        return []
    text = meta_path.read_text(encoding='utf-8')
    blocks = re.split(r'\n(?=game:)', text)
    games = []
    mapping = {
        'game': 'title', 'file': 'file', 'developer': 'developer',
        'genre': 'genre', 'players': 'players', 'release': 'release',
        'rating': 'rating', 'description': 'description',
        'x-id': 'game_id',
    }
    for block in blocks:
        block = block.strip()
        if not block.startswith('game:'):
            continue
        game = {}
        for line in block.split('\n'):
            line = line.strip()
            if ':' not in line:
                continue
            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip()
            if key in mapping:
                game[mapping[key]] = value
            elif key in ('assets.boxFront', 'assets.box_front'):
                game['cover'] = _resolve_asset(base_dir, value)
            elif key in ('assets.video', 'assets.Video'):
                game['video'] = str(base_dir / value)
        if game.get('title'):
            game['source'] = 'pegasus'
            games.append(game)
    return games


def parse_gamelist_for_showcase(gamelist_path, base_dir):
    if not gamelist_path.exists():
        return []
    import xml.etree.ElementTree as ET
    try:
        tree = ET.parse(gamelist_path)
    except Exception:
        return []
    games = []
    tag_map = {
        'name': 'title', 'desc': 'description', 'developer': 'developer',
        'publisher': 'publisher', 'genre': 'genre', 'players': 'players',
        'releasedate': 'release', 'rating': 'rating',
    }
    for game_el in tree.findall('game'):
        game = {'source': 'gamelist'}
        for tag, key in tag_map.items():
            el = game_el.find(tag)
            if el is not None and el.text:
                game[key] = el.text
        id_el = game_el.find('id')
        if id_el is not None and id_el.text:
            game['game_id'] = id_el.text
        path_el = game_el.find('path')
        if path_el is not None and path_el.text:
            game['file'] = path_el.text.lstrip('./')
        image_el = game_el.find('image')
        if image_el is not None and image_el.text:
            img = image_el.text
            if img.startswith('./'):
                img = img[2:]
            game['cover'] = _resolve_asset(base_dir, img)
        video_el = game_el.find('video')
        if video_el is not None and video_el.text:
            vid = video_el.text
            if vid.startswith('./'):
                vid = vid[2:]
            game['video'] = str(base_dir / vid)
        if game.get('title'):
            games.append(game)
    return games


def load_showcase_games(directory, extensions=None):
    base_dir = Path(directory)
    pegasus_path = base_dir / 'metadata.pegasus.txt'
    gamelist_path = base_dir / 'gamelist.xml'
    has_pegasus = pegasus_path.exists()
    has_gamelist = gamelist_path.exists()
    pegasus_games = parse_pegasus_for_showcase(pegasus_path, base_dir) if has_pegasus else []
    gamelist_games = parse_gamelist_for_showcase(gamelist_path, base_dir) if has_gamelist else []
    meta_by_file = {}
    for g in pegasus_games:
        key = g.get('file', g.get('title', ''))
        meta_by_file[key] = g
    for g in gamelist_games:
        key = g.get('file', g.get('title', ''))
        if key not in meta_by_file:
            meta_by_file[key] = g
        else:
            for k, v in g.items():
                if k not in meta_by_file[key] or not meta_by_file[key][k]:
                    meta_by_file[key][k] = v
    if extensions:
        ext_set = set(f'.{e}' for e in extensions.split())
        ext_set.add('.zip')
        games = []
        for f in sorted(base_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() not in ext_set:
                continue
            fname = f.name
            if fname in meta_by_file:
                g = meta_by_file[fname]
                g['path'] = str(f)
                games.append(g)
            else:
                games.append({'title': f.stem, 'file': fname, 'path': str(f), 'source': 'file'})
    else:
        games = list(meta_by_file.values())
    for g in games:
        if not g.get('path') and g.get('file'):
            full = base_dir / g['file']
            if full.exists():
                g['path'] = str(full)
    for g in games:
        if not g.get('title'):
            continue
        cover = g.get('cover', '')
        if not cover or not Path(cover).exists():
            found = _find_media_cover(base_dir, g)
            if found:
                g['cover'] = found
        video = g.get('video', '')
        if not video or (not video.startswith('http') and not Path(video).exists()):
            found = _find_media_video(base_dir, g)
            if found:
                g['video'] = found
    return games, has_pegasus, has_gamelist


# ===== 平台分组 =====

PUBLISHER_GROUPS = [
    ('Nintendo', ['Nintendo Switch', 'Nintendo DS', 'Nintendo 3DS',
                  'Nintendo Wii', 'Nintendo GameCube', 'Game Boy Advance']),
    ('Sony', ['PlayStation Portable', 'PlayStation 1']),
    ('Sega', ['Sega Dreamcast']),
]


# ===== BasePlatformTab =====

try:
    from PySide6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
        QCheckBox, QTextEdit, QScrollArea, QFrame, QFileDialog, QMessageBox,
        QMenu, QInputDialog, QDialog, QDialogButtonBox, QToolButton,
    )
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QCursor

    class BasePlatformTab(QWidget):
        platform_title = ""
        config_filename = ""
        tgdb_platform_id = 0
        collection_defaults = {}
        file_extensions = ()
        extract_fn = None
        dir_key = 'game_dir'
        dir_placeholder = "选择包含游戏文件的目录..."

        def __init__(self, parent=None):
            super().__init__(parent)
            self._worker = None
            self._selected_cards = set()
            self._build_ui()

        def _build_ui(self):
            from main import FlowLayout, GameCard
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
            self.dir_input.setPlaceholderText(self.dir_placeholder)
            r1.addWidget(self.dir_input, 1)
            db = QPushButton("浏览")
            db.setFixedWidth(60)
            db.clicked.connect(self._pick_dir)
            r1.addWidget(db)
            cl.addLayout(r1)
            self._build_extra_ui(cl)
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

        def _build_extra_ui(self, layout):
            pass

        def _get_extract_kwargs(self):
            return {}

        def _validate_before_extract(self):
            return True

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
            from main import GameCard
            self.showcase_layout.clear()
            self._selected_cards.clear()
            exts = self.collection_defaults.get('extensions', '')
            games, has_pegasus, has_gamelist = load_showcase_games(directory, exts)
            if not games:
                self.showcase_status.setText("未找到游戏文件")
                return
            sources = []
            if has_pegasus:
                sources.append('metadata.pegasus.txt')
            if has_gamelist:
                sources.append('gamelist.xml')
            src = f" (元数据: {', '.join(sources)})" if sources else ""
            self.showcase_status.setText(f"已加载 {len(games)} 款游戏{src}")
            for game in games:
                card = GameCard(game, self.showcase_widget)
                card.clicked.connect(self._show_detail)
                card.context_menu_requested.connect(self._on_card_context_menu)
                card.selection_toggled.connect(self._on_card_selection_toggled)
                self.showcase_layout.addWidget(card)

        def _show_detail(self, game):
            from main import GameDetailDialog
            GameDetailDialog(game, self).exec()

        def _on_card_selection_toggled(self, card, selected):
            if selected:
                self._selected_cards.add(card)
            else:
                self._selected_cards.discard(card)

        def _on_card_context_menu(self, card):
            if self._worker and self._worker.isRunning():
                return
            menu = QMenu(self)
            menu.setStyleSheet(
                'QMenu { background: #161b22; border: 1px solid #30363d; padding: 4px; }'
                'QMenu::item { color: #e6edf3; padding: 6px 20px; }'
                'QMenu::item:selected { background: #264f78; }'
                'QMenu::item:disabled { color: #484f58; }')
            title = card.game.get('title', '')
            if len(title) > 20:
                title = title[:20] + '...'
            file_name = card.game.get('file', '')
            act_complement = menu.addAction(f"补全此游戏: {title}")
            act_complement.triggered.connect(
                lambda: self._scrape_games([file_name], 'complement'))
            act_refresh = menu.addAction(f"刷新此游戏: {title}")
            act_refresh.triggered.connect(
                lambda: self._scrape_games([file_name], 'refresh'))
            act_manual = menu.addAction(f"手动搜索: {title}")
            act_manual.triggered.connect(
                lambda: self._manual_search(card))
            menu.addSeparator()
            count = len(self._selected_cards)
            if count > 0:
                files = [c.game.get('file', '') for c in self._selected_cards]
                menu.addAction(f"补全选中游戏 ({count}款)").triggered.connect(
                    lambda f=files: self._scrape_games(f, 'complement'))
                menu.addAction(f"刷新选中游戏 ({count}款)").triggered.connect(
                    lambda f=files: self._scrape_games(f, 'refresh'))
            else:
                act_sel = menu.addAction("补全/刷新选中游戏 (未选择)")
                act_sel.setEnabled(False)
            menu.exec(QCursor.pos())

        def _manual_search(self, card):
            if self._worker and self._worker.isRunning():
                QMessageBox.warning(self, "提示", "当前有任务正在执行，请等待完成")
                return
            current_title = card.game.get('title', '')
            file_name = card.game.get('file', '')

            dlg = QDialog(self)
            dlg.setWindowTitle("手动搜索")
            dlg.setMinimumWidth(400)
            layout = QVBoxLayout(dlg)
            layout.addWidget(QLabel("输入搜索关键词:"))

            row = QHBoxLayout()
            line_edit = QLineEdit(current_title)
            row.addWidget(line_edit)

            btn_extract = QToolButton()
            btn_extract.setText("📦")
            btn_extract.setToolTip("从ROM中获取英文游戏名")
            btn_extract.setStyleSheet(
                'QToolButton { font-size: 16px; padding: 2px 6px; }'
                'QToolButton:hover { background: #264f78; border-radius: 4px; }')

            def _fill_rom_title():
                game_dir = self.dir_input.text().strip()
                if not game_dir:
                    self._log("[游戏搜索] 未设置游戏目录")
                    return
                try:
                    game_files, temp_dir = collect_game_files(
                        game_dir, self.file_extensions)
                except Exception as e:
                    self._log(f"[文件扫描] 手动搜索扫描目录失败: {e}")
                    return
                try:
                    found = False
                    for fpath, dname in game_files:
                        if dname == file_name:
                            found = True
                            self._log(f"[游戏解析] 手动搜索解析 ROM: {dname}")
                            info = self.extract_fn(
                                str(fpath), lang_code='en',
                                log=self._log,
                                **self._get_extract_kwargs())
                            if info:
                                t = info.get('title_en') or info.get('title', '')
                                if t:
                                    line_edit.setText(t)
                                    self._log(f"[游戏解析] 已获取英文名: {t}")
                                else:
                                    self._log("[游戏解析] ROM 中未找到英文名")
                            else:
                                self._log("[游戏解析] ROM 解析返回空")
                            break
                    if not found:
                        self._log(f"[文件扫描] 未在目录中找到文件: {file_name}")
                except Exception as e:
                    self._log(f"[游戏解析] 手动搜索解析 ROM 异常: {e}")
                finally:
                    if temp_dir:
                        import shutil
                        shutil.rmtree(temp_dir, ignore_errors=True)

            btn_extract.clicked.connect(_fill_rom_title)
            row.addWidget(btn_extract)
            layout.addLayout(row)

            buttons = QDialogButtonBox(
                QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.accepted.connect(dlg.accept)
            buttons.rejected.connect(dlg.reject)
            layout.addWidget(buttons)

            if dlg.exec() != QDialog.Accepted:
                return
            text = line_edit.text().strip()
            if not text:
                return
            self._scrape_games([file_name], 'refresh',
                               override_search_name=text)

        def _scrape_games(self, filenames, scrape_mode='refresh',
                          override_search_name=''):
            if self._worker and self._worker.isRunning():
                QMessageBox.warning(self, "提示", "当前有任务正在执行，请等待完成")
                return
            game_dir = self.dir_input.text().strip()
            if not game_dir or not Path(game_dir).is_dir():
                QMessageBox.warning(self, "错误", "请先选择有效的游戏目录")
                return
            mode_label = "补全" if scrape_mode == 'complement' else "刷新"
            self.log_text.clear()
            self._log(f"[刮削] {mode_label}指定游戏: {len(filenames)} 个")
            mw = self.window()
            g = mw.get_global_settings()
            params = dict(
                game_dir=game_dir,
                extract_fn=self.extract_fn,
                file_extensions=self.file_extensions,
                platform_id=self.tgdb_platform_id,
                platform_name=self.platform_title,
                collection_defaults=self.collection_defaults,
                extract_kwargs=self._get_extract_kwargs(),
                generate_meta=self.meta_check.isChecked(),
                generate_gamelist=self.gamelist_check.isChecked(),
                online_mode=g['online_mode'],
                api_key=g['api_key'] or None,
                datasource_name=g.get('datasource', 'thegamesdb'),
                lang_code=g['lang_code'],
                google_lang=g['google_lang'],
                translate=g['translate'],
                video=g.get('video', False),
                filename_as_title=g.get('filename_as_title', False),
                thread_count=g.get('thread_count', 4),
                scrape_mode=scrape_mode,
                target_files=set(filenames),
                proxy=g.get('proxy', ''),
                override_search_name=override_search_name,
                normalize_media_paths=g.get('normalize_media_paths', True),
                anbernic_compatible=g.get('anbernic_compatible', False),
            )
            mw.save_config()
            self.start_btn.setText("取消")
            self.start_btn.setStyleSheet(
                'background: #da3633; border: none; color: white; '
                'font-weight: 600; padding: 10px 28px; font-size: 14px;')
            from scrape import ExtractWorker
            self._worker = ExtractWorker(params)
            self._worker.log_signal.connect(self._log)
            self._worker.finished_signal.connect(self._on_done)
            self._worker.start()

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
            game_dir = self.dir_input.text().strip()
            if not game_dir or not Path(game_dir).is_dir():
                QMessageBox.warning(self, "错误", "请选择有效的游戏目录")
                return
            if not self._validate_before_extract():
                return
            self.start_btn.setText("取消")
            self.start_btn.setStyleSheet(
                'background: #da3633; border: none; color: white; '
                'font-weight: 600; padding: 10px 28px; font-size: 14px;')
            self.log_text.clear()
            mw = self.window()
            g = mw.get_global_settings()
            params = dict(
                game_dir=game_dir,
                extract_fn=self.extract_fn,
                file_extensions=self.file_extensions,
                platform_id=self.tgdb_platform_id,
                platform_name=self.platform_title,
                collection_defaults=self.collection_defaults,
                extract_kwargs=self._get_extract_kwargs(),
                generate_meta=self.meta_check.isChecked(),
                generate_gamelist=self.gamelist_check.isChecked(),
                online_mode=g['online_mode'],
                api_key=g['api_key'] or None,
                datasource_name=g.get('datasource', 'thegamesdb'),
                lang_code=g['lang_code'],
                google_lang=g['google_lang'],
                translate=g['translate'],
                video=g.get('video', False),
                filename_as_title=g.get('filename_as_title', False),
                thread_count=g.get('thread_count', 4),
                scrape_mode=g.get('scrape_mode', 'refresh'),
                proxy=g.get('proxy', ''),
                normalize_media_paths=g.get('normalize_media_paths', True),
                anbernic_compatible=g.get('anbernic_compatible', False),
            )
            mw.save_config()
            from scrape import ExtractWorker
            self._worker = ExtractWorker(params)
            self._worker.log_signal.connect(self._log)
            self._worker.finished_signal.connect(self._on_done)
            self._worker.start()

        def _on_done(self):
            self.start_btn.setText("开始提取")
            self.start_btn.setStyleSheet('')
            self.start_btn.setEnabled(True)
            QMessageBox.information(self, "完成", "处理完成，请查看日志输出")
            game_dir = self.dir_input.text().strip()
            if game_dir and Path(game_dir).is_dir():
                self._load_showcase(game_dir)

        def load_config(self, cfg):
            if cfg.get(self.dir_key):
                self.dir_input.setText(cfg[self.dir_key])
            if 'generate_meta' in cfg:
                self.meta_check.setChecked(cfg['generate_meta'])
            if 'generate_gamelist' in cfg:
                self.gamelist_check.setChecked(cfg['generate_gamelist'])
            game_dir = cfg.get(self.dir_key, '')
            if game_dir and Path(game_dir).is_dir():
                self._on_dir_changed(game_dir)

        def save_config(self):
            return {
                self.dir_key: self.dir_input.text().strip(),
                'generate_meta': self.meta_check.isChecked(),
                'generate_gamelist': self.gamelist_check.isChecked(),
            }

except ImportError:
    pass


# ===== 平台子类注册 =====

def _load_platforms():
    from platform_switch import extract_xci_info, parse_keys, PLATFORM_TITLE as SWITCH_TITLE, CONFIG_FILENAME as SWITCH_CONFIG, TGDB_PLATFORM_ID as SWITCH_TGDB, COLLECTION_DEFAULTS as SWITCH_COLL
    from platform_nds import extract_nds_info, PLATFORM_TITLE as NDS_TITLE, CONFIG_FILENAME as NDS_CONFIG, TGDB_PLATFORM_ID as NDS_TGDB, COLLECTION_DEFAULTS as NDS_COLL
    from platform_3ds import extract_3ds_info, PLATFORM_TITLE as N3DS_TITLE, CONFIG_FILENAME as N3DS_CONFIG, TGDB_PLATFORM_ID as N3DS_TGDB, COLLECTION_DEFAULTS as N3DS_COLL
    from platform_psp import extract_psp_info, PLATFORM_TITLE as PSP_TITLE, CONFIG_FILENAME as PSP_CONFIG, TGDB_PLATFORM_ID as PSP_TGDB, COLLECTION_DEFAULTS as PSP_COLL
    from platform_wii import extract_wii_info, PLATFORM_TITLE as WII_TITLE, CONFIG_FILENAME as WII_CONFIG, TGDB_PLATFORM_ID as WII_TGDB, COLLECTION_DEFAULTS as WII_COLL
    from platform_ps1 import extract_ps1_info, PLATFORM_TITLE as PS1_TITLE, CONFIG_FILENAME as PS1_CONFIG, TGDB_PLATFORM_ID as PS1_TGDB, COLLECTION_DEFAULTS as PS1_COLL
    from platform_ngc import extract_ngc_info, PLATFORM_TITLE as NGC_TITLE, CONFIG_FILENAME as NGC_CONFIG, TGDB_PLATFORM_ID as NGC_TGDB, COLLECTION_DEFAULTS as NGC_COLL
    from platform_dc import extract_dc_info, PLATFORM_TITLE as DC_TITLE, CONFIG_FILENAME as DC_CONFIG, TGDB_PLATFORM_ID as DC_TGDB, COLLECTION_DEFAULTS as DC_COLL
    from platform_gba import extract_gba_info, PLATFORM_TITLE as GBA_TITLE, CONFIG_FILENAME as GBA_CONFIG, TGDB_PLATFORM_ID as GBA_TGDB, COLLECTION_DEFAULTS as GBA_COLL

    class SwitchTab(BasePlatformTab):
        platform_title = SWITCH_TITLE
        config_filename = SWITCH_CONFIG
        tgdb_platform_id = SWITCH_TGDB
        collection_defaults = SWITCH_COLL
        file_extensions = ('xci', 'nsp')
        extract_fn = staticmethod(extract_xci_info)
        dir_key = 'xci_dir'
        dir_placeholder = "选择包含 Switch 游戏文件的目录..."

        def _build_extra_ui(self, layout):
            from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QFileDialog
            r = QHBoxLayout()
            lbl = QLabel("prod.keys")
            lbl.setFixedWidth(65)
            r.addWidget(lbl)
            self.keys_input = QLineEdit()
            self.keys_input.setPlaceholderText("Switch prod.keys 文件路径...")
            r.addWidget(self.keys_input, 1)
            kb = QPushButton("浏览")
            kb.setFixedWidth(60)
            kb.clicked.connect(self._pick_keys)
            r.addWidget(kb)
            layout.addLayout(r)

        def _pick_keys(self):
            path, _ = QFileDialog.getOpenFileName(self, "选择 prod.keys")
            if path:
                self.keys_input.setText(path)

        def _validate_before_extract(self):
            keys_path = self.keys_input.text().strip()
            if not keys_path or not Path(keys_path).is_file():
                QMessageBox.warning(self, "错误", "请选择有效的 prod.keys 文件")
                return False
            return True

        def _get_extract_kwargs(self):
            keys_path = self.keys_input.text().strip()
            if keys_path and Path(keys_path).is_file():
                return {'keys': parse_keys(keys_path)}
            return {}

        def load_config(self, cfg):
            super().load_config(cfg)
            if cfg.get('keys_path'):
                self.keys_input.setText(cfg['keys_path'])

        def save_config(self):
            cfg = super().save_config()
            cfg['keys_path'] = self.keys_input.text().strip()
            return cfg

    class NDSTab(BasePlatformTab):
        platform_title = NDS_TITLE
        config_filename = NDS_CONFIG
        tgdb_platform_id = NDS_TGDB
        collection_defaults = NDS_COLL
        file_extensions = ('nds',)
        extract_fn = staticmethod(extract_nds_info)
        dir_key = 'nds_dir'
        dir_placeholder = "选择包含 NDS 游戏文件的目录..."

    class N3DSTab(BasePlatformTab):
        platform_title = N3DS_TITLE
        config_filename = N3DS_CONFIG
        tgdb_platform_id = N3DS_TGDB
        collection_defaults = N3DS_COLL
        file_extensions = ('3ds', 'cia')
        extract_fn = staticmethod(extract_3ds_info)
        dir_key = '3ds_dir'
        dir_placeholder = "选择包含 3DS 游戏文件的目录..."

    class PSPTab(BasePlatformTab):
        platform_title = PSP_TITLE
        config_filename = PSP_CONFIG
        tgdb_platform_id = PSP_TGDB
        collection_defaults = PSP_COLL
        file_extensions = ('iso', 'cso')
        extract_fn = staticmethod(extract_psp_info)
        dir_key = 'psp_dir'
        dir_placeholder = "选择包含 PSP 游戏文件的目录..."

    class WiiTab(BasePlatformTab):
        platform_title = WII_TITLE
        config_filename = WII_CONFIG
        tgdb_platform_id = WII_TGDB
        collection_defaults = WII_COLL
        file_extensions = ('iso', 'wbfs', 'rvz')
        extract_fn = staticmethod(extract_wii_info)
        dir_key = 'wii_dir'
        dir_placeholder = "选择包含 Wii 游戏文件的目录..."

    class PS1Tab(BasePlatformTab):
        platform_title = PS1_TITLE
        config_filename = PS1_CONFIG
        tgdb_platform_id = PS1_TGDB
        collection_defaults = PS1_COLL
        file_extensions = ('chd', 'bin', 'iso', 'pbp')
        extract_fn = staticmethod(extract_ps1_info)
        dir_key = 'ps1_dir'
        dir_placeholder = "选择包含 PS1 游戏文件的目录..."

    class NGCTab(BasePlatformTab):
        platform_title = NGC_TITLE
        config_filename = NGC_CONFIG
        tgdb_platform_id = NGC_TGDB
        collection_defaults = NGC_COLL
        file_extensions = ('iso', 'gcz', 'rvz')
        extract_fn = staticmethod(extract_ngc_info)
        dir_key = 'ngc_dir'
        dir_placeholder = "选择包含 NGC 游戏文件的目录..."

    class DCTab(BasePlatformTab):
        platform_title = DC_TITLE
        config_filename = DC_CONFIG
        tgdb_platform_id = DC_TGDB
        collection_defaults = DC_COLL
        file_extensions = ('chd', 'cdi', 'gdi')
        extract_fn = staticmethod(extract_dc_info)
        dir_key = 'game_dir'
        dir_placeholder = "选择包含 DC 游戏文件的目录..."

    class GBATab(BasePlatformTab):
        platform_title = GBA_TITLE
        config_filename = GBA_CONFIG
        tgdb_platform_id = GBA_TGDB
        collection_defaults = GBA_COLL
        file_extensions = ('gba',)
        extract_fn = staticmethod(extract_gba_info)
        dir_key = 'gba_dir'
        dir_placeholder = "选择包含 GBA 游戏文件的目录..."

    platforms = [
        (SWITCH_TITLE, SWITCH_CONFIG, SwitchTab),
        (NDS_TITLE, NDS_CONFIG, NDSTab),
        (N3DS_TITLE, N3DS_CONFIG, N3DSTab),
        (PSP_TITLE, PSP_CONFIG, PSPTab),
        (WII_TITLE, WII_CONFIG, WiiTab),
        (PS1_TITLE, PS1_CONFIG, PS1Tab),
        (NGC_TITLE, NGC_CONFIG, NGCTab),
        (DC_TITLE, DC_CONFIG, DCTab),
        (GBA_TITLE, GBA_CONFIG, GBATab),
    ]
    return platforms
