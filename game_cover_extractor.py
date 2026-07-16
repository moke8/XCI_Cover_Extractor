#!/usr/bin/env python3
"""Game Cover Extractor - 从游戏 ROM 文件中提取封面和元数据"""

import re
import struct
import json
import sys
import locale
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import urlencode, quote

from datasource import set_proxy, google_translate, list_datasources, get_datasource


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


# ===== 共享工具函数 =====

def _has_cjk(text):
    return any('一' <= c <= '鿿' for c in text)


_TC_CHARS = set(
    '優記點開關學東與號說麗買義書實總這從進時間問題長們線還過處對電動員請種業經連結個機數產無裡發準現環樂選項認設據應體歡視頁練網幣節讓際計畫區觀覺單歷歸調變標響廣達歲當積類會島語車輕齊廳觸創職護編譯'
    '騎夢魘級瑪歐驚寶劍傳險戰鬥義務構圖館絡話輛運遊錄訊號該還結頭圍廠廢廚龍鳳鑰鍵鐵銀釘針腦臟藝術蘭藥獲獎獵猶瑣爾燈燒營獻獨狀滅漲測決減準溫視離種積穩競節篇範築縣織繼續縮繩約紀純紅納級終組細織續華萬著薩處裝製複觀詢試詳認誤課談調請論證識議護變讓議豐貝負財販貧貨質購貿費貼資賊賓賞賢賣賦質賬賴賺購贈贊趙輕載較輝輩輪轉辦辭辯達遙適選遷邊邏還進遠違連過運過適選遲遺鄰醫醬釋鑒鋼錢錯鍋鍵鏡鐘關隊階隨險隱雙雜離難電靈靜響頂預領頻題顏額願類顯飛養餘駐駕驅驗體'
)


def _is_traditional(text):
    return any(c in _TC_CHARS for c in text)


def sanitize_filename(name):
    illegal = '<>:"/\\|?*'
    for c in illegal:
        name = name.replace(c, '_')
    return name.strip().strip('.')


def collect_game_files(directory, extensions):
    """扫描目录中的游戏文件，包括 ZIP 压缩包。
    extensions: 小写后缀元组，不含点，如 ('xci',) 或 ('nds',)
    返回 (entries, temp_dir)。
    entries: [(文件路径, 显示文件名), ...]
    temp_dir: 临时目录路径或 None，调用方处理完后需清理。
    """
    import zipfile
    import tempfile

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


# ===== 配置文件 =====

_APP_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
GLOBAL_CONFIG_FILE = _APP_DIR / 'config.json'


def _platform_config_path(filename):
    return _APP_DIR / filename


