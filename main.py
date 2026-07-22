#!/usr/bin/env python3
"""Game Cover Extractor — 主界面入口"""

import sys
import json
from pathlib import Path

from config import (
    LANGUAGES, _get_default_lang_index, GLOBAL_CONFIG_FILE,
    load_json_config, save_json_config, _platform_config_path, _APP_DIR,
)
from platform_base import PUBLISHER_GROUPS, _load_platforms
from scrape import ExtractWorker
from datasource_base import list_datasources

# ===== Qt GUI =====

try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QTabWidget, QLabel, QLineEdit, QPushButton, QCheckBox, QComboBox,
        QSpinBox, QTextEdit, QScrollArea, QFrame, QFileDialog, QMessageBox,
        QLayout, QDialog, QMenu, QStackedWidget,
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
    context_menu_requested = Signal(object)
    selection_toggled = Signal(object, bool)

    def __init__(self, game_data, parent=None):
        super().__init__(parent)
        self.game = game_data
        self._selected = False
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
            if event.modifiers() & Qt.ControlModifier:
                self._selected = not self._selected
                self.update()
                self.selection_toggled.emit(self, self._selected)
            else:
                self.clicked.emit(self.game)

    def contextMenuEvent(self, event):
        self.context_menu_requested.emit(self)
        event.accept()

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

        if self._selected:
            p.setPen(QPen(QColor(88, 166, 255), 2))
            p.drawRoundedRect(1, 1, CARD_W - 2, CARD_H - 2, 10, 10)
            p.setPen(Qt.NoPen)
        elif hp > 0:
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
            p.drawText(cover_rect, Qt.AlignCenter, "\U0001f3ae")

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


class ScrapeSettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("刮削设置")
        self.setMinimumWidth(440)
        self.setStyleSheet("""
            QDialog { background: #161b22; }
            QLabel#sectionTitle { font-size: 14px; font-weight: bold; color: #e6edf3; }
        """)
        self._settings = settings.copy()
        self._api_keys = dict(settings.get('api_keys', {}))
        self._build_ui()
        self._load_from_settings()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 20, 24, 20)

        t1 = QLabel("刮削选项")
        t1.setObjectName('sectionTitle')
        layout.addWidget(t1)

        r1 = QHBoxLayout()
        self.online_check = QCheckBox("在线补全")
        r1.addWidget(self.online_check)
        r1.addSpacing(16)
        r1.addWidget(QLabel("模式"))
        self.scrape_mode_combo = QComboBox()
        self.scrape_mode_combo.addItem("补全模式", "complement")
        self.scrape_mode_combo.addItem("刷新模式", "refresh")
        r1.addWidget(self.scrape_mode_combo)
        r1.addStretch()
        layout.addLayout(r1)

        r2 = QHBoxLayout()
        self.video_check = QCheckBox("视频")
        r2.addWidget(self.video_check)
        r2.addSpacing(16)
        self.translate_check = QCheckBox("翻译")
        r2.addWidget(self.translate_check)
        r2.addSpacing(16)
        self.filename_as_title_check = QCheckBox("优先使用文件名作为游戏名称")
        r2.addWidget(self.filename_as_title_check)
        r2.addStretch()
        layout.addLayout(r2)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet('background: #30363d; max-height: 1px;')
        layout.addWidget(sep)

        t2 = QLabel("数据源")
        t2.setObjectName('sectionTitle')
        layout.addWidget(t2)

        r3 = QHBoxLayout()
        lbl1 = QLabel("代理")
        lbl1.setFixedWidth(55)
        r3.addWidget(lbl1)
        self.proxy_input = QLineEdit()
        self.proxy_input.setPlaceholderText("http://127.0.0.1:7890")
        r3.addWidget(self.proxy_input)
        layout.addLayout(r3)

        r4 = QHBoxLayout()
        lbl2 = QLabel("数据源")
        lbl2.setFixedWidth(55)
        r4.addWidget(lbl2)
        self.datasource_combo = QComboBox()
        for ds in list_datasources():
            self.datasource_combo.addItem(ds.display_name, ds.name)
        r4.addWidget(self.datasource_combo)
        layout.addLayout(r4)

        r5 = QHBoxLayout()
        self.apikey_label = QLabel("API Key")
        self.apikey_label.setFixedWidth(55)
        r5.addWidget(self.apikey_label)
        self.apikey_input = QLineEdit()
        self.apikey_input.setPlaceholderText("API Key")
        r5.addWidget(self.apikey_input)
        layout.addLayout(r5)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton("确定")
        ok_btn.setObjectName('startBtn')
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        self.datasource_combo.currentIndexChanged.connect(self._on_ds_changed)

    def _on_ds_changed(self, _idx):
        from datasource_base import get_datasource
        old_ds = getattr(self, '_current_ds', None)
        if old_ds:
            self._api_keys[old_ds] = self.apikey_input.text().strip()
        new_ds = self.datasource_combo.currentData()
        self._current_ds = new_ds
        ds = get_datasource(new_ds)
        needs = ds.needs_api_key if ds else False
        self.apikey_label.setVisible(needs)
        self.apikey_input.setVisible(needs)
        self.apikey_input.setText(self._api_keys.get(new_ds, ''))
        if new_ds == 'screenscraper':
            self.apikey_label.setText("账号")
            self.apikey_input.setPlaceholderText("用户名:密码")
        else:
            self.apikey_label.setText("API Key")
            self.apikey_input.setPlaceholderText("API Key")

    def _load_from_settings(self):
        s = self._settings
        self.online_check.setChecked(s.get('online_mode', True))
        idx = self.scrape_mode_combo.findData(s.get('scrape_mode', 'complement'))
        if idx >= 0:
            self.scrape_mode_combo.setCurrentIndex(idx)
        self.video_check.setChecked(s.get('video', False))
        self.translate_check.setChecked(s.get('translate', True))
        self.filename_as_title_check.setChecked(s.get('filename_as_title', False))
        self.proxy_input.setText(s.get('proxy', ''))
        current_ds = s.get('datasource', 'thegamesdb')
        if s.get('api_key') and current_ds not in self._api_keys:
            self._api_keys[current_ds] = s['api_key']
        self._current_ds = None
        idx = self.datasource_combo.findData(current_ds)
        if idx >= 0:
            self.datasource_combo.setCurrentIndex(idx)
        self._on_ds_changed(0)

    def get_settings(self):
        current_ds = self.datasource_combo.currentData()
        self._api_keys[current_ds] = self.apikey_input.text().strip()
        return {
            'online_mode': self.online_check.isChecked(),
            'scrape_mode': self.scrape_mode_combo.currentData(),
            'video': self.video_check.isChecked(),
            'translate': self.translate_check.isChecked(),
            'filename_as_title': self.filename_as_title_check.isChecked(),
            'proxy': self.proxy_input.text().strip(),
            'api_key': self._api_keys.get(current_ds, ''),
            'datasource': current_ds,
            'api_keys': dict(self._api_keys),
        }


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
            cover_label.setText("\U0001f3ae")
            cover_label.setStyleSheet(
                'background: #0d1117; border-radius: 8px; font-size: 48px; color: #484f58;')
        layout.addWidget(cover_label, 0, Qt.AlignTop)

        right = QVBoxLayout()
        right.setSpacing(10)

        title = QLabel(self.game.get('title', 'Unknown'))
        title.setObjectName('titleLabel')
        title.setWordWrap(True)
        right.addWidget(title)

        for label, key in [('游戏ID', 'game_id'), ('开发商', 'developer'),
                           ('类型', 'genre'), ('玩家数', 'players'),
                           ('发售日', 'release'), ('评分', 'rating')]:
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

        rom_path = self.game.get('path', '')
        if rom_path:
            row = QHBoxLayout()
            fl = QLabel('路径:')
            fl.setObjectName('fieldName')
            fl.setFixedWidth(55)
            row.addWidget(fl)
            vl = QLabel(rom_path)
            vl.setObjectName('fieldValue')
            vl.setWordWrap(True)
            vl.setTextInteractionFlags(Qt.TextSelectableByMouse)
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