def load_json_config(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_json_config(path, data):
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass


# ===== Pegasus Metadata =====

def parse_pegasus_meta(meta_path):
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
    if info.get('youtube'):
        lines.append(f"assets.video: {info['youtube']}")
    return '\n'.join(lines)


def backup_file(path):
    if not path.exists():
        return
    import shutil
    shutil.copy2(path, Path(str(path) + '.bak'))


def write_pegasus_meta(meta_path, results, collection_defaults=None):
    backup_file(meta_path)
    collection_lines, existing_games = parse_pegasus_meta(meta_path)

    if not collection_lines and collection_defaults:
        d = collection_defaults
        collection_lines = [
            f"collection: {d['collection']}\n"
            f"shortname: {d['shortname']}\n"
            f"extensions: {d['extensions']}\n"
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
        if info.get('youtube'):
            _set('video', info['youtube'])

    tree = ET.ElementTree(root)
    ET.indent(tree, space='  ')
    tree.write(str(gamelist_path), encoding='utf-8', xml_declaration=True)


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


def load_showcase_games(directory):
    base_dir = Path(directory)
    pegasus_path = base_dir / 'metadata.pegasus.txt'
    gamelist_path = base_dir / 'gamelist.xml'
    has_pegasus = pegasus_path.exists()
    has_gamelist = gamelist_path.exists()
    pegasus_games = parse_pegasus_for_showcase(pegasus_path, base_dir) if has_pegasus else []
    gamelist_games = parse_gamelist_for_showcase(gamelist_path, base_dir) if has_gamelist else []
    games_by_file = {}
    for g in pegasus_games:
        key = g.get('file', g.get('title', ''))
        games_by_file[key] = g
    for g in gamelist_games:
        key = g.get('file', g.get('title', ''))
        if key not in games_by_file:
            games_by_file[key] = g
        else:
            for k, v in g.items():
                if k not in games_by_file[key] or not games_by_file[key][k]:
                    games_by_file[key][k] = v
    return list(games_by_file.values()), has_pegasus, has_gamelist


# ===== Qt GUI =====

try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QTabWidget, QLabel, QLineEdit, QPushButton, QCheckBox, QComboBox,
        QSpinBox, QTextEdit, QScrollArea, QFrame, QFileDialog, QMessageBox,
        QLayout, QDialog,
    )
    from PySide6.QtCore import (
        Qt, QSize, Signal, QThread, QPropertyAnimation, QEasingCurve,
        QPoint, QRect, Property,
    )
    from PySide6.QtGui import (
        QPixmap, QFont, QColor, QPainter, QLinearGradient, QPen, QPainterPath,
    )
except ImportError:
    print("请先安装 PySide6: pip install PySide6")
    exit(1)

CARD_W = 180
CARD_H = 260
COVER_H = 180

STYLESHEET = """
QMainWindow { background-color: #0d1117; }
QWidget {
    color: #e6edf3;
    font-family: "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif;
    font-size: 13px;
}

QTabWidget::pane { border: none; background: #0d1117; }
QTabBar { background: #161b22; }
QTabBar::tab {
    background: #161b22; color: #8b949e; padding: 12px 28px;
    border: none; border-bottom: 3px solid transparent;
    font-size: 14px; font-weight: 600;
}
QTabBar::tab:selected { color: #e6edf3; border-bottom-color: #e60012; }
QTabBar::tab:hover:!selected { color: #c9d1d9; background: #1c2128; }

QLineEdit {
    background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
    padding: 8px 12px; color: #e6edf3; selection-background-color: #264f78;
}
QLineEdit:focus { border-color: #58a6ff; }

QPushButton {
    background: #21262d; border: 1px solid #30363d; border-radius: 6px;
    padding: 8px 16px; color: #e6edf3; font-weight: 500;
}
QPushButton:hover { background: #30363d; border-color: #8b949e; }
QPushButton:pressed { background: #161b22; }
QPushButton#startBtn {
    background: #e60012; border: none; color: white;
    font-weight: 600; padding: 10px 28px; font-size: 14px;
}
QPushButton#startBtn:hover { background: #ff1a2d; }
QPushButton#startBtn:disabled { background: #484f58; color: #8b949e; }

QCheckBox { spacing: 6px; color: #c9d1d9; }
QCheckBox::indicator {
    width: 16px; height: 16px; border-radius: 3px;
    border: 1px solid #30363d; background: #0d1117;
}
QCheckBox::indicator:checked { background: #e60012; border-color: #e60012; }
QCheckBox::indicator:hover { border-color: #58a6ff; }

QComboBox {
    background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
    padding: 6px 12px; color: #e6edf3; min-width: 120px;
}
QComboBox:hover { border-color: #58a6ff; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background: #161b22; border: 1px solid #30363d;
    color: #e6edf3; selection-background-color: #264f78;
}

QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #0d1117; width: 8px; border: none; }
QScrollBar::handle:vertical {
    background: #30363d; border-radius: 4px; min-height: 40px;
}
QScrollBar::handle:vertical:hover { background: #484f58; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

QTextEdit {
    background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
    padding: 8px; color: #7ee787;
    font-family: "Cascadia Code", "Consolas", "Microsoft YaHei", monospace;
    font-size: 12px;
}

QFrame#controlPanel {
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
}
QFrame#globalBar {
    background: #161b22; border-bottom: 1px solid #30363d;
}
QPushButton#logToggle {
    background: transparent; border: none; color: #8b949e;
    font-size: 12px; padding: 4px 8px; text-align: left;
}
QPushButton#logToggle:hover { color: #e6edf3; }
QDialog { background: #161b22; }
"""


class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing=16):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0
        for item in self._items:
            sz = item.sizeHint()
            next_x = x + sz.width() + self._spacing
            if next_x - self._spacing > effective.right() + 1 and line_height > 0:
                x = effective.x()
                y += line_height + self._spacing
                next_x = x + sz.width() + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), sz))
            x = next_x
            line_height = max(line_height, sz.height())
        return y + line_height - rect.y() + m.bottom()

    def clear(self):
        while self.count():
            item = self.takeAt(0)
            if item.widget():
                item.widget().deleteLater()


class GameCard(QWidget):
    clicked = Signal(dict)

    def __init__(self, game_data, parent=None):
        super().__init__(parent)
        self.game = game_data
        self.setFixedSize(CARD_W, CARD_H)
        self.setCursor(Qt.PointingHandCursor)
        self._hover_progress = 0.0
        self._cover_pixmap = None
        self._load_cover()
        self._anim = QPropertyAnimation(self, b"hoverProgress")
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)

    def _get_hover(self):
        return self._hover_progress

    def _set_hover(self, val):
        self._hover_progress = val
        self.update()

    hoverProgress = Property(float, _get_hover, _set_hover)

    def _load_cover(self):
        cover = self.game.get('cover', '')
        if cover and Path(cover).exists():
            img = QPixmap(cover)
            if not img.isNull():
                self._cover_pixmap = img.scaled(
                    CARD_W - 8, COVER_H - 8,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def enterEvent(self, event):
        self._anim.stop()
        self._anim.setStartValue(self._hover_progress)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def leaveEvent(self, event):
        self._anim.stop()
        self._anim.setStartValue(self._hover_progress)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.game)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        hp = self._hover_progress
        bg_r = int(28 + hp * 9)
        bg_g = int(33 + hp * 12)
        bg_b = int(40 + hp * 16)

        card_path = QPainterPath()
        card_path.addRoundedRect(0, 0, CARD_W, CARD_H, 10, 10)
        p.fillPath(card_path, QColor(bg_r, bg_g, bg_b))

        if hp > 0:
            p.setPen(QPen(QColor(230, 0, 18, int(hp * 180)), 2))
            p.drawRoundedRect(1, 1, CARD_W - 2, CARD_H - 2, 10, 10)
            p.setPen(Qt.NoPen)

        cover_rect = QRect(4, 4, CARD_W - 8, COVER_H - 8)

        if self._cover_pixmap:
            cx = cover_rect.x() + (cover_rect.width() - self._cover_pixmap.width()) // 2
            cy = cover_rect.y() + (cover_rect.height() - self._cover_pixmap.height()) // 2
            clip = QPainterPath()
            clip.addRoundedRect(float(cx), float(cy),
                                float(self._cover_pixmap.width()),
                                float(self._cover_pixmap.height()), 6, 6)
            p.setClipPath(clip)
            p.drawPixmap(cx, cy, self._cover_pixmap)
            p.setClipping(False)
        else:
            nc = QPainterPath()
            nc.addRoundedRect(float(cover_rect.x()), float(cover_rect.y()),
                              float(cover_rect.width()), float(cover_rect.height()), 6, 6)
            p.fillPath(nc, QColor('#2d333b'))
            p.setPen(QColor('#484f58'))
            p.setFont(QFont("Segoe UI", 24))
            p.drawText(cover_rect, Qt.AlignCenter, "🎮")

        if hp > 0.05:
            ov = QPainterPath()
            ov.addRoundedRect(float(cover_rect.x()), float(cover_rect.y()),
                              float(cover_rect.width()), float(cover_rect.height()), 6, 6)
            gradient = QLinearGradient(cover_rect.x(), cover_rect.y(),
                                      cover_rect.x(), cover_rect.bottom())
            gradient.setColorAt(0, QColor(0, 0, 0, int(hp * 60)))
            gradient.setColorAt(0.4, QColor(0, 0, 0, int(hp * 100)))
            gradient.setColorAt(1, QColor(0, 0, 0, int(hp * 210)))
            p.fillPath(ov, gradient)

            desc = self.game.get('description', '')
            if desc:
                p.setPen(QColor(255, 255, 255, int(hp * 220)))
                p.setFont(QFont("Segoe UI", 9))
                dr = cover_rect.adjusted(10, 10, -10, -30)
                p.drawText(dr, Qt.AlignTop | Qt.TextWordWrap,
                           desc[:150] + ('...' if len(desc) > 150 else ''))

            info = []
            if self.game.get('genre'):
                info.append(self.game['genre'][:25])
            if self.game.get('developer'):
                info.append(self.game['developer'][:20])
            if info:
                p.setPen(QColor(88, 166, 255, int(hp * 200)))
                p.setFont(QFont("Segoe UI", 8))
                p.drawText(cover_rect.adjusted(8, 0, -8, -6),
                           Qt.AlignBottom | Qt.AlignLeft, ' · '.join(info))

            rating = self.game.get('rating', '')
            if rating:
                try:
                    rv = float(rating.rstrip('%'))
                    if rv > 1:
                        rv /= 100
                    rt = f"★ {rv:.0%}"
                except (ValueError, TypeError):
                    rt = None
                if rt:
                    p.setFont(QFont("Segoe UI", 8, QFont.Bold))
                    br = QRect(cover_rect.right() - 55, cover_rect.y() + 6, 50, 20)
                    bp = QPainterPath()
                    bp.addRoundedRect(float(br.x()), float(br.y()),
                                      float(br.width()), float(br.height()), 10, 10)
                    p.fillPath(bp, QColor(230, 0, 18, int(hp * 220)))
                    p.setPen(QColor(255, 255, 255, int(hp * 255)))
                    p.drawText(br, Qt.AlignCenter, rt)

        title_rect = QRect(8, COVER_H + 2, CARD_W - 16, CARD_H - COVER_H - 6)
        p.setPen(QColor('#e6edf3'))
        title_font = QFont("Segoe UI", 10)
        title_font.setWeight(QFont.DemiBold)
        p.setFont(title_font)
        p.drawText(title_rect, Qt.AlignTop | Qt.AlignHCenter | Qt.TextWordWrap,
                   self.game.get('title', 'Unknown'))
        p.end()