class _DropDown(QWidget):
    """无焦点抢占的下拉面板"""
    action_triggered = Signal(str)

    def __init__(self, platform_names, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self._items = platform_names
        self._hovered = -1
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 4, 1, 4)
        layout.setSpacing(0)
        self.setStyleSheet(
            'QWidget { background: #161b22; border: 1px solid #30363d; }')
        for i, name in enumerate(self._items):
            btn = QPushButton(name)
            btn.setStyleSheet(
                'QPushButton { border: none; color: #c9d1d9; padding: 8px 24px;'
                '  font-size: 13px; text-align: left; background: transparent; }'
                'QPushButton:hover { background: #264f78; color: #e6edf3; }')
            btn.clicked.connect(lambda checked, n=name: self.action_triggered.emit(n))
            layout.addWidget(btn)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        nav = self.parent()
        if hasattr(nav, '_on_dropdown_leave'):
            nav._on_dropdown_leave()


class _NavButton(QPushButton):
    """厂商导航按钮"""
    hovered = Signal(object)
    platform_selected = Signal(str)

    def __init__(self, publisher, platform_names, parent=None):
        super().__init__(publisher, parent)
        self._publisher = publisher
        self._platform_names = platform_names
        self._active = False
        self.setStyleSheet(self._style(False))

    def _style(self, active):
        base = ('border: none; padding: 12px 28px; font-size: 14px;'
                ' font-weight: 600;')
        if active:
            return (f'QPushButton {{ {base} color: #e6edf3;'
                    ' border-bottom: 3px solid #e60012; background: #161b22; }')
        return (f'QPushButton {{ {base} color: #8b949e;'
                ' border-bottom: 3px solid transparent; background: #161b22; }'
                f'QPushButton:hover {{ color: #c9d1d9; background: #1c2128; }}')

    def set_active(self, active):
        self._active = active
        self.setStyleSheet(self._style(active))

    def enterEvent(self, event):
        super().enterEvent(event)
        self.hovered.emit(self)


class PublisherNavBar(QWidget):
    """厂商分组导航栏，替代 QTabWidget 的 tab bar"""
    platform_switched = Signal(str)

    def __init__(self, publisher_groups, parent=None):
        super().__init__(parent)
        self.setObjectName('publisherNav')
        self.setStyleSheet(
            'QWidget#publisherNav { background: #161b22;'
            ' border-bottom: 1px solid #30363d; }')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(0)
        self._buttons = []
        self._dropdowns = {}
        self._active_dropdown = None
        self._hide_timer = None
        for publisher, platforms in publisher_groups:
            btn = _NavButton(publisher, platforms, self)
            btn.hovered.connect(self._show_dropdown)
            btn.platform_selected.connect(self._on_platform_selected)
            layout.addWidget(btn)
            self._buttons.append((publisher, btn))
            dd = _DropDown(platforms, self)
            dd.action_triggered.connect(self._on_platform_selected)
            self._dropdowns[btn] = dd
        layout.addStretch()

    def _show_dropdown(self, btn):
        self._cancel_hide_timer()
        if self._active_dropdown and self._active_dropdown is not self._dropdowns[btn]:
            self._active_dropdown.hide()
        dd = self._dropdowns[btn]
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        dd.move(pos)
        dd.show()
        self._active_dropdown = dd

    def _on_dropdown_leave(self):
        self._start_hide_timer()

    def _start_hide_timer(self):
        self._cancel_hide_timer()
        from PySide6.QtCore import QTimer
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._check_hide)
        self._hide_timer.start(150)

    def _cancel_hide_timer(self):
        if self._hide_timer:
            self._hide_timer.stop()
            self._hide_timer = None

    def _check_hide(self):
        for _, btn in self._buttons:
            if btn.underMouse():
                return
        if self._active_dropdown and self._active_dropdown.underMouse():
            return
        if self._active_dropdown:
            self._active_dropdown.hide()
            self._active_dropdown = None

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._start_hide_timer()

    def _on_platform_selected(self, platform_name):
        if self._active_dropdown:
            self._active_dropdown.hide()
            self._active_dropdown = None
        self.platform_switched.emit(platform_name)
        for _, btn in self._buttons:
            btn.set_active(platform_name in btn._platform_names)

    def set_active_platform(self, platform_name):
        for _, btn in self._buttons:
            btn.set_active(platform_name in btn._platform_names)


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
        self.settings_btn = QPushButton("刮削设置")
        self.settings_btn.clicked.connect(self._open_scrape_settings)
        gl.addWidget(self.settings_btn)
        layout.addWidget(gbar)

        self._scrape_settings = {
            'online_mode': True,
            'scrape_mode': 'complement',
            'video': False,
            'translate': True,
            'filename_as_title': False,
            'proxy': '',
            'api_key': '',
            'datasource': 'thegamesdb',
            'api_keys': {},
        }

        self._nav = PublisherNavBar(PUBLISHER_GROUPS, self)
        self._nav.platform_switched.connect(self._switch_platform)
        layout.addWidget(self._nav)

        self._stack = QStackedWidget()
        self._platform_tabs = []
        self._platform_index = {}
        for idx, (title, config_filename, TabClass) in enumerate(_load_platforms()):
            tab = TabClass()
            self._stack.addWidget(tab)
            self._platform_tabs.append((config_filename, tab))
            self._platform_index[title] = idx
        layout.addWidget(self._stack, 1)

        self.statusBar().setStyleSheet(
            'QStatusBar { background: #161b22; color: #484f58; '
            'border-top: 1px solid #30363d; padding: 4px 12px; }')
        self.statusBar().showMessage(
            "by mokevip | QQ 652831080 | github.com/moke8/XCI_Cover_Extractor")

        self._load_config()
        first_platform = PUBLISHER_GROUPS[0][1][0]
        self._switch_platform(first_platform)

    def _switch_platform(self, platform_name):
        idx = self._platform_index.get(platform_name)
        if idx is not None:
            self._stack.setCurrentIndex(idx)
            self._nav.set_active_platform(platform_name)

    def _open_scrape_settings(self):
        dlg = ScrapeSettingsDialog(self._scrape_settings, self)
        if dlg.exec():
            self._scrape_settings = dlg.get_settings()

    def get_global_settings(self):
        lang_idx = self.lang_combo.currentIndex()
        s = self._scrape_settings
        return {
            'lang_code': LANGUAGES[lang_idx][1],
            'google_lang': LANGUAGES[lang_idx][2],
            'thread_count': self.thread_spin.value(),
            'online_mode': s.get('online_mode', True),
            'video': s.get('video', False),
            'translate': s.get('translate', True),
            'proxy': s.get('proxy', ''),
            'api_key': s.get('api_key', ''),
            'scrape_mode': s.get('scrape_mode', 'complement'),
            'filename_as_title': s.get('filename_as_title', False),
            'datasource': s.get('datasource', 'thegamesdb'),
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
        scrape_keys = ('online_mode', 'scrape_mode', 'video', 'translate',
                       'filename_as_title', 'proxy', 'api_key', 'datasource', 'api_keys')
        for k in scrape_keys:
            if k in cfg:
                self._scrape_settings[k] = cfg[k]
        for config_filename, tab in self._platform_tabs:
            pcfg = load_json_config(_platform_config_path(config_filename))
            tab.load_config(pcfg)

    def save_config(self):
        cfg = {
            'language': self.lang_combo.currentText(),
            'thread_count': self.thread_spin.value(),
        }
        cfg.update(self._scrape_settings)
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