class GameDetailDialog(QDialog):
    def __init__(self, game_data, parent=None):
        super().__init__(parent)
        self.game = game_data
        self.setWindowTitle(game_data.get('title', '游戏详情'))
        self.setMinimumSize(620, 420)
        self.setStyleSheet("""
            QLabel#titleLabel { font-size: 20px; font-weight: bold; color: #e6edf3; }
            QLabel#fieldName  { color: #8b949e; font-size: 12px; }
            QLabel#fieldValue { color: #c9d1d9; font-size: 13px; }
        """)
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setSpacing(24)
        layout.setContentsMargins(24, 24, 24, 24)

        cover_label = QLabel()
        cover_label.setFixedSize(240, 240)
        cover_label.setAlignment(Qt.AlignCenter)
        cover_label.setStyleSheet('background: #0d1117; border-radius: 8px;')
        cover = self.game.get('cover', '')
        if cover and Path(cover).exists():
            px = QPixmap(cover)
            if not px.isNull():
                cover_label.setPixmap(px.scaled(
                    236, 236, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            cover_label.setText("🎮")
            cover_label.setStyleSheet(
                'background: #0d1117; border-radius: 8px; font-size: 48px; color: #484f58;')
        layout.addWidget(cover_label, 0, Qt.AlignTop)

        right = QVBoxLayout()
        right.setSpacing(10)

        title = QLabel(self.game.get('title', 'Unknown'))
        title.setObjectName('titleLabel')
        title.setWordWrap(True)
        right.addWidget(title)

        for label, key in [('开发商', 'developer'), ('类型', 'genre'),
                           ('玩家数', 'players'), ('发售日', 'release'),
                           ('评分', 'rating')]:
            val = self.game.get(key, '')
            if not val:
                continue
            row = QHBoxLayout()
            fl = QLabel(f'{label}:')
            fl.setObjectName('fieldName')
            fl.setFixedWidth(55)
            row.addWidget(fl)
            vl = QLabel(str(val))
            vl.setObjectName('fieldValue')
            vl.setWordWrap(True)
            row.addWidget(vl)
            right.addLayout(row)

        desc = self.game.get('description', '')
        if desc:
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            sep.setStyleSheet('background: #30363d; max-height: 1px;')
            right.addWidget(sep)
            dl = QLabel(desc)
            dl.setObjectName('fieldValue')
            dl.setWordWrap(True)
            scroll = QScrollArea()
            scroll.setWidget(dl)
            scroll.setWidgetResizable(True)
            scroll.setMaximumHeight(200)
            scroll.setStyleSheet('QScrollArea { border: none; }')
            right.addWidget(scroll)

        right.addStretch()
        layout.addLayout(right, 1)


# ===== 平台注册 =====

def _load_platforms():
    platforms = []
    from platform_switch import PlatformTab as SwitchTab, PLATFORM_TITLE as SWITCH_TITLE, CONFIG_FILENAME as SWITCH_CONFIG
    platforms.append((SWITCH_TITLE, SWITCH_CONFIG, SwitchTab))
    from platform_nds import PlatformTab as NDSTab, PLATFORM_TITLE as NDS_TITLE, CONFIG_FILENAME as NDS_CONFIG
    platforms.append((NDS_TITLE, NDS_CONFIG, NDSTab))
    return platforms


# ===== 主窗口 =====

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Game Cover Extractor")
        self.setMinimumSize(900, 650)
        self.resize(1000, 700)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        gbar = QFrame()
        gbar.setObjectName('globalBar')
        gl = QHBoxLayout(gbar)
        gl.setContentsMargins(16, 8, 16, 8)
        gl.setSpacing(12)

        gl.addStretch()

        gl.addWidget(QLabel("语言"))
        self.lang_combo = QComboBox()
        for name, _, _, _ in LANGUAGES:
            self.lang_combo.addItem(name)
        self.lang_combo.setCurrentIndex(_get_default_lang_index())
        gl.addWidget(self.lang_combo)

        gl.addSpacing(8)
        gl.addWidget(QLabel("线程"))
        self.thread_spin = QSpinBox()
        self.thread_spin.setRange(1, 16)
        self.thread_spin.setValue(4)
        self.thread_spin.setFixedWidth(50)
        gl.addWidget(self.thread_spin)

        gl.addSpacing(8)
        self.online_check = QCheckBox("在线补全")
        self.online_check.setChecked(True)
        gl.addWidget(self.online_check)

        self.online_container = QWidget()
        ol = QHBoxLayout(self.online_container)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(12)

        self.video_check = QCheckBox("视频")
        ol.addWidget(self.video_check)
        self.translate_check = QCheckBox("翻译")
        self.translate_check.setChecked(True)
        ol.addWidget(self.translate_check)

        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setFixedHeight(20)
        sep.setStyleSheet('background: #30363d;')
        ol.addSpacing(4)
        ol.addWidget(sep)
        ol.addSpacing(4)

        ol.addWidget(QLabel("代理"))
        self.proxy_input = QLineEdit()
        self.proxy_input.setPlaceholderText("http://127.0.0.1:7890")
        self.proxy_input.setMaximumWidth(200)
        ol.addWidget(self.proxy_input)

        ol.addSpacing(8)
        ol.addWidget(QLabel("数据源"))
        self.datasource_combo = QComboBox()
        for ds in list_datasources():
            self.datasource_combo.addItem(ds.display_name, ds.name)
        self.datasource_combo.setMaximumWidth(160)
        ol.addWidget(self.datasource_combo)

        ol.addSpacing(8)
        self.apikey_label = QLabel("API Key")
        ol.addWidget(self.apikey_label)
        self.apikey_input = QLineEdit()
        self.apikey_input.setPlaceholderText("TheGamesDB")
        self.apikey_input.setMaximumWidth(200)
        ol.addWidget(self.apikey_input)

        gl.addWidget(self.online_container)
        self.online_check.toggled.connect(self._on_online_toggled)
        self.datasource_combo.currentIndexChanged.connect(self._on_datasource_changed)
        layout.addWidget(gbar)

        self.tabs = QTabWidget()
        self._platform_tabs = []
        for title, config_filename, TabClass in _load_platforms():
            tab = TabClass()
            self.tabs.addTab(tab, title)
            self._platform_tabs.append((config_filename, tab))
        layout.addWidget(self.tabs, 1)

        self.statusBar().setStyleSheet(
            'QStatusBar { background: #161b22; color: #484f58; '
            'border-top: 1px solid #30363d; padding: 4px 12px; }')
        self.statusBar().showMessage(
            "by mokevip | QQ 652831080 | github.com/moke8/XCI_Cover_Extractor")

        self._load_config()

    def _on_online_toggled(self, checked):
        self.online_container.setVisible(checked)

    def _on_datasource_changed(self, _index):
        ds_name = self.datasource_combo.currentData()
        ds = get_datasource(ds_name) if ds_name else None
        needs_key = ds.needs_api_key if ds else False
        self.apikey_label.setVisible(needs_key)
        self.apikey_input.setVisible(needs_key)
        if ds:
            self.apikey_input.setPlaceholderText(ds.display_name)

    def get_global_settings(self):
        lang_idx = self.lang_combo.currentIndex()
        return {
            'lang_code': LANGUAGES[lang_idx][1],
            'google_lang': LANGUAGES[lang_idx][2],
            'thread_count': self.thread_spin.value(),
            'online_mode': self.online_check.isChecked(),
            'video': self.video_check.isChecked(),
            'translate': self.translate_check.isChecked(),
            'proxy': self.proxy_input.text().strip(),
            'api_key': self.apikey_input.text().strip(),
            'datasource': self.datasource_combo.currentData(),
        }

    def _migrate_old_config(self):
        old_path = _APP_DIR / 'xci_extractor_config.json'
        if not old_path.exists() or GLOBAL_CONFIG_FILE.exists():
            return
        try:
            old = json.loads(old_path.read_text(encoding='utf-8'))
            global_keys = {'language', 'online_mode', 'translate', 'proxy', 'api_key'}
            save_json_config(GLOBAL_CONFIG_FILE,
                             {k: v for k, v in old.items() if k in global_keys})
            switch_keys = {'xci_dir', 'keys_path', 'generate_meta', 'generate_gamelist'}
            save_json_config(_platform_config_path('switch_config.json'),
                             {k: v for k, v in old.items() if k in switch_keys})
        except Exception:
            pass

    def _load_config(self):
        self._migrate_old_config()
        cfg = load_json_config(GLOBAL_CONFIG_FILE)
        if cfg.get('language'):
            idx = next((i for i, l in enumerate(LANGUAGES)
                        if l[0] == cfg['language']), -1)
            if idx >= 0:
                self.lang_combo.setCurrentIndex(idx)
        if 'thread_count' in cfg:
            self.thread_spin.setValue(cfg['thread_count'])
        if 'online_mode' in cfg:
            self.online_check.setChecked(cfg['online_mode'])
        if 'video' in cfg:
            self.video_check.setChecked(cfg['video'])
        if 'translate' in cfg:
            self.translate_check.setChecked(cfg['translate'])
        if 'proxy' in cfg:
            self.proxy_input.setText(cfg['proxy'])
        if 'api_key' in cfg:
            self.apikey_input.setText(cfg['api_key'])
        if 'datasource' in cfg:
            idx = self.datasource_combo.findData(cfg['datasource'])
            if idx >= 0:
                self.datasource_combo.setCurrentIndex(idx)
        self._on_online_toggled(self.online_check.isChecked())
        self._on_datasource_changed(0)
        for config_filename, tab in self._platform_tabs:
            pcfg = load_json_config(_platform_config_path(config_filename))
            tab.load_config(pcfg)

    def save_config(self):
        cfg = {
            'language': self.lang_combo.currentText(),
            'thread_count': self.thread_spin.value(),
            'online_mode': self.online_check.isChecked(),
            'video': self.video_check.isChecked(),
            'translate': self.translate_check.isChecked(),
            'proxy': self.proxy_input.text().strip(),
            'api_key': self.apikey_input.text().strip(),
            'datasource': self.datasource_combo.currentData(),
        }
        save_json_config(GLOBAL_CONFIG_FILE, cfg)
        for config_filename, tab in self._platform_tabs:
            pcfg = tab.save_config()
            save_json_config(_platform_config_path(config_filename), pcfg)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
