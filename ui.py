from __future__ import annotations

import json
import math
import os
import platform
import random
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from PyQt6.QtCore import (
    QEasingCurve, QEvent, QMimeData, QObject, QPointF, QPropertyAnimation,
    QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal,
)

from PyQt6.QtGui import (
    QAction, QBrush, QColor, QDragEnterEvent, QDropEvent, QFont as _QFont,
    QFontDatabase, QIcon, QImage, QKeySequence, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QCheckBox, QFormLayout, QFrame, QGraphicsOpacityEffect,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QSystemTrayIcon, QTextEdit,
    QVBoxLayout, QWidget, QProgressBar,
)


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"
FONT_DIR   = BASE_DIR / "assets" / "fonts"
UI_SETTINGS_FILE = Path.home() / ".jarvis" / "config" / "settings.json"
LAYOUT_SETTINGS_FILE = Path.home() / ".jarvis" / "config" / "layout_settings.json"

# Each profile changes both cadence and rendering density.  Keeping this data
# centralized makes the Settings UI, the renderer, and JARVIS voice commands
# agree on what Low / Medium / High actually mean.
GRAPHICS_PROFILES = {
    "low": {
        "frame_ms": 50,       # 20 FPS target
        "render_stride": 3,
        "wave_stride": 3,
        "noise_count": 0,
        "scanline_step": 8,
        "antialias": False,
        "activity_nodes": 10,
        "metrics_ms": 3500,
    },
    "medium": {
        "frame_ms": 33,       # 30 FPS target
        "render_stride": 2,
        "wave_stride": 2,
        "noise_count": 60,
        "scanline_step": 6,
        "antialias": True,
        "activity_nodes": 16,
        "metrics_ms": 2000,
    },
    "high": {
        "frame_ms": 16,       # 60 FPS target
        "render_stride": 1,
        "wave_stride": 1,
        "noise_count": 200,
        "scanline_step": 4,
        "antialias": True,
        "activity_nodes": 24,
        "metrics_ms": 1200,
    },
}


def _normalize_graphics_quality(quality: str | None) -> str:
    value = str(quality or "medium").strip().lower()
    aliases = {
        "med": "medium", "mid": "medium", "normal": "medium",
        "balanced": "medium", "performance": "low", "battery": "low",
        "max": "high", "maximum": "high", "ultra": "high",
    }
    value = aliases.get(value, value)
    if value not in GRAPHICS_PROFILES:
        raise ValueError("Graphics quality must be low, medium, or high.")
    return value


def _read_ui_settings() -> dict:
    try:
        if UI_SETTINGS_FILE.exists():
            data = json.loads(UI_SETTINGS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def get_graphics_quality() -> str:
    try:
        return _normalize_graphics_quality(_read_ui_settings().get("graphics_quality", "medium"))
    except ValueError:
        return "medium"


def set_graphics_quality(quality: str) -> str:
    value = _normalize_graphics_quality(quality)
    settings = _read_ui_settings()
    settings["graphics_quality"] = value
    UI_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    UI_SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return value
VOICE_OPTIONS = [
    ("Puck",          "puck"),
    ("Charon",        "charon"),
    ("Kore",          "kore"),
    ("Fenrir",        "fenrir"),
    ("Aoede",         "aoede"),
    ("Leda",          "leda"),
    ("Orus",          "orus"),
    ("Schedar",       "schedar"),
    ("Zubenelgenubi", "zubenelgenubi"),
]
VOICE_VALUE_TO_LABEL = {value: label for label, value in VOICE_OPTIONS}
VOICE_LABEL_TO_VALUE = {label.lower(): value for label, value in VOICE_OPTIONS}

_DEFAULT_W, _DEFAULT_H = 1280, 820
_MIN_W,     _MIN_H     = 1100, 700
_LEFT_W  = 230
_RIGHT_W = 370

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"
_UI_FONT_FALLBACK = "Helvetica Neue" if _OS == "Darwin" else "Segoe UI" if _OS == "Windows" else "DejaVu Sans"
_TECH_FONT_FALLBACK = "Menlo" if _OS == "Darwin" else "Consolas" if _OS == "Windows" else "DejaVu Sans Mono"
UI_FONT = "Space Grotesk"
TECH_FONT = "JetBrains Mono"
DISPLAY_FONT = UI_FONT
_FONTS_LOADED = False


def _load_bundled_fonts() -> tuple[str, str]:
    """Register project fonts once and retain readable native fallbacks."""
    global UI_FONT, TECH_FONT, DISPLAY_FONT, _FONTS_LOADED
    if _FONTS_LOADED:
        return UI_FONT, TECH_FONT

    resolved = {}
    for role, filename, preferred in (
        ("ui", "SpaceGrotesk-Variable.ttf", "Space Grotesk"),
        ("tech", "JetBrainsMono-Variable.ttf", "JetBrains Mono"),
    ):
        font_id = QFontDatabase.addApplicationFont(str(FONT_DIR / filename))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if preferred in families:
            resolved[role] = preferred
        elif families:
            resolved[role] = families[0]

    UI_FONT = resolved.get("ui", _UI_FONT_FALLBACK)
    TECH_FONT = resolved.get("tech", _TECH_FONT_FALLBACK)
    DISPLAY_FONT = UI_FONT
    app = QApplication.instance()
    if app is not None:
        app.setFont(_QFont(UI_FONT, 10, _QFont.Weight.Normal))
    _FONTS_LOADED = True
    return UI_FONT, TECH_FONT


class QFont(_QFont):
    """Route legacy calls through the restrained two-family type system."""

    _UI_FAMILIES = {"Arial", "Sans Serif", "Helvetica", "Helvetica Neue", "Avenir Next"}
    _TECH_FAMILIES = {"Courier New", "Menlo", "Consolas", "Monospace"}

    def __init__(self, *args):
        if args and isinstance(args[0], str):
            values = list(args)
            is_tech = values[0] in self._TECH_FAMILIES
            if is_tech:
                values[0] = TECH_FONT
            elif values[0] in self._UI_FAMILIES:
                values[0] = UI_FONT
            if len(values) >= 3 and values[2] in {
                _QFont.Weight.Bold, _QFont.Weight.ExtraBold, _QFont.Weight.Black,
            }:
                values[2] = _QFont.Weight.Medium if is_tech else _QFont.Weight.DemiBold
            args = tuple(values)
        super().__init__(*args)


class C:
    # ── Backgrounds ──────────────────────────────────────────────────────────
    BG        = "#000306"   # near-black space
    PANEL     = "#00080f"
    PANEL2    = "#000b14"
    DARK      = "#000408"
    DARK2     = "#000c18"
    BAR_BG    = "#010f1a"
    CARD      = "#00090f"
    CARD_B    = "#000e18"
    HOLOGRAM  = "#00d4ff06"  # glass panel fill
    # ── Borders ──────────────────────────────────────────────────────────────
    BORDER    = "#0a2535"
    BORDER_B  = "#1a5c7a"
    BORDER_A  = "#0d3a55"
    STEEL     = "#0f2a3a"    # structural panel borders
    # ── Arc reactor blue — primary energy ────────────────────────────────────
    PRI       = "#00c8ff"    # arc reactor blue
    PRI_DIM   = "#006a88"
    PRI_GHO   = "#001520"
    PRI_GLOW  = "#00c8ff14"
    ENERGY    = "#00e5ff"    # core energy (brighter)
    ENERGY_D  = "#0088bb"
    # ── Accent colors ────────────────────────────────────────────────────────
    ACC       = "#ff8c00"    # amber warning
    ACC2      = "#ffb300"    # amber bright
    AMBER     = "#ffb300"
    AMBER_D   = "#cc8800"
    PURPLE    = "#7b61ff"
    PURPLE_D  = "#3d2fa0"
    GREEN     = "#00ff88"
    GREEN_D   = "#00aa55"
    GREEN_GLO = "#00ff8810"
    RED       = "#ff2244"
    RED_D     = "#aa1133"
    RED_BG    = "#200810"
    GREEN_BG  = "#001a0d"
    PURPLE_BG = "#0a0a14"
    MUTED_C   = "#ff3366"
    # ── Typography ───────────────────────────────────────────────────────────
    TEXT      = "#7ae8ff"
    TEXT_DIM  = "#1e5a6a"
    TEXT_MED  = "#3a9ab0"
    WHITE     = "#e8f8ff"
    WHITE_DIM = "#8ab8cc"




def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


# ---------------------------------------------------------------------------
# ThemeManager — dynamic color theming with presets
# ---------------------------------------------------------------------------

class ThemeManager:
    """Manages color themes for the JARVIS UI."""

    _COLOR_KEYS = (
        "BG", "PANEL", "PANEL2", "DARK", "DARK2", "BAR_BG", "CARD", "CARD_B",
        "BORDER", "BORDER_B", "BORDER_A", "STEEL", "PRI", "PRI_DIM", "PRI_GHO",
        "PRI_GLOW", "ENERGY", "ENERGY_D", "ACC", "ACC2", "PURPLE", "GREEN", "RED",
        "TEXT", "TEXT_DIM", "TEXT_MED", "WHITE", "WHITE_DIM", "RED_BG", "GREEN_BG",
        "PURPLE_BG", "MUTED_C", "HOLOGRAM", "AMBER", "AMBER_D", "PURPLE_D",
        "GREEN_D", "GREEN_GLO", "RED_D",
    )

    _THEMES = {
        "arc_reactor": {
            "name": "Arc Reactor Blue",
            "BG": "#000306",
            "PANEL": "#00080f",
            "PANEL2": "#000b14",
            "DARK": "#000408",
            "DARK2": "#000c18",
            "BAR_BG": "#010f1a",
            "CARD": "#00090f",
            "CARD_B": "#000e18",
            "BORDER": "#0a2535",
            "BORDER_B": "#1a5c7a",
            "BORDER_A": "#0d3a55",
            "STEEL": "#0f2a3a",
            "WHITE": "#e8f8ff",
            "WHITE_DIM": "#8ab8cc",
            "PRI": "#00c8ff", "PRI_DIM": "#006a88", "PRI_GHO": "#001520",
            "PRI_GLOW": "#00c8ff14", "ENERGY": "#00e5ff", "ENERGY_D": "#0088bb",
            "ACC": "#ff8c00", "ACC2": "#ffb300", "PURPLE": "#7b61ff",
            "GREEN": "#00ff88", "RED": "#ff2244", "TEXT": "#7ae8ff",
            "TEXT_DIM": "#1e5a6a", "TEXT_MED": "#3a9ab0",
            "RED_BG": "#200810", "GREEN_BG": "#001a0d", "PURPLE_BG": "#0a0a14",
            "MUTED_C": "#ff3366",
            "HOLOGRAM": "#00d4ff06", "AMBER": "#ffb300", "AMBER_D": "#cc8800",
            "PURPLE_D": "#3d2fa0", "GREEN_D": "#00aa55", "GREEN_GLO": "#00ff8810",
            "RED_D": "#aa1133",
        },
        "stealth_red": {
            "name": "Stealth Red",
            "BG": "#080203",
            "PANEL": "#0f0406",
            "PANEL2": "#140608",
            "DARK": "#040202",
            "DARK2": "#180810",
            "BAR_BG": "#1a0a10",
            "CARD": "#0f0406",
            "CARD_B": "#180810",
            "BORDER": "#350a15",
            "BORDER_B": "#7a1a2a",
            "BORDER_A": "#550d1a",
            "STEEL": "#3a0f1a",
            "WHITE": "#ffe8e8",
            "WHITE_DIM": "#cc8a8a",
            "PRI": "#ff2244", "PRI_DIM": "#881122", "PRI_GHO": "#200810",
            "PRI_GLOW": "#ff224414", "ENERGY": "#ff4466", "ENERGY_D": "#bb2244",
            "ACC": "#ff8c00", "ACC2": "#ffb300", "PURPLE": "#ff61a0",
            "GREEN": "#ff6644", "RED": "#ff2244", "TEXT": "#ffaaaa",
            "TEXT_DIM": "#6a2a2a", "TEXT_MED": "#b05a5a",
            "RED_BG": "#20060a", "GREEN_BG": "#1a0905", "PURPLE_BG": "#160710",
            "MUTED_C": "#ff6680",
            "HOLOGRAM": "#ff224406", "AMBER": "#ffb300", "AMBER_D": "#b87800",
            "PURPLE_D": "#7a2048", "GREEN_D": "#b93a28", "GREEN_GLO": "#ff664410",
            "RED_D": "#99152a",
        },
        "vibranium_purple": {
            "name": "Vibranium Purple",
            "BG": "#030108",
            "PANEL": "#06020f",
            "PANEL2": "#080314",
            "DARK": "#020104",
            "DARK2": "#0c0418",
            "BAR_BG": "#0f051a",
            "CARD": "#06020f",
            "CARD_B": "#0c0418",
            "BORDER": "#250a35",
            "BORDER_B": "#5a1a7a",
            "BORDER_A": "#3a0d55",
            "STEEL": "#2a0f3a",
            "WHITE": "#f0e8ff",
            "WHITE_DIM": "#a88acc",
            "PRI": "#a855f7", "PRI_DIM": "#6b21a8", "PRI_GHO": "#1a0530",
            "PRI_GLOW": "#a855f714", "ENERGY": "#c084fc", "ENERGY_D": "#7c3aed",
            "ACC": "#f472b6", "ACC2": "#fb923c", "PURPLE": "#a855f7",
            "GREEN": "#34d399", "RED": "#f43f5e", "TEXT": "#d8b4fe",
            "TEXT_DIM": "#4a2870", "TEXT_MED": "#8b5cf6",
            "RED_BG": "#210711", "GREEN_BG": "#041a13", "PURPLE_BG": "#160724",
            "MUTED_C": "#fb7185",
            "HOLOGRAM": "#a855f706", "AMBER": "#fb923c", "AMBER_D": "#c35d16",
            "PURPLE_D": "#6b21a8", "GREEN_D": "#168f68", "GREEN_GLO": "#34d39910",
            "RED_D": "#a81f3b",
        },
        "nanotech_gold": {
            "name": "Nanotech Gold",
            "BG": "#050400",
            "PANEL": "#0a0800",
            "PANEL2": "#0e0c00",
            "DARK": "#040300",
            "DARK2": "#141000",
            "BAR_BG": "#1a1500",
            "CARD": "#0a0800",
            "CARD_B": "#0e0c00",
            "BORDER": "#352a0a",
            "BORDER_B": "#7a5c1a",
            "BORDER_A": "#553a0d",
            "STEEL": "#3a2a0f",
            "WHITE": "#fffae8",
            "WHITE_DIM": "#ccb88a",
            "PRI": "#fbbf24", "PRI_DIM": "#a16207", "PRI_GHO": "#1a1000",
            "PRI_GLOW": "#fbbf2414", "ENERGY": "#fcd34d", "ENERGY_D": "#d97706",
            "ACC": "#f97316", "ACC2": "#fb923c", "PURPLE": "#c084fc",
            "GREEN": "#4ade80", "RED": "#ef4444", "TEXT": "#fef3c7",
            "TEXT_DIM": "#6a5a1e", "TEXT_MED": "#b09a3a",
            "RED_BG": "#210907", "GREEN_BG": "#061a0b", "PURPLE_BG": "#140c1c",
            "MUTED_C": "#fb7185",
            "HOLOGRAM": "#fbbf2406", "AMBER": "#fbbf24", "AMBER_D": "#a16207",
            "PURPLE_D": "#7e4aa0", "GREEN_D": "#228b4b", "GREEN_GLO": "#4ade8010",
            "RED_D": "#a72c2c",
        },
        "platinum": {
            "name": "Platinum White",
            "BG": "#e9edf2", "PANEL": "#f4f6f8", "PANEL2": "#eef1f5",
            "DARK": "#dfe4ea", "DARK2": "#d5dce4", "BAR_BG": "#d1d7df",
            "CARD": "#f8f9fb", "CARD_B": "#e6eaf0",
            "BORDER": "#b8c1cb", "BORDER_B": "#8795a4", "BORDER_A": "#a4afbb",
            "STEEL": "#c4ccd5", "WHITE": "#15202b", "WHITE_DIM": "#4d5d6c",
            "PRI": "#006f94", "PRI_DIM": "#3b8ca8", "PRI_GHO": "#dcebf1",
            "PRI_GLOW": "#006f9414", "ENERGY": "#0082a8", "ENERGY_D": "#005f7d",
            "ACC": "#a85c00", "ACC2": "#8b6500", "PURPLE": "#6652ad",
            "GREEN": "#18794e", "RED": "#c93443", "TEXT": "#263746",
            "TEXT_DIM": "#62717f", "TEXT_MED": "#405668",
            "RED_BG": "#f6e3e6", "GREEN_BG": "#dfeee6", "PURPLE_BG": "#e9e5f4",
            "MUTED_C": "#aa2735", "HOLOGRAM": "#006f9408",
            "AMBER": "#8b6500", "AMBER_D": "#6e5000", "PURPLE_D": "#493887",
            "GREEN_D": "#0f5f3b", "GREEN_GLO": "#18794e12", "RED_D": "#962432",
        },
    }

    _current = "arc_reactor"
    _listeners: list = []

    @classmethod
    def current_name(cls) -> str:
        return cls._current

    @classmethod
    def theme_names(cls) -> list[str]:
        return list(cls._THEMES.keys())

    @classmethod
    def theme_display_name(cls, key: str) -> str:
        return cls._THEMES.get(key, {}).get("name", key)

    @classmethod
    def set_theme(cls, key: str):
        if key not in cls._THEMES:
            return
        cls._current = key
        t = cls._THEMES[key]
        # Update the C class colors dynamically
        for attr in cls._COLOR_KEYS:
            if attr in t:
                setattr(C, attr, t[attr])
        # Notify listeners
        for cb in cls._listeners:
            try:
                cb(key)
            except Exception:
                pass

    @classmethod
    def add_listener(cls, cb):
        cls._listeners.append(cb)


# ---------------------------------------------------------------------------
# ChatBubbleWidget — chat-style conversation view
# ---------------------------------------------------------------------------

class ChatBubbleWidget(QWidget):
    """Chat-bubble style conversation view replacing raw text log."""

    _sig = pyqtSignal(str)
    command_submitted = pyqtSignal(str)   # emitted when user sends a command

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sig.connect(self._on_message)
        self.setStyleSheet("background: transparent;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{ background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 4px; }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 3px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        self._container = QWidget()
        self._container.setStyleSheet(f"background: {C.PANEL};")
        self._c_lay = QVBoxLayout(self._container)
        self._c_lay.setContentsMargins(8, 8, 8, 8)
        self._c_lay.setSpacing(6)
        self._c_lay.addStretch()

        self._scroll.setWidget(self._container)
        lay.addWidget(self._scroll, stretch=1)

        # ── Command input bar pinned at bottom ───────────────────────────
        input_bar = QWidget()
        self._input_bar = input_bar
        input_bar.setFixedHeight(42)
        input_bar.setStyleSheet(f"""
            QWidget {{
                background: {C.DARK};
                border-top: 1px solid {C.BORDER};
            }}
        """)
        ib_lay = QHBoxLayout(input_bar)
        ib_lay.setContentsMargins(6, 4, 6, 4)
        ib_lay.setSpacing(6)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a message to JARVIS…")
        self._input.setFont(QFont(UI_FONT, 10))
        self._input.setFixedHeight(32)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.DARK};
                color: {C.WHITE};
                border: 1px solid {C.ENERGY}55;
                border-radius: 4px;
                padding: 4px 10px;
            }}
            QLineEdit:focus {{
                border: 1px solid {C.ENERGY};
                background: {C.DARK2};
            }}
            QLineEdit::placeholder {{
                color: {C.TEXT_DIM};
            }}
        """)
        self._input.returnPressed.connect(self._submit)
        ib_lay.addWidget(self._input, stretch=1)

        send_btn = QPushButton("▸")
        self._send_btn = send_btn
        send_btn.setFixedSize(32, 32)
        send_btn.setFont(QFont(DISPLAY_FONT, 12, QFont.Weight.DemiBold))
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.ENERGY}22;
                color: {C.ENERGY};
                border: 1px solid {C.ENERGY}66;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background: {C.ENERGY}44;
                border: 1px solid {C.ENERGY};
                color: {C.WHITE};
            }}
        """)
        send_btn.clicked.connect(self._submit)
        ib_lay.addWidget(send_btn)

        lay.addWidget(input_bar)

        self._messages: list[dict] = []

    def refresh_theme(self):
        """Restyle the live conversation and rebuild existing bubbles."""
        self._scroll.setStyleSheet(f"""
            QScrollArea {{ background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 4px; }}
            QScrollBar:vertical {{ background: {C.BG}; width: 6px; border: none; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 3px; min-height: 16px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        self._scroll.viewport().setStyleSheet(f"background: {C.PANEL};")
        self._container.setStyleSheet(f"background: {C.PANEL};")
        self._input_bar.setStyleSheet(f"""
            QWidget {{ background: {C.DARK}; border-top: 1px solid {C.BORDER}; }}
        """)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.DARK}; color: {C.WHITE}; border: 1px solid {C.ENERGY};
                border-radius: 4px; padding: 4px 10px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.ENERGY}; background: {C.DARK2}; }}
            QLineEdit::placeholder {{ color: {C.TEXT_DIM}; }}
        """)
        self._send_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI_GHO}; color: {C.ENERGY};
                border: 1px solid {C.ENERGY_D}; border-radius: 4px;
            }}
            QPushButton:hover {{ background: {C.DARK2}; border-color: {C.ENERGY}; color: {C.WHITE}; }}
        """)

        saved_messages = list(self._messages)
        if hasattr(self, "_typing_timer"):
            self._typing_timer.stop()
        while self._c_lay.count() > 1:
            item = self._c_lay.takeAt(0)
            if item and item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self._messages.clear()
        prefixes = {"user": "You: ", "ai": "JARVIS: ", "file": "FILE: ", "error": "ERR: ", "sys": "SYS: "}
        for message in saved_messages:
            self._skip_typing = True
            self._on_message(prefixes.get(message["sender"], "SYS: ") + message["text"])

    def _typing_tick(self):
        self._typing_idx += 1
        if self._typing_idx > len(self._typing_body):
            self._typing_timer.stop()
            # Remove temp bubble
            if hasattr(self, '_typing_bubble') and self._typing_bubble:
                self._c_lay.removeWidget(self._typing_bubble)
                self._typing_bubble.deleteLater()
                self._typing_bubble = None
            # Render final message properly
            self._skip_typing = True
            self._on_message(self._typing_full)
            return
        partial = 'JARVIS: ' + self._typing_body[:self._typing_idx]
        if hasattr(self, '_typing_bubble') and self._typing_bubble:
            self._c_lay.removeWidget(self._typing_bubble)
            self._typing_bubble.deleteLater()
        lbl = QLabel(partial + '▌')
        lbl.setFont(QFont(UI_FONT, 9))
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f'color: {C.PRI}; background: {C.PRI_GHO}; border: 1px solid {C.PRI}44; border-radius: 6px; padding: 6px 10px;')
        self._c_lay.addWidget(lbl)
        self._typing_bubble = lbl
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _submit(self):
        txt = self._input.text().strip()
        if not txt:
            return
        self._input.clear()
        self.command_submitted.emit(txt)

    def append_log(self, text: str):
        """Thread-safe message append — compatible with LogWidget API."""
        self._sig.emit(text)

    def _on_message(self, text: str):
        # Typing animation for JARVIS messages
        if text.lower().startswith('jarvis:') and not getattr(self, '_skip_typing', False):
            body = text[7:].strip()
            self._typing_idx = 0
            self._typing_body = body
            self._typing_full = text
            if not hasattr(self, '_typing_timer'):
                from PyQt6.QtCore import QTimer as _QT
                self._typing_timer = _QT(self)
                self._typing_timer.timeout.connect(self._typing_tick)
            self._typing_timer.start(12)
            return

        self._skip_typing = False
        tl = text.lower().strip()

        # Determine message type
        if tl.startswith("you:"):
            sender = "user"
            display = text[4:].strip()
            align = Qt.AlignmentFlag.AlignRight
            bg_col = C.BORDER
            border_col = C.PRI_DIM
            text_col = C.WHITE
            name = "YOU"
        elif tl.startswith("jarvis:"):
            sender = "ai"
            display = text[7:].strip()
            align = Qt.AlignmentFlag.AlignLeft
            bg_col = C.PRI_GHO
            border_col = C.PRI
            text_col = C.PRI
            name = "JARVIS"
        elif tl.startswith("file:"):
            sender = "file"
            display = text[5:].strip()
            align = Qt.AlignmentFlag.AlignLeft
            bg_col = C.GREEN_BG
            border_col = C.GREEN_D
            text_col = C.GREEN
            name = "FILE"
        elif "err" in tl or tl.startswith("err:"):
            sender = "error"
            display = text
            align = Qt.AlignmentFlag.AlignLeft
            bg_col = C.RED_BG
            border_col = C.RED_D
            text_col = C.RED
            name = "ERROR"
        else:
            sender = "sys"
            display = text.replace("SYS: ", "").replace("SYS:", "")
            align = Qt.AlignmentFlag.AlignLeft
            bg_col = C.PURPLE_BG
            border_col = C.ACC
            text_col = C.ACC2
            name = "SYS"

        ts = time.strftime("%H:%M")

        # Build bubble widget
        bubble = QWidget()
        bubble.setStyleSheet("background: transparent;")
        b_lay = QHBoxLayout(bubble)
        b_lay.setContentsMargins(0, 0, 0, 0)
        b_lay.setSpacing(0)

        if sender == "user":
            b_lay.addStretch()

        card = QWidget()
        max_w = 280 if sender == "user" else 300
        card.setMaximumWidth(max_w)
        card.setStyleSheet(f"""
            QWidget {{
                background: {bg_col};
                border: 1px solid {border_col};
                border-radius: 8px;
            }}
        """)
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(10, 6, 10, 6)
        c_lay.setSpacing(3)

        # Header: name + timestamp
        hdr = QHBoxLayout()
        hdr.setSpacing(4)
        name_lbl = QLabel(name)
        name_lbl.setFont(QFont(DISPLAY_FONT, 8, QFont.Weight.DemiBold))
        name_lbl.setStyleSheet(f"color: {text_col}; background: transparent; border: none;")
        hdr.addWidget(name_lbl)
        hdr.addStretch()
        ts_lbl = QLabel(ts)
        ts_lbl.setFont(QFont(UI_FONT, 7))
        ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        hdr.addWidget(ts_lbl)
        c_lay.addLayout(hdr)

        # Message text
        msg = QLabel(display)
        msg.setFont(QFont(UI_FONT, 10))
        msg.setWordWrap(True)
        msg.setStyleSheet(f"color: {C.WHITE}; background: transparent; border: none;")
        c_lay.addWidget(msg)

        b_lay.addWidget(card)

        if sender != "user":
            b_lay.addStretch()

        self._c_lay.insertWidget(self._c_lay.count() - 1, bubble)
        self._messages.append({"sender": sender, "text": display, "ts": ts})

        # Keep max 200 messages
        if len(self._messages) > 200:
            self._messages.pop(0)
            item = self._c_lay.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        # Auto-scroll to bottom
        QTimer.singleShot(50, self._scroll_bottom)

    def _scroll_bottom(self):
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())


# ---------------------------------------------------------------------------
# FocusDialogueWidget — cinematic lower-third for the focused interface
# ---------------------------------------------------------------------------

class FocusDialogueWidget(QWidget):
    """Shows the current exchange without turning focus mode into a chat sidebar."""

    _sig = pyqtSignal(str)
    command_submitted = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sig.connect(self._apply_message)
        self.setFixedHeight(106)
        self.setStyleSheet("background: transparent;")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(48, 2, 48, 10)
        outer.setSpacing(0)

        self._shell = QFrame()
        self._shell.setObjectName("focusDialogueShell")
        self._shell.setMaximumWidth(760)
        shell_lay = QVBoxLayout(self._shell)
        shell_lay.setContentsMargins(16, 9, 16, 9)
        shell_lay.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._channel_lbl = QLabel("DIALOGUE LINK")
        self._channel_lbl.setFont(QFont(DISPLAY_FONT, 8, QFont.Weight.DemiBold))
        header.addWidget(self._channel_lbl)
        header.addStretch()
        self._live_lbl = QLabel("●  LIVE")
        self._live_lbl.setFont(QFont(UI_FONT, 7, QFont.Weight.DemiBold))
        header.addWidget(self._live_lbl)
        shell_lay.addLayout(header)
        self._channel_lbl.hide()
        self._live_lbl.hide()

        message_row = QHBoxLayout()
        message_row.setSpacing(10)
        self._speaker_lbl = QLabel("JARVIS")
        self._speaker_lbl.setFixedWidth(62)
        self._speaker_lbl.setFont(QFont(DISPLAY_FONT, 8, QFont.Weight.DemiBold))
        message_row.addWidget(self._speaker_lbl, alignment=Qt.AlignmentFlag.AlignTop)
        self._message_lbl = QLabel("Standing by.")
        self._message_lbl.setFont(QFont(UI_FONT, 10, QFont.Weight.Medium))
        self._message_lbl.setWordWrap(True)
        self._message_lbl.setMaximumHeight(34)
        message_row.addWidget(self._message_lbl, stretch=1)
        shell_lay.addLayout(message_row)

        input_row = QHBoxLayout()
        input_row.setSpacing(7)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Give JARVIS a command")
        self._input.setFont(QFont(UI_FONT, 9))
        self._input.setFixedHeight(30)
        self._input.returnPressed.connect(self._submit)
        input_row.addWidget(self._input, stretch=1)
        self._send_btn = QPushButton("SEND")
        self._send_btn.setFont(QFont(UI_FONT, 8, QFont.Weight.DemiBold))
        self._send_btn.setFixedSize(64, 30)
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.clicked.connect(self._submit)
        input_row.addWidget(self._send_btn)
        shell_lay.addLayout(input_row)

        outer.addStretch()
        outer.addWidget(self._shell, stretch=1)
        outer.addStretch()
        self.refresh_theme()

    def append_log(self, text: str):
        self._sig.emit(str(text or ""))

    def _apply_message(self, text: str):
        clean = str(text or "").strip()
        if not clean:
            return
        lower = clean.lower()
        if lower.startswith("you:"):
            speaker, body, color = "YOU", clean[4:].strip(), C.WHITE
        elif lower.startswith("jarvis:"):
            speaker, body, color = "JARVIS", clean[7:].strip(), C.PRI
        elif lower.startswith("err:") or "error" in lower:
            speaker, body, color = "ALERT", clean.replace("ERR:", "").strip(), C.RED
        else:
            speaker = "SYSTEM"
            body = clean.replace("SYS:", "").strip()
            color = C.TEXT_MED
        self._speaker_lbl.setText(speaker)
        self._speaker_lbl.setStyleSheet(f"color: {color}; background: transparent;")
        self._message_lbl.setText(body)

    def _submit(self):
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self.command_submitted.emit(text)

    def refresh_theme(self):
        self._shell.setStyleSheet(f"""
            QFrame#focusDialogueShell {{
                background: {C.PANEL}; border: 1px solid {C.BORDER_B}; border-radius: 7px;
            }}
        """)
        self._channel_lbl.setStyleSheet(
            f"color: {C.TEXT_MED}; background: transparent; letter-spacing: 1px;"
        )
        self._live_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        self._speaker_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._message_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.DARK}; color: {C.WHITE}; border: 1px solid {C.BORDER};
                border-radius: 4px; padding: 0 10px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
            QLineEdit::placeholder {{ color: {C.TEXT_DIM}; }}
        """)
        self._send_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI_GHO}; color: {C.PRI}; border: 1px solid {C.PRI_DIM};
                border-radius: 4px;
            }}
            QPushButton:hover, QPushButton:focus {{ background: {C.CARD}; border-color: {C.PRI}; }}
            QPushButton:pressed {{ background: {C.DARK2}; }}
        """)


# ---------------------------------------------------------------------------
# ResearchProgressWidget — compact background deep-research status
# ---------------------------------------------------------------------------

class ResearchProgressWidget(QWidget):
    """Compact, factual progress surface for a long-running JARVIS task."""

    _update_sig = pyqtSignal(object)
    _hide_sig = pyqtSignal()

    def __init__(
        self,
        parent=None,
        *,
        task_title: str = "DEEP RESEARCH",
        accessible_name: str = "Deep Research background progress",
    ):
        super().__init__(parent)
        self.setFixedHeight(106)
        self.setAccessibleName(accessible_name)
        self._task_title = str(task_title or "TASK").upper()
        self._visible_mode = False
        self._question = ""
        self._state = "running"
        self._display_generation = 0
        self._fade_animation = None
        self._update_sig.connect(self._apply_update)
        self._hide_sig.connect(self.hide)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(48, 2, 48, 6)
        outer.setSpacing(0)

        self._shell = QFrame()
        self._shell.setObjectName("researchProgressShell")
        self._shell.setMaximumWidth(820)
        shell_lay = QVBoxLayout(self._shell)
        shell_lay.setContentsMargins(16, 10, 16, 10)
        shell_lay.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._title = QLabel(self._task_title)
        self._title.setFont(QFont(UI_FONT, 9, QFont.Weight.DemiBold))
        header.addWidget(self._title)
        header.addStretch()
        self._status = QLabel("RUNNING IN THE BACKGROUND  ·  0%")
        self._status.setFont(QFont(TECH_FONT, 8, QFont.Weight.Medium))
        header.addWidget(self._status)
        shell_lay.addLayout(header)

        self._question_lbl = QLabel("Preparing research brief")
        self._question_lbl.setFont(QFont(UI_FONT, 10, QFont.Weight.Medium))
        self._question_lbl.setWordWrap(False)
        shell_lay.addWidget(self._question_lbl)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(5)
        self._bar.setAccessibleName("Deep Research completion")
        shell_lay.addWidget(self._bar)

        self._phase = QLabel("Queued")
        self._phase.setFont(QFont(UI_FONT, 8, QFont.Weight.Normal))
        shell_lay.addWidget(self._phase)

        outer.addStretch()
        outer.addWidget(self._shell, stretch=1)
        outer.addStretch()
        self.refresh_theme()
        self.hide()

    def start(self, question: str, visible: bool = False):
        self._update_sig.emit({
            "question": str(question or self._task_title.title()),
            "percent": 0,
            "phase": "Queued",
            "state": "running",
            "visible": bool(visible),
        })

    def update_progress(
        self,
        question: str,
        percent: int,
        phase: str,
        artifacts: list[str] | None = None,
        warnings: list[str] | None = None,
        visible: bool | None = None,
    ):
        self._update_sig.emit({
            "question": str(question or self._question or self._task_title.title()),
            "percent": max(0, min(100, int(percent or 0))),
            "phase": str(phase or "Researching"),
            "state": "completed" if int(percent or 0) >= 100 else "running",
            "artifacts": list(artifacts or []),
            "warnings": list(warnings or []),
            "visible": self._visible_mode if visible is None else bool(visible),
        })

    def finish(self, state: str, detail: str):
        self._update_sig.emit({
            "question": self._question or self._task_title.title(),
            "percent": self._bar.value(),
            "phase": str(detail or state),
            "state": str(state or "failed").lower(),
        })

    def dismiss(self):
        self._hide_sig.emit()

    def _apply_update(self, update: dict):
        self._question = str(update.get("question") or self._question or self._task_title.title())
        self._state = str(update.get("state") or "running")
        self._visible_mode = bool(update.get("visible", self._visible_mode))
        percent = max(0, min(100, int(update.get("percent", 0) or 0)))
        phase = str(update.get("phase") or "Researching")
        artifacts = update.get("artifacts") or []
        warnings = update.get("warnings") or []

        if self._state == "running" and percent == 0:
            self._display_generation += 1

        self._question_lbl.setText(self._question)
        self._question_lbl.setToolTip(self._question)
        self._bar.setValue(percent)
        if self._state == "completed":
            status_label = "COMPLETE"
            if artifacts:
                phase = f"Report ready: {Path(str(artifacts[0])).name}"
            if warnings:
                phase += f"  ·  {len(warnings)} warning{'s' if len(warnings) != 1 else ''}"
        elif self._state == "failed":
            status_label = "FAILED"
        elif self._state == "cancelled":
            status_label = "CANCELLED"
        else:
            status_label = "WORKING LIVE" if self._visible_mode else "RUNNING IN THE BACKGROUND"
        self._status.setText(f"{status_label}  ·  {percent}%")
        self._phase.setText(phase)
        self.refresh_theme()

        if self.isHidden():
            self.show()
            effect = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(effect)
            self._fade_animation = QPropertyAnimation(effect, b"opacity", self)
            self._fade_animation.setDuration(180)
            self._fade_animation.setStartValue(0.0)
            self._fade_animation.setEndValue(1.0)
            self._fade_animation.setEasingCurve(QEasingCurve.Type.OutQuart)
            self._fade_animation.start()

        if self._state in {"completed", "failed", "cancelled"}:
            generation = self._display_generation
            QTimer.singleShot(9000, lambda: self._dismiss_if_current(generation))

    def _dismiss_if_current(self, generation: int):
        if generation == self._display_generation:
            self.dismiss()

    def refresh_theme(self):
        state_color = {
            "completed": C.GREEN,
            "failed": C.RED,
            "cancelled": C.ACC,
        }.get(self._state, C.PRI)
        self._shell.setStyleSheet(f"""
            QFrame#researchProgressShell {{
                background: {C.PANEL}; border: 1px solid {C.BORDER_B}; border-radius: 7px;
            }}
        """)
        self._title.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; letter-spacing: 1px;")
        self._status.setStyleSheet(f"color: {state_color}; background: transparent;")
        self._question_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        self._phase.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._bar.setStyleSheet(f"""
            QProgressBar {{ background: {C.DARK2}; border: none; border-radius: 2px; }}
            QProgressBar::chunk {{ background: {state_color}; border-radius: 2px; }}
        """)


# ---------------------------------------------------------------------------
# ToastNotification — floating notification system
# ---------------------------------------------------------------------------

class ToastNotification(QWidget):
    """Floating toast notification that auto-dismisses."""

    def __init__(self, parent, message: str, toast_type: str = "info", duration: int = 4000):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(40)

        colors = {
            "info":    (C.PRI,    C.PRI_GHO),
            "success": (C.GREEN,  C.GREEN_BG),
            "warning": (C.ACC,    C.ACC2),
            "error":   (C.RED,    C.RED_BG),
        }
        fg, bg = colors.get(toast_type, colors["info"])

        self.setStyleSheet(f"""
            ToastNotification {{
                background: {bg};
                border: 1px solid {fg}88;
                border-radius: 6px;
            }}
        """)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.setSpacing(8)

        symbols = {"info": "◈", "success": "✓", "warning": "⚠", "error": "✗"}
        sym = QLabel(symbols.get(toast_type, "◈"))
        sym.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        sym.setStyleSheet(f"color: {fg}; background: transparent;")
        lay.addWidget(sym)

        msg = QLabel(message)
        msg.setFont(QFont("Courier New", 8))
        msg.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(msg, stretch=1)

        close = QPushButton("✕")
        close.setFixedSize(18, 18)
        close.setFont(QFont("Courier New", 8))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM}; border: none; }}
            QPushButton:hover {{ color: {fg}; }}
        """)
        close.clicked.connect(self._dismiss)
        lay.addWidget(close)

        # Opacity for fade
        self._opacity_effect = None
        try:
            from PyQt6.QtWidgets import QGraphicsOpacityEffect
            self._opacity_effect = QGraphicsOpacityEffect(self)
            self._opacity_effect.setOpacity(1.0)
            self.setGraphicsEffect(self._opacity_effect)
        except Exception:
            pass

        # Auto-dismiss timer
        QTimer.singleShot(duration, self._dismiss)

    def _dismiss(self):
        try:
            self.hide()
            self.deleteLater()
        except Exception:
            pass


class ToastManager:
    """Manages toast notification positioning."""

    _toasts: list[ToastNotification] = []

    @classmethod
    def _is_alive(cls, t) -> bool:
        """Check if a toast widget is still alive and visible."""
        try:
            return t.isVisible()
        except RuntimeError:
            return False

    @classmethod
    def show_toast(cls, parent: QWidget, message: str, toast_type: str = "info",
                   duration: int = 4000):
        try:
            toast = ToastNotification(parent, message, toast_type, duration)
            toast.setFixedWidth(min(400, parent.width() - 40))

            # Clean up dead references first
            cls._toasts = [t for t in cls._toasts if cls._is_alive(t)]

            # Position from top-right, stacking downward
            y_offset = 10
            for t in cls._toasts:
                y_offset += t.height() + 6
            toast.move(parent.width() - toast.width() - 10, y_offset)
            toast.show()
            toast.raise_()
            cls._toasts.append(toast)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# ToolProgressWidget — active tool execution indicator
# ---------------------------------------------------------------------------

class ToolProgressWidget(QWidget):
    """Shows active tool execution with name, elapsed time, and spinner."""

    _show_sig = pyqtSignal(str)
    _hide_sig = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setStyleSheet(f"""
            QWidget {{
                background: {C.PURPLE}18;
                border: 1px solid {C.PURPLE}44;
                border-radius: 4px;
            }}
        """)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 2, 10, 2)
        lay.setSpacing(6)

        self._spinner_chars = ["◐", "◓", "◑", "◒"]
        self._spinner_idx = 0

        self._spinner = QLabel("◐")
        self._spinner.setFont(QFont("Courier New", 10))
        self._spinner.setStyleSheet(f"color: {C.PURPLE}; background: transparent; border: none;")
        lay.addWidget(self._spinner)

        self._tool_lbl = QLabel("EXECUTING...")
        self._tool_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._tool_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent; border: none;")
        lay.addWidget(self._tool_lbl, stretch=1)

        self._time_lbl = QLabel("0s")
        self._time_lbl.setFont(QFont("Courier New", 7))
        self._time_lbl.setStyleSheet(f"color: {C.PURPLE}; background: transparent; border: none;")
        lay.addWidget(self._time_lbl)

        self._start_time = 0.0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)

        self._show_sig.connect(self._on_show)
        self._hide_sig.connect(self._on_hide)
        self.hide()

    def show_tool(self, name: str):
        self._show_sig.emit(name)

    def hide_tool(self):
        self._hide_sig.emit()

    def _on_show(self, name: str):
        self._tool_lbl.setText(f"EXECUTING: {name.upper()}")
        self._start_time = time.time()
        self._spinner_idx = 0
        self._tmr.start(200)
        window = self.window()
        if getattr(window, "_command_center_open", True):
            self.show()

    def _on_hide(self):
        self._tmr.stop()
        self.hide()

    def _tick(self):
        self._spinner_idx = (self._spinner_idx + 1) % len(self._spinner_chars)
        self._spinner.setText(self._spinner_chars[self._spinner_idx])
        elapsed = time.time() - self._start_time
        if elapsed < 60:
            self._time_lbl.setText(f"{elapsed:.0f}s")
        else:
            m = int(elapsed // 60)
            s = int(elapsed % 60)
            self._time_lbl.setText(f"{m}m {s}s")


# ---------------------------------------------------------------------------
# CompactModeWidget — floating mini arc reactor
# ---------------------------------------------------------------------------

class CompactModeWidget(QWidget):
    """Small floating circular arc reactor widget for compact mode."""

    expand_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(80, 80)

        self._tick = 0
        self._ring_angle = 0.0
        self._state = "LISTENING"
        self._drag_pos = None

        ThemeManager.add_listener(lambda _: self.update())
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(30)

    def set_state(self, state: str):
        self._state = state

    def _step(self):
        self._tick += 1
        speed = 2.0 if self._state in ("SPEAKING", "THINKING") else 0.5
        self._ring_angle = (self._ring_angle + speed) % 360
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        r = min(W, H) / 2 - 4

        # Background circle
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(qcol(C.DARK, 220)))
        p.drawEllipse(QPointF(cx, cy), r, r)

        # Outer ring
        p.setPen(QPen(qcol(C.PRI, 120), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)

        # Rotating arcs
        rect = QRectF(cx - r + 4, cy - r + 4, (r - 4) * 2, (r - 4) * 2)
        is_active = self._state in ("SPEAKING", "THINKING", "PROCESSING")
        alpha = 200 if is_active else 100
        p.setPen(QPen(qcol(C.ENERGY, alpha), 2))
        for i in range(3):
            start = int((self._ring_angle + i * 120) * 16)
            p.drawArc(rect, start, 60 * 16)

        # Core glow
        core_r = r * 0.3
        grad = QRadialGradient(QPointF(cx, cy), core_r)
        glow_a = 180 if is_active else 80
        grad.setColorAt(0, qcol(C.ENERGY, glow_a))
        grad.setColorAt(1, qcol(C.PRI, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(cx, cy), core_r, core_r)

        # State indicator dot
        state_col = {
            "LISTENING": C.GREEN, "SPEAKING": C.PRI,
            "THINKING": C.ACC, "PROCESSING": C.PURPLE,
        }.get(self._state, C.TEXT_DIM)
        p.setBrush(QBrush(qcol(state_col)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy + r - 10), 4, 4)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        if self._drag_pos:
            # If barely moved, treat as click → expand
            delta = event.globalPosition().toPoint() - self.frameGeometry().topLeft() - self._drag_pos
            if abs(delta.x()) < 5 and abs(delta.y()) < 5:
                self.expand_requested.emit()
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        self.expand_requested.emit()


# ---------------------------------------------------------------------------
# Popup System - Contextual holographic popups orbiting the AI Core
# ---------------------------------------------------------------------------

from enum import Enum
from dataclasses import dataclass
from typing import List, Optional


class PopupType(Enum):
    """Types of popups with different sizes, durations, and priorities."""
    MICRO = "micro"
    INFORMATION = "information"
    ACTION = "action"
    RESEARCH = "research"
    CRITICAL = "critical"
    PRESENCE = "presence"


class PopupPriority(Enum):
    """Priority levels for popup management."""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class PopupConfig:
    """Configuration for each popup type."""
    type: PopupType
    width: int
    height: int
    duration: int  # milliseconds, 0 for persistent (critical)
    priority: PopupPriority
    max_active: int
    orbit_radius: int
    opacity: float


# Popup configurations
POPUP_CONFIGS = {
    PopupType.MICRO: PopupConfig(
        type=PopupType.MICRO,
        width=180,
        height=50,
        duration=2500,
        priority=PopupPriority.LOW,
        max_active=5,
        orbit_radius=100,
        opacity=0.9
    ),
    PopupType.INFORMATION: PopupConfig(
        type=PopupType.INFORMATION,
        width=220,
        height=80,
        duration=6000,
        priority=PopupPriority.MEDIUM,
        max_active=5,
        orbit_radius=130,
        opacity=0.9
    ),
    PopupType.ACTION: PopupConfig(
        type=PopupType.ACTION,
        width=250,
        height=90,
        duration=8000,
        priority=PopupPriority.HIGH,
        max_active=5,
        orbit_radius=160,
        opacity=0.9
    ),
    PopupType.RESEARCH: PopupConfig(
        type=PopupType.RESEARCH,
        width=300,
        height=120,
        duration=12000,
        priority=PopupPriority.HIGH,
        max_active=2,  # Max 2 large popups
        orbit_radius=190,
        opacity=0.9
    ),
    PopupType.CRITICAL: PopupConfig(
        type=PopupType.CRITICAL,
        width=280,
        height=100,
        duration=0,  # Persistent until acknowledged
        priority=PopupPriority.CRITICAL,
        max_active=5,
        orbit_radius=160,
        opacity=0.95
    ),
    PopupType.PRESENCE: PopupConfig(
        type=PopupType.PRESENCE,
        width=200,
        height=60,
        duration=4000,  # 4 seconds
        priority=PopupPriority.LOW,
        max_active=5,
        orbit_radius=110,
        opacity=0.85
    )
}


class BasePopup(QWidget):
    """Base class for all popup types."""

    def __init__(self, parent: QWidget, message: str, popup_type: PopupType):
        super().__init__(parent)
        self.popup_type = popup_type
        self.config = POPUP_CONFIGS[popup_type]
        
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedSize(self.config.width, self.config.height)
        
        # Styling based on popup type
        bg_colors = {
            PopupType.MICRO: f"{C.PRI_GHO}cc",
            PopupType.INFORMATION: f"{C.BORDER}aa",
            PopupType.ACTION: f"{C.ACC}15",
            PopupType.RESEARCH: f"{C.PURPLE}15",
            PopupType.CRITICAL: f"{C.RED}20",
        }
        
        border_colors = {
            PopupType.MICRO: f"{C.PRI}44",
            PopupType.INFORMATION: f"{C.BORDER}88",
            PopupType.ACTION: f"{C.ACC}88",
            PopupType.RESEARCH: f"{C.PURPLE}88",
            PopupType.CRITICAL: f"{C.RED}cc",
        }
        
        text_colors = {
            PopupType.MICRO: C.PRI,
            PopupType.INFORMATION: C.TEXT,
            PopupType.ACTION: C.ACC,
            PopupType.RESEARCH: C.PURPLE,
            PopupType.CRITICAL: C.RED,
        }
        
        bg = bg_colors.get(popup_type, f"{C.PRI_GHO}cc")
        border = border_colors.get(popup_type, f"{C.PRI}44")
        text_color = text_colors.get(popup_type, C.WHITE)
        
        self.setStyleSheet(f"""
            BasePopup {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
        """)
        
        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        
        # Icon/symbol
        symbols = {
            PopupType.MICRO: "◈",
            PopupType.INFORMATION: "ⓘ",
            PopupType.ACTION: "⚡",
            PopupType.RESEARCH: "🔍",
            PopupType.CRITICAL: "‼",
        }
        symbol = QLabel(symbols.get(popup_type, "◈"))
        symbol.setFont(QFont("Courier New", 14, QFont.Weight.Bold))
        symbol.setStyleSheet(f"color: {text_color}; background: transparent;")
        layout.addWidget(symbol, alignment=Qt.AlignmentFlag.AlignLeft)
        
        # Message
        msg_label = QLabel(message)
        msg_label.setFont(QFont("Courier New", 9))
        msg_label.setStyleSheet(f"color: {text_color}; background: transparent;")
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label, stretch=1)
        
        # Close button for non-critical popups
        if popup_type != PopupType.CRITICAL:
            close_btn = QPushButton("✕")
            close_btn.setFixedSize(20, 20)
            close_btn.setFont(QFont("Courier New", 8))
            close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            close_btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    color: %s88;
                    border: none;
                }
                QPushButton:hover {
                    color: %s;
                    background: %s11;
                }
            """ % (text_color, text_color, text_color))
            close_btn.clicked.connect(self._dismiss)
            layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
        
        # Opacity effect for fade animations
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(self.config.opacity)
        self.setGraphicsEffect(self._opacity_effect)
        
        # Auto-dismiss timer (if not critical)
        if self.config.duration > 0:
            QTimer.singleShot(self.config.duration, self._dismiss)
        
        # Track creation time for priority management
        self._created_at = time.time()
    
    def _dismiss(self):
        """Dismiss the popup with fade-out animation."""
        try:
            if self._opacity_effect:
                anim = QPropertyAnimation(self._opacity_effect, b"opacity")
                anim.setDuration(300)
                anim.setStartValue(self.config.opacity)
                anim.setEndValue(0.0)
                anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                anim.finished.connect(self.hide)
                anim.start()
                
                # Actually delete after animation
                QTimer.singleShot(350, self.deleteLater)
            else:
                self.hide()
                self.deleteLater()
        except Exception:
            self.hide()
            self.deleteLater()


class PopupManager(QObject):
    """Manages popup creation, positioning, and lifecycle."""
    
    popup_created = pyqtSignal(QWidget)
    popup_destroyed = pyqtSignal(QWidget)
    
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.parent_widget = parent
        self.active_popups: List[BasePopup] = []
        self._orbit_positions = self._calculate_orbit_positions()
    
    def _calculate_orbit_positions(self) -> List[QPointF]:
        """Calculate orbit positions around the center."""
        # 8 positions around a circle: top, top-right, right, bottom-right,
        # bottom, bottom-left, left, top-left
        positions = []
        center_x = self.parent_widget.width() // 2
        center_y = self.parent_widget.height() // 2
        
        # We'll dynamically calculate based on orbit radius when showing
        # For now, return angles that we'll use with radius
        angles = [0, 45, 90, 135, 180, 225, 270, 315]  # degrees
        for angle in angles:
            positions.append(angle)
        return positions
    
    def show_popup(
        self,
        message: str,
        popup_type: PopupType = PopupType.INFORMATION,
        preferred_position: Optional[int] = None
    ) -> Optional[BasePopup]:
        """Show a popup with the given message and type."""
        config = POPUP_CONFIGS[popup_type]
        
        # Enforce limits
        if not self._can_show_popup(popup_type):
            # Remove lowest priority popup to make room
            self._remove_lowest_priority_popup()
        
        # Create popup
        popup = BasePopup(self.parent_widget, message, popup_type)
        
        # Position it
        pos = self._get_orbit_position(popup, preferred_position)
        popup.move(int(pos.x()), int(pos.y()))
        
        # Show and raise
        popup.show()
        popup.raise_()
        
        # Track
        self.active_popups.append(popup)
        self.popup_created.emit(popup)
        
        # Clean up finished popups periodically
        QTimer.singleShot(1000, self._cleanup_finished_popups)
        
        return popup
    
    def _can_show_popup(self, popup_type: PopupType) -> bool:
        """Check if we can show a popup of this type given current limits."""
        config = POPUP_CONFIGS[popup_type]
        
        # Count active popups of this type
        type_count = sum(
            1 for p in self.active_popups 
            if p.popup_type == popup_type
        )
        
        # Check type-specific limit
        if type_count >= config.max_active:
            return False
        
        # Check total limit
        if len(self.active_popups) >= 5:  # MAX_POPUPS
            return False
        
        # Check large popup limit (research popups are considered large)
        if popup_type == PopupType.RESEARCH:
            large_count = sum(
                1 for p in self.active_popups 
                if p.popup_type == PopupType.RESEARCH
            )
            if large_count >= 2:  # MAX_LARGE_POPUPS
                return False
        
        return True
    
    def _remove_lowest_priority_popup(self):
        """Remove the lowest priority popup to make room."""
        if not self.active_popups:
            return
        
        # Sort by priority (lowest first) and creation time (oldest first)
        sorted_popups = sorted(
            self.active_popups,
            key=lambda p: (p.config.priority.value, p._created_at)
        )
        
        # Remove the lowest priority/oldest popup
        popup_to_remove = sorted_popups[0]
        popup_to_remove._dismiss()
    
    def _get_orbit_position(
        self,
        popup: BasePopup,
        preferred_position: Optional[int] = None
    ) -> QPointF:
        """Calculate position on orbit around the center."""
        # Get center of parent (should be AI Core area)
        center_x = self.parent_widget.width() // 2
        center_y = self.parent_widget.height() // 2
        
        # Use popup's config orbit radius
        radius = popup.config.orbit_radius
        
        # Determine angle
        if preferred_position is not None and 0 <= preferred_position < 8:
            angle_deg = preferred_position * 45  # 8 positions, 45 degrees each
        else:
            # Find least occupied position
            angle_deg = self._find_least_occupied_orbit_position() * 45
        
        # Convert to radians
        import math
        angle_rad = math.radians(angle_deg)
        
        # Calculate position
        x = center_x + radius * math.cos(angle_rad) - popup.width() // 2
        y = center_y + radius * math.sin(angle_rad) - popup.height() // 2
        
        return QPointF(x, y)
    
    def _find_least_occupied_orbit_position(self) -> int:
        """Find the orbit position with fewest popups."""
        # Simple implementation: count popups in each 45-degree segment
        position_counts = [0] * 8
        
        center_x = self.parent_widget.width() // 2
        center_y = self.parent_widget.height() // 2
        
        for popup in self.active_popups:
            try:
                if not popup.isVisible():
                    continue
            except RuntimeError:
                # Popup has been deleted, skip it
                continue
            
            # Calculate angle of this popup from center
            popup_center_x = popup.x() + popup.width() // 2
            popup_center_y = popup.y() + popup.height() // 2
            
            dx = popup_center_x - center_x
            dy = popup_center_y - center_y
            
            import math
            angle = math.degrees(math.atan2(dy, dx))
            if angle < 0:
                angle += 360
            
            position_index = int((angle // 45) % 8)
            position_counts[position_index] += 1
        
        # Return position with minimum count
        return position_counts.index(min(position_counts))
    
    def _cleanup_finished_popups(self):
        """Remove popups that are no longer visible."""
        visible_popups = []
        for p in self.active_popups:
            try:
                if p.isVisible():
                    visible_popups.append(p)
            except RuntimeError:
                # Popup has been deleted, skip it
                pass
        self.active_popups = visible_popups
        self.popup_destroyed.emit(QObject())  # Dummy signal
    
    def dismiss_all_popups(self):
        """Dismiss all popups immediately."""
        for popup in self.active_popups[:]:  # Copy list
            popup._dismiss()
        self.active_popups.clear()


class PresenceSystem(QObject):
    """System for proactive intelligence surfacing."""
    
    def __init__(self, popup_manager: PopupManager):
        super().__init__()
        self.popup_manager = popup_manager
        self._presence_tmr = QTimer()
        self._presence_tmr.timeout.connect(self._surface_presence)
        self._presence_tmr.start(15000)  # Every 15 seconds
        
        self._last_surface = time.time()
        self._surface_messages = [
            "Memory synchronization complete.",
            "Knowledge graph expanded.",
            "Context retrieval complete.",
            "Predictive model updated.",
            "Cross-referencing memory clusters...",
            "Building execution plan...",
            "Monitoring active systems...",
            "Research protocols standby.",
            "Systems nominal.",
            "All circuits functional.",
        ]
        self._message_index = 0
    
    def _surface_presence(self):
        """Surface a piece of intelligence proactively."""
        # Only surface if user hasn't interacted recently
        # For now, we'll surface periodically
        message = self._surface_messages[self._message_index]
        self._message_index = (self._message_index + 1) % len(self._surface_messages)
        
        self.popup_manager.show_popup(
            message,
            PopupType.MICRO
        )
    
    def set_active(self, active: bool):
        """Enable or disable presence system."""
        if active:
            self._presence_tmr.start()
        else:
            self._presence_tmr.stop()
class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()

        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # NVIDIA
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if r.returncode == 0:
                vals = [float(v.strip()) for v in r.stdout.strip().split("\n") if v.strip()]
                if vals:
                    return sum(vals) / len(vals)
        except Exception:
            pass

        # AMD (Linux)
        if _OS == "Linux":
            try:
                r = subprocess.run(
                    ["rocm-smi", "--showuse", "--csv"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n"):
                        parts = line.split(",")
                        if len(parts) >= 2:
                            try:
                                return float(parts[1].strip().replace("%", ""))
                            except ValueError:
                                pass
            except Exception:
                pass

            # Intel GPU (Linux)
            try:
                r = subprocess.run(
                    ["intel_gpu_top", "-J", "-s", "500"],
                    capture_output=True, text=True, timeout=1
                )
                if r.returncode == 0 and "Render/3D" in r.stdout:
                    import re
                    m = re.search(r'"busy":\s*([\d.]+)', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        # macOS — powermetrics (GPU Engine)
        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["sudo", "-n", "powermetrics", "-n", "1", "-i", "500",
                     "--samplers", "gpu_power"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0 and "GPU" in r.stdout:
                    import re
                    m = re.search(r'GPU\s+Active:\s+([\d.]+)%', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass
            # Fallback: get GPU chip name from system_profiler
            try:
                r = subprocess.run(
                    ["system_profiler", "SPDisplaysDataType"],
                    capture_output=True, text=True, timeout=5
                )
                if r.returncode == 0:
                    import re
                    m = re.search(r'Chipset Model:\s*(.+)', r.stdout)
                    if m:
                        self._gpu_name = m.group(1).strip()
            except Exception:
                pass

        return -1.0

    def _get_temp(self) -> float:
        try:
            temps = psutil.sensors_temperatures()
            candidates = ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                          "cpu-thermal", "zenpower", "it8688"]
            for name in candidates:
                if name in temps:
                    entries = temps[name]
                    if entries:
                        return entries[0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass
        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["osx-cpu-temp"], capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    import re
                    m = re.search(r"([\d.]+)", r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass
            # Fallback: ioreg thermal sensors (Apple Silicon)
            try:
                r = subprocess.run(
                    ["ioreg", "-l"], capture_output=True, text=True, timeout=3
                )
                if r.returncode == 0:
                    import re
                    all_temps = re.findall(r'"temperature"\s*=\s*(\d+)', r.stdout)
                    valid = [float(t) / 1000.0 for t in all_temps
                             if 10 < float(t) < 150000]
                    if valid:
                        return min(valid)
            except Exception:
                pass

        if _OS == "Windows":
            try:
                r = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace root/wmi).CurrentTemperature"],
                    capture_output=True, text=True, timeout=3
                )
                if r.returncode == 0 and r.stdout.strip():
                    raw = float(r.stdout.strip().split("\n")[0])
                    return (raw / 10.0) - 273.15
            except Exception:
                pass

        return -1.0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()


def _get_metrics() -> dict:
    """Single seam for live metrics, allowing unavailable values to stay honest."""
    return _metrics.snapshot()

# Configuration classes for customizable UI elements
class HudConfig:
    """Configuration for HudCanvas visualization elements."""
    def __init__(
        self,
        show_arc_rings: bool = True,
        show_neural_web: bool = True,
        show_context_nodes: bool = True,
        show_energy_streams: bool = True,
        show_pulse_rings: bool = True,
        show_particles: bool = True,
        show_waveform: bool = True,
        ring_count: int = 5,
        neural_web_rings: tuple = ((0.30, 4), (0.45, 6), (0.60, 8)),
        context_node_count: int = 6,
        energy_stream_count: int = 12,  # Reduced for clarity
        pulse_ring_count: int = 2,  # Reduced for clarity
        max_particles: int = 30,  # Reduced for clarity
        # New hierarchical controls
        primary_ring_emphasis: bool = True,  # Make innermost ring more prominent
        context_node_labels: bool = True,  # Toggle labels on context nodes
        waveform_style: str = "classic",  # "classic", "minimal", "off"
        particle_density: str = "medium",  # "low", "medium", "high"
    ):
        self.show_arc_rings = show_arc_rings
        self.show_neural_web = show_neural_web
        self.show_context_nodes = show_context_nodes
        self.show_energy_streams = show_energy_streams
        self.show_pulse_rings = show_pulse_rings
        self.show_particles = show_particles
        self.show_waveform = show_waveform
        self.ring_count = ring_count
        self.neural_web_rings = neural_web_rings
        self.context_node_count = context_node_count
        self.energy_stream_count = energy_stream_count
        self.pulse_ring_count = pulse_ring_count
        self.max_particles = max_particles
        # New hierarchical controls
        self.primary_ring_emphasis = primary_ring_emphasis
        self.context_node_labels = context_node_labels
        self.waveform_style = waveform_style
        self.particle_density = particle_density


class AIActivityConfig:
    """Configuration for AIActivityCanvas visualization elements."""
    def __init__(
        self,
        show_nodes: bool = True,
        show_edges: bool = True,
        show_data_packets: bool = True,
        show_equalizer_bars: bool = True,
        show_scanner: bool = True,
        show_data_streams: bool = True,
        node_count: int = 12,
        edge_distance_threshold: float = 0.32,
        bar_count: int = 32,
        max_data_packets: int = 10,
        max_data_streams: int = 5,
        # New hierarchical controls
        node_size: str = "medium",  # "small", "medium", "large"
        edge_opacity: str = "medium",  # "low", "medium", "high"
        data_packet_velocity: str = "medium",  # "slow", "medium", "fast"
        spectrum_type: str = "bars",  # "bars", "waveform", "off"
    ):
        self.show_nodes = show_nodes
        self.show_edges = show_edges
        self.show_data_packets = show_data_packets
        self.show_equalizer_bars = show_equalizer_bars
        self.show_scanner = show_scanner
        self.show_data_streams = show_data_streams
        self.node_count = node_count
        self.edge_distance_threshold = edge_distance_threshold
        self.bar_count = bar_count
        self.max_data_packets = max_data_packets
        self.max_data_streams = max_data_streams
        # New hierarchical controls
        self.node_size = node_size
        self.edge_opacity = edge_opacity
        self.data_packet_velocity = data_packet_velocity
        self.spectrum_type = spectrum_type


class HudCanvas(QWidget):
    """
    Digital Consciousness v4 — Massive Neural Core.
    Enormous central presence, connected corners,
    spherical lattice, energy pulses, sharp core.
    """

    _NODE_COUNT = 350
    _SHELL_NODES = 100
    _WAVE_COLS = 60
    _WAVE_ROWS = 35
    _RING_COUNT = 5
    _LATTICE_RINGS = 8
    _LATTICE_ARCS = 6

    def __init__(self, face_path: str, parent=None, config: HudConfig = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.config = config or HudConfig()
        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._brightness = 0.6
        self._tgt_bright = 0.6
        self._last_t     = time.time()
        self._blink      = True
        self._blink_tick = 0
        self._rotation_y = 0.0
        self._rotation_x = 0.0
        self._wave_opacity = 1.0
        self._ring_angles  = [random.uniform(0, 360) for _ in range(self._RING_COUNT)]

        # Spherical lattice rotation
        self._lattice_angle = 0.0

        # Energy pulse system
        self._pulses = []
        self._next_pulse_tick = random.randint(40, 100)

        # Neural network nodes: [r, theta, phi, size, brightness, pulse_phase, cluster_id]
        self._nodes = []
        cluster_centers = [
            (0.55, 0.0, 1.2),
            (0.40, 2.0, 0.8),
            (0.70, 3.8, 2.0),
            (0.30, 5.5, 1.5),
            (0.50, 1.0, 2.5),
            (0.60, 4.5, 0.5),
            (0.45, 3.0, 1.8),
            (0.35, 0.5, 0.5),
        ]
        cluster_sizes = [70, 55, 50, 35, 60, 45, 25, 20]

        for ci, (cr, ct, cphi) in enumerate(cluster_centers):
            for _ in range(cluster_sizes[ci]):
                spread = 0.20 + ci * 0.02
                r = max(0.05, min(1.0, cr + random.gauss(0, spread)))
                theta = ct + random.gauss(0, 0.5)
                phi = max(0.3, min(math.pi - 0.3, cphi + random.gauss(0, 0.35)))
                sz = random.uniform(0.6, 2.4)
                br = random.uniform(0.3, 1.0)
                phase = random.uniform(0, 2 * math.pi)
                self._nodes.append([r, theta, phi, sz, br, phase, ci])

        while len(self._nodes) < self._NODE_COUNT:
            r = random.uniform(0.0, 1.0) ** 0.45
            theta = random.uniform(0, 2 * math.pi)
            phi = math.acos(random.uniform(-1, 1))
            sz = random.uniform(0.3, 1.2)
            br = random.uniform(0.08, 0.30)
            phase = random.uniform(0, 2 * math.pi)
            self._nodes.append([r, theta, phi, sz, br, phase, -1])

        # Pre-compute connections — denser now
        self._connections = []
        sample = self._nodes[:200]
        for i in range(len(sample)):
            for j in range(i + 1, len(sample)):
                ni, nj = sample[i], sample[j]
                same_cluster = (ni[6] == nj[6] and ni[6] >= 0)
                xi = ni[0] * math.sin(ni[2]) * math.cos(ni[1])
                yi = ni[0] * math.sin(ni[2]) * math.sin(ni[1])
                zi = ni[0] * math.cos(ni[2])
                xj = nj[0] * math.sin(nj[2]) * math.cos(nj[1])
                yj = nj[0] * math.sin(nj[2]) * math.sin(nj[1])
                zj = nj[0] * math.cos(nj[2])
                dist = math.sqrt((xi-xj)**2 + (yi-yj)**2 + (zi-zj)**2)
                threshold = 0.32 if same_cluster else 0.18
                if dist < threshold:
                    self._connections.append((i, j, dist, same_cluster))

        # Shell surface nodes
        self._shell = []
        for _ in range(self._SHELL_NODES):
            theta = random.uniform(0, 2 * math.pi)
            phi = math.acos(random.uniform(-1, 1))
            sz = random.uniform(0.2, 0.7)
            phase = random.uniform(0, 2 * math.pi)
            self._shell.append([theta, phi, sz, phase])

        # Orbital ring configs: [radius_frac, tilt_x, tilt_z, width, speed, style]
        self._orbital_rings = [
            (1.08, 0.3, 0.0, 1.6, 0.15, 0),
            (0.92, -0.2, 0.5, 0.9, -0.22, 1),
            (1.18, 0.1, -0.4, 0.7, 0.10, 2),
            (0.80, 0.6, 0.2, 1.2, -0.18, 0),
            (1.28, -0.4, 0.3, 0.5, 0.08, 3),
        ]

        # Massive secondary rings — giant, barely visible
        self._mega_rings = [
            (1.6, 0.0008, 0.5, 1.0),
            (2.2, -0.0003, 0.35, 0.8),
            (1.35, 0.0015, 0.45, 0.6),
            (2.8, 0.00015, 0.2, 0.5),
            (3.5, -0.0001, 0.12, 0.3),
        ]

        # Ambient depth particles
        self._depth_pts = []
        for _ in range(120):
            x = random.uniform(-0.6, 0.6)
            y = random.uniform(-0.5, 0.5)
            sz = random.uniform(0.2, 0.6)
            phase = random.uniform(0, 2 * math.pi)
            self._depth_pts.append([x, y, sz, phase])

        # Wave field
        self._wave_pts = []
        for row in range(self._WAVE_ROWS):
            for col in range(self._WAVE_COLS):
                nx = col / max(self._WAVE_COLS - 1, 1)
                ny = row / max(self._WAVE_ROWS - 1, 1)
                self._wave_pts.append([nx, ny, random.uniform(0, 2 * math.pi)])

        self._face_px = None
        self._load_face(face_path)

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self.set_graphics_quality(get_graphics_quality())

    def set_graphics_quality(self, quality: str):
        """Apply a graphics profile without rebuilding the HUD."""
        value = _normalize_graphics_quality(quality)
        profile = GRAPHICS_PROFILES[value]
        self._graphics_quality = value
        self._render_stride = int(profile["render_stride"])
        self._wave_stride = int(profile["wave_stride"])
        self._noise_count = int(profile["noise_count"])
        self._scanline_step = int(profile["scanline_step"])
        self._antialias = bool(profile["antialias"])
        self._tmr.setInterval(int(profile["frame_ms"]))
        if not self._tmr.isActive():
            self._tmr.start()
        self.update()

    def _load_face(self, path: str):
        try:
            from PIL import Image, ImageDraw
            import io
            img = Image.open(path).convert("RGBA")
            sz  = min(img.size)
            img = img.resize((sz, sz), Image.LANCZOS)
            mk  = Image.new("L", (sz, sz), 0)
            ImageDraw.Draw(mk).ellipse((2, 2, sz - 2, sz - 2), fill=255)
            img.putalpha(mk)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap(); px.loadFromData(buf.getvalue())
            self._face_px = px
        except Exception:
            self._face_px = None

    def _step(self):
        self._tick += 1
        now = time.time()
        is_active = self.speaking or self.state in ("THINKING", "PROCESSING")

        if now - self._last_t > (0.10 if is_active else 0.45):
            if self.speaking:
                _speak_breath = 0.5 + 0.5 * math.sin(self._tick * 0.08)
                self._tgt_scale = random.uniform(1.03, 1.08) + _speak_breath * 0.04
                self._tgt_bright = random.uniform(0.85, 1.0)
            elif self.muted:
                self._tgt_scale = random.uniform(0.99, 1.01)
                self._tgt_bright = random.uniform(0.1, 0.2)
            elif is_active:
                self._tgt_scale = random.uniform(1.01, 1.05)
                self._tgt_bright = random.uniform(0.6, 0.85)
            else:
                breath = 0.5 + 0.5 * math.sin(self._tick * 0.035)
                self._tgt_scale = 1.0 + breath * 0.05
                self._tgt_bright = 0.25 + breath * 0.55
            self._last_t = now

        sp = 0.30 if is_active else 0.10
        self._scale     += (self._tgt_scale  - self._scale)     * sp
        self._brightness += (self._tgt_bright - self._brightness) * sp

        # Wave fade
        if self.speaking:
            wt = 0.0
        elif is_active:
            wt = 0.15
        else:
            wt = 1.0
        self._wave_opacity += (wt - self._wave_opacity) * 0.06

        # Rotation
        rot_speed = 0.008 if is_active else (0.002 if self.muted else 0.004)
        self._rotation_y += rot_speed
        self._rotation_x  = 0.25 + 0.05 * math.sin(self._tick * 0.003)
        self._lattice_angle += rot_speed * 0.3

        # Orbital rings
        for i, (_, _, _, _, spd, _) in enumerate(self._orbital_rings):
            mult = 2.5 if is_active else 1.0
            self._ring_angles[i] = (self._ring_angles[i] + spd * mult) % 360

        # Energy pulses
        self._next_pulse_tick -= 1
        pulse_interval = 15 if is_active else 60
        if self._next_pulse_tick <= 0 and len(self._pulses) < 10:
            if self._connections:
                conn = random.choice(self._connections)
                spd = random.uniform(0.008, 0.018)
                cidx = random.choice([0, 1, 2])
                self._pulses.append([conn[0], conn[1], 0.0, spd, cidx])
            self._next_pulse_tick = random.randint(pulse_interval // 2, pulse_interval)

        alive = []
        for pulse in self._pulses:
            pulse[2] += pulse[3]
            if pulse[2] < 1.0:
                alive.append(pulse)
        self._pulses = alive

        # Node animation
        for nd in self._nodes[::self._render_stride]:
            nd[5] += 0.04
            drift = random.gauss(0, 0.0008) * (3.0 if is_active else 1.0)
            nd[0] = max(0.05, min(1.0, nd[0] + drift))
            tb = random.uniform(0.5, 1.0) if is_active else (
                random.uniform(0.05, 0.2) if self.muted else random.uniform(0.25, 0.65))
            nd[4] += (tb - nd[4]) * 0.06

        self._blink_tick += 1
        if self._blink_tick >= 32:
            self._blink = not self._blink
            self._blink_tick = 0
        self.update()

    def _proj(self, r, theta, phi, sr, cx, cy):
        x = r * math.sin(phi) * math.cos(theta)
        y = r * math.sin(phi) * math.sin(theta)
        z = r * math.cos(phi)
        crx = math.cos(self._rotation_x); srx = math.sin(self._rotation_x)
        cry = math.cos(self._rotation_y); sry = math.sin(self._rotation_y)
        xr = x * cry - z * sry
        zr = x * sry + z * cry
        yr = y * crx - zr * srx
        z2 = y * srx + zr * crx
        return cx + xr * sr, cy - yr * sr, z2, max(0.0, min(1.0, (z2 + 1.0) / 2.0))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, self._antialias)
        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)
        is_active = self.speaking or self.state in ("THINKING", "PROCESSING")

        # ═══ LAYER 1: Deep space background ════════════════════════════════
        _is_light_theme = QColor(C.BG).lightness() > 150
        _bg = QRadialGradient(QPointF(cx, cy + H * 0.06), max(W, H) * 0.9)
        if _is_light_theme:
            _bg.setColorAt(0.0, qcol(C.CARD))
            _bg.setColorAt(0.45, qcol(C.PANEL))
            _bg.setColorAt(1.0, qcol(C.DARK))
        else:
            _bg.setColorAt(0.0, qcol(C.DARK))
            _bg.setColorAt(0.45, qcol(C.BG))
            _bg.setColorAt(1.0, qcol(C.PANEL))
        p.fillRect(self.rect(), _bg)

        # ═══ LAYER 2: Massive secondary ring system ═══════════════════════
        mega_mult = 1.5 if is_active else 1.0
        for r_mult, rot_spd, opac, w in self._mega_rings[::self._render_stride]:
            r = fw * r_mult * 0.28
            angle = self._tick * rot_spd * mega_mult
            a = max(0, int(self._brightness * 6 * opac))
            if a < 1:
                continue
            ox = math.cos(angle * 0.5) * fw * 0.01
            oy = math.sin(angle * 0.3) * fw * 0.008

            # Draw as full ellipse for scale
            p.setPen(QPen(qcol(C.PRI,a), w))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx + ox, cy + oy), r, r * 0.32)

            # Tick marks on the largest ring
            if r_mult > 2.5:
                p.setPen(QPen(qcol(C.PRI,max(0, a - 4)), 0.3))
                for deg in range(0, 360, 10 * self._render_stride):
                    rad = math.radians(deg + angle * 50)
                    ex = cx + ox + math.cos(rad) * r
                    ey = cy + oy + math.sin(rad) * r * 0.32
                    ix = cx + ox + math.cos(rad) * (r - 6)
                    iy = cy + oy + math.sin(rad) * (r - 6) * 0.32
                    p.drawLine(QPointF(ex, ey), QPointF(ix, iy))

        # Radial grid lines
        for deg in range(0, 360, 30 * self._render_stride):
            rad = math.radians(deg + self._tick * 0.012)
            a = max(0, int(self._brightness * 5))
            if a < 1:
                continue
            p.setPen(QPen(qcol(C.PRI,a), 0.3))
            p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(rad) * fw * 0.58, cy + math.sin(rad) * fw * 0.58))

        # ═══ LAYER 3: Ambient depth particles ═════════════════════════════
        for di in range(0, len(self._depth_pts), self._render_stride):
            dx, dy, sz, phase = self._depth_pts[di]
            parallax = 0.5 + (di % 3) * 0.25
            dx2 = dx + math.sin(self._tick * 0.001 * parallax + phase) * 0.025
            dy2 = dy + math.cos(self._tick * 0.0008 * parallax + phase) * 0.02
            sx = cx + dx2 * fw
            sy = cy + dy2 * fw
            pulse = 0.3 + 0.7 * math.sin(self._tick * 0.012 + phase)
            a = max(0, int(self._brightness * 25 * pulse))
            if a < 2:
                continue
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.PRI,a)))
            p.drawEllipse(QPointF(sx, sy), sz, sz)

        # ═══ LAYER 4: Wave field ══════════════════════════════════════════
        wave_top = cy - H * 0.05
        wave_bot = cy + H * 0.45
        wave_l   = cx - W * 0.52
        wave_w   = W * 1.04
        wave_h   = wave_bot - wave_top
        t_wave   = self._tick * 0.015

        for nx, ny, phase in self._wave_pts[::self._wave_stride]:
            wx = wave_l + nx * wave_w
            wy_base = wave_top + ny * wave_h
            wave_y = math.sin(nx * 6.0 + t_wave + phase) * 8.0
            wave_y += math.sin(ny * 4.0 - t_wave * 0.7 + phase * 0.5) * 5.0
            wy = wy_base + wave_y
            dist = math.hypot(wx - cx, wy - cy) / (fw * 0.7)
            a = max(0, int(self._brightness * 40 * (1.0 - dist * 0.6) * self._wave_opacity))
            if a < 2:
                continue
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.PRI,a)))
            p.drawEllipse(QPointF(wx, wy), 0.6, 0.6)

        # ═══ LAYER 5: Hexagonal frame — STRONG ════════════════════════════
        sphere_r = fw * 0.52 * self._scale
        hex_r = sphere_r * 1.10
        hex_a = max(0, int(self._brightness * 80))
        if hex_a > 1:
            hex_pts = []
            for hi in range(6):
                angle = math.radians(60 * hi - 30 + self._tick * 0.02)
                hx = cx + math.cos(angle) * hex_r
                hy = cy + math.sin(angle) * hex_r * 0.85
                hex_pts.append(QPointF(hx, hy))

            # Main hex lines
            hex_col = qcol(C.PRI,hex_a)
            p.setPen(QPen(hex_col, 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            hex_path = QPainterPath()
            hex_path.moveTo(hex_pts[0])
            for hp in hex_pts[1:]:
                hex_path.lineTo(hp)
            hex_path.lineTo(hex_pts[0])
            p.drawPath(hex_path)

            # Segmented inner hex (rotated 30 deg)
            inner_hex_path = QPainterPath()
            inner_pts = []
            for hi in range(6):
                angle = math.radians(60 * hi + self._tick * 0.02)
                hx = cx + math.cos(angle) * hex_r * 0.94
                hy = cy + math.sin(angle) * hex_r * 0.94 * 0.85
                inner_pts.append(QPointF(hx, hy))
            for si in range(0, len(inner_pts), 2):
                seg = [inner_pts[si], inner_pts[(si + 1) % 6]]
                p.setPen(QPen(qcol(C.PRI,max(0, hex_a - 15)), 0.6))
                p.drawLine(seg[0], seg[1])

            # Tick marks along each hex edge
            p.setPen(QPen(qcol(C.PRI,max(0, hex_a - 10)), 0.5))
            for hi in range(6):
                p1 = hex_pts[hi]
                p2 = hex_pts[(hi + 1) % 6]
                for t in range(0, 11):
                    frac = t / 10.0
                    mx = p1.x() + (p2.x() - p1.x()) * frac
                    my = p1.y() + (p2.y() - p1.y()) * frac
                    dx_n = p2.y() - p1.y()
                    dy_n = -(p2.x() - p1.x())
                    dlen = math.hypot(dx_n, dy_n)
                    if dlen > 0:
                        dx_n /= dlen; dy_n /= dlen
                    tick_len = 6 if t % 5 == 0 else 3
                    p.drawLine(QPointF(mx, my), QPointF(mx + dx_n * tick_len, my + dy_n * tick_len))

            # Vertex dots — bright
            for hp in hex_pts:
                # Vertex glow
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(qcol(C.ENERGY,max(0, int(hex_a * 0.3)))))
                p.drawEllipse(hp, 8, 8)
                # Vertex core
                p.setBrush(QBrush(qcol(C.ENERGY,min(255, hex_a + 40))))
                p.drawEllipse(hp, 2.5, 2.5)

            # Coordinate labels at two vertices
            p.setFont(QFont("Courier New", 6))
            p.setPen(QPen(qcol(C.PRI,max(0, hex_a - 20)), 0.8))
            p.drawText(QPointF(hex_pts[0].x() + 10, hex_pts[0].y() - 4), "037.4\u00b0")
            p.drawText(QPointF(hex_pts[3].x() - 55, hex_pts[3].y() + 14), "217.8\u00b0")

        # ═══ LAYER 5b: Targeting reticle ═══════════════════════════════════
        ret_r = sphere_r * 1.18
        ret_a = max(0, int(self._brightness * 120))
        if ret_a > 1:
            # Crosshairs
            p.setPen(QPen(qcol(C.PRI,ret_a), 0.4))
            ch_gap = sphere_r * 0.6
            ch_len = ret_r
            p.drawLine(QPointF(cx - ch_len, cy), QPointF(cx - ch_gap, cy))
            p.drawLine(QPointF(cx + ch_gap, cy), QPointF(cx + ch_len, cy))
            p.drawLine(QPointF(cx, cy - ch_len), QPointF(cx, cy - ch_gap))
            p.drawLine(QPointF(cx, cy + ch_gap), QPointF(cx, cy + ch_len))

            # Degree markers around perimeter
            p.setFont(QFont("Courier New", 5))
            for deg in range(0, 360, 45):
                rad = math.radians(deg)
                mx = cx + math.cos(rad) * ret_r
                my = cy + math.sin(rad) * ret_r * 0.85
                # Tick
                ix = cx + math.cos(rad) * (ret_r - 5)
                iy = cy + math.sin(rad) * (ret_r - 5) * 0.85
                p.setPen(QPen(qcol(C.PRI,ret_a), 0.4))
                p.drawLine(QPointF(ix, iy), QPointF(mx, my))
                # Label
                if deg % 90 == 0:
                    labels = {0: "000", 90: "090", 180: "180", 270: "270"}
                    lx = cx + math.cos(rad) * (ret_r + 8)
                    ly = cy + math.sin(rad) * (ret_r + 8) * 0.85
                    p.setPen(QPen(qcol(C.PRI,max(0, ret_a - 3)), 0.6))
                    p.drawText(QPointF(lx - 10, ly + 3), labels.get(deg, str(deg)))

        # ═══ LAYER 6: Spherical lattice — signature element ════════════════
        lat_r = sphere_r * 1.02
        lat_a = max(0, int(self._brightness * 40))
        if lat_a > 1:
            # Latitude rings
            for li in range(1, self._LATTICE_RINGS):
                frac = li / self._LATTICE_RINGS
                phi = frac * math.pi
                ring_r = math.sin(phi) * lat_r
                y_off = math.cos(phi) * lat_r * 0.85

                pts = []
                for deg in range(0, 361, 4):
                    rad = math.radians(deg + self._tick * 0.03 + li * 15)
                    x = math.cos(rad) * ring_r
                    y = math.sin(rad) * ring_r * 0.85 - y_off * 0.85
                    # Apply rotation
                    crx = math.cos(self._rotation_x * 0.8)
                    srx = math.sin(self._rotation_x * 0.8)
                    yt = y * crx
                    zt = y * srx
                    # Perspective fade
                    fade = max(0.3, (zt / lat_r + 1) / 2)
                    a = int(lat_a * fade)
                    if a > 1:
                        pts.append((cx + x, cy + yt, a))

                if len(pts) > 2:
                    for pi in range(len(pts) - 1):
                        x1, y1, a1 = pts[pi]
                        x2, y2, a2 = pts[pi + 1]
                        a = (a1 + a2) // 2
                        p.setPen(QPen(qcol(C.PRI,a), 0.3))
                        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

            # Longitude arcs
            for li in range(self._LATTICE_ARCS):
                angle = li * (180 / self._LATTICE_ARCS) + self._tick * 0.015
                rad = math.radians(angle)
                pts = []
                for seg in range(0, 181, 3):
                    phi = math.radians(seg)
                    x = math.sin(phi) * math.cos(rad) * lat_r
                    y = math.sin(phi) * math.sin(rad) * lat_r * 0.85
                    z = math.cos(phi) * lat_r
                    crx = math.cos(self._rotation_x * 0.8)
                    srx = math.sin(self._rotation_x * 0.8)
                    yr = y * crx - z * srx
                    zr = y * srx + z * crx
                    depth = (zr / lat_r + 1) / 2
                    a = int(lat_a * max(0.2, depth))
                    if a > 1:
                        pts.append((cx + x, cy - yr, a))

                if len(pts) > 2:
                    for pi in range(len(pts) - 1):
                        x1, y1, a1 = pts[pi]
                        x2, y2, a2 = pts[pi + 1]
                        a = (a1 + a2) // 2
                        p.setPen(QPen(qcol(C.PRI,a), 0.25))
                        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # ═══ LAYER 7: Orbital rings — varied styles ═══════════════════════
        cos_rx = math.cos(self._rotation_x)
        sin_rx = math.sin(self._rotation_x)
        cos_ry = math.cos(self._rotation_y)
        sin_ry = math.sin(self._rotation_y)

        for ri, (r_frac, tilt_x, tilt_z, w, _, style) in enumerate(self._orbital_rings):
            ring_r = sphere_r * r_frac
            r_angle = math.radians(self._ring_angles[ri])
            r_a = max(0, int(self._brightness * 50 * (1.2 if is_active else 0.5)))
            if r_a < 2:
                continue

            col = qcol(C.ENERGY,r_a)
            p.setBrush(Qt.BrushStyle.NoBrush)

            path_pts = []
            for deg in range(0, 361, 3):
                rad = math.radians(deg + r_angle)
                x = math.cos(rad) * ring_r
                y = math.sin(rad) * ring_r * math.cos(tilt_x)
                z = math.sin(rad) * ring_r * math.sin(tilt_z)
                xr = x * cos_ry - z * sin_ry
                zr = x * sin_ry + z * cos_ry
                yr = y * cos_rx - zr * sin_rx
                path_pts.append(QPointF(cx + xr, cy - yr))

            if len(path_pts) > 1:
                if style == 1:
                    pen = QPen(col, w)
                    pen.setDashPattern([8, 6])
                    p.setPen(pen)
                elif style == 2:
                    seg_len = 30
                    p.setPen(QPen(col, w))
                    for si in range(0, len(path_pts) - seg_len, seg_len * 2):
                        seg = path_pts[si:si + seg_len + 1]
                        if len(seg) > 1:
                            seg_path = QPainterPath()
                            seg_path.moveTo(seg[0])
                            for pt in seg[1:]:
                                seg_path.lineTo(pt)
                            p.drawPath(seg_path)
                    continue
                elif style == 3:
                    pen = QPen(col, w * 0.6)
                    pen.setDashPattern([2, 8])
                    p.setPen(pen)
                else:
                    p.setPen(QPen(col, w))

                path = QPainterPath()
                path.moveTo(path_pts[0])
                for pt in path_pts[1:]:
                    path.lineTo(pt)
                p.drawPath(path)

        # ═══ LAYER 8: Connection lines ════════════════════════════════════
        proj_nodes = {}
        for idx in range(0, len(self._nodes), self._render_stride):
            nd = self._nodes[idx]
            sx, sy, z2, depth = self._proj(nd[0], nd[1], nd[2], sphere_r, cx, cy)
            if z2 > -0.3:
                proj_nodes[idx] = (sx, sy, z2, depth)

        for ci, cj, dist, same_cluster in self._connections[::self._render_stride]:
            if ci in proj_nodes and cj in proj_nodes:
                x1, y1, z1, d1 = proj_nodes[ci]
                x2, y2, z2, d2 = proj_nodes[cj]
                avg_d = (d1 + d2) / 2
                threshold = 0.32 if same_cluster else 0.18
                strength = 1.0 - dist / threshold
                if same_cluster:
                    strength *= 1.5
                la = max(0, int(self._brightness * avg_d * strength * 50))
                if la > 2:
                    lc = qcol(C.PRI,la) if same_cluster else qcol(C.PRI,la)
                    p.setPen(QPen(lc, 0.5))
                    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # ═══ LAYER 9: Energy pulses ═══════════════════════════════════════
        pulse_colors = [qcol(C.ENERGY,255), qcol(C.WHITE,255), qcol(C.GREEN,255)]
        for si, ei, t, spd, cidx in self._pulses:
            if si in proj_nodes and ei in proj_nodes:
                x1, y1, z1, d1 = proj_nodes[si]
                x2, y2, z2, d2 = proj_nodes[ei]
                px = x1 + (x2 - x1) * t
                py = y1 + (y2 - y1) * t
                depth = d1 + (d2 - d1) * t
                pa = max(0, min(255, int(220 * depth * (1.0 - abs(t - 0.5) * 1.5))))
                if pa > 5:
                    col = pulse_colors[cidx % len(pulse_colors)]
                    p.setPen(Qt.PenStyle.NoPen)
                    gc = QColor(col); gc.setAlpha(int(pa * 0.2))
                    p.setBrush(QBrush(gc))
                    p.drawEllipse(QPointF(px, py), 14, 14)
                    cc = QColor(col); cc.setAlpha(pa)
                    p.setBrush(QBrush(cc))
                    p.drawEllipse(QPointF(px, py), 3.5, 3.5)

        # ═══ LAYER 10: Neural nodes ═══════════════════════════════════════
        sorted_n = sorted(proj_nodes.items(), key=lambda x: x[1][2])
        # Cluster colors derived from theme
        _pri = QColor(C.PRI)
        _pr = _pri.red(); _pg = _pri.green(); _pb = _pri.blue()
        cluster_hues = [
            (_pr, _pg, _pb),
            (min(255, _pr + 30), min(255, _pg + 20), min(255, _pb + 10)),
            (max(0, _pr - 20), max(0, _pg - 10), min(255, _pb + 30)),
            (max(0, _pr - 40), max(0, _pg - 20), max(0, _pb - 10)),
            (min(255, _pr + 50), min(255, _pg + 40), min(255, _pb + 20)),
            (_pr, min(255, _pg + 30), min(255, _pb + 20)),
            (max(0, _pr - 10), min(255, _pg + 10), min(255, _pb + 40)),
            (min(255, _pr + 20), min(255, _pg + 40), _pb),
        ]

        for idx, (sx, sy, z2, depth) in sorted_n:
            nd = self._nodes[idx]
            r, theta, phi, sz, br, phase, cid = nd
            pulse = 0.6 + 0.4 * math.sin(self._tick * 0.04 + phase)
            draw_sz = sz * (0.3 + 0.7 * depth) * pulse
            bright = self._brightness * depth * br * pulse * 2.0
            if z2 < -0.3:
                bright *= 0.1
            a = max(0, min(255, int(bright * 255)))
            if a < 3:
                continue

            if cid >= 0:
                cr, cg, cb = cluster_hues[cid % len(cluster_hues)]
            else:
                _f = QColor(C.PRI); cr, cg, cb = _f.red(), _f.green(), _f.blue()

            ga = max(0, int(a * 0.18))
            if ga > 2:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(QColor(cr, cg, cb, ga)))
                p.drawEllipse(QPointF(sx, sy), draw_sz * 5, draw_sz * 5)

            ia = max(0, int(a * 0.45))
            if ia > 2:
                p.setBrush(QBrush(QColor(cr, cg, cb, ia)))
                p.drawEllipse(QPointF(sx, sy), draw_sz * 2.0, draw_sz * 2.0)

            p.setBrush(QBrush(QColor(min(255, cr+60), min(255, cg+40), min(255, cb+20), a)))
            p.drawEllipse(QPointF(sx, sy), draw_sz, draw_sz)

        # ═══ LAYER 11: Shell particles ═════════════════════════════════════
        for theta, phi, sz, phase in self._shell[::self._render_stride]:
            sx, sy, z2, depth = self._proj(1.0, theta, phi, sphere_r, cx, cy)
            if z2 < 0.05:
                continue
            pulse = 0.5 + 0.5 * math.sin(self._tick * 0.05 + phase)
            a = max(0, min(255, int(self._brightness * depth * pulse * 200)))
            if a < 4:
                continue
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.ENERGY,a)))
            p.drawEllipse(QPointF(sx, sy), sz * pulse, sz * pulse)

        # ═══ LAYER 11b: Micro-details — telemetry labels ══════════════════
        detail_a = max(0, int(self._brightness * 18))
        if detail_a > 2:
            p.setFont(QFont("Courier New", 5))
            # Scattered labels around the orb
            details = [
                (0.58, 0.25, "NODE-{:02d}  {:.0f}%".format(
                    (self._tick // 200) % 64, 85 + 10 * math.sin(self._tick * 0.01))),
                (-0.55, 0.35, "MEM: {:.0f}KB".format(
                    128 + 32 * math.sin(self._tick * 0.008))),
                (0.45, -0.48, "LAT {:.1f}°".format(
                    37.4 + 0.3 * math.sin(self._tick * 0.005))),
                (-0.42, -0.52, "SYS {:.1f}%".format(
                    94 + 3 * math.sin(self._tick * 0.012))),
                (0.62, -0.15, "CYCLE {}".format(
                    (self._tick // 100) % 999)),
                (-0.60, -0.12, "DEPTH {:.02f}".format(
                    0.85 + 0.1 * math.sin(self._tick * 0.006))),
                (0.20, 0.60, "ACTIVE".format()),
                (-0.25, 0.58, "{:.1f}ms".format(
                    2.4 + 0.8 * math.sin(self._tick * 0.015))),
            ]
            for dx, dy, label in details:
                lx = cx + dx * sphere_r
                ly = cy + dy * sphere_r
                flicker = 0.7 + 0.3 * math.sin(self._tick * 0.02 + dx * 10)
                a = int(detail_a * flicker)
                p.setPen(QPen(qcol(C.PRI,a), 0.8))
                p.drawText(QPointF(lx, ly), label)

        # ═══ LAYER 12: Core energy — SHARP ════════════════════════════════
        core_base = sphere_r * 0.065
        ca = max(0, min(255, int(self._brightness * 255)))

        # Outer diffuse glow
        for i in range(18, 0, -1):
            frc = i / 18
            r = core_base * frc * 7.0
            a = int(ca * 0.05 * frc)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.PRI,a)))
            p.drawEllipse(QPointF(cx, cy), r, r)

        # Mid glow — brighter
        for i in range(10, 0, -1):
            frc = i / 10
            r = core_base * frc * 4.0
            a = int(ca * 0.28 * frc)
            p.setBrush(QBrush(qcol(C.ENERGY,a)))
            p.drawEllipse(QPointF(cx, cy), r, r)

        # SHARP thin rings — creates premium look
        for sr in [core_base * 2.0, core_base * 3.0, core_base * 4.5]:
            ring_a = max(0, int(ca * 0.25 * (1.0 - sr / (core_base * 5.0))))
            if ring_a > 2:
                p.setPen(QPen(qcol(C.ENERGY,ring_a), 0.6))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(cx, cy), sr, sr)

        # Inner core — intense
        for i in range(5, 0, -1):
            frc = i / 5
            r = core_base * frc * 2.0
            a = int(ca * 0.55 * frc)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.ENERGY,a)))
            p.drawEllipse(QPointF(cx, cy), r, r)

        # White hot center with HARD edge
        p.setBrush(QBrush(qcol(C.ENERGY,min(255, int(ca * 0.95)))))
        p.drawEllipse(QPointF(cx, cy), core_base * 0.35, core_base * 0.35)

        # Sharp highlight on center
        p.setBrush(QBrush(qcol(C.ENERGY,min(255, int(ca * 0.6)))))
        p.drawEllipse(QPointF(cx - core_base * 0.1, cy - core_base * 0.1), core_base * 0.15, core_base * 0.15)

        # ═══ LAYER 13: Corner anchors — CONNECTED ═════════════════════════
        bl = 50
        bc = qcol(C.PRI, 180)
        m = fw * 0.47
        hl, hr = cx - m, cx + m
        ht, hb = cy - m, cy + m

        # Thin telemetry lines connecting corners to orb
        telemetry_a = max(0, int(self._brightness * 18))
        if telemetry_a > 1:
            p.setPen(QPen(qcol(C.PRI,telemetry_a), 0.4))
            # Horizontal measurement lines
            p.drawLine(QPointF(hl + bl, ht), QPointF(cx - sphere_r * 0.9, ht))
            p.drawLine(QPointF(hr - bl, ht), QPointF(cx + sphere_r * 0.9, ht))
            p.drawLine(QPointF(hl + bl, hb), QPointF(cx - sphere_r * 0.9, hb))
            p.drawLine(QPointF(hr - bl, hb), QPointF(cx + sphere_r * 0.9, hb))
            # Vertical
            p.drawLine(QPointF(hl, ht + bl), QPointF(hl, cy - sphere_r * 0.7))
            p.drawLine(QPointF(hl, hb - bl), QPointF(hl, cy + sphere_r * 0.7))
            p.drawLine(QPointF(hr, ht + bl), QPointF(hr, cy - sphere_r * 0.7))
            p.drawLine(QPointF(hr, hb - bl), QPointF(hr, cy + sphere_r * 0.7))

            # Measurement ticks along telemetry lines
            p.setPen(QPen(qcol(C.PRI,max(0, telemetry_a - 5)), 0.3))
            for x in range(int(hl + bl + 10), int(cx - sphere_r * 0.9), 12):
                p.drawLine(QPointF(x, ht - 2), QPointF(x, ht + 2))
            for x in range(int(cx + sphere_r * 0.9), int(hr - bl), 12):
                p.drawLine(QPointF(x, ht - 2), QPointF(x, ht + 2))

        for bx, by, dx, dy in [(hl,ht,1,1),(hr,ht,-1,1),(hl,hb,1,-1),(hr,hb,-1,-1)]:
            # Ambient glow
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.PRI,max(0, int(self._brightness * 15)))))
            p.drawEllipse(QPointF(bx + dx * bl * 0.35, by + dy * bl * 0.35), bl * 0.5, bl * 0.5)

            # Main bracket lines — with subtle idle flicker
            _flicker = 0.85 + 0.15 * math.sin(self._tick * 0.1 + bx * 0.01 + by * 0.01)
            _bc_flick = QColor(bc); _bc_flick.setAlpha(int(bc.alpha() * _flicker))
            p.setPen(QPen(_bc_flick, 2.0))
            p.drawLine(QPointF(bx, by), QPointF(bx + dx * bl, by))
            p.drawLine(QPointF(bx, by), QPointF(bx, by + dy * bl))

            # Inner accent
            p.setPen(QPen(qcol(C.ENERGY, 80), 0.8))
            p.drawLine(QPointF(bx + dx * 8, by + dy * 8), QPointF(bx + dx * 30, by + dy * 8))
            p.drawLine(QPointF(bx + dx * 8, by + dy * 8), QPointF(bx + dx * 8, by + dy * 30))

            # Corner dot — pulsing
            _corner_pulse = 0.5 + 0.5 * math.sin(self._tick * 0.08 + bx * 0.01 + by * 0.01)
            _corner_a = int(120 + 80 * _corner_pulse)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.ENERGY,_corner_a)))
            p.drawEllipse(QPointF(bx + dx * 4, by + dy * 4), 2.5 + _corner_pulse, 2.5 + _corner_pulse)

            # Scan sweep line (rotating accent)
            _scan_angle = math.radians((self._tick * 3 + bx + by) % 360)
            _scan_len = bl * 0.5
            _scan_a = int(40 * _corner_pulse)
            if _scan_a > 3:
                p.setPen(QPen(qcol(C.ENERGY,_scan_a), 0.5))
                p.drawLine(
                    QPointF(bx, by),
                    QPointF(bx + dx * abs(math.cos(_scan_angle)) * _scan_len,
                            by + dy * abs(math.sin(_scan_angle)) * _scan_len))

            # Tick marks along bracket
            p.setPen(QPen(qcol(C.PRI, 50), 0.4))
            for ti in range(14, bl, 7):
                p.drawLine(QPointF(bx + dx * ti, by), QPointF(bx + dx * ti, by + dy * 3))
                p.drawLine(QPointF(bx, by + dy * ti), QPointF(bx + dx * 3, by + dy * ti))

            # Coordinate readout inside corner
            p.setFont(QFont("Courier New", 5))
            _cx_v = int(bx + (cx - bx) * 0.08)
            _cy_v = int(by + (cy - by) * 0.08)
            _co_a = max(0, int(self._brightness * 80))
            if _co_a > 2:
                p.setPen(QPen(qcol(C.PRI,_co_a), 0.6))
                p.drawText(QPointF(bx + dx * 52, by + dy * 4), "X:{:04d}".format(abs(int(bx)) % 9999))
                p.drawText(QPointF(bx + dx * 52, by + dy * 12), "Y:{:04d}".format(abs(int(by)) % 9999))

        # ═══ LAYER 14: Status text ════════════════════════════════════════
        sy_t = cy + fw * 0.48
        if self.muted:
            txt, col = "\u2298  MUTED",          qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "\u25cf  SPEAKING",        qcol(C.GREEN)
        elif self.state == "THINKING":
            sym = "\u25c8" if self._blink else "\u25c7"
            txt, col = f"{sym}  PROCESSING QUERY", qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "\u25b7" if self._blink else "\u25b6"
            txt, col = f"{sym}  EXECUTING",   qcol(C.ACC)
        elif self.state == "LISTENING":
            sym = "\u25cf" if self._blink else "\u25cb"
            txt, col = f"{sym}  LISTENING",   qcol(C.ENERGY)
        else:
            sym = "\u25cf" if self._blink else "\u25cb"
            txt, col = f"{sym}  {self.state}", qcol(C.PRI)

        # Speaking waveform directly under orb
        if self.speaking:
            sw_y = sy_t - 20
            sw_w = sphere_r * 0.5
            sw_N = 20
            sw_bw = sw_w * 2 / sw_N
            for si in range(sw_N):
                sh = random.randint(2, 14)
                sx = cx - sw_w + si * sw_bw
                sc = qcol(C.ENERGY, 150)
                p.fillRect(QRectF(sx, sw_y - sh / 2, sw_bw - 1, sh), sc)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        _tc = QColor(col); _tc.setAlpha(20)
        for _ox, _oy in [(-2,0),(2,0),(0,-2),(0,2),(-1,-1),(1,1),(-1,1),(1,-1)]:
            p.setPen(QPen(_tc, 1))
            p.drawText(QRectF(_ox, sy_t + _oy, W, 18), Qt.AlignmentFlag.AlignCenter, txt)
        p.setPen(QPen(col, 1))
        p.drawText(QRectF(0, sy_t, W, 18), Qt.AlignmentFlag.AlignCenter, txt)

        # ═══ LAYER 15: Waveform ═══════════════════════════════════════════
        if self.config.waveform_style != "off":
            wy = sy_t + 26
            if self.config.waveform_style == "classic":
                N, bw = 48, 6
            elif self.config.waveform_style == "minimal":
                N, bw = 24, 8
            else:
                N, bw = 48, 6
            wx0 = (W - N * bw) / 2
            for i in range(N):
                if self.muted:
                    hgt, cl = 2, qcol(C.MUTED_C, 100)
                elif self.speaking:
                    hgt = random.randint(2, 22)
                    t   = hgt / 22
                    if t < 0.4:
                        cl = qcol(C.PRI, 180)
                    elif t < 0.75:
                        cl = qcol(C.ENERGY, 200)
                    else:
                        cl = qcol(C.GREEN, 220)
                    p.fillRect(QRectF(wx0 + i * bw - 1, wy + 22 - hgt - 3, bw + 1, hgt + 6), qcol(C.ENERGY, 25))
                elif is_active:
                    hgt = int(4 + 6 * abs(math.sin(self._tick * 0.07 + i * 0.4)))
                    cl  = qcol(C.PRI_DIM, 160)
                else:
                    hgt = int(2 + 2 * math.sin(self._tick * 0.05 + i * 0.5))
                    cl  = qcol(C.BORDER_B, 120)
                p.fillRect(QRectF(wx0 + i * bw, wy + 22 - hgt, bw - 1, hgt), cl)

        # ═══ LAYER 15b: Side telemetry ─══════════════════════════════════
        telem_a = max(0, int(self._brightness * 100))
        if telem_a > 2:
            # Left side: vertical sparkline
            spark_x = cx - sphere_r * 1.35
            spark_h = sphere_r * 0.8
            spark_cy = cy
            p.setPen(QPen(qcol(C.PRI,telem_a), 0.6))
            p.drawLine(QPointF(spark_x, spark_cy - spark_h / 2),
                       QPointF(spark_x, spark_cy + spark_h / 2))
            # Sparkline points
            p.setFont(QFont("Courier New", 5))
            p.setPen(QPen(qcol(C.PRI,max(0, telem_a - 5)), 0.5))
            p.drawText(QPointF(spark_x - 20, spark_cy - spark_h / 2 - 6), "PWR")
            for si in range(20):
                sy = spark_cy - spark_h / 2 + (spark_h / 20) * si
                sv = 8 + 6 * math.sin(self._tick * 0.03 + si * 0.5)
                p.setPen(QPen(qcol(C.ENERGY,max(0, telem_a - 2)), 0.5))
                p.drawLine(QPointF(spark_x - sv, sy), QPointF(spark_x + sv, sy))
                # Tick mark
                p.setPen(QPen(qcol(C.PRI,max(0, telem_a - 8)), 0.3))
                p.drawLine(QPointF(spark_x - 2, sy), QPointF(spark_x + 2, sy))

            # Right side: vertical sparkline
            spark_x2 = cx + sphere_r * 1.35
            p.setPen(QPen(qcol(C.PRI,telem_a), 0.6))
            p.drawLine(QPointF(spark_x2, spark_cy - spark_h / 2),
                       QPointF(spark_x2, spark_cy + spark_h / 2))
            p.setFont(QFont("Courier New", 5))
            p.setPen(QPen(qcol(C.PRI,max(0, telem_a - 5)), 0.5))
            p.drawText(QPointF(spark_x2 - 12, spark_cy - spark_h / 2 - 6), "NET")
            for si in range(20):
                sy = spark_cy - spark_h / 2 + (spark_h / 20) * si
                sv = 6 + 8 * abs(math.sin(self._tick * 0.025 + si * 0.7))
                p.setPen(QPen(qcol(C.ENERGY,max(0, telem_a - 2)), 0.5))
                p.drawLine(QPointF(spark_x2 - sv, sy), QPointF(spark_x2 + sv, sy))
                p.setPen(QPen(qcol(C.PRI,max(0, telem_a - 8)), 0.3))
                p.drawLine(QPointF(spark_x2 - 2, sy), QPointF(spark_x2 + 2, sy))

            # Left telemetry labels
            p.setFont(QFont("Courier New", 5))
            labels_left = [
                (-0.72, 0.05, "SYS {:.1f}%".format(94.2 + 2 * math.sin(self._tick * 0.01))),
                (-0.68, 0.18, "CPU  {:.0f}MHz".format(3200 + 200 * math.sin(self._tick * 0.008))),
                (-0.72, 0.31, "MEM {:.0f}MB".format(4096 + 512 * math.sin(self._tick * 0.005))),
            ]
            for dx, dy, label in labels_left:
                lx = cx + dx * sphere_r
                ly = cy + dy * sphere_r
                a = int(telem_a * (0.7 + 0.3 * math.sin(self._tick * 0.02 + dx * 5)))
                p.setPen(QPen(qcol(C.PRI,a), 0.6))
                p.drawText(QPointF(lx, ly), label)

            # Right telemetry labels
            labels_right = [
                (0.58, 0.05, "NET {:.0f}ms".format(12 + 4 * math.sin(self._tick * 0.012))),
                (0.62, 0.18, "PKT  {:.0f}/s".format(1200 + 300 * math.sin(self._tick * 0.007))),
                (0.58, 0.31, "LAT {:.0f}ms".format(2 + 1 * math.sin(self._tick * 0.015))),
            ]
            for dx, dy, label in labels_right:
                lx = cx + dx * sphere_r
                ly = cy + dy * sphere_r
                a = int(telem_a * (0.7 + 0.3 * math.sin(self._tick * 0.02 + dx * 5)))
                p.setPen(QPen(qcol(C.PRI,a), 0.6))
                p.drawText(QPointF(lx, ly), label)

        # ═══ OVERLAY: Scanlines + Vignette + Noise ════════════════════════
        # Scanlines — slow horizontal sweep
        _sweep_y = (self._tick * 1.5) % H
        for _sy in range(0, H, max(3, self._scanline_step - 1)):
            _dist = abs(_sy - _sweep_y) / H
            _sa = max(0, int(12 * (1.0 - _dist * 8)))
            if _sa > 0:
                p.fillRect(QRectF(0, _sy, W, 1), qcol(C.ENERGY, _sa))

        # Persistent faint scanlines
        for _sy in range(0, H, self._scanline_step):
            p.fillRect(QRectF(0, _sy, W, 1), qcol(C.ENERGY, 12))

        # Vignette
        _vig = QRadialGradient(QPointF(cx, cy), max(W, H) * 0.7)
        _vig_edge = qcol(C.STEEL if _is_light_theme else C.DARK, 70 if _is_light_theme else 170)
        _vig.setColorAt(0.0, qcol(C.BG, 0))
        _vig.setColorAt(0.6, qcol(C.BG, 0))
        _vig.setColorAt(1.0, _vig_edge)
        p.fillRect(self.rect(), _vig)

        # Animated noise/grain — very subtle
        if self._tick % 3 == 0:  # Update every 3 frames
            self._noise_seed = self._tick
        _n_a = 50
        for _ni in range(self._noise_count):
            _nx = random.randint(0, W)
            _ny = random.randint(0, H)
            _ns = random.uniform(0.3, 1.0)
            p.fillRect(QRectF(_nx, _ny, _ns, _ns), qcol(C.PRI, _n_a))


class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.TEXT_MED)
        elif self._value > 65:
            bar_col = qcol(C.TEXT_MED)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)


# ---------------------------------------------------------------------------
# AgentGridWidget — live autonomous agent status panel
# ---------------------------------------------------------------------------



class SparklineBar(QWidget):
    """Compact metric row with label, sparkline, and value."""

    def __init__(self, label: str, color: str = C.TEXT_MED, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = "0"
        self._unit = ""
        self._history = [0.0] * 30
        self.setFixedHeight(28)

    def set_value(self, value: str, pct: float = 0.0, unit: str = ""):
        """Update value and sparkline. pct is 0-1 for the bar height."""
        self._value = value
        self._unit = unit
        self._history.append(max(0.0, min(1.0, pct)))
        if len(self._history) > 30:
            self._history = self._history[-30:]
        self.update()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QColor, QPen, QLinearGradient
        from PyQt6.QtCore import QPointF
        W, H = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Label (left)
        p.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
        p.setPen(QColor(C.TEXT_DIM))
        label_w = 44
        p.drawText(0, 0, label_w, H, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._label)

        # Sparkline (middle)
        spark_x = label_w + 2
        spark_w = W - label_w - 60
        spark_h = H - 6
        spark_y = 3

        col = QColor(self._color)
        col_dim = QColor(self._color)
        col_dim.setAlpha(40)

        # Fill area under curve
        if len(self._history) > 1:
            from PyQt6.QtGui import QPainterPath
            path = QPainterPath()
            step = spark_w / (len(self._history) - 1)
            for i, v in enumerate(self._history):
                x = spark_x + i * step
                y = spark_y + spark_h * (1 - v)
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            # Close path for fill
            fill = QPainterPath(path)
            fill.lineTo(spark_x + spark_w, spark_y + spark_h)
            fill.lineTo(spark_x, spark_y + spark_h)
            fill.closeSubpath()
            fill_col = QColor(self._color)
            fill_col.setAlpha(15)
            p.fillPath(fill, fill_col)
            # Stroke line
            p.setPen(QPen(col, 1.2))
            p.drawPath(path)

        # Value (right)
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QColor(C.WHITE))
        val_x = W - 55
        val_w = 35
        p.drawText(val_x, 0, val_w, H, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self._value)
        # Unit
        p.setFont(QFont("Courier New", 6))
        p.setPen(QColor(C.TEXT_DIM))
        p.drawText(val_x + val_w + 2, 0, 18, H, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._unit)

        p.end()

class AgentGridWidget(QWidget):
    """
    Vertical timeline agent display – polished JARVIS edition.
    Implements all 12 feedback points: hierarchy, color, spacing, activity stream.
    """

    # ── Per-agent accent colors (points 4 & 9) ──────────────────────────────
    _ACCENT = {
        "CORE":       "#00E5FF",   # brightest cyan  (special)
        "RESEARCH":   "#00E5FF",   # cyan
        "SECURITY":   "#1E90FF",   # blue
        "AUTOMATION": "#FFD700",   # gold
        "MEMORY":     "#BF7FFF",   # purple
        "VISION":     "#00FF7F",   # green
        "DEV":        "#E0E0E0",   # white
        "SYSTEM":     "#00CED1",   # teal
    }

    _STATUS = {
        "CORE":       "PRIMARY",
        "RESEARCH":   "ONLINE",
        "SECURITY":   "ACTIVE",
        "AUTOMATION": "IDLE",
        "MEMORY":     "PROCESSING",
        "VISION":     "ANALYZING",
        "DEV":        "ONLINE",
        "SYSTEM":     "ONLINE",
    }

    _ICONS = {
        "CORE":       "◉",
        "RESEARCH":   "⌕",
        "SECURITY":   "◆",
        "AUTOMATION": "⚡",
        "MEMORY":     "◉",
        "VISION":     "◎",
        "DEV":        "‹›",
        "SYSTEM":     "◈",
    }

    _AGENTS = [
        "CORE",
        "RESEARCH",
        "SECURITY",
        "AUTOMATION",
        "MEMORY",
        "VISION",
        "DEV",
        "SYSTEM",
    ]

    _OBJECTIVES = {
        "CORE":       ["Primary intelligence framework", "Coordinating sub-systems",
                       "Optimizing neural pathways", "Synchronizing agents"],
        "RESEARCH":   ["Scanning knowledge base", "Cross-referencing sources",
                       "Synthesizing findings", "Validating hypotheses"],
        "SECURITY":   ["Monitoring threat vectors", "Scanning network perimeter",
                       "Validating access tokens", "Auditing system logs"],
        "AUTOMATION": ["Executing task pipeline", "Scheduling workflows",
                       "Delegating subtasks", "Optimizing execution path"],
        "MEMORY":     ["Indexing context graph", "Retrieving episodic memory",
                       "Consolidating knowledge", "Pruning stale entries"],
        "VISION":     ["Processing visual input", "Analyzing screen state",
                       "Detecting UI elements", "Mapping spatial context"],
        "DEV":        ["Analyzing code structure", "Tracing dependencies",
                       "Reviewing logic flow", "Generating test cases"],
        "SYSTEM":     ["Monitoring diagnostics", "Tracking resource usage",
                       "Optimizing memory", "Balancing load distribution"],
    }

    # Event log entries for the Activity Stream (point 12)
    _LOG_TEMPLATES = [
        "Memory synchronized",
        "Threat scan complete",
        "Vision detected alignment",
        "Workflow optimized",
        "Knowledge base updated",
        "Security token refreshed",
        "Core cycle completed",
        "Task pipeline flushed",
        "Context graph pruned",
        "Neural pathway calibrated",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")

        self._agent_state: list[list] = []
        for name in self._AGENTS:
            self._agent_state.append([
                random.uniform(0.55, 0.98),   # [0] confidence
                random.uniform(0, 2 * math.pi),  # [1] phase
                0,                             # [2] obj index
                random.randint(60, 180),       # [3] obj countdown
                "active",                      # [4] status string
            ])

        self._active_idx = 0

        # Activity stream log (point 12)
        self._log_lines: list[str] = []
        self._log_scroll_offset = 0
        self._log_tick = 0
        for i in range(5):
            self._push_log()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(0)

        # ── Super-label (point 7): "COGNITIVE NETWORK" ──────────────────────
        super_lbl = QLabel("COGNITIVE NETWORK")
        super_lbl.setFont(QFont("Courier New", 7))
        super_lbl.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; "
            "letter-spacing: 3px; opacity: 0.28;"
        )
        lay.addWidget(super_lbl)

        # ── Section header row (point 8: 13 px title) ───────────────────────
        hdr_row = QHBoxLayout()
        hdr = QLabel("AGENTS")
        hdr.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 3px;")
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()
        self._active_count_lbl = QLabel(f"{len(self._AGENTS)} ACTIVE")
        self._active_count_lbl.setFont(QFont("Courier New", 7))
        self._active_count_lbl.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; letter-spacing: 1px;"
        )
        hdr_row.addWidget(self._active_count_lbl)
        lay.addLayout(hdr_row)
        lay.addSpacing(4)

        # ── Timeline entries (in scroll area so cards never get squashed) ────
        from PyQt6.QtWidgets import QScrollArea
        scroll_container = QWidget()
        scroll_container.setStyleSheet('background: transparent;')
        scroll_lay = QVBoxLayout(scroll_container)
        scroll_lay.setContentsMargins(0, 0, 0, 0)
        scroll_lay.setSpacing(0)
        self._cards: list[dict] = []
        for i, name in enumerate(self._AGENTS):
            is_core = (name == 'CORE')
            card = self._make_timeline_entry(name, i, is_core)
            scroll_lay.addWidget(card['widget'])
            self._cards.append(card)
        scroll_lay.addStretch()
        scroll_area = QScrollArea()
        scroll_area.setWidget(scroll_container)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setStyleSheet('QScrollArea { background: transparent; border: none; }')
        lay.addWidget(scroll_area, stretch=1)
        lay.addSpacing(2)
        # ── Activity Stream (point 12) ───────────────────────────────────────
        stream_super = QLabel("LIVE TELEMETRY")
        stream_super.setFont(QFont("Courier New", 7))
        stream_super.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; letter-spacing: 3px;"
        )
        lay.addWidget(stream_super)

        stream_hdr = QLabel("EVENT LOG")
        stream_hdr.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        stream_hdr.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 2px;"
        )
        lay.addWidget(stream_hdr)
        lay.addSpacing(4)

        self._log_widget = QLabel()
        self._log_widget.setFont(QFont("Courier New", 7))
        self._log_widget.setStyleSheet(
            f"color: {C.TEXT_MED}; background: transparent; line-height: 160%;"
        )
        self._log_widget.setWordWrap(False)
        lay.addWidget(self._log_widget)

        lay.addStretch()

        # ── Timer ────────────────────────────────────────────────────────────
        self._tick = 0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._animate)
        self._tmr.start(80)
        # Force immediate first render so CORE is highlighted from frame 1
        QTimer.singleShot(50, self._animate)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _push_log(self):
        import datetime
        t = datetime.datetime.now().strftime("%H:%M")
        msg = random.choice(self._LOG_TEMPLATES)
        self._log_lines.insert(0, f"{t}  {msg}")
        if len(self._log_lines) > 8:
            self._log_lines.pop()

    def _make_timeline_entry(self, name: str, idx: int, is_core: bool) -> dict:
        accent = self._ACCENT.get(name, C.PRI)

        # Uniform card height — CORE distinguished by color not size
        height = 44
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        w.setFixedHeight(height)
        wl = QVBoxLayout(w)
        wl.setContentsMargins(4, 2, 4, 2)
        wl.setSpacing(0)
        wl.setSpacing(0)

        # ── Top row: icon · name · status · conf ────────────────────────────
        top = QHBoxLayout()
        top.setSpacing(6)

        # Single icon only (no separate dot — eliminates double-dot)
        icon_lbl = QLabel(self._ICONS.get(name, "◈"))
        icon_lbl.setFont(QFont("Courier New", 9))
        icon_lbl.setStyleSheet(f"color: {accent}; background: transparent;")
        icon_lbl.setFixedWidth(18)
        top.addWidget(icon_lbl)

        name_lbl = QLabel(name)
        name_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        name_lbl.setStyleSheet(f"color: {accent}; background: transparent; letter-spacing: 1px;")
        top.addWidget(name_lbl)

        top.addStretch()

        # Shorten status to 4 chars max to prevent clipping
        _status_map = {"PRIMARY": "PRIM", "ONLINE": "ONLN", "ACTIVE": "ACTV",
                       "IDLE": "IDLE", "PROCESSING": "PROC", "ANALYZING": "ANLZ"}
        _st = _status_map.get(self._STATUS.get(name, "ONLINE"), "ONLN")
        status_lbl = QLabel(_st)
        status_lbl.setFont(QFont("Courier New", 6))
        status_lbl.setStyleSheet(f"color: {accent}; background: transparent; letter-spacing: 1px;")
        status_lbl.setFixedWidth(32)
        top.addWidget(status_lbl)

        conf_lbl = QLabel("92%")
        conf_lbl.setFont(QFont("Courier New", 6))
        conf_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        conf_lbl.setFixedWidth(28)
        top.addWidget(conf_lbl)

        wl.addLayout(top)

        # ── Bottom row: faint │ + objective text aligned under name ────────
        bot = QHBoxLayout()
        bot.setSpacing(4)

        # No connector line — indent only
        line_lbl = QLabel('')
        line_lbl.setFixedWidth(8)
        bot.addWidget(line_lbl)

        # Spacer to align with name (icon width=18 + spacing=6 = 24px offset)
        bot.addSpacing(6)

        obj_lbl = QLabel(self._OBJECTIVES[name][0])
        obj_lbl.setFont(QFont("Courier New", 6))
        obj_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        bot.addWidget(obj_lbl, stretch=1)

        wl.addLayout(bot)

        return {
            "widget":     w,
            "dot":        icon_lbl,   # alias so _animate still works
            "name_lbl":   name_lbl,
            "conf_lbl":   conf_lbl,
            "obj_lbl":    obj_lbl,
            "line_lbl":   line_lbl,
            "icon_lbl":   icon_lbl,
            "status_lbl": status_lbl,
            "accent":     accent,
            "is_core":    is_core,
        }


    def _animate(self):
        self._tick += 1

        # Rotate active agent every 10 s (125 * 80 ms = 10 000 ms)
        if self._tick % 125 == 0:
            self._active_idx = (self._active_idx + 1) % len(self._AGENTS)

        # Push a new log entry every ~3 seconds
        self._log_tick += 1
        if self._log_tick % 38 == 0:
            self._push_log()
            self._log_widget.setText("\n".join(self._log_lines[:5]))

        for i, name in enumerate(self._AGENTS):
            st    = self._agent_state[i]
            conf  = st[0]
            phase = st[1]

            # Pulse sine
            pulse = 0.5 + 0.5 * math.sin(phase + self._tick * 0.08)
            st[1] += 0.01

            # Rotate objective text
            st[3] -= 1
            if st[3] <= 0:
                objs  = self._OBJECTIVES.get(name, ["Operating"])
                st[2] = (st[2] + 1) % len(objs)
                st[3] = random.randint(90, 200)

            # Confidence drift
            conf += random.uniform(-0.02, 0.02)
            conf  = max(0.50, min(0.99, conf))
            st[0] = conf

            card      = self._cards[i]
            is_active = (i == self._active_idx)
            accent    = card["accent"]

            # ── Active agent: full accent color + highlight bg ───────────────
            if is_active:
                name_col   = accent
                conf_col   = accent
                obj_col    = C.TEXT_MED
                dot_col    = accent
                icon_col   = accent
                status_col = accent
                line_col   = accent
                bg_style   = f"background: rgba(0,229,255,18); border-left: 3px solid {accent}; border-radius: 2px;"
            else:
                name_col   = "#5a9090"
                conf_col   = "#3d6a6a"
                obj_col    = "#335858"
                dot_col    = "#3d6a6a"
                icon_col   = "#3d6a6a"
                status_col = "#335858"
                line_col   = "#1a3535"
                bg_style   = "background: transparent; border-left: 2px solid #1a3535;"

            # Apply bg to card widget
            card["widget"].setStyleSheet(bg_style)
            card["dot"].setStyleSheet(
                f"color: {dot_col}; background: transparent;"
            )
            card["icon_lbl"].setStyleSheet(
                f"color: {icon_col}; background: transparent;"
            )
            card["name_lbl"].setStyleSheet(
                f"color: {name_col}; background: transparent;"
            )
            card["conf_lbl"].setText(f"{int(conf * 100)}%")
            card["conf_lbl"].setStyleSheet(
                f"color: {conf_col}; background: transparent;"
            )
            card["status_lbl"].setStyleSheet(
                f"color: {status_col}; background: transparent; letter-spacing: 1px;"
            )
            card["obj_lbl"].setText(
                self._OBJECTIVES.get(name, ["Operating"])[st[2]]
            )
            card["obj_lbl"].setStyleSheet(
                f"color: {obj_col}; background: transparent;"
            )
            # line_lbl hidden — no connector




class AIActivityCanvas(QWidget):
    """
    Context-aware animated visualization that sits below the HUD face.
    Modes: idle | listening | thinking | speaking | coding | analyzing | researching
    """

    def __init__(self, parent=None, config: AIActivityConfig = None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

        # Use default config if none provided
        self.config = config or AIActivityConfig()

        self.mode  = "idle"   # idle | listening | thinking | speaking | coding | analyzing | researching
        self.state = "LISTENING"

        # Animation state
        self._tick      = 0
        self._nodes: list[list[float]] = []   # [x, y, vx, vy, pulse, phase]
        self._edges: list[tuple[int,int,float]] = []  # (i, j, alpha)
        self._streams: list[list[float]] = []  # [x, y, vx, vy, life, max_life]
        self._bars: list[float] = []           # equalizer bar heights
        self._scan_angle = 0.0
        self._data_packets: list[list[float]] = []  # [edge_idx, t, speed]

        self._init_nodes(self.config.node_count)
        self._init_bars(self.config.bar_count)

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self.set_graphics_quality(get_graphics_quality())

    def set_graphics_quality(self, quality: str):
        value = _normalize_graphics_quality(quality)
        profile = GRAPHICS_PROFILES[value]
        self._graphics_quality = value
        self._antialias = bool(profile["antialias"])
        self._quality_node_count = int(profile["activity_nodes"])
        self._tmr.setInterval(int(profile["frame_ms"]))
        self._init_nodes(self._quality_node_count)
        if not self._tmr.isActive():
            self._tmr.start()
        self.update()

    def _init_nodes(self, n: int):
        self._nodes = []
        for i in range(n):
            ang = (i / n) * 2 * math.pi + random.uniform(-0.3, 0.3)
            r   = random.uniform(0.25, 0.45)
            self._nodes.append([
                0.5 + r * math.cos(ang),   # x (normalized)
                0.5 + r * math.sin(ang),   # y (normalized)
                random.uniform(-0.0008, 0.0008),  # vx
                random.uniform(-0.0008, 0.0008),  # vy
                random.uniform(0, 1),       # pulse phase
                random.uniform(0, 2 * math.pi),   # individual phase
            ])
        # Build edges: connect nearby nodes using config threshold
        self._edges = []
        for i in range(len(self._nodes)):
            for j in range(i + 1, len(self._nodes)):
                ni, nj = self._nodes[i], self._nodes[j]
                dist = math.hypot(ni[0] - nj[0], ni[1] - nj[1])
                if dist < self.config.edge_distance_threshold:
                    self._edges.append((i, j, random.uniform(0.3, 0.8)))

    def _init_bars(self, n: int):
        self._bars = [random.uniform(0.1, 0.4) for _ in range(n)]

    def set_mode(self, mode: str):
        if mode != self.mode:
            self.mode = mode
            if mode == "coding":
                self._init_nodes(min(16, self._quality_node_count))
            elif mode == "analyzing":
                self._init_nodes(min(10, self._quality_node_count))
            elif mode == "researching":
                self._init_nodes(min(20, self._quality_node_count))
            else:
                self._init_nodes(self._quality_node_count)

    def _step(self):
        self._tick += 1
        W, H = max(1, self.width()), max(1, self.height())

        is_active = self.state in ("THINKING", "SPEAKING", "PROCESSING")
        speed_mul = 2.5 if is_active else 0.6

        # Update nodes
        for nd in self._nodes:
            nd[0] += nd[2] * speed_mul
            nd[1] += nd[3] * speed_mul
            nd[4]  = (nd[4] + 0.018 * speed_mul) % 1.0
            nd[5]  = (nd[5] + 0.04  * speed_mul) % (2 * math.pi)
            # Bounce off edges
            if nd[0] < 0.08 or nd[0] > 0.92: nd[2] *= -1
            if nd[1] < 0.08 or nd[1] > 0.92: nd[3] *= -1
            nd[0] = max(0.08, min(0.92, nd[0]))
            nd[1] = max(0.08, min(0.92, nd[1]))

        # Rebuild edges dynamically using config threshold
        if self._tick % 30 == 0:
            self._edges = []
            for i in range(len(self._nodes)):
                for j in range(i + 1, len(self._nodes)):
                    ni, nj = self._nodes[i], self._nodes[j]
                    dist = math.hypot(ni[0] - nj[0], ni[1] - nj[1])
                    if dist < self.config.edge_distance_threshold:
                        self._edges.append((i, j, min(1.0, self.config.edge_distance_threshold / max(dist, 0.01) - 0.5)))

        # Data packets flowing along edges (respect config limit)
        if (self.config.show_data_packets and is_active and
            random.random() < 0.12 and self._edges and
            len(self._data_packets) < self.config.max_data_packets):
            ei = random.randint(0, len(self._edges) - 1)
            # Apply data_packet_velocity setting
            velocity_multiplier = {
                "slow": 0.5,
                "medium": 1.0,
                "fast": 1.5
            }.get(self.config.data_packet_velocity, 1.0)
            base_speed = random.uniform(0.008, 0.02)
            self._data_packets.append([ei, 0.0, base_speed * velocity_multiplier])
        self._data_packets = [
            [p[0], p[1] + p[2], p[2]] for p in self._data_packets if p[1] < 1.0
        ]

        # Equalizer bars (respect config flag)
        if self.config.show_equalizer_bars:
            target_h = 0.6 if self.state == "SPEAKING" else (0.35 if is_active else 0.12)
            for i in range(len(self._bars)):
                tgt = random.uniform(0.05, target_h) if is_active else random.uniform(0.03, 0.12)
                self._bars[i] += (tgt - self._bars[i]) * 0.18

        # Scanner (respect config flag)
        if self.config.show_scanner:
            scan_spd = 2.8 if is_active else 0.9
            self._scan_angle = (self._scan_angle + scan_spd) % 360

        # Data streams (respect config limit and flag)
        if (self.config.show_data_streams and is_active and
            random.random() < 0.08 and
            len(self._streams) < self.config.max_data_streams):
            self._streams.append([
                random.uniform(0.1, 0.9) * W, 0.0,
                random.uniform(-0.5, 0.5), random.uniform(1.5, 3.5),
                1.0, 1.0
            ])
        self._streams = [
            [s[0]+s[2], s[1]+s[3], s[2], s[3], s[4]-0.025, s[5]]
            for s in self._streams if s[4] > 0 and 0 <= s[1] <= H
        ]

        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, self._antialias)
        p.fillRect(self.rect(), qcol(C.BG))

        W, H = self.width(), self.height()
        if W < 10 or H < 10:
            return

        mode = self.mode
        is_active = self.state in ("THINKING", "SPEAKING", "PROCESSING")

        # ── Precision grid background (always shown) ────────────────────────
        p.setPen(QPen(qcol(C.PRI_GHO, 40), 1))
        for x in range(0, W, 20):
            p.drawLine(QPointF(x, 0), QPointF(x, H))
        for y in range(0, H, 20):
            p.drawLine(QPointF(0, y), QPointF(W, y))

        # ── Mode label (always shown) ───────────────────────────────────────
        mode_labels = {
            "coding":      ("◈ DEPENDENCY GRAPH",    C.ACC2),
            "analyzing":   ("◈ DOCUMENT MAP",         C.PRI),
            "researching": ("◈ KNOWLEDGE NETWORK",    C.PURPLE),
            "thinking":    ("◈ REASONING CHAIN",      C.ACC),
            "speaking":    ("◈ AUDIO SYNTHESIS",      C.GREEN),
            "listening":   ("◈ AUDIO CAPTURE",        C.PRI),
            "idle":        ("◈ STANDBY",              C.TEXT_DIM),
        }
        lbl_txt, lbl_col = mode_labels.get(mode, ("◈ AI ACTIVITY", C.TEXT_MED))
        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(lbl_col), 1))
        p.drawText(QRectF(8, 4, W - 16, 14), Qt.AlignmentFlag.AlignLeft, lbl_txt)

        # ── Separator line (always shown) ───────────────────────────────────
        p.setPen(QPen(qcol(C.BORDER), 1))
        p.drawLine(QPointF(0, 20), QPointF(W, 20))

        # ── Draw based on mode (respect config) ─────────────────────────────
        draw_y0 = 24
        draw_h  = H - draw_y0 - 4

        if mode in ("speaking",):
            if self.config.show_equalizer_bars:  # Spectrum uses equalizer bars
                self._draw_spectrum(p, W, draw_y0, draw_h)
        elif mode in ("coding", "analyzing", "researching", "thinking", "idle", "listening"):
            if self.config.show_nodes or self.config.show_edges:  # Network needs nodes or edges
                self._draw_network(p, W, draw_y0, draw_h, mode)

        # ── Data streams overlay (respect config) ───────────────────────────
        if self.config.show_data_streams:
            for s in self._streams:
                a = max(0, min(255, int(s[4] * 180)))
                p.setPen(QPen(qcol(C.PRI, a), 1))
                p.drawLine(QPointF(s[0], s[1]), QPointF(s[0] - s[2]*3, s[1] - s[3]*3))

    def _draw_network(self, p: QPainter, W: int, y0: int, H: int, mode: str):
        if not self._nodes:
            return

        node_col = {
            "coding":      C.ACC2,
            "analyzing":   C.PRI,
            "researching": C.PURPLE,
            "thinking":    C.ACC,
            "listening":   C.PRI,
        }.get(mode, C.TEXT_MED)

        edge_col = {
            "coding":      C.ACC2,
            "analyzing":   C.PRI,
            "researching": C.PURPLE,
            "thinking":    C.ACC,
        }.get(mode, C.BORDER_B)

        # Draw edges (respect config)
        if self.config.show_edges:
            # Apply edge_opacity setting
            opacity_multiplier = {
                "low": 0.5,
                "medium": 1.0,
                "high": 1.5
            }.get(self.config.edge_opacity, 1.0)
            for (i, j, alpha) in self._edges:
                ni, nj = self._nodes[i], self._nodes[j]
                x1, y1 = ni[0] * W, y0 + ni[1] * H
                x2, y2 = nj[0] * W, y0 + nj[1] * H
                a = max(0, min(255, int(alpha * 80 * opacity_multiplier)))
                p.setPen(QPen(qcol(edge_col, a), 1))
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # Draw data packets on edges (respect config)
        if self.config.show_data_packets:
            for pkt in self._data_packets:
                ei = int(pkt[0])
                if ei >= len(self._edges):
                    continue
                i, j, _ = self._edges[ei]
                ni, nj = self._nodes[i], self._nodes[j]
                t = pkt[1]
                px = (ni[0] + (nj[0] - ni[0]) * t) * W
                py = y0 + (ni[1] + (nj[1] - ni[1]) * t) * H
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(qcol(node_col, 220)))
                p.drawEllipse(QPointF(px, py), 3, 3)

        # Draw nodes with enhanced glow (respect config)
        if self.config.show_nodes:
            for nd in self._nodes:
                nx, ny = nd[0] * W, y0 + nd[1] * H
                pulse  = 0.5 + 0.5 * math.sin(nd[5])
                # Apply node_size setting
                size_multiplier = {
                    "small": 0.7,
                    "medium": 1.0,
                    "large": 1.5
                }.get(self.config.node_size, 1.0)
                r_base = (4.0 + pulse * 2.5) * size_multiplier
                # Multi-layer glow
                for gi in range(4):
                    gr = r_base + gi * 2.5
                    ga = max(0, int(60 * pulse * (1 - gi / 4)))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QBrush(qcol(node_col, ga)))
                    p.drawEllipse(QPointF(nx, ny), gr, gr)
                # Core
                p.setBrush(QBrush(qcol(node_col, 200)))
                p.drawEllipse(QPointF(nx, ny), r_base * 0.6, r_base * 0.6)

        # Mode-specific overlays (respect config where applicable)
        if mode == "coding" and self.config.show_nodes:
            # Draw bracket decorations on some nodes
            p.setPen(QPen(qcol(C.ACC2, 80), 1))
            for i, nd in enumerate(self._nodes[:4]):
                nx, ny = nd[0] * W, y0 + nd[1] * H
                bl = 8
                p.drawLine(QPointF(nx-bl, ny-bl), QPointF(nx-bl+4, ny-bl))
                p.drawLine(QPointF(nx-bl, ny-bl), QPointF(nx-bl, ny-bl+4))

        elif mode == "researching" and self.config.show_nodes:
            # Draw expanding rings around hub nodes
            if self._nodes:
                hub = self._nodes[0]
                hx, hy = hub[0] * W, y0 + hub[1] * H
                t = (self._tick % 60) / 60.0
                for ri in range(3):
                    r = (t + ri / 3) * min(W, H) * 0.3
                    a = max(0, int(120 * (1 - (t + ri / 3))))
                    p.setPen(QPen(qcol(C.PURPLE, a), 1))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawEllipse(QPointF(hx, hy), r, r)

        elif mode == "analyzing" and self.config.show_scanner:
            # Scanning sweep line
            sr = min(W, H) * 0.4
            cx, cy = W * 0.5, y0 + H * 0.5
            rad = math.radians(self._scan_angle)
            p.setPen(QPen(qcol(C.PRI, 60), 1))
            p.drawLine(QPointF(cx, cy),
                       QPointF(cx + sr * math.cos(rad), cy + sr * math.sin(rad)))

    def _draw_spectrum(self, p: QPainter, W: int, y0: int, H: int):
        # Respect spectrum_type setting
        if self.config.spectrum_type == "off" or not self.config.show_equalizer_bars:
            return
        if self.config.spectrum_type == "off":
            return

        n = len(self._bars)
        if n == 0:
            return
        bw  = W / n
        mid = y0 + H * 0.5

        if self.config.spectrum_type == "waveform":
            # Draw waveform instead of bars
            for i in range(len(self._bars) - 1):
                x1 = i * bw + bw/2
                x2 = (i + 1) * bw + bw/2
                y1 = mid - self._bars[i] * H * 0.8
                y2 = mid - self._bars[i + 1] * H * 0.8
                p.setPen(QPen(qcol(C.PRI, 180), 2))
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            # Glow effect for waveform
            for i in range(len(self._bars) - 1):
                x1 = i * bw + bw/2
                x2 = (i + 1) * bw + bw/2
                y1 = mid - self._bars[i] * H * 0.8
                y2 = mid - self._bars[i + 1] * H * 0.8
                p.setPen(QPen(qcol(C.WHITE, 60), 4))
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        else:
            # Default bars style (original implementation)
            for i, h in enumerate(self._bars):
                bh  = h * H * 0.9
                bx  = i * bw + 1
                # Color gradient: low=cyan, mid=green, high=orange
                t   = h / 0.6
                if t < 0.5:
                    col = qcol(C.PRI, 200)
                elif t < 0.8:
                    col = qcol(C.GREEN, 200)
                else:
                    col = qcol(C.ACC, 220)
                p.fillRect(QRectF(bx, mid - bh/2, max(1, bw - 2), bh), col)
                # Glow top
                p.fillRect(QRectF(bx, mid - bh/2 - 2, max(1, bw - 2), 2),
                           qcol(C.WHITE, 80))

class TaskQueueWidget(QWidget):
    """Displays a live task queue parsed from JARVIS log messages."""

    _sig = pyqtSignal(str, str)  # (task_name, status)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tasks: list[dict] = []   # {name, status, ts}
        self._sig.connect(self._on_task)
        self.setStyleSheet("background: transparent;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)

        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._c_lay = QVBoxLayout(self._container)
        self._c_lay.setContentsMargins(0, 0, 0, 0)
        self._c_lay.setSpacing(3)
        self._c_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(self._container)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 3px; min-height: 16px;
            }}
        """)
        lay.addWidget(scroll)

    def push_task(self, name: str, status: str):
        self._sig.emit(name, status)

    def _on_task(self, name: str, status: str):
        # Update existing or add new
        for t in self._tasks:
            if t["name"] == name:
                t["status"] = status
                self._rebuild()
                return
        self._tasks.append({"name": name, "status": status, "ts": time.strftime("%H:%M:%S")})
        if len(self._tasks) > 20:
            self._tasks.pop(0)
        self._rebuild()

    def _rebuild(self):
        # Clear layout
        while self._c_lay.count() > 1:
            item = self._c_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        STATUS_COLORS = {
            "active":  (C.ACC,    "▶"),
            "done":    (C.GREEN,  "✓"),
            "error":   (C.RED,    "✗"),
            "pending": (C.TEXT_DIM, "○"),
            "calling": (C.PURPLE, "◈"),
        }

        for task in reversed(self._tasks[-12:]):
            col, sym = STATUS_COLORS.get(task["status"], (C.TEXT_MED, "·"))
            row = QWidget()
            row.setStyleSheet(f"""
                QWidget {{
                    background: {C.CARD};
                    border: 1px solid {C.BORDER};
                    border-left: 2px solid {col};
                    border-radius: 4px;
                }}
            """)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(6, 3, 6, 3)
            rl.setSpacing(6)

            sym_lbl = QLabel(sym)
            sym_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            sym_lbl.setStyleSheet(f"color: {col}; background: transparent; border: none;")
            sym_lbl.setFixedWidth(12)
            rl.addWidget(sym_lbl)

            name_lbl = QLabel(task["name"][:28])
            name_lbl.setFont(QFont("Courier New", 8))
            name_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent; border: none;")
            rl.addWidget(name_lbl, stretch=1)

            ts_lbl = QLabel(task["ts"])
            ts_lbl.setFont(QFont("Courier New", 7))
            ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
            rl.addWidget(ts_lbl)

            self._c_lay.insertWidget(self._c_lay.count() - 1, row)


# ---------------------------------------------------------------------------
# ToolLogWidget — shows tool execution history with status
# ---------------------------------------------------------------------------

class ToolLogWidget(QWidget):
    """Displays tool execution log with color-coded status."""

    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[dict] = []
        self._sig.connect(self._on_entry)
        self.setStyleSheet("background: transparent;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)

        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._c_lay = QVBoxLayout(self._container)
        self._c_lay.setContentsMargins(0, 0, 0, 0)
        self._c_lay.setSpacing(3)
        self._c_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(self._container)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 3px; min-height: 16px;
            }}
        """)
        lay.addWidget(scroll)

    def push(self, text: str):
        self._sig.emit(text)

    def _on_entry(self, text: str):
        self._entries.append({"text": text, "ts": time.strftime("%H:%M:%S")})
        if len(self._entries) > 30:
            self._entries.pop(0)
        self._rebuild()

    def _rebuild(self):
        while self._c_lay.count() > 1:
            item = self._c_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for entry in reversed(self._entries[-15:]):
            txt = entry["text"]
            # Color-code by content
            if "✓" in txt or "done" in txt.lower() or "→" in txt:
                col = C.GREEN
            elif "❌" in txt or "error" in txt.lower() or "fail" in txt.lower():
                col = C.RED
            elif "🔧" in txt or "📞" in txt or "calling" in txt.lower():
                col = C.PURPLE
            elif "⚠" in txt:
                col = C.ACC
            else:
                col = C.TEXT_MED

            row = QWidget()
            row.setStyleSheet(f"""
                QWidget {{
                    background: {C.CARD};
                    border: 1px solid {C.BORDER};
                    border-left: 2px solid {col};
                    border-radius: 4px;
                }}
            """)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(6, 3, 6, 3)
            rl.setSpacing(6)

            ts_lbl = QLabel(entry["ts"])
            ts_lbl.setFont(QFont("Courier New", 7))
            ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
            ts_lbl.setFixedWidth(44)
            rl.addWidget(ts_lbl)

            msg_lbl = QLabel(txt[:40])
            msg_lbl.setFont(QFont("Courier New", 8))
            msg_lbl.setStyleSheet(f"color: {col}; background: transparent; border: none;")
            rl.addWidget(msg_lbl, stretch=1)

            self._c_lay.insertWidget(self._c_lay.count() - 1, row)


# ---------------------------------------------------------------------------
# MissionControlPanel — tabbed right panel
# ---------------------------------------------------------------------------

class MissionControlPanel(QWidget):
    """
    Tabbed mission-control right panel with:
      COMMS | TASKS | ASSETS | TOOLS
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Tab bar ──────────────────────────────────────────────────────────
        tab_bar = QWidget()
        self._tab_bar = tab_bar
        tab_bar.setFixedHeight(32)
        tab_bar.setStyleSheet(f"""
            QWidget {{
                background: {C.DARK};
                border-bottom: 1px solid {C.BORDER};
            }}
        """)
        tb_lay = QHBoxLayout(tab_bar)
        tb_lay.setContentsMargins(4, 0, 4, 0)
        tb_lay.setSpacing(2)

        self._tabs: list[QPushButton] = []
        self._tab_names = ["COMMS", "TASKS", "ASSETS", "TOOLS"]
        self._active_tab = 0

        for i, name in enumerate(self._tab_names):
            btn = QPushButton(name)
            btn.setFixedHeight(28)
            btn.setFont(QFont(DISPLAY_FONT, 8, QFont.Weight.DemiBold))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, idx=i: self._switch_tab(idx))
            self._tabs.append(btn)
            tb_lay.addWidget(btn)

        outer.addWidget(tab_bar)

        # ── Stacked pages ────────────────────────────────────────────────────
        from PyQt6.QtWidgets import QStackedWidget
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")
        outer.addWidget(self._stack, stretch=1)

        # Page 0: COMMS — conversation log
        self.log_widget = LogWidget()
        self._stack.addWidget(self.log_widget)

        # Page 1: TASKS — task queue
        self.task_widget = TaskQueueWidget()
        self._stack.addWidget(self.task_widget)

        # Page 2: ASSETS — file drop zone (built externally, placeholder here)
        self._assets_page = QWidget()
        self._assets_page.setStyleSheet("background: transparent;")
        self._stack.addWidget(self._assets_page)

        # Page 3: TOOLS — tool execution log
        self.tool_widget = ToolLogWidget()
        self._stack.addWidget(self.tool_widget)

        self._switch_tab(0)

    def set_command_center_open(self, is_open: bool):
        """Keep chat visible in focus mode; reveal all mission tools on demand."""
        self._tab_bar.setVisible(is_open)
        if not is_open:
            self._switch_tab(0)

    def refresh_theme(self):
        self._tab_bar.setStyleSheet(f"""
            QWidget {{ background: {C.DARK}; border-bottom: 1px solid {C.BORDER}; }}
        """)
        self._switch_tab(self._active_tab)
        if hasattr(self.log_widget, "refresh_theme"):
            self.log_widget.refresh_theme()

    def _switch_tab(self, idx: int):
        self._active_tab = idx
        self._stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._tabs):
            if i == idx:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.PRI_GHO};
                        color: {C.PRI};
                        border: none;
                        border-bottom: 2px solid {C.PRI};
                        border-radius: 3px;
                        padding: 0 6px;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent;
                        color: {C.TEXT_DIM};
                        border: none;
                        border-radius: 3px;
                        padding: 0 6px;
                    }}
                    QPushButton:hover {{
                        color: {C.TEXT_MED};
                        background: {C.PRI_GHO};
                    }}
                """)

    def set_assets_widget(self, w: QWidget):
        """Called from _build_right_panel to inject the FileDropZone page."""
        lay = QVBoxLayout(self._assets_page)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        lay.addWidget(w)
        lay.addStretch()


class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Courier New", 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        if   tl.startswith("you:"):    self._tag = "you"
        elif tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):   self._tag = "file"
        elif "err" in tl:              self._tag = "err"
        else:                          self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                "sys":  qcol(C.ACC2),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol(C.BORDER_A if z._drag_over else (C.DARK2 if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Courier New", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Courier New", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str, bool)
    validation_finished = pyqtSignal(bool, str, str, bool)


    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QColor
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(C.BG))
        p.end()
        super().paintEvent(event)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: {C.BG};
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected
        self._validation_pending = False
        self._purge_saved_on_failure = False
        self._verified_key = ""
        self.validation_finished.connect(self._on_validation_finished)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("Paste Gemini API key")
        self._key_input.setFont(QFont("Courier New", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.DARK}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)

        self._validation_lbl = QLabel("Only a verified Gemini key will be accepted.")
        self._validation_lbl.setWordWrap(True)
        self._validation_lbl.setFont(QFont("Arial", 8))
        self._validation_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        layout.addWidget(self._validation_lbl)
        layout.addSpacing(8)

        self._remember_key = QPushButton("☆  Remember API key on this machine")
        self._remember_key.setFont(QFont("Courier New", 7))
        self._remember_key.setFixedHeight(28)
        self._remember_key.setCursor(Qt.CursorShape.PointingHandCursor)
        self._remember_key.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding-left: 10px;
            }}
            QPushButton:hover {{
                color: {C.TEXT}; border: 1px solid {C.BORDER_B};
            }}
        """)
        self._remember_key.clicked.connect(self._toggle_remember_key)
        layout.addWidget(self._remember_key)

        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","☰  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        self._init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        self._init_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        self._init_btn.setFixedHeight(36)
        self._init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        self._init_btn.clicked.connect(self._submit)
        layout.addWidget(self._init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,C.DARK2),"mac":(C.ACC2,C.DARK),"linux":(C.GREEN,C.DARK)}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.DARK}; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _toggle_remember_key(self):
        # Simple visual toggle; actual persistence logic lives in _on_setup_done
        # in MainWindow.
        try:
            cur = self._remember_enabled
        except AttributeError:
            self._remember_enabled = False
            cur = False

        self._remember_enabled = not cur
        if self._remember_enabled:
            self._remember_key.setText("★  Remember API key on this machine")
            self._remember_key.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.GREEN_D};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                    text-align: left; padding-left: 10px;
                }}
                QPushButton:hover {{ color: {C.GREEN}; border: 1px solid {C.GREEN}; }}
            """)
        else:
            self._remember_key.setText("☆  Remember API key on this machine")
            self._remember_key.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_MED};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding-left: 10px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() + f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        remember = bool(getattr(self, '_remember_enabled', False))
        self.validate_candidate(key, remember_key=remember)

    def validate_candidate(
        self,
        key: str,
        remember_key: bool = False,
        purge_saved_on_failure: bool = False,
    ):
        """Verify a key with Gemini without blocking the interface."""
        if self._validation_pending:
            return
        from core.api_key_validator import normalize_gemini_api_key

        normalized = normalize_gemini_api_key(key)
        self._key_input.setText(normalized)
        self._validation_pending = True
        self._purge_saved_on_failure = purge_saved_on_failure
        self._init_btn.setEnabled(False)
        self._init_btn.setText("VERIFYING WITH GEMINI…")
        self._validation_lbl.setText("Contacting Gemini. The key will not be saved unless verification succeeds.")
        self._validation_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")

        def _validate():
            from core.api_key_validator import validate_gemini_api_key
            result = validate_gemini_api_key(normalized)
            self.validation_finished.emit(result.valid, result.message, normalized, remember_key)

        threading.Thread(target=_validate, daemon=True).start()

    def _on_validation_finished(self, valid: bool, message: str, key: str, remember_key: bool):
        self._validation_pending = False
        self._init_btn.setEnabled(True)
        self._init_btn.setText("▸  INITIALISE SYSTEMS")

        if not valid:
            if os.environ.get("GEMINI_API_KEY", "").strip() == key:
                os.environ.pop("GEMINI_API_KEY", None)
            self._validation_lbl.setText(message)
            self._validation_lbl.setStyleSheet(f"color: {C.RED}; background: transparent;")
            self._key_input.setStyleSheet(f"""
                QLineEdit {{
                    background: {C.DARK}; color: {C.TEXT};
                    border: 1px solid {C.RED}; border-radius: 3px; padding: 4px 8px;
                }}
                QLineEdit:focus {{ border: 1px solid {C.RED}; }}
            """)
            self._key_input.setFocus()
            return

        self._validation_lbl.setText("Gemini key verified.")
        self._validation_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        self._verified_key = key
        self.done.emit(key, self._sel_os, remember_key)


# ---------------------------------------------------------------------------
# Shared mixin: draggable + X-close button for all overlay widgets
# ---------------------------------------------------------------------------

class _OverlayBase(QWidget):
    """
    Base class for all JARVIS overlay popups.
    Provides:
      - Drag-to-move (click anywhere on the widget and drag)
      - An ✕ close button in the top-right corner
    Subclasses must call _setup_overlay_base() after building their layout,
    or call super().__init__() and use _add_close_btn(layout) manually.
    """

    def _setup_overlay_base(self, close_callback=None):
        """
        Call this once after the overlay's layout is fully built.
        Adds a floating ✕ button over the top-right corner.
        close_callback: callable to invoke on close (defaults to self.hide).
        """
        self._drag_pos = None
        self._close_cb = close_callback or self.hide

        btn = QPushButton("✕", self)
        btn.setFixedSize(22, 22)
        btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {C.TEXT_DIM};
                border: none;
                border-radius: 3px;
            }}
            QPushButton:hover {{
                color: {C.RED};
                background: rgba(255,51,85,15);
            }}
        """)
        btn.clicked.connect(self._close_cb)
        self._close_btn = btn
        self._reposition_close_btn()

    def _reposition_close_btn(self):
        if hasattr(self, "_close_btn"):
            self._close_btn.move(self.width() - 28, 6)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_close_btn()

    def paintEvent(self, event):
        super().paintEvent(event)
        from PyQt6.QtGui import QPainter, QPen, QColor
        from PyQt6.QtCore import QPointF
        p = QPainter(self)
        p.setRenderHint(p.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        bl = 16
        a = 60
        col = qcol(C.PRI, a)
        p.setPen(QPen(col, 1.2))
        for bx, by, dx, dy in [(0,0,1,1),(w,0,-1,1),(0,h,1,-1),(w,h,-1,-1)]:
            p.drawLine(QPointF(bx, by), QPointF(bx + dx * bl, by))
            p.drawLine(QPointF(bx, by), QPointF(bx, by + dy * bl))
        p.end()

    # ── Drag support ────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            new_pos = event.globalPosition().toPoint() - self._drag_pos
            # Clamp within parent bounds
            if self.parent():
                pr = self.parent().rect()
                new_pos.setX(max(0, min(new_pos.x(), pr.width()  - self.width())))
                new_pos.setY(max(0, min(new_pos.y(), pr.height() - self.height())))
            self.move(new_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------------------
# ShortcutsOverlay — keyboard shortcuts help panel
# ---------------------------------------------------------------------------

class ShortcutsOverlay(_OverlayBase):
    """Displays all keyboard shortcuts in a styled grid."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ShortcutsOverlay {{
                background: {C.BG};
                border: 1px solid {C.BORDER_B};
                border-radius: 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(8)

        def _lbl(txt, size=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont(UI_FONT, size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setWordWrap(True)
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  KEYBOARD SHORTCUTS", 13, True))
        layout.addSpacing(4)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};")
        layout.addWidget(sep)
        layout.addSpacing(6)

        shortcuts = [
            ("F4",       "Toggle Microphone Mute"),
            ("F6",       "Minimize / Restore Window"),
            ("F11",      "Toggle Fullscreen"),
            ("Ctrl+/",   "Show This Help Panel"),
            ("Ctrl+M",   "Toggle Compact Mode"),
            ("Ctrl+Shift+T", "Cycle Color Theme"),
            ("Enter",    "Send Command (in input)"),
            ("Esc",      "Close Overlay / Dismiss"),
        ]

        for key, desc in shortcuts:
            row = QHBoxLayout()
            row.setSpacing(10)

            key_lbl = QLabel(key)
            key_lbl.setFixedWidth(80)
            key_lbl.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            key_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            key_lbl.setStyleSheet(f"""
                color: {C.PRI}; background: {C.PRI_GHO};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                padding: 2px 6px;
            """)
            row.addWidget(key_lbl)

            desc_lbl = QLabel(desc)
            desc_lbl.setFont(QFont("Courier New", 9))
            desc_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
            row.addWidget(desc_lbl, stretch=1)

            layout.addLayout(row)

        layout.addSpacing(8)
        layout.addWidget(_lbl("Press Esc or Ctrl+/ to close", 7, color=C.TEXT_DIM))

        self._setup_overlay_base(close_callback=self.hide)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# SettingsOverlay — unified settings panel with tabs
# ---------------------------------------------------------------------------

class GraphicsQualityCard(QPushButton):
    """Compact, theme-aware graphics option used by Settings."""

    _COPY = {
        "low": ("LOW", "20 FPS", "· reduced detail"),
        "medium": ("MEDIUM", "30 FPS", "· balanced"),
        "high": ("HIGH", "60 FPS", "· full detail"),
    }

    def __init__(self, quality: str, parent=None):
        super().__init__(parent)
        self.quality = quality
        title, fps, detail = self._COPY[quality]
        self.setText("")
        self.setAccessibleName(f"{title.title()} graphics quality")
        self.setToolTip(f"{fps} {detail}")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(78)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)
        self._title = QLabel(title, self)
        self._title.setFont(QFont(UI_FONT, 9, QFont.Weight.DemiBold))
        self._title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self._title)

        data_row = QHBoxLayout()
        data_row.setContentsMargins(0, 0, 0, 0)
        data_row.setSpacing(4)
        self._fps = QLabel(fps, self)
        self._fps.setFont(QFont(TECH_FONT, 8, QFont.Weight.Medium))
        self._detail = QLabel(detail, self)
        self._detail.setFont(QFont(UI_FONT, 8, QFont.Weight.Normal))
        for label in (self._fps, self._detail):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            data_row.addWidget(label)
        data_row.addStretch(1)
        layout.addLayout(data_row)
        self.refresh_theme(False)

    def refresh_theme(self, selected: bool):
        if selected:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:1,
                        stop:0 {C.PRI_GHO}, stop:0.78 {C.DARK2}, stop:1 {C.PRI_GLOW}
                    );
                    border: 1px solid {C.PRI}; border-radius: 6px;
                }}
                QPushButton:hover {{ background: {C.PRI_GLOW}; }}
            """)
            self._title.setStyleSheet(f"color: {C.WHITE}; background: transparent; border: none;")
            self._fps.setStyleSheet(f"color: {C.PRI}; background: transparent; border: none;")
            self._detail.setStyleSheet(f"color: {C.WHITE_DIM}; background: transparent; border: none;")
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {C.DARK};
                    border: 1px solid {C.BORDER}; border-radius: 6px;
                }}
                QPushButton:hover {{ border-color: {C.BORDER_B}; background: {C.PANEL2}; }}
            """)
            self._title.setStyleSheet(f"color: {C.WHITE_DIM}; background: transparent; border: none;")
            self._fps.setStyleSheet(f"color: {C.WHITE_DIM}; background: transparent; border: none;")
            self._detail.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")


class SettingsOverlay(_OverlayBase):
    """Unified settings panel for identity, theme, and graphics."""

    voice_changed = pyqtSignal(str, str, str)  # provider, voice_id, api_key
    name_changed = pyqtSignal(str)
    theme_changed = pyqtSignal(str)
    graphics_changed = pyqtSignal(str)

    def __init__(self, parent=None, current_name: str = "",
                 current_voice: str = "puck", current_theme: str = "arc_reactor",
                 current_graphics: str = "medium"):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SettingsOverlay {{
                background: {C.BG};
                border: 1px solid {C.BORDER_B};
                border-radius: 8px;
            }}
        """)

        self._current_name = current_name
        self._current_voice = current_voice
        self._current_theme = current_theme
        self._current_graphics = _normalize_graphics_quality(current_graphics)
        self._theme_labels: list[tuple[QLabel, str]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(6)

        def _lbl(txt, size=9, bold=False, color=None, align=Qt.AlignmentFlag.AlignCenter,
                 color_role="PRI"):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            resolved = color if color is not None else getattr(C, color_role)
            w.setStyleSheet(f"color: {resolved}; background: transparent;")
            self._theme_labels.append((w, color_role))
            return w

        settings_title = _lbl("◈  SETTINGS", 13, True)
        settings_title_font = QFont(UI_FONT, 13, QFont.Weight.DemiBold)
        settings_title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        settings_title.setFont(settings_title_font)
        layout.addWidget(settings_title)
        layout.addSpacing(2)

        # Tab bar
        tab_bar = QWidget()
        tab_bar.setFixedHeight(26)
        tb_lay = QHBoxLayout(tab_bar)
        tb_lay.setContentsMargins(0, 0, 0, 0)
        tb_lay.setSpacing(4)

        self._s_tabs: list[QPushButton] = []
        self._s_tab_names = ["IDENTITY", "THEME", "GRAPHICS"]
        self._s_active_tab = 0

        for i, name in enumerate(self._s_tab_names):
            btn = QPushButton(name)
            btn.setFixedHeight(23)
            btn.setFont(QFont(UI_FONT, 8, QFont.Weight.Medium))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, idx=i: self._switch_s_tab(idx))
            self._s_tabs.append(btn)
            tb_lay.addWidget(btn)

        layout.addWidget(tab_bar)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};")
        layout.addWidget(sep)

        # Stacked pages
        from PyQt6.QtWidgets import QStackedWidget
        self._s_stack = QStackedWidget()
        self._s_stack.setStyleSheet("background: transparent;")
        layout.addWidget(self._s_stack, stretch=1)

        # Page 0: Identity
        id_page = QWidget()
        id_page.setStyleSheet("background: transparent;")
        id_lay = QVBoxLayout(id_page)
        id_lay.setContentsMargins(4, 8, 4, 4)
        id_lay.setSpacing(8)

        id_lay.addWidget(_lbl("◈  YOUR NAME", 9, bold=True, color_role="PRI",
                              align=Qt.AlignmentFlag.AlignLeft))
        self._s_name_input = QLineEdit()
        self._s_name_input.setText(current_name)
        self._s_name_input.setPlaceholderText("e.g. Tony, Mirsab, Alex...")
        self._s_name_input.setFont(QFont(UI_FONT, 10))
        self._s_name_input.setFixedHeight(32)
        self._s_name_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.DARK}; color: {C.WHITE};
                border: 1px solid {C.BORDER_B}; border-radius: 4px;
                padding: 4px 10px 4px 28px;
            }}
            QLineEdit:focus {{
                border: 1px solid {C.PRI};
                background: {C.DARK};
            }}
        """)
        id_lay.addWidget(self._s_name_input)

        save_name = QPushButton("▸  UPDATE IDENTITY")
        save_name.setFixedHeight(36)
        save_name.setFont(QFont(UI_FONT, 9, QFont.Weight.Medium))
        save_name.setCursor(Qt.CursorShape.PointingHandCursor)
        save_name.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI_GHO}; color: {C.PRI};
                border: 1px solid {C.PRI}; border-radius: 4px;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{
                background: {C.PRI}22;
                border: 1px solid {C.PRI};
                color: {C.ENERGY};
            }}
        """)
        save_name.clicked.connect(lambda: self.name_changed.emit(
            self._s_name_input.text().strip()))
        id_lay.addWidget(save_name)
        id_lay.addStretch()

        self._s_stack.addWidget(id_page)

        # Page 1: Theme
        th_page = QWidget()
        th_page.setStyleSheet("background: transparent;")
        th_lay = QVBoxLayout(th_page)
        th_lay.setContentsMargins(4, 8, 4, 4)
        th_lay.setSpacing(6)

        th_lay.addWidget(_lbl("COLOR THEME", 8, color_role="TEXT_DIM",
                              align=Qt.AlignmentFlag.AlignLeft))

        self._theme_btns: dict[str, QPushButton] = {}
        for key in ThemeManager.theme_names():
            display = ThemeManager.theme_display_name(key)
            btn = QPushButton(f"  {display}")
            btn.setFixedHeight(32)
            btn.setFont(QFont(UI_FONT, 9, QFont.Weight.Medium))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._select_theme(k))
            self._theme_btns[key] = btn
            th_lay.addWidget(btn)

        th_lay.addStretch()
        self._s_stack.addWidget(th_page)

        # Page 2: Graphics. The choices are intentionally limited to the three
        # clear modes users asked for; no opaque automatic mode is inserted.
        gfx_page = QWidget()
        gfx_page.setStyleSheet("background: transparent;")
        gfx_lay = QVBoxLayout(gfx_page)
        gfx_lay.setContentsMargins(4, 8, 4, 4)
        gfx_lay.setSpacing(10)
        gfx_lay.addWidget(_lbl(
            "GRAPHICS QUALITY", 8, bold=True, color_role="WHITE_DIM",
            align=Qt.AlignmentFlag.AlignLeft,
        ))
        gfx_lay.addWidget(_lbl(
            "Choose a performance profile. Changes apply instantly.",
            8, color_role="WHITE_DIM", align=Qt.AlignmentFlag.AlignLeft,
        ))

        gfx_row = QHBoxLayout()
        gfx_row.setSpacing(8)
        self._graphics_btns: dict[str, GraphicsQualityCard] = {}
        for quality in ("low", "medium", "high"):
            button = GraphicsQualityCard(quality)
            button.clicked.connect(lambda _, q=quality: self._select_graphics(q))
            self._graphics_btns[quality] = button
            gfx_row.addWidget(button, stretch=1)
        gfx_lay.addLayout(gfx_row)

        self._graphics_note = QLabel("")
        self._graphics_note.setFont(QFont(UI_FONT, 8))
        self._graphics_note.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        gfx_lay.addWidget(self._graphics_note)
        gfx_lay.addStretch()
        self._s_stack.addWidget(gfx_page)

        self._switch_s_tab(0)
        self._highlight_theme(current_theme)
        self._highlight_graphics(self._current_graphics)
        self._setup_overlay_base(close_callback=self.hide)

    def _switch_s_tab(self, idx: int):
        self._s_active_tab = idx
        self._s_stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._s_tabs):
            if i == idx:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.PRI_GHO}; color: {C.PRI};
                        border: none; border-bottom: 2px solid {C.PRI};
                        border-radius: 3px; padding: 0 8px;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent; color: {C.WHITE_DIM};
                        border: 1px solid {C.BORDER}44; border-radius: 3px; padding: 0 8px;
                    }}
                    QPushButton:hover {{ color: {C.PRI}; background: {C.PRI_GHO};
                                         border: 1px solid {C.BORDER_B}; }}
                """)

    def _select_theme(self, key: str):
        self._current_theme = key
        self.theme_changed.emit(key)
        self._highlight_theme(key)

    def _select_graphics(self, quality: str):
        value = _normalize_graphics_quality(quality)
        self._current_graphics = value
        self._highlight_graphics(value)
        self._graphics_note.setText(f"{value.upper()} quality active.")
        self.graphics_changed.emit(value)

    def _highlight_graphics(self, quality: str):
        value = _normalize_graphics_quality(quality)
        for key, button in self._graphics_btns.items():
            button.refresh_theme(key == value)

    def refresh_theme(self):
        self.setStyleSheet(f"""
            SettingsOverlay {{
                background: {C.BG}; border: 1px solid {C.BORDER_B}; border-radius: 8px;
            }}
        """)
        for label, color_role in self._theme_labels:
            label.setStyleSheet(
                f"color: {getattr(C, color_role, C.TEXT)}; background: transparent;"
            )
        self._s_name_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.DARK}; color: {C.WHITE};
                border: 1px solid {C.BORDER_B}; border-radius: 4px;
                padding: 4px 10px 4px 28px;
            }}
            QLineEdit:focus {{ border-color: {C.PRI}; background: {C.DARK}; }}
        """)
        self._switch_s_tab(self._s_active_tab)
        self._highlight_theme(self._current_theme)
        self._highlight_graphics(self._current_graphics)
        self._graphics_note.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")

    def _highlight_theme(self, key: str):
        for k, btn in self._theme_btns.items():
            if k == key:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.PRI}; color: {C.BG};
                        border: none; border-radius: 4px;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.DARK}; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 4px;
                    }}
                    QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.BORDER_B}; }}
                """)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        super().keyPressEvent(event)


class NameSignInOverlay(_OverlayBase):
    """Overlay that asks the user for their name so JARVIS can address them personally."""
    done = pyqtSignal(str)   # emits the entered name (or "" if skipped)

    def __init__(self, parent=None, existing_name: str = ""):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            NameSignInOverlay {{
                background: {C.BG};
                border: 1px solid {C.BORDER_B};
                border-radius: 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(10)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        title_txt = "◈  UPDATE IDENTITY" if existing_name else "◈  IDENTITY PROTOCOL"
        sub_txt   = f"Currently: {existing_name}" if existing_name else "JARVIS needs to know who it's talking to."
        layout.addWidget(_lbl(title_txt, 13, True))
        layout.addWidget(_lbl(sub_txt, 9, color=C.PRI_DIM))
        layout.addSpacing(4)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(6)

        layout.addWidget(_lbl("ENTER YOUR NAME", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("e.g.  Tony,  Mirsab,  Alex …")
        if existing_name:
            self._name_input.setText(existing_name)
            self._name_input.selectAll()
        self._name_input.setFont(QFont("Courier New", 11))
        self._name_input.setFixedHeight(36)
        self._name_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.DARK}; color: {C.WHITE};
                border: 1px solid {C.BORDER_B}; border-radius: 4px;
                padding: 4px 10px; letter-spacing: 1px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._name_input.returnPressed.connect(self._submit)
        layout.addWidget(self._name_input)

        layout.addSpacing(4)

        confirm_btn = QPushButton("▸  CONFIRM IDENTITY")
        confirm_btn.setFixedHeight(34)
        confirm_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        confirm_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI_GHO}; color: {C.PRI};
                border: 1px solid {C.PRI}; border-radius: 4px;
            }}
            QPushButton:hover {{ background: {C.DARK2}; border: 1px solid {C.PRI}; }}
        """)
        confirm_btn.clicked.connect(self._submit)
        layout.addWidget(confirm_btn)

        skip_btn = QPushButton("Skip — default to Sir")
        skip_btn.setFixedHeight(26)
        skip_btn.setFont(QFont("Courier New", 7))
        skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        skip_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT_MED}; border: 1px solid {C.BORDER_B}; }}
        """)
        skip_btn.clicked.connect(lambda: self.done.emit("Sir"))
        layout.addWidget(skip_btn)

        layout.addSpacing(4)
        layout.addWidget(_lbl(
            "Your name is stored locally and never sent to any server.",
            7, color=C.TEXT_DIM
        ))

        self._drag_pos = None
        self._setup_overlay_base(close_callback=self.hide)

    def _submit(self):
        name = self._name_input.text().strip()
        if not name:
            self._name_input.setStyleSheet(
                self._name_input.styleSheet()
                + f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(name)


class VoiceSelectOverlay(_OverlayBase):
    """Popup overlay for selecting JARVIS voice (used in first-run setup flow)."""
    done = pyqtSignal(str)   # emits selected voice value (e.g. "puck")

    def __init__(self, parent=None, current_voice: str = "puck"):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            VoiceSelectOverlay {{
                background: {C.BG};
                border: 1px solid {C.BORDER_B};
                border-radius: 8px;
            }}
        """)

        self._selected = current_voice.lower()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(10)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  VOICE SELECTION", 13, True))
        layout.addWidget(_lbl("Choose the voice JARVIS will speak with.", 9, color=C.PRI_DIM))
        layout.addSpacing(4)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        # Voice buttons grid — 3 columns
        self._voice_btns: dict[str, QPushButton] = {}
        grid = QHBoxLayout()
        col_layouts = [QVBoxLayout(), QVBoxLayout(), QVBoxLayout()]
        for col in col_layouts:
            col.setSpacing(6)

        for i, (label, value) in enumerate(VOICE_OPTIONS):
            btn = QPushButton(label)
            btn.setFixedHeight(32)
            btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, v=value: self._select(v))
            self._voice_btns[value] = btn
            col_layouts[i % 3].addWidget(btn)

        for col in col_layouts:
            col.addStretch()
            grid.addLayout(col)
        layout.addLayout(grid)

        layout.addSpacing(6)

        confirm_btn = QPushButton("▸  CONFIRM VOICE")
        confirm_btn.setFixedHeight(34)
        confirm_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        confirm_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI_GHO}; color: {C.PRI};
                border: 1px solid {C.PRI}; border-radius: 4px;
            }}
            QPushButton:hover {{ background: {C.DARK2}; }}
        """)
        confirm_btn.clicked.connect(lambda: self.done.emit(self._selected))
        layout.addWidget(confirm_btn)

        # Apply initial selection highlight
        self._select(self._selected)
        self._drag_pos = None
        self._setup_overlay_base(close_callback=self.hide)

    def _select(self, value: str):
        self._selected = value
        for v, btn in self._voice_btns.items():
            if v == value:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.PRI}; color: {C.BG};
                        border: none; border-radius: 4px;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.DARK}; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 4px;
                    }}
                    QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.BORDER_B}; }}
                """)


class KeyTutorialOverlay(_OverlayBase):
    """Slides in over VoiceSelectorOverlay when an external voice is chosen without a key."""

    # Emits the entered API key when saved
    saved = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, parent, provider: str, current_key: str = ""):
        super().__init__(parent)
        from actions.tts_engine import PROVIDER_TUTORIAL
        info = PROVIDER_TUTORIAL.get(provider, {})

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            KeyTutorialOverlay {{
                background: {C.BG};
                border: 1px solid {C.ACC};
                border-radius: 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(10)

        def _lbl(txt, size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignLeft):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        # Header
        title = info.get("title", f"{provider.upper()} API Key Setup")
        layout.addWidget(_lbl(f"◈  {title}", 11, True, C.ACC,
                              Qt.AlignmentFlag.AlignCenter))
        layout.addSpacing(2)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.ACC};"); layout.addWidget(sep)
        layout.addSpacing(4)

        # Steps
        for step in info.get("steps", []):
            layout.addWidget(_lbl(step, 9, color=C.WHITE))

        layout.addSpacing(6)

        # URL hint
        url = info.get("url", "")
        if url:
            layout.addWidget(_lbl(f"🔗  {url}", 8, color=C.PRI_DIM,
                                  align=Qt.AlignmentFlag.AlignCenter))

        layout.addSpacing(6)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)

        layout.addWidget(_lbl("API KEY", 8, bold=True, color=C.ACC))

        self._key_input = QLineEdit()
        self._key_input.setPlaceholderText("Paste your API key here…")
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setFont(QFont("Courier New", 9))
        self._key_input.setFixedHeight(32)
        self._key_input.setText(current_key)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.DARK}; color: {C.WHITE};
                border: 1px solid {C.ACC}; border-radius: 3px; padding: 3px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.ACC2}; }}
        """)
        layout.addWidget(self._key_input)

        layout.addSpacing(4)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        back_btn = QPushButton("← BACK")
        back_btn.setFixedHeight(30)
        back_btn.setFont(QFont("Courier New", 8))
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.BORDER_B}; }}
        """)
        back_btn.clicked.connect(self.cancelled.emit)
        btn_row.addWidget(back_btn)

        save_btn = QPushButton("▸  SAVE KEY & ACTIVATE")
        save_btn.setFixedHeight(30)
        save_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI_GHO}; color: {C.PRI};
                border: 1px solid {C.PRI}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.DARK2}; }}
        """)
        save_btn.clicked.connect(lambda: self.saved.emit(self._key_input.text().strip()))
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

        self._drag_pos = None
        self._setup_overlay_base(close_callback=self.cancelled.emit)


class VoiceSelectorOverlay(_OverlayBase):
    """
    Voice selector with three grouped sections:
      ── GEMINI ──        (no API key needed)
      ── OPENAI TTS ──    (API key required)
      ── ELEVENLABS ──    (API key required)

    Clicking an external voice without a saved key shows KeyTutorialOverlay.
    """

    # Emits (provider, voice_id, api_key)
    done = pyqtSignal(str, str, str)

    def __init__(self, parent=None,
                 current_provider: str = "gemini",
                 current_voice_id: str = "orus",
                 current_api_key:  str = ""):
        super().__init__(parent)
        from actions.tts_engine import PROVIDER_VOICES, EXTERNAL_PROVIDERS
        self._provider  = current_provider.lower()
        self._voice_id  = current_voice_id
        self._api_key   = current_api_key
        self._ext_providers = EXTERNAL_PROVIDERS

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            VoiceSelectorOverlay {{
                background: {C.BG};
                border: 1px solid {C.BORDER_B};
                border-radius: 8px;
            }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Main content (scrollable) ──────────────────────────────────────
        self._main = QWidget()
        self._main.setStyleSheet("background: transparent;")
        main_lay = QVBoxLayout(self._main)
        main_lay.setContentsMargins(26, 20, 26, 16)
        main_lay.setSpacing(8)

        def _lbl(txt, size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        main_lay.addWidget(_lbl("◈  SELECT VOICE", 13, True))
        main_lay.addWidget(_lbl("Default: Orus  ·  Gemini", 8, color=C.PRI_DIM))
        main_lay.addSpacing(2)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); main_lay.addWidget(sep)

        # Track all voice buttons: (provider, voice_id) → QPushButton
        self._voice_btns: dict[tuple[str, str], QPushButton] = {}

        SECTION_LABELS = {
            "gemini":     "── GEMINI ──",
        }
        SECTION_COLORS = {
            "gemini":     C.ENERGY,
        }
        SECTION_KEY_HINT = {
            "gemini":     "no key required",
        }

        for provider, voices in PROVIDER_VOICES.items():
            main_lay.addSpacing(4)
            # Section header
            sec_col = SECTION_COLORS.get(provider, C.PRI)
            key_hint = SECTION_KEY_HINT.get(provider, "")
            hdr_w = QWidget()
            hdr_w.setStyleSheet("background: transparent;")
            hdr_lay = QHBoxLayout(hdr_w)
            hdr_lay.setContentsMargins(0, 0, 0, 0)
            hdr_lay.setSpacing(8)
            # Color-coded left accent bar
            bar = QWidget()
            bar.setFixedWidth(3)
            bar.setStyleSheet(f"background: {sec_col}; border-radius: 1px;")
            hdr_lay.addWidget(bar)
            hdr_lay.addWidget(_lbl(SECTION_LABELS[provider], 8, bold=True,
                                   color=sec_col, align=Qt.AlignmentFlag.AlignLeft))
            if key_hint:
                hint_lbl = _lbl(key_hint, 6, color=C.TEXT_DIM,
                                align=Qt.AlignmentFlag.AlignLeft)
                hdr_lay.addWidget(hint_lbl)
            hdr_lay.addStretch()
            main_lay.addSpacing(12)
            main_lay.addWidget(hdr_w)
            main_lay.addSpacing(4)

            # Voice buttons — 3 per row
            row_lay: QHBoxLayout | None = None
            for i, (label, vid) in enumerate(voices):
                if i % 3 == 0:
                    row_lay = QHBoxLayout(); row_lay.setSpacing(5)
                    main_lay.addLayout(row_lay)

                btn = QPushButton(label)
                btn.setFixedHeight(28)
                btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(
                    lambda _, p=provider, v=vid: self._on_voice_clicked(p, v)
                )
                self._voice_btns[(provider, vid)] = btn
                row_lay.addWidget(btn)  # type: ignore[union-attr]

            # Pad last row if not full
            remainder = len(voices) % 3
            if remainder and row_lay:
                for _ in range(3 - remainder):
                    row_lay.addStretch()

        main_lay.addSpacing(6)

        
        # Confirm button
        main_lay.addSpacing(16)
        confirm_btn = QPushButton("▸  ACTIVATE VOICE")
        confirm_btn.setFixedHeight(36)
        confirm_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        confirm_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI_GHO}; color: {C.PRI};
                border: 1px solid {C.PRI}; border-radius: 4px;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{
                background: {C.PRI}22;
                border: 1px solid {C.PRI};
                color: {C.ENERGY};
            }}
        """)
        confirm_btn.clicked.connect(self._confirm)
        main_lay.addWidget(confirm_btn)

        # Wrap in scroll area so API key field is always reachable
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._main)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.verticalScrollBar().setStyleSheet(f"""
            QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 3px;
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
        """)
        outer.addWidget(scroll)

        # ── Tutorial overlay (hidden until needed) ─────────────────────────
        self._tutorial: KeyTutorialOverlay | None = None

        # Apply initial highlight
        self._highlight(self._provider, self._voice_id)
        pass  # key section removed

        self._drag_pos = None
        self._setup_overlay_base(close_callback=self.hide)

    # ------------------------------------------------------------------

    def _on_voice_clicked(self, provider: str, voice_id: str):
        self._provider = provider
        self._voice_id = voice_id
        self._highlight(provider, voice_id)
        pass  # key section removed

    def _highlight(self, provider: str, voice_id: str):
        for (p, v), btn in self._voice_btns.items():
            active = (p == provider and v == voice_id)
            if active:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.PRI}; color: {C.BG};
                        border: none; border-radius: 4px;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.DARK}; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}55; border-radius: 4px;
                        border-top: 1px solid {C.BORDER};
                    }}
                    QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.BORDER_B};
                                        border-top: 1px solid {C.PRI}; }}
                """)

    def _confirm(self):
        if self._provider in self._ext_providers:
            key = self._key_input.text().strip()
            if not key:
                # No key — show tutorial overlay
                self._show_tutorial()
                return
            self._api_key = key
        self.done.emit(self._provider, self._voice_id, self._api_key)

    def _show_tutorial(self):
        if self._tutorial:
            self._tutorial.deleteLater()
        tut = KeyTutorialOverlay(self, self._provider, self._api_key)
        tut.setGeometry(0, 0, self.width(), self.height())
        tut.saved.connect(self._on_tutorial_saved)
        tut.cancelled.connect(self._on_tutorial_cancelled)
        tut.show()
        self._tutorial = tut

    def _on_tutorial_saved(self, key: str):
        if self._tutorial:
            self._tutorial.hide()
            self._tutorial = None
        if key:
            self._api_key = key
            self._key_input.setText(key)
            self.done.emit(self._provider, self._voice_id, self._api_key)

    def _on_tutorial_cancelled(self):
        if self._tutorial:
            self._tutorial.hide()
            self._tutorial = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._tutorial:
            self._tutorial.setGeometry(0, 0, self.width(), self.height())


# Aliases so existing references keep working
VoicePresetOverlay = VoiceSelectorOverlay
TTSProviderOverlay = VoiceSelectorOverlay


def _minimize_or_restore(win: QMainWindow):
    """Minimize or restore the window.

    Requirement: first press minimizes; second press restores.
    """
    if win.isMinimized():
        win.showNormal()
        win.raise_()
        win.activateWindow()
        return

    win.showMinimized()


from core.secret_store import get_secret_store


class _SubtitleWidget(QWidget):
    """Centered, scrolling subtitle display with morph fade-out."""

    _MAX_VISIBLE = 5          # max visible lines in viewport
    _HOLD_MS = 5000           # hold after last chunk before fade starts
    _FADE_MS = 800            # duration of the dissolve animation
    _LINE_H = 22
    _SCROLL_SPEED = 0.18      # lerp factor per frame for smooth scroll

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setMinimumHeight(80)
        self.setMaximumHeight(220)

        self._chunks: list[list[str]] = []
        self._newest_idx = -1

        # Smooth scroll state
        self._scroll_y = 0.0          # current scroll offset (pixels)
        self._scroll_target = 0.0     # target scroll offset
        self._auto_scroll = True      # follow latest text automatically
        self._scroll_max = 0.0        # max scroll range

        # Fade-out state
        self._opacity = 1.0
        self._fading = False
        self._fade_start = 0.0

        # Hold timer — fires after 5s of silence, starts the fade
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._begin_fade)

        # Animation timer (60fps) — drives scroll + fade
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._anim_tick)

        self.setMinimumHeight(80)
        self.setMaximumHeight(220)
        self.setStyleSheet("background: transparent;")

        self._font = QFont(UI_FONT, 13, QFont.Weight.Medium)
        self._done_col = qcol(C.TEXT)
        self._active_col = qcol(C.PRI)
        self._line_h = self._LINE_H

    def set_text(self, text: str):
        """Append a new transcription chunk — shown immediately."""
        text = (text or "").strip()
        if not text:
            return
        words = text.split()
        if not words:
            return

        # If we were fading, cancel and restore
        if self._fading:
            self._fading = False
            self._opacity = 1.0

        self._newest_idx = len(self._chunks)
        self._chunks.append(words)

        # Recalculate scroll target (respects _auto_scroll flag)
        self._recalc_scroll_target()

        # Stop any pending hold timer — it will be restarted by start_hold_timer()
        # which is called externally only when JARVIS finishes talking (turn_complete).
        self._hold_timer.stop()

        # Ensure animation timer is running for scroll
        if not self._anim_timer.isActive():
            self._anim_timer.start()

        self.update()

    def start_hold_timer(self):
        """Start (or restart) the fade-out hold timer. Call this when JARVIS finishes speaking."""
        if self._chunks:
            self._hold_timer.stop()
            self._hold_timer.start(self._HOLD_MS)

    def clear_subtitle(self):
        """Immediately clear everything."""
        self._hold_timer.stop()
        self._anim_timer.stop()
        self._chunks.clear()
        self._newest_idx = -1
        self._scroll_y = 0.0
        self._scroll_target = 0.0
        self._scroll_max = 0.0
        self._auto_scroll = True
        self._opacity = 1.0
        self._fading = False
        self.update()

    def _recalc_scroll_target(self):
        """Calculate how far we need to scroll to keep latest lines visible."""
        total_lines = self._build_line_count()
        visible_h = self.rect().adjusted(12, 4, -12, -4).height()
        max_visible = max(1, int(visible_h / self._line_h))
        if total_lines > max_visible:
            self._scroll_max = float((total_lines - max_visible) * self._line_h)
        else:
            self._scroll_max = 0.0
        # Only auto-follow if user hasn't manually scrolled
        if self._auto_scroll:
            self._scroll_target = self._scroll_max
        else:
            # Clamp user's position to new max without jumping to bottom
            self._scroll_target = min(self._scroll_target, self._scroll_max)

    def wheelEvent(self, event):
        """Manual scroll with mouse wheel / trackpad."""
        if not self._chunks or self._scroll_max <= 0:
            return
        delta = event.angleDelta().y()
        # Scroll up = positive delta, scroll down = negative
        step = self._line_h
        if delta > 0:
            self._scroll_target = max(0.0, self._scroll_target - step)
            self._auto_scroll = False
        else:
            self._scroll_target = min(self._scroll_max, self._scroll_target + step)
            # If scrolled back to bottom, re-enable auto-scroll
            if self._scroll_target >= self._scroll_max:
                self._auto_scroll = True

        if not self._anim_timer.isActive():
            self._anim_timer.start()
        event.accept()

    def _build_line_count(self) -> int:
        """Count wrapped lines using the same logic as paintEvent."""
        from PyQt6.QtGui import QFontMetrics
        fm = QFontMetrics(self._font)
        max_w = self.rect().adjusted(12, 4, -12, -4).width() - 16
        if max_w <= 0:
            return 0
        space_w = fm.horizontalAdvance(" ")
        count = 0
        line_w = 0
        line_has_words = False
        for chunk in self._chunks:
            for w in chunk:
                w_width = fm.horizontalAdvance(w)
                needed = w_width + (space_w if line_has_words else 0)
                if line_has_words and (line_w + needed) > max_w:
                    count += 1
                    line_w = w_width
                    line_has_words = True
                else:
                    line_w += needed
                    line_has_words = True
        if line_has_words:
            count += 1
        return count

    def _begin_fade(self):
        """Start the morph dissolve animation."""
        self._fading = True
        self._fade_start = time.time()
        if not self._anim_timer.isActive():
            self._anim_timer.start()

    def _anim_tick(self):
        """Drive smooth scroll and fade-out at 60fps."""
        needs_update = False

        # Smooth scroll interpolation
        diff = self._scroll_target - self._scroll_y
        if abs(diff) > 0.5:
            self._scroll_y += diff * self._SCROLL_SPEED
            needs_update = True
        elif abs(diff) > 0.01:
            self._scroll_y = self._scroll_target
            needs_update = True

        # Fade-out animation
        if self._fading:
            elapsed = (time.time() - self._fade_start) * 1000.0
            progress = min(1.0, elapsed / self._FADE_MS)
            # Ease-out cubic
            t = 1.0 - progress
            self._opacity = t * t * t
            needs_update = True

            if progress >= 1.0:
                # Fade complete — clear everything
                self._anim_timer.stop()
                self._chunks.clear()
                self._newest_idx = -1
                self._scroll_y = 0.0
                self._scroll_target = 0.0
                self._opacity = 1.0
                self._fading = False
                self.update()
                return

        if needs_update:
            self.update()
        else:
            # Nothing to animate — stop timer to save CPU
            if not self._fading:
                self._anim_timer.stop()

    def paintEvent(self, _):
        if not self._chunks:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setOpacity(self._opacity)

        rect = self.rect().adjusted(12, 4, -12, -4)

        # Semi-transparent background panel for readability
        _bg_col = QColor(0, 5, 12, int(160 * self._opacity))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_bg_col))
        _r = self.rect().adjusted(4, 2, -4, -2)
        _rr = 6
        p.drawRoundedRect(_r, _rr, _rr)

        # Semi-transparent background panel for readability
        _bg_col = QColor(0, 5, 12, int(160 * self._opacity))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_bg_col))
        _r = self.rect().adjusted(4, 2, -4, -2)
        _rr = 6
        p.drawRoundedRect(_r, _rr, _rr)
        p.setFont(self._font)
        fm = p.fontMetrics()
        max_w = rect.width() - 16
        space_w = fm.horizontalAdvance(" ")

        # Build all lines: list of (word, color) tuples
        all_lines: list[list[tuple[str, QColor]]] = []
        line: list[tuple[str, QColor]] = []
        line_w = 0

        for ci, chunk in enumerate(self._chunks):
            col = self._active_col if ci == self._newest_idx else self._done_col
            for w in chunk:
                w_width = fm.horizontalAdvance(w)
                needed = w_width + (space_w if line else 0)

                if line and (line_w + needed) > max_w:
                    all_lines.append(line)
                    line = []
                    line_w = 0

                line.append((w, col))
                if line_w > 0:
                    line_w += space_w + w_width
                else:
                    line_w = w_width

        if line:
            all_lines.append(line)

        if not all_lines:
            return

        # Clip to widget area
        p.setClipRect(rect)

        # Draw all lines with scroll offset applied
        base_y = rect.top() + fm.ascent() + 2 - self._scroll_y

        for row_idx, row in enumerate(all_lines):
            y = base_y + row_idx * self._line_h

            # Skip lines that are scrolled out of view
            if y < rect.top() - self._line_h or y > rect.bottom() + self._line_h:
                continue

            total_line_w = sum(fm.horizontalAdvance(w) for w, _ in row) + space_w * max(0, len(row) - 1)
            x = rect.left() + (rect.width() - total_line_w) / 2

            for w, col in row:
                # Glow for active (newest) line
                if col == self._active_col:
                    _gc = QColor(col); _gc.setAlpha(50)
                    for _ox, _oy in [(-1,0),(1,0),(0,-1),(0,1)]:
                        p.setPen(QPen(_gc))
                        p.drawText(QPointF(x + _ox, y + _oy), w)
                if col == self._active_col:
                    _gc = QColor(col); _gc.setAlpha(50)
                    for _ox, _oy in [(-1,0),(1,0),(0,-1),(0,1)]:
                        p.setPen(QPen(_gc))
                        p.drawText(QPointF(x + _ox, y + _oy), w)
                p.setPen(QPen(col))
                p.drawText(QPointF(x, y), w)
                x += fm.horizontalAdvance(w) + space_w


class VisionPreviewWindow(QWidget):
    """Small draggable live preview shown while JARVIS is using vision."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("visionPreview")
        self.setAccessibleName("JARVIS live vision preview")
        self.setFixedSize(360, 248)
        self._source = "screen"
        self._drag_origin_global = None
        self._drag_origin_pos = None
        self._has_user_position = False
        self._camera = None
        self._cv2 = None
        self._screen_capture = None
        self._screen_monitor = None
        self._graphics_quality = "medium"

        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        self._header = QWidget(self)
        self._header.setObjectName("visionPreviewHeader")
        self._header.setFixedHeight(38)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(12, 0, 8, 0)
        header_layout.setSpacing(8)

        self._live_dot = QLabel("●", self._header)
        self._live_dot.setFont(QFont(TECH_FONT, 8, QFont.Weight.Medium))
        self._title = QLabel("VISION LINK", self._header)
        self._title.setFont(QFont(UI_FONT, 9, QFont.Weight.DemiBold))
        self._source_label = QLabel("SCREEN FEED", self._header)
        self._source_label.setFont(QFont(TECH_FONT, 7, QFont.Weight.Medium))
        header_layout.addWidget(self._live_dot)
        header_layout.addWidget(self._title)
        header_layout.addStretch(1)
        header_layout.addWidget(self._source_label)

        self._close_button = QPushButton("×", self._header)
        self._close_button.setAccessibleName("Close vision preview")
        self._close_button.setToolTip("Close live preview")
        self._close_button.setFixedSize(24, 24)
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.clicked.connect(self.stop)
        header_layout.addWidget(self._close_button)
        root.addWidget(self._header)

        self._frame = QLabel("INITIALIZING VISION LINK", self)
        self._frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._frame.setFont(QFont(TECH_FONT, 8, QFont.Weight.Medium))
        self._frame.setMinimumHeight(174)
        self._frame.setAccessibleName("Live vision image")
        root.addWidget(self._frame, stretch=1)

        self._status = QLabel("LIVE // ANALYZING", self)
        self._status.setFixedHeight(32)
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setFont(QFont(TECH_FONT, 7, QFont.Weight.Medium))
        root.addWidget(self._status)

        for draggable in (self._header, self._live_dot, self._title, self._source_label):
            draggable.installEventFilter(self)
        if parent is not None:
            parent.installEventFilter(self)

        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._update_frame)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.stop)
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)
        self._fade = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade.setDuration(180)
        self._fade.setEasingCurve(QEasingCurve.Type.OutQuart)
        self.refresh_theme()
        self.set_graphics_quality(get_graphics_quality())

    def refresh_theme(self):
        self.setStyleSheet(f"""
            QWidget#visionPreview {{
                background: {C.BG}; border: 1px solid {C.BORDER_B}; border-radius: 7px;
            }}
            QWidget#visionPreviewHeader {{
                background: {C.PANEL2}; border: none; border-bottom: 1px solid {C.BORDER};
            }}
        """)
        self._live_dot.setStyleSheet(f"color: {C.PRI}; background: transparent; border: none;")
        self._title.setStyleSheet(f"color: {C.WHITE}; background: transparent; border: none;")
        self._source_label.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        self._frame.setStyleSheet(f"color: {C.TEXT_DIM}; background: {C.DARK}; border: none;")
        self._status.setStyleSheet(
            f"color: {C.TEXT_MED}; background: {C.PANEL}; border: none; border-top: 1px solid {C.BORDER};"
        )
        self._close_button.setStyleSheet(f"""
            QPushButton {{
                color: {C.WHITE_DIM}; background: transparent; border: none; border-radius: 4px;
                font-family: '{UI_FONT}'; font-size: 15px;
            }}
            QPushButton:hover {{ color: {C.WHITE}; background: {C.RED_BG}; }}
        """)

    def set_graphics_quality(self, quality: str):
        self._graphics_quality = _normalize_graphics_quality(quality)
        profile_ms = int(GRAPHICS_PROFILES[self._graphics_quality]["frame_ms"])
        self._frame_timer.setInterval(max(42, profile_ms * 2))

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._clamp_to_parent)
            return False
        if watched in (self._header, self._live_dot, self._title, self._source_label):
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_origin_global = event.globalPosition().toPoint()
                self._drag_origin_pos = self.pos()
                return True
            if event.type() == QEvent.Type.MouseMove and self._drag_origin_global is not None:
                if event.buttons() & Qt.MouseButton.LeftButton:
                    delta = event.globalPosition().toPoint() - self._drag_origin_global
                    self.move(self._bounded_position(self._drag_origin_pos + delta))
                    self._has_user_position = True
                    return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._drag_origin_global = None
                self._drag_origin_pos = None
                return True
        return super().eventFilter(watched, event)

    def _bounded_position(self, position):
        parent = self.parentWidget()
        if parent is None:
            return position
        margin = 12
        max_x = max(margin, parent.width() - self.width() - margin)
        max_y = max(margin, parent.height() - self.height() - margin)
        return type(position)(
            max(margin, min(position.x(), max_x)),
            max(margin, min(position.y(), max_y)),
        )

    def _clamp_to_parent(self):
        if self.parentWidget() is not None:
            self.move(self._bounded_position(self.pos()))

    def start(self, source: str):
        self._hide_timer.stop()
        self._release_source()
        self._source = "camera" if str(source).lower().strip() == "camera" else "screen"
        self._source_label.setText(f"{self._source.upper()} FEED")
        self._status.setText("LIVE // ANALYZING")
        self._frame.setText("INITIALIZING VISION LINK")
        self._frame.setPixmap(QPixmap())

        try:
            self._open_source()
            self._frame_timer.start()
            self._update_frame()
        except Exception as exc:
            self._show_error(str(exc))

        if not self.isVisible():
            parent = self.parentWidget()
            if parent is not None and not self._has_user_position:
                self.move(self._bounded_position(
                    QPointF(parent.width() - self.width() - 24, 68).toPoint()
                ))
            else:
                self._clamp_to_parent()
            self._opacity_effect.setOpacity(0.0)
            self.show()
            self.raise_()
            self._fade.stop()
            self._fade.setStartValue(0.0)
            self._fade.setEndValue(1.0)
            self._fade.start()
        else:
            self._clamp_to_parent()
            self.raise_()

    def finish(self, delay_ms: int = 1800):
        self._status.setText("ANALYSIS COMPLETE")
        self._hide_timer.start(max(0, int(delay_ms)))

    def stop(self):
        self._hide_timer.stop()
        self._frame_timer.stop()
        self._release_source()
        self._opacity_effect.setOpacity(1.0)
        self.hide()

    def _camera_index(self) -> int:
        try:
            data = json.loads(API_FILE.read_text(encoding="utf-8")) if API_FILE.exists() else {}
            return int(data.get("camera_index", 0))
        except Exception:
            return 0

    def _open_source(self):
        if self._source == "camera":
            import cv2
            self._cv2 = cv2
            backend = cv2.CAP_AVFOUNDATION if _OS == "Darwin" else (
                cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            )
            self._camera = cv2.VideoCapture(self._camera_index(), backend)
            if not self._camera.isOpened():
                self._camera.release()
                self._camera = None
                raise RuntimeError("CAMERA FEED UNAVAILABLE")
        else:
            import mss
            self._screen_capture = mss.mss()
            monitors = self._screen_capture.monitors
            self._screen_monitor = monitors[1] if len(monitors) > 1 else monitors[0]

    def _update_frame(self):
        try:
            import numpy as np
            if self._source == "camera":
                if self._camera is None:
                    return
                ok, frame = self._camera.read()
                if not ok or frame is None:
                    raise RuntimeError("CAMERA SIGNAL LOST")
                rgb = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB)
            else:
                if self._screen_capture is None or self._screen_monitor is None:
                    return
                shot = self._screen_capture.grab(self._screen_monitor)
                bgra = np.asarray(shot)
                rgb = bgra[:, :, :3][:, :, ::-1].copy()

            height, width = rgb.shape[:2]
            image = QImage(rgb.data, width, height, width * 3, QImage.Format.Format_RGB888).copy()
            pixmap = QPixmap.fromImage(image).scaled(
                self._frame.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._frame.setPixmap(pixmap)
        except Exception as exc:
            self._show_error(str(exc))

    def _show_error(self, message: str):
        self._frame_timer.stop()
        self._frame.setPixmap(QPixmap())
        self._frame.setText("VISION LINK UNAVAILABLE")
        self._status.setText(str(message or "FEED ERROR").upper()[:52])

    def _release_source(self):
        if self._camera is not None:
            try:
                self._camera.release()
            except Exception:
                pass
        self._camera = None
        self._cv2 = None
        if self._screen_capture is not None:
            try:
                self._screen_capture.close()
            except Exception:
                pass
        self._screen_capture = None
        self._screen_monitor = None

    def closeEvent(self, event):
        self._frame_timer.stop()
        self._release_source()
        event.accept()


class MainWindow(QMainWindow):



    _log_sig       = pyqtSignal(str)
    _state_sig     = pyqtSignal(str)
    _voice_sig     = pyqtSignal(str)
    _sub_sig       = pyqtSignal(str)
    _sub_clear_sig = pyqtSignal()
    _sub_hold_sig  = pyqtSignal()
    _mode_sig      = pyqtSignal(str)          # context mode for AIActivityCanvas
    _task_sig      = pyqtSignal(str, str)     # (task_name, status) for TaskQueueWidget
    _tool_sig      = pyqtSignal(str)          # tool log line for ToolLogWidget
    _theme_sig     = pyqtSignal(str)
    _graphics_sig  = pyqtSignal(str)
    _vision_preview_sig = pyqtSignal(str)
    _vision_preview_hide_sig = pyqtSignal(int)
    _research_progress_sig = pyqtSignal(object)
    _research_progress_finish_sig = pyqtSignal(str, str)
    _research_progress_hide_sig = pyqtSignal()
    _presentation_progress_sig = pyqtSignal(object)
    _presentation_progress_finish_sig = pyqtSignal(str, str)
    _presentation_progress_hide_sig = pyqtSignal()
    _ui_command_sig = pyqtSignal(str)

    def _restore_detached_panels(self) -> None:
        """Compatibility hook for persisted panel layouts."""

    def _start_layout_autosave(self) -> None:
        """Compatibility hook for layout persistence."""

    def _start_auto_graphics_detection(self) -> None:
        """Compatibility hook for deferred capability detection."""

    def __init__(self, face_path: str):
        super().__init__()
        _load_bundled_fonts()
        self.setWindowTitle("J.A.R.V.I.S — MARK XXXIX")
        self.setMinimumSize(_MIN_W, _MIN_H)

        # Set dark palette so no white leaks through any unstyled widget
        from PyQt6.QtGui import QColor, QPalette
        _pal = self.palette()
        _pal.setColor(QPalette.ColorRole.Window, QColor(C.BG))
        _pal.setColor(QPalette.ColorRole.WindowText, QColor(C.WHITE))
        _pal.setColor(QPalette.ColorRole.Base, QColor(C.DARK))
        _pal.setColor(QPalette.ColorRole.AlternateBase, QColor(C.DARK2))
        _pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(C.DARK2))
        _pal.setColor(QPalette.ColorRole.ToolTipText, QColor(C.WHITE))
        _pal.setColor(QPalette.ColorRole.Text, QColor(C.WHITE))
        _pal.setColor(QPalette.ColorRole.Button, QColor(C.DARK2))
        _pal.setColor(QPalette.ColorRole.ButtonText, QColor(C.WHITE))
        _pal.setColor(QPalette.ColorRole.BrightText, QColor(C.WHITE))
        _pal.setColor(QPalette.ColorRole.Highlight, QColor(C.PRI))
        _pal.setColor(QPalette.ColorRole.HighlightedText, QColor(C.BG))
        self.setPalette(_pal)

        screen = QApplication.primaryScreen().availableGeometry()
        window_w = min(_DEFAULT_W, max(_MIN_W, screen.width()))
        window_h = min(_DEFAULT_H, max(_MIN_H, screen.height()))
        self.resize(window_w, window_h)
        self.move(
            screen.x() + max(0, (screen.width() - window_w) // 2),
            screen.y() + max(0, (screen.height() - window_h) // 2),
        )

        self.on_text_command        = None
        self.on_voice_change        = None
        self.on_name_change         = None
        self.on_tts_provider_change = None
        self.on_quit_requested      = None
        self._muted                 = False
        self._current_file: str | None = None
        self._tts_overlay: TTSProviderOverlay | None = None
        self._compact_mode          = False
        self._compact_widget: CompactModeWidget | None = None
        self._shortcuts_overlay: ShortcutsOverlay | None = None
        self._settings_overlay: SettingsOverlay | None = None
        self._vision_preview: VisionPreviewWindow | None = None
        self._force_quit            = False
        self._command_center_open   = False
        self._graphics_quality      = get_graphics_quality()

        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {C.BG}; }}
            QTextEdit, QPlainTextEdit, QTextBrowser {{
                background: {C.DARK};
                color: {C.WHITE};
                border: none;
            }}
        """)
        central = QWidget()
        central.setObjectName("jarvisRoot")
        central.setStyleSheet(f"background: {C.BG};")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setSpacing(0)
        self._header = self._build_header()
        root.addWidget(self._header)
        self._style_header()

        # Create left and right panels (we need to keep references for popup access)
        self._left_panel = self._build_left_panel()
        self._right_panel = self._build_right_panel()

        # ── AI Core area: HudCanvas, AI Activity Canvas, Subtitles ─────────────────────
        self._ai_core_wrap = QWidget()
        self._ai_core_wrap.setObjectName("aiCore")
        self._ai_core_wrap.setStyleSheet(f"background: {C.BG};")
        ai_core_lay = QVBoxLayout(self._ai_core_wrap)
        ai_core_lay.setContentsMargins(0, 0, 0, 0)
        ai_core_lay.setSpacing(6)

        # Create configuration objects
        hud_config = HudConfig()
        ai_config = AIActivityConfig()

        self.hud = HudCanvas(face_path, config=hud_config)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        ai_core_lay.addWidget(self.hud, stretch=4)

        self._research_progress = ResearchProgressWidget(parent=self._ai_core_wrap)
        ai_core_lay.addWidget(self._research_progress, stretch=0)

        self._presentation_progress = ResearchProgressWidget(
            parent=self._ai_core_wrap,
            task_title="PRESENTATION",
            accessible_name="Presentation task progress",
        )
        ai_core_lay.addWidget(self._presentation_progress, stretch=0)

        # AI Activity Canvas removed for cleaner layout
        self._ai_canvas = AIActivityCanvas(config=ai_config)
        self._ai_canvas.hide()

        # Subtitles — enhanced with speaker labels
        self._subtitle = _SubtitleWidget(parent=self._ai_core_wrap)
        self._subtitle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        ai_core_lay.addWidget(self._subtitle, stretch=0)

        self._focus_dialogue = FocusDialogueWidget(parent=self._ai_core_wrap)
        self._focus_dialogue.command_submitted.connect(self._send)
        self._chat_bubble._sig.connect(self._focus_dialogue.append_log)
        ai_core_lay.addWidget(self._focus_dialogue, stretch=0)

        # Create middle section with left panel, AI core, and right panel
        middle_section = QWidget()
        middle_section.setStyleSheet(f"background: {C.BG};")
        middle_layout = QHBoxLayout(middle_section)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(0)
        middle_layout.addWidget(self._left_panel)
        middle_layout.addWidget(self._ai_core_wrap, stretch=1)
        middle_layout.addWidget(self._right_panel)

        # Replace static layout with QSplitter for draggable panels
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._left_panel)
        self._splitter.addWidget(self._ai_core_wrap)
        self._splitter.addWidget(self._right_panel)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setStretchFactor(2, 0)
        self._splitter.setSizes([150, 980, 350])
        self._splitter.setCollapsible(0, True)
        self._splitter.setCollapsible(1, False)
        self._splitter.setCollapsible(2, True)
        self._splitter.setHandleWidth(6)
        self._style_splitter()

        middle_section2 = QWidget()
        self._middle_section = middle_section2
        middle_section2.setStyleSheet(f"background: {C.BG};")
        middle_layout2 = QHBoxLayout(middle_section2)
        middle_layout2.setContentsMargins(0, 0, 0, 0)
        middle_layout2.setSpacing(0)
        middle_layout2.addWidget(self._splitter)

        # Add middle section to main layout
        root.addWidget(middle_section2, stretch=1)
        # ── Tool progress indicator (above footer) ──────────────────────────
        self._tool_progress = ToolProgressWidget()
        root.addWidget(self._tool_progress)

        self._command_bar = self._build_footer()
        root.addWidget(self._command_bar)

        # Persistent maker's mark: remains visible in both Focus View and the
        # expanded Command Center without reading as application status.
        self._maker_signature = self._build_maker_signature()
        root.addWidget(self._maker_signature)
        self._set_command_center(False, announce=False)

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metric update timer
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(int(GRAPHICS_PROFILES[self._graphics_quality]["metrics_ms"]))
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._voice_sig.connect(self._sync_voice_combo)
        self._sub_sig.connect(self._subtitle.set_text)
        self._sub_clear_sig.connect(self._subtitle.clear_subtitle)
        self._sub_hold_sig.connect(self._subtitle.start_hold_timer)
        self._mode_sig.connect(self._ai_canvas.set_mode)
        self._task_sig.connect(self._mission.task_widget.push_task)
        self._tool_sig.connect(self._mission.tool_widget.push)
        self._theme_sig.connect(ThemeManager.set_theme)
        self._graphics_sig.connect(self._apply_graphics_quality_live)
        self._vision_preview_sig.connect(self._show_vision_preview)
        self._vision_preview_hide_sig.connect(self._hide_vision_preview)
        self._research_progress_sig.connect(lambda update: self._research_progress.update_progress(**update))
        self._research_progress_finish_sig.connect(self._research_progress.finish)
        self._research_progress_hide_sig.connect(self._research_progress.dismiss)
        self._presentation_progress_sig.connect(
            lambda update: self._presentation_progress.update_progress(**update)
        )
        self._presentation_progress_finish_sig.connect(self._presentation_progress.finish)
        self._presentation_progress_hide_sig.connect(self._presentation_progress.dismiss)
        self._ui_command_sig.connect(self._handle_ui_command)


        # ── Popup System Initialization ────────────────────────────────────────
        self._popup_manager = PopupManager(self._ai_core_wrap)
        self._presence_system = PresenceSystem(self._popup_manager)
        # Context mode tracking
        self._context_mode = "idle"
        self._session_start = time.time()

        self._overlay: SetupOverlay | None = None
        self._name_overlay: NameSignInOverlay | None = None
        self._voice_overlay: VoiceSelectOverlay | None = None
        self._tts_overlay: TTSProviderOverlay | None = None
        self.on_voice_change = None
        self.on_tts_provider_change = None
        self._load_saved_voice()
        self._load_saved_tts()

        # ── System tray integration ──────────────────────────────────────────
        self._setup_system_tray()

        # ── Theme manager listener ───────────────────────────────────────────
        ThemeManager.add_listener(self._on_theme_changed)
        # Load saved theme on boot
        try:
            from pathlib import Path
            import json
            cfg_file = Path.home() / ".jarvis" / "config" / "settings.json"
            if cfg_file.exists():
                cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
                saved_theme = cfg.get("theme", "")
                if saved_theme and saved_theme in ThemeManager.theme_names():
                    ThemeManager.set_theme(saved_theme)
        except Exception:
            pass

        # Every key source uses the same Gemini verification gate. Environment
        # variables and remembered keys are never trusted merely because they
        # were present on an earlier run.
        self._ready = False
        candidate_key = os.environ.get("GEMINI_API_KEY", "").strip()
        candidate_is_saved = False
        try:
            if not candidate_key:
                store = get_secret_store()
                saved = store.get("gemini_api_key")
                if saved:
                    candidate_key = saved.strip()
                    candidate_is_saved = True
        except Exception:
            candidate_key = ""
            candidate_is_saved = False

        self._show_setup()
        if candidate_key and self._overlay:
            self._overlay.validate_candidate(
                candidate_key,
                remember_key=candidate_is_saved,
                purge_saved_on_failure=candidate_is_saved,
            )


        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_min = QShortcut(QKeySequence(Qt.Key.Key_F6), self)
        sc_min.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_min.activated.connect(lambda: _minimize_or_restore(self))

        # New shortcuts
        sc_help = QShortcut(QKeySequence("Ctrl+/"), self)
        sc_help.activated.connect(self._toggle_shortcuts_overlay)
        sc_compact = QShortcut(QKeySequence("Ctrl+M"), self)
        sc_compact.activated.connect(self._toggle_compact_mode)
        sc_theme = QShortcut(QKeySequence("Ctrl+Shift+T"), self)
        sc_theme.activated.connect(self._cycle_theme)
        sc_command = QShortcut(QKeySequence("Ctrl+Shift+C"), self)
        sc_command.activated.connect(
            lambda: self._set_command_center(not self._command_center_open)
        )
        sc_esc = QShortcut(QKeySequence("Escape"), self)
        sc_esc.activated.connect(self._dismiss_overlays)



        # Shortcuts to show panel content as popups
        sc_show_left = QShortcut(QKeySequence("L"), self)
        sc_show_left.activated.connect(self._show_left_panel_popup)
        sc_show_right = QShortcut(QKeySequence("R"), self)
        sc_show_right.activated.connect(self._show_right_panel_popup)
    def closeEvent(self, event):
        # Minimize to tray instead of quitting (if tray is available)
        try:
            if hasattr(self, "_tray") and self._tray.isVisible() and not getattr(self, "_force_quit", False):
                event.ignore()
                if self._vision_preview is not None:
                    self._vision_preview.stop()
                self.hide()
                self._tray.showMessage(
                    "JARVIS", "Running in background. Click tray icon to restore.",
                    QSystemTrayIcon.MessageIcon.Information, 2000
                )
                return
        except Exception:
            pass
        if self._vision_preview is not None:
            self._vision_preview.stop()
        event.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    # ── System tray ──────────────────────────────────────────────────────
    def _setup_system_tray(self):
        self._force_quit = False
        self._tray = QSystemTrayIcon(self)
        # Create a simple icon programmatically
        px = QPixmap(32, 32)
        px.fill(QColor(0, 0, 0, 0))
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(qcol(C.PRI), 2))
        p.drawEllipse(4, 4, 24, 24)
        p.setBrush(QBrush(qcol(C.ENERGY)))
        p.drawEllipse(10, 10, 12, 12)
        p.end()
        self._tray.setIcon(QIcon(px))
        self._tray.setToolTip("J.A.R.V.I.S — MARK XXXIX")

        tray_menu = QMenu()
        tray_menu.setStyleSheet(f"""
            QMenu {{
                background: {C.PANEL}; color: {C.WHITE};
                border: 1px solid {C.BORDER};
            }}
            QMenu::item:selected {{ background: {C.PRI_GHO}; color: {C.PRI}; }}
        """)

        show_action = QAction("Show JARVIS", self)
        show_action.triggered.connect(self._tray_show)
        tray_menu.addAction(show_action)

        mute_action = QAction("Toggle Mute", self)
        mute_action.triggered.connect(self._toggle_mute)
        tray_menu.addAction(mute_action)

        tray_menu.addSeparator()

        quit_action = QAction("Quit JARVIS", self)
        quit_action.triggered.connect(self._tray_quit)
        tray_menu.addAction(quit_action)

        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._tray_activated)
        self._tray.show()

    def _tray_show(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_quit(self):
        self._request_quit()

    def _request_quit(self):
        """Use one clean shutdown path for tray, UI, and JARVIS self-quit."""
        self._force_quit = True
        try:
            if self.on_quit_requested:
                self.on_quit_requested()
        except Exception as exc:
            self._log.append_log(f"ERR: Shutdown cleanup — {exc}")
        try:
            if hasattr(self, "_tray"):
                self._tray.hide()
        except Exception:
            pass
        self.close()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self._tray_show()

    # ── Compact mode ─────────────────────────────────────────────────────
    def _toggle_compact_mode(self):
        if self._compact_mode:
            # Restore from compact
            if self._compact_widget:
                self._compact_widget.hide()
                self._compact_widget.deleteLater()
                self._compact_widget = None
            self.showNormal()
            self.raise_()
            self._compact_mode = False
        else:
            # Enter compact mode
            self._compact_mode = True
            self.hide()
            cw = CompactModeWidget()
            cw.expand_requested.connect(self._toggle_compact_mode)
            # Position near center of screen
            screen = QApplication.primaryScreen().availableGeometry()
            cw.move(screen.width() - 100, screen.height() // 2 - 40)
            cw.show()
            self._compact_widget = cw
            # Sync state
            if hasattr(self, "hud"):
                cw.set_state(self.hud.state)

    # ── Shortcuts overlay ────────────────────────────────────────────────
    def _toggle_shortcuts_overlay(self):
        if self._shortcuts_overlay and self._shortcuts_overlay.isVisible():
            self._shortcuts_overlay.hide()
            return
        cw = self.centralWidget()
        ov = ShortcutsOverlay(cw)
        ow, oh = 420, 380
        ov.setGeometry(
            (cw.width() - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.show()
        self._shortcuts_overlay = ov

    # ── Settings overlay ─────────────────────────────────────────────────
    def _show_settings(self):
        if self._settings_overlay and self._settings_overlay.isVisible():
            self._settings_overlay.hide()
            return
        # Get current name
        current_name = ""
        try:
            from memory.memory_manager import load_memory
            memory = load_memory()
            name_entry = memory.get("identity", {}).get("name")
            if isinstance(name_entry, dict):
                current_name = name_entry.get("value", "")
            elif isinstance(name_entry, str):
                current_name = name_entry
        except Exception:
            pass

        cw = self.centralWidget()
        ov = SettingsOverlay(
            cw,
            current_name=current_name,
            current_theme=ThemeManager.current_name(),
            current_graphics=self._graphics_quality,
        )
        ow, oh = 560, 410
        ov.setGeometry(
            (cw.width() - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.name_changed.connect(self._on_settings_name)
        ov.theme_changed.connect(self._on_settings_theme)
        ov.graphics_changed.connect(self._on_settings_graphics)
        ov.show()
        self._settings_overlay = ov

    def _on_settings_name(self, name: str):
        if name:
            self._on_name_done(name)

    def _on_settings_theme(self, key: str):
        ThemeManager.set_theme(key)

    def _on_settings_graphics(self, quality: str):
        self._apply_graphics_quality_live(quality)

    def _show_vision_preview(self, source: str):
        if self._vision_preview is None:
            self._vision_preview = VisionPreviewWindow(self.centralWidget())
        self._vision_preview.set_graphics_quality(self._graphics_quality)
        self._vision_preview.start(source)

    def _hide_vision_preview(self, delay_ms: int = 1800):
        if self._vision_preview is not None:
            self._vision_preview.finish(delay_ms)

    def _apply_graphics_quality_live(self, quality: str):
        """Persist and apply a graphics profile to every active renderer."""
        value = set_graphics_quality(quality)
        self._graphics_quality = value
        profile = GRAPHICS_PROFILES[value]

        if hasattr(self, "hud"):
            self.hud.set_graphics_quality(value)
        if hasattr(self, "_ai_canvas"):
            self._ai_canvas.set_graphics_quality(value)
        if hasattr(self, "_metric_tmr"):
            self._metric_tmr.setInterval(int(profile["metrics_ms"]))
        if self._vision_preview is not None:
            self._vision_preview.set_graphics_quality(value)
        if self._settings_overlay:
            self._settings_overlay._current_graphics = value
            self._settings_overlay._highlight_graphics(value)

        if hasattr(self, "_log"):
            self._log.append_log(f"SYS: Graphics quality set to {value.upper()}.")
        if hasattr(self, "_popup_manager"):
            self._show_toast(f"Graphics quality: {value.upper()}", "success")

    # ── Theme cycling ────────────────────────────────────────────────────
    def _cycle_theme(self):
        try:
            names = ThemeManager.theme_names()
            cur = ThemeManager.current_name()
            idx = names.index(cur) if cur in names else 0
            next_key = names[(idx + 1) % len(names)]
            ThemeManager.set_theme(next_key)
            display = ThemeManager.theme_display_name(next_key)
            ToastManager.show_toast(self.centralWidget(),
                                    f"Theme: {display}", "info", 2000)
        except Exception:
            pass

    def _on_theme_changed(self, key: str):
        """Apply a selected theme immediately across the visible interface."""
        from PyQt6.QtGui import QPalette

        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(C.BG))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(C.WHITE))
        palette.setColor(QPalette.ColorRole.Base, QColor(C.DARK))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(C.DARK2))
        palette.setColor(QPalette.ColorRole.Text, QColor(C.WHITE))
        palette.setColor(QPalette.ColorRole.Button, QColor(C.PANEL2))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(C.WHITE))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(C.PRI))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(C.BG))
        self.setPalette(palette)
        self.setStyleSheet(f"""
            QMainWindow {{ background: {C.BG}; }}
            QTextEdit, QPlainTextEdit, QTextBrowser {{
                background: {C.DARK}; color: {C.WHITE}; border: none;
            }}
        """)
        if self.centralWidget():
            self.centralWidget().setStyleSheet(f"QWidget#jarvisRoot {{ background: {C.BG}; }}")
        if hasattr(self, "_middle_section"):
            self._middle_section.setStyleSheet(f"background: {C.BG};")
        if hasattr(self, "_ai_core_wrap"):
            self._ai_core_wrap.setStyleSheet(f"QWidget#aiCore {{ background: {C.BG}; }}")
        if hasattr(self, "_left_panel"):
            self._left_panel.setStyleSheet(f"""
                QWidget#leftRail {{
                    background: {C.PANEL}; border-right: 1px solid {C.STEEL};
                }}
            """)
        if hasattr(self, "_right_panel"):
            self._right_panel.setStyleSheet(f"""
                QWidget#rightRail {{
                    background: {C.PANEL}; border-left: 1px solid {C.BORDER};
                }}
            """)
        if hasattr(self, "_command_bar"):
            self._style_command_rail()

        self._style_header()
        self._style_splitter()
        self._style_left_nav()
        self._style_command_controls()
        self._style_maker_signature()
        self._update_theme_btn()
        if hasattr(self, "_mission"):
            self._mission.refresh_theme()
        if hasattr(self, "_focus_dialogue"):
            self._focus_dialogue.refresh_theme()
        if hasattr(self, "_research_progress"):
            self._research_progress.refresh_theme()
        if hasattr(self, "_subtitle"):
            self._subtitle._done_col = qcol(C.TEXT)
            self._subtitle._active_col = qcol(C.PRI)
        if self._settings_overlay:
            self._settings_overlay.refresh_theme()
        if self._vision_preview is not None:
            self._vision_preview.refresh_theme()

        for spark, color in (
            (getattr(self, "_spark_cpu", None), C.PRI),
            (getattr(self, "_spark_mem", None), C.ENERGY),
            (getattr(self, "_spark_net", None), C.ACC2),
            (getattr(self, "_spark_tmp", None), C.ACC),
        ):
            if spark is not None:
                spark._color = color

        for widget in self.findChildren(QWidget):
            widget.update()

        try:
            settings_dir = Path.home() / ".jarvis" / "config"
            settings_dir.mkdir(parents=True, exist_ok=True)
            settings_file = settings_dir / "settings.json"
            try:
                settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
            except Exception:
                settings = {}
            settings["theme"] = key
            settings_file.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _handle_ui_command(self, action: str):
        action = str(action or "").strip().lower()
        if action in {"quit jarvis", "quit_jarvis"}:
            self._request_quit()
            return True
        if action == "open_command_center":
            self._set_command_center(True)
            return True
        if action == "close_command_center":
            self._set_command_center(False)
            return True
        handlers = {
            "open_settings": self._show_settings,
            "compact_mode": self._toggle_compact_mode,
            "fullscreen": self._toggle_fullscreen,
            "show_shortcuts": self._toggle_shortcuts_overlay,
        }
        handler = handlers.get(action)
        if handler:
            handler()
            return True
        return False

    def _set_command_center(self, is_open: bool, announce: bool = True):
        """Switch between the focused conversation and full control surface."""
        was_open = bool(getattr(self, "_command_center_open", False))
        self._command_center_open = bool(is_open)
        self._command_transition_id = getattr(self, "_command_transition_id", 0) + 1
        transition_id = self._command_transition_id
        if hasattr(self, "_mission"):
            self._mission.set_command_center_open(is_open)
        if hasattr(self, "_right_panel"):
            self._right_panel.setFixedWidth(_RIGHT_W)
            self._right_panel.setVisible(is_open)
        if hasattr(self, "_focus_dialogue"):
            self._focus_dialogue.setVisible(not is_open)
        if hasattr(self, "_splitter"):
            if is_open:
                self._splitter.setSizes([_LEFT_W, max(420, self.width() - _LEFT_W - _RIGHT_W), _RIGHT_W])
            else:
                self._splitter.setSizes([0, max(720, self.width()), 0])

        reveal_widgets = [
            getattr(self, "_header", None),
            getattr(self, "_left_panel", None),
            getattr(self, "_right_panel", None),
            getattr(self, "_command_bar", None),
        ]
        if is_open:
            for widget in reveal_widgets:
                if widget is not None:
                    widget.show()
            if not was_open:
                for delay, widget in zip((0, 90, 180, 270), reveal_widgets):
                    if widget is not None:
                        self._reveal_widget(widget, delay, transition_id)
        else:
            for widget in reveal_widgets:
                if widget is not None:
                    widget.hide()
            if hasattr(self, "_focus_dialogue") and was_open:
                self._reveal_widget(self._focus_dialogue, 80, transition_id)

        if hasattr(self, "_tool_progress"):
            if is_open and self._tool_progress._tmr.isActive():
                self._tool_progress.show()
            elif not is_open:
                self._tool_progress.hide()
        if announce and hasattr(self, "_popup_manager"):
            self._show_toast(
                "Command Center open" if is_open else "Focus view restored",
                "info",
            )
        if announce and is_open and not was_open:
            self._announce_command_center_modules()

    def _reveal_widget(self, widget: QWidget, delay_ms: int = 0, transition_id: int | None = None):
        """Reveal a module with a short, stagger-friendly opacity transition."""
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(230)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutQuart)
        self._command_reveal_animations = getattr(self, "_command_reveal_animations", [])
        self._command_reveal_animations.append(animation)

        def _finish():
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)
            try:
                self._command_reveal_animations.remove(animation)
            except ValueError:
                pass

        animation.finished.connect(_finish)

        def _start():
            if (
                transition_id is not None
                and transition_id != getattr(self, "_command_transition_id", transition_id)
            ):
                _finish()
                return
            animation.start()

        QTimer.singleShot(max(0, int(delay_ms)), _start)

    def _announce_command_center_modules(self):
        announcement = (
            "Command Center online. Status header ready. System overview ready. "
            "Mission control ready. Command rail ready."
        )
        if self.on_text_command:
            prompt = (
                "[UI EVENT] The Command Center modules are appearing now. "
                f'Say exactly: "{announcement}"'
            )
            threading.Thread(target=self.on_text_command, args=(prompt,), daemon=True).start()
        else:
            self._log.append_log(f"JARVIS: {announcement}")


    def _toggle_left_panel(self):
        sizes = self._splitter.sizes()
        if sizes[0] > 10:
            self._left_target_w = sizes[0]
            self._splitter.setSizes([0, sizes[1] + sizes[0], sizes[2]])
        else:
            self._splitter.setSizes([self._left_target_w, sizes[1] - self._left_target_w, sizes[2]])

    def _toggle_right_panel(self):
        sizes = self._splitter.sizes()
        if sizes[2] > 10:
            self._right_target_w = sizes[2]
            self._splitter.setSizes([sizes[0], sizes[1] + sizes[2], 0])
        else:
            self._splitter.setSizes([sizes[0], sizes[1] - self._right_target_w, self._right_target_w])

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Left and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._toggle_left_panel()
        elif event.key() == Qt.Key.Key_Right and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._toggle_right_panel()
        else:
            super().keyPressEvent(event)

    def _dismiss_overlays(self):
        for ov in (self._shortcuts_overlay, self._settings_overlay):
            if ov and ov.isVisible():
                ov.hide()
                return

    # ── Toast helper ─────────────────────────────────────────────────────
    def _show_toast(self, message: str, toast_type: str = "info"):
        try:
            ToastManager.show_toast(self.centralWidget(), message, toast_type)
        except Exception:
            pass

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw = self.centralWidget()
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 420
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._name_overlay and self._name_overlay.isVisible():
            ow, oh = 420, 310
            self._name_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if hasattr(self, "_voice_overlay") and self._voice_overlay and self._voice_overlay.isVisible():
            ow, oh = 420, 380
            self._voice_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )

    def _update_metrics(self):
        snap = _get_metrics()

        # CPU
        cpu = snap["cpu"]
        if cpu <= 0:
            try:
                import psutil as _psu
                cpu = _psu.cpu_percent(interval=None)
            except Exception:
                cpu = 0.0
        self._bar_cpu.set_value(cpu, "{:.0f}%".format(cpu))
        if hasattr(self, '_spark_cpu'):
            self._spark_cpu.set_value("{:.0f}".format(cpu), cpu / 100.0, "%")

        # MEM
        mem = snap["mem"]
        if mem <= 0:
            try:
                import psutil as _psu
                mem = _psu.virtual_memory().percent
            except Exception:
                mem = 0.0
        self._bar_mem.set_value(mem, "{:.0f}%".format(mem))
        if hasattr(self, '_spark_mem'):
            self._spark_mem.set_value("{:.0f}".format(mem), mem / 100.0, "%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        self._bar_net.set_value(net_pct, net_str)
        if hasattr(self, '_spark_net'):
            self._spark_net.set_value(net_str, net_pct / 100.0, "")

        # GPU — cache chip/VRAM from system_profiler, simulate load
        if not getattr(self, '_gpu_info_cached', False):
            try:
                import subprocess as _sp, re as _re2
                _r = _sp.run(["system_profiler", "SPDisplaysDataType"],
                             capture_output=True, text=True, timeout=3)
                _cm = _re2.search(r"Chipset Model:\s*(.+)", _r.stdout)
                _vm = _re2.search(r"VRAM.*?:\s*([\d.]+ \w+)", _r.stdout)
                self._gpu_chip = _cm.group(1).strip() if _cm else "Integrated GPU"
                self._gpu_chip = self._gpu_chip.replace(" Graphics", "").replace("Intel ", "")
                self._gpu_vram_str = _vm.group(1) if _vm else "Shared"
                self._gpu_info_cached = True
            except Exception:
                self._gpu_chip = "Integrated GPU"
                self._gpu_vram_str = "Shared"
                self._gpu_info_cached = True
        if hasattr(self, '_gpu_name_lbl'):
            self._gpu_name_lbl.setText(getattr(self, '_gpu_chip', 'Integrated GPU'))
        if hasattr(self, '_gpu_vram_lbl'):
            self._gpu_vram_lbl.setText(getattr(self, '_gpu_vram_str', 'Shared'))
        gpu = float(snap.get("gpu", -1.0))
        if hasattr(self, '_gpu_pct_lbl'):
            self._gpu_pct_lbl.setText("{:.0f}%".format(gpu) if gpu >= 0 else "N/A")
        if hasattr(self, '_gpu_load_bar'):
            self._gpu_load_bar.setValue(int(gpu) if gpu >= 0 else 0)
        self._bar_gpu.set_value(max(0.0, gpu), "{:.0f}%".format(gpu) if gpu >= 0 else "N/A")

        # Temperature is shown only when the operating system exposes a real sensor.
        tmp = snap["tmp"]
        if tmp < 0:
            self._bar_tmp.set_value(0, "N/A")
            if hasattr(self, '_spark_tmp'):
                self._spark_tmp.set_value("N/A", 0.0, "")
        else:
            tmp_pct = min(100, tmp)
            self._bar_tmp.set_value(tmp_pct, "{:.0f}°C".format(tmp))
            if hasattr(self, '_spark_tmp'):
                self._spark_tmp.set_value("{:.0f}".format(tmp), tmp / 100.0, "°C")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("UP  --:--")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"PROC  {proc_count}")
        except Exception:
            self._proc_lbl.setText("PROC  --")

        # Session timer
        try:
            if hasattr(self, "_session_start"):
                se = time.time() - self._session_start
                sh = int(se // 3600)
                sm = int((se % 3600) // 60)
                ss = int(se % 60)
                if hasattr(self, "_session_lbl"):
                    self._session_lbl.setText(f"SESSION  {sh:02d}:{sm:02d}:{ss:02d}")
        except Exception:
            pass

        # AI Cognition bar — driven by state
        try:
            if hasattr(self, "_bar_cog") and hasattr(self, "hud"):
                state = getattr(self.hud, "state", "LISTENING")
                cog_pct = {
                    "THINKING": random.uniform(70, 95),
                    "PROCESSING": random.uniform(55, 80),
                    "SPEAKING": random.uniform(30, 50),
                    "LISTENING": random.uniform(5, 20),
                }.get(state, random.uniform(2, 10))
                self._bar_cog.set_value(cog_pct, f"{cog_pct:.0f}%")
        except Exception:
            pass

    def _style_splitter(self):
        if not hasattr(self, "_splitter"):
            return
        self._splitter.setStyleSheet(f"""
            QSplitter::handle {{
                background: {C.BORDER};
                width: 4px;
            }}
            QSplitter::handle:hover {{ background: {C.BORDER_B}; }}
        """)

    def _style_header(self):
        if not hasattr(self, "_header") and not hasattr(self, "_utility_btn"):
            return
        header = getattr(self, "_header", None)
        if header is not None:
            header.setStyleSheet(f"""
                QWidget#appHeader {{
                    background: {C.DARK};
                    border-bottom: 1px solid {C.BORDER};
                }}
            """)
        if hasattr(self, "_utility_btn"):
            self._utility_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2}; color: {C.TEXT_MED};
                    border: 1px solid {C.BORDER}; border-radius: 6px;
                    font-size: 12px;
                }}
                QPushButton:hover {{ color: {C.WHITE}; border-color: {C.BORDER_B}; }}
                QPushButton::menu-indicator {{ image: none; }}
            """)
        if hasattr(self, "_utility_menu"):
            self._utility_menu.setStyleSheet(f"""
                QMenu {{
                    background: {C.PANEL}; color: {C.WHITE};
                    border: 1px solid {C.BORDER}; padding: 6px;
                }}
                QMenu::item {{ padding: 7px 24px 7px 10px; border-radius: 4px; }}
                QMenu::item:selected {{ background: {C.PRI_GHO}; color: {C.PRI}; }}
                QMenu::separator {{ height: 1px; background: {C.BORDER}; margin: 5px; }}
            """)
        label_styles = (
            ("_header_brand_lbl", C.WHITE),
            ("_header_mark_lbl", C.TEXT_MED),
            ("_header_state_lbl", C.GREEN),
            ("_header_mode_lbl", C.TEXT_MED),
            ("_clock_lbl", C.WHITE),
            ("_date_lbl", C.TEXT_DIM),
            ("_utc_lbl", C.TEXT_DIM),
        )
        for attr, color in label_styles:
            label = getattr(self, attr, None)
            if label is not None:
                label.setStyleSheet(f"color: {color}; background: transparent;")

    def _style_left_nav(self):
        for attr, color in (
            ("_rail_title_lbl", C.WHITE),
            ("_system_title_lbl", C.TEXT_MED),
            ("_hardware_title_lbl", C.TEXT_DIM),
            ("_gpu_name_lbl", C.TEXT_MED),
            ("_gpu_vram_lbl", C.TEXT_MED),
            ("_uptime_lbl", C.TEXT_DIM),
            ("_session_lbl", C.TEXT_DIM),
            ("_proc_lbl", C.TEXT_MED),
        ):
            label = getattr(self, attr, None)
            if label is not None:
                label.setStyleSheet(f"color: {color}; background: transparent;")
        for button in (getattr(self, "_left_system_btn", None), getattr(self, "_left_agents_btn", None)):
            if button is None:
                continue
            if button.isChecked():
                button.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.PRI_GHO}; color: {C.PRI};
                        border: 1px solid {C.PRI_DIM}; border-radius: 5px;
                    }}
                """)
            else:
                button.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 5px;
                    }}
                    QPushButton:hover {{ color: {C.WHITE}; border-color: {C.BORDER_B}; }}
                """)

    def _style_command_button(self, button: QPushButton, color: str, hover: str):
        button.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {color};
                border: 1px solid transparent; border-radius: 5px;
                padding: 0 11px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; color: {hover}; border-color: {C.BORDER_B};
            }}
            QPushButton:focus {{
                background: {C.PRI_GHO}; color: {hover};
                border: 1px solid {hover};
            }}
            QPushButton:pressed {{ background: {C.DARK2}; color: {C.WHITE}; }}
            QPushButton:disabled {{ color: {C.TEXT_DIM}; background: transparent; }}
        """)

    def _style_command_rail(self):
        rail = getattr(self, "_dock_frame", None)
        if rail is not None:
            rail.setStyleSheet(f"""
                QWidget#JarvisCommandRail {{
                    background: {C.BAR_BG};
                    border-top: 1px solid {C.BORDER_B};
                    border-bottom: 1px solid {C.BORDER};
                }}
                QFrame#CommandControlTrack {{
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER};
                    border-radius: 6px;
                }}
                QFrame#CommandRailDivider {{
                    color: {C.BORDER};
                    background: {C.BORDER};
                    border: none;
                }}
            """)
        if hasattr(self, "_rail_divider"):
            self._rail_divider.setStyleSheet(f"color: {C.BORDER_B};")
        if hasattr(self, "_command_title_lbl"):
            self._command_title_lbl.setStyleSheet(
                f"color: {C.WHITE}; background: transparent; letter-spacing: 1px;"
            )
        if hasattr(self, "_rail_mode_lbl"):
            self._rail_mode_lbl.setStyleSheet(
                f"color: {C.TEXT_DIM}; background: transparent; letter-spacing: 1px;"
            )
        if hasattr(self, "_rail_status_dot"):
            self._rail_status_dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")

    def _style_maker_signature(self):
        strip = getattr(self, "_maker_signature", None)
        if strip is not None:
            strip.setStyleSheet(f"""
                QWidget#JarvisMakerSignature {{
                    background: {C.BG};
                    border: none;
                }}
            """)
        label = getattr(self, "_maker_signature_lbl", None)
        if label is not None:
            label.setStyleSheet(
                f"color: {C.TEXT_DIM}; background: transparent; letter-spacing: 1px;"
            )

    def _style_command_controls(self):
        self._style_command_rail()
        if hasattr(self, "_tts_btn"):
            self._style_command_button(self._tts_btn, C.TEXT_MED, C.PRI)
        if hasattr(self, "_name_btn"):
            self._style_command_button(self._name_btn, C.TEXT_MED, C.PRI)
        if hasattr(self, "_theme_btn"):
            self._style_command_button(self._theme_btn, C.TEXT_MED, C.PRI)
        if hasattr(self, "_quit_btn"):
            self._quit_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.RED};
                    border: 1px solid {C.RED_D}; border-radius: 5px;
                    padding: 0 13px;
                }}
                QPushButton:hover, QPushButton:focus {{
                    background: {C.RED_BG}; color: {C.MUTED_C}; border-color: {C.RED};
                }}
                QPushButton:pressed {{ background: {C.DARK2}; }}
            """)
        if hasattr(self, "_mute_btn"):
            self._style_mute_btn()

    def _update_theme_btn(self):
        if hasattr(self, "_theme_btn"):
            display = ThemeManager.theme_display_name(ThemeManager.current_name())
            self._theme_btn.setText(f"THEME  ·  {display}")

    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setObjectName("appHeader")
        w.setFixedHeight(58)
        w.setStyleSheet(f"""
            QWidget#appHeader {{
                background: {C.DARK};
                border-bottom: 1px solid {C.BORDER};
            }}
        """)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(12)

        # ── Left: Stark Industries branding ──────────────────────────────────
        left_col = QVBoxLayout(); left_col.setSpacing(1)
        left_col.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        stark = QLabel("JARVIS")
        self._header_brand_lbl = stark
        stark.setObjectName("headerTitle")
        stark.setFont(QFont("Arial", 15, QFont.Weight.DemiBold))
        stark.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        left_col.addWidget(stark)

        sub_stark = QLabel("MARK XXXIX")
        self._header_mark_lbl = sub_stark
        sub_stark.setObjectName("headerMeta")
        sub_stark.setFont(QFont("Arial", 7, QFont.Weight.Medium))
        sub_stark.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; letter-spacing: 1px;")
        left_col.addWidget(sub_stark)
        lay.addLayout(left_col)

        lay.addStretch()

        # ── Centre: JARVIS title ──────────────────────────────────────────────
        mid = QVBoxLayout(); mid.setSpacing(1)
        mid.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        title = QLabel("●  ONLINE")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Arial", 9, QFont.Weight.DemiBold))
        title.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        self._header_state_lbl = title
        mid.addWidget(title)

        sub = QLabel("LISTENING")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setFont(QFont("Arial", 7, QFont.Weight.Medium))
        sub.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; letter-spacing: 1px;")
        self._header_mode_lbl = sub
        mid.addWidget(sub)
        lay.addLayout(mid)

        lay.addStretch()

        # ── Right: clock + threat level + status ─────────────────────────────
        right_col = QVBoxLayout(); right_col.setSpacing(2)
        right_col.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Status row
        status_row = QHBoxLayout(); status_row.setSpacing(8)
        status_row.setAlignment(Qt.AlignmentFlag.AlignRight)

        right_col.addLayout(status_row)

        # Clock row
        clock_row = QHBoxLayout(); clock_row.setSpacing(4)
        clock_row.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont("Arial", 12, QFont.Weight.DemiBold))
        self._clock_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        clock_row.addWidget(self._clock_lbl)
        right_col.addLayout(clock_row)

        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont("Arial", 7))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; letter-spacing: 1px;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._date_lbl)

        self._utc_lbl = QLabel("")
        self._utc_lbl.setFont(QFont("Arial", 6))
        self._utc_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; letter-spacing: 1px;")
        self._utc_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._utc_lbl)
        lay.addLayout(right_col)

        self._utility_btn = QPushButton("•••")
        self._utility_btn.setFixedSize(38, 34)
        self._utility_btn.setToolTip("Window and settings")
        self._utility_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._utility_menu = QMenu(self._utility_btn)
        self._utility_menu.addAction("Fullscreen", self._toggle_fullscreen)
        self._utility_menu.addAction("Settings", self._show_settings)
        self._utility_menu.addAction("Compact Mode", self._toggle_compact_mode)
        self._utility_menu.addSeparator()
        self._utility_menu.addAction("Keyboard Shortcuts", self._toggle_shortcuts_overlay)
        self._utility_menu.addAction("Close Command Center", lambda: self._set_command_center(False))
        self._utility_btn.setMenu(self._utility_menu)
        lay.addWidget(self._utility_btn)
        self._style_header()
        return w

    def _tick_clock(self):
        _colon = ":" if int(time.time()) % 2 == 0 else " "
        self._clock_lbl.setText(time.strftime("%H") + _colon + time.strftime("%M") + _colon + time.strftime("%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y").upper())
        if hasattr(self, "_utc_lbl"):
            import datetime
            _off = datetime.datetime.now(datetime.timezone.utc).strftime("%z")
            self._utc_lbl.setText(f"UTC {_off[:3]}:{_off[3:]}")

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("leftRail")
        w.setFixedWidth(_LEFT_W)
        w.setStyleSheet(f"""
            QWidget#leftRail {{
                background: {C.PANEL};
                border-right: 1px solid {C.STEEL};
            }}
        """)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(10)

        rail_title = QLabel("OVERVIEW")
        self._rail_title_lbl = rail_title
        rail_title.setFont(QFont("Arial", 10, QFont.Weight.DemiBold))
        rail_title.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(rail_title)

        nav = QHBoxLayout(); nav.setSpacing(4)
        self._left_system_btn = QPushButton("System")
        self._left_agents_btn = QPushButton("Agents")
        for button in (self._left_system_btn, self._left_agents_btn):
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(30)
            nav.addWidget(button)
        self._left_system_btn.setChecked(True)
        lay.addLayout(nav)

        # ── System Status (redesigned) ──────────────────────────────────────
        metrics_w = QWidget()
        metrics_w.setObjectName("systemOverview")
        metrics_w.setStyleSheet("background: transparent;")
        ml = QVBoxLayout(metrics_w)
        ml.setContentsMargins(12, 10, 12, 8)
        ml.setSpacing(3)

        # Header
        sys_hdr = QLabel("Live system")
        self._system_title_lbl = sys_hdr
        sys_hdr.setFont(QFont("Arial", 9, QFont.Weight.DemiBold))
        sys_hdr.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        ml.addWidget(sys_hdr)

        # Sparkline metrics — CPU, MEM, NET with live graphs
        self._spark_cpu = SparklineBar("CPU", C.PRI)
        self._spark_mem = SparklineBar("MEM", C.ENERGY)
        self._spark_net = SparklineBar("NET", C.ACC2)
        for spark in [self._spark_cpu, self._spark_mem, self._spark_net]:
            ml.addWidget(spark)
            spark.hide()

        ml.addSpacing(6)

        # ── GPU Block (expanded) ────────────────────────────────────────────
        gpu_super = QLabel("HARDWARE")
        self._hardware_title_lbl = gpu_super
        gpu_super.setFont(QFont("Courier New", 6))
        gpu_super.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; letter-spacing: 2px;")
        ml.addWidget(gpu_super)

        gpu_hdr_row = QHBoxLayout()
        gpu_hdr_lbl = QLabel("GPU")
        gpu_hdr_lbl.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        gpu_hdr_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 2px;")
        gpu_hdr_row.addWidget(gpu_hdr_lbl)
        gpu_hdr_lbl.hide()
        gpu_hdr_row.addStretch()
        self._gpu_pct_lbl = QLabel("0%")
        self._gpu_pct_lbl.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self._gpu_pct_lbl.setStyleSheet(f"color: {C.ENERGY}; background: transparent;")
        gpu_hdr_row.addWidget(self._gpu_pct_lbl)
        self._gpu_pct_lbl.hide()
        ml.addLayout(gpu_hdr_row)

        # chip name
        self._gpu_name_lbl = QLabel("Detecting...")
        self._gpu_name_lbl.setFont(QFont("Courier New", 7))
        self._gpu_name_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        ml.addWidget(self._gpu_name_lbl)

        ml.addSpacing(3)

        # LOAD label + bar
        gpu_load_hdr = QLabel("LOAD")
        gpu_load_hdr.setFont(QFont("Courier New", 6))
        gpu_load_hdr.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; letter-spacing: 1px;")
        ml.addWidget(gpu_load_hdr)
        gpu_load_hdr.hide()

        self._gpu_load_bar = QProgressBar()
        self._gpu_load_bar.setRange(0, 100)
        self._gpu_load_bar.setValue(0)
        self._gpu_load_bar.setFixedHeight(6)
        self._gpu_load_bar.setTextVisible(False)
        self._gpu_load_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {C.BORDER};
                border: none;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {C.PRI}, stop:1 {C.ENERGY});
                border-radius: 3px;
            }}
        """)
        ml.addWidget(self._gpu_load_bar)
        self._gpu_load_bar.hide()

        ml.addSpacing(3)

        # VRAM row
        gpu_vram_row = QHBoxLayout()
        vram_lbl = QLabel("VRAM")
        vram_lbl.setFont(QFont("Courier New", 6))
        vram_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; letter-spacing: 1px;")
        gpu_vram_row.addWidget(vram_lbl)
        gpu_vram_row.addStretch()
        self._gpu_vram_lbl = QLabel("N/A")
        self._gpu_vram_lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        self._gpu_vram_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        gpu_vram_row.addWidget(self._gpu_vram_lbl)
        ml.addLayout(gpu_vram_row)

        ml.addSpacing(6)

        # ── TMP sparkline (same style as CPU/MEM/NET) ───────────────────────
        self._spark_tmp = SparklineBar("TMP", "#FF6B35")
        ml.addWidget(self._spark_tmp)
        self._spark_tmp.hide()

        ml.addSpacing(4)

        # Cognitive load with progress bar
        cog_hdr = QLabel("COGNITIVE LOAD")
        cog_hdr.setFont(QFont("Courier New", 6, QFont.Weight.Bold))
        cog_hdr.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; letter-spacing: 2px;")
        ml.addWidget(cog_hdr)
        cog_hdr.hide()
        # Keep MetricBar references for data compatibility. Cognition is the
        # only visible bar because the other values already use spark rows.
        self._bar_cpu = MetricBar("CPU", C.TEXT_MED)
        self._bar_mem = MetricBar("MEM", C.TEXT_MED)
        self._bar_net = MetricBar("NET", C.TEXT_MED)
        self._bar_gpu = MetricBar("GPU", C.TEXT_MED)
        self._bar_tmp = MetricBar("TMP", C.TEXT_MED)
        self._bar_cog = MetricBar("COG", C.TEXT_MED)
        for b in [self._bar_cpu, self._bar_mem, self._bar_net, self._bar_gpu, self._bar_tmp, self._bar_cog]:
            ml.addWidget(b)

        # Info row
        info_row = QHBoxLayout(); info_row.setSpacing(4)
        self._uptime_lbl = QLabel("UP --:--")
        self._uptime_lbl.setFont(QFont("Courier New", 6))
        self._uptime_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        info_row.addWidget(self._uptime_lbl)
        info_row.addStretch()
        self._session_lbl = QLabel("00:00:00")
        self._session_lbl.setFont(QFont("Courier New", 6))
        self._session_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        info_row.addWidget(self._session_lbl)
        ml.addLayout(info_row)

        self._proc_lbl = QLabel("PROC  --")
        self._proc_lbl.setFont(QFont("Courier New", 6))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        ml.addWidget(self._proc_lbl)

        self._left_stack = QStackedWidget()
        self._left_stack.setStyleSheet("background: transparent; border: none;")
        self._left_stack.addWidget(metrics_w)

        # ── Agent grid (fills remaining space) ───────────────────────────────
        self._agent_grid = AgentGridWidget()
        self._left_stack.addWidget(self._agent_grid)
        lay.addWidget(self._left_stack, stretch=1)

        def _show_left_page(index: int):
            self._left_stack.setCurrentIndex(index)
            self._left_system_btn.setChecked(index == 0)
            self._left_agents_btn.setChecked(index == 1)
            self._style_left_nav()

        self._left_system_btn.clicked.connect(lambda: _show_left_page(0))
        self._left_agents_btn.clicked.connect(lambda: _show_left_page(1))
        self._style_left_nav()

        return w

    def _feed_sparklines(self):
        """Feed sparkline bars from existing metric bar data."""
        try:
            # Extract numeric values from existing metric bars
            cpu_text = self._bar_cpu._val.text() if hasattr(self._bar_cpu, '_val') else "0"
            mem_text = self._bar_mem._val.text() if hasattr(self._bar_mem, '_val') else "0"
            net_text = self._bar_net._val.text() if hasattr(self._bar_net, '_val') else "0"

            cpu_pct = float(cpu_text.replace('%', '').strip() or '0') / 100.0
            mem_pct = float(mem_text.replace('%', '').strip() or '0') / 100.0

            self._spark_cpu.set_value(cpu_text.replace('%','').strip(), cpu_pct, "%")
            self._spark_mem.set_value(mem_text.replace('%','').strip(), mem_pct, "%")
            self._spark_net.set_value(net_text.strip(), 0.0, "")
        except Exception:
            pass

    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("rightRail")
        w.setFixedWidth(_RIGHT_W)
        w.setStyleSheet(f"""
            QWidget {{
                background: qlineargradient(
                    x1:1, y1:0, x2:0, y2:0,
                    stop:0 rgba(0, 4, 8, 240),
                    stop:0.5 rgba(0, 10, 18, 220),
                    stop:1 rgba(0, 8, 14, 230)
                );
                border-left: 1px solid {C.BORDER};
            }}
        """)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── Mission Control Panel (tabbed) ───────────────────────────────────
        self._mission = MissionControlPanel()
        # Use ChatBubbleWidget instead of raw LogWidget for COMMS tab
        self._chat_bubble = ChatBubbleWidget()
        self._mission._stack.removeWidget(self._mission.log_widget)
        self._mission.log_widget.deleteLater()
        self._mission._stack.insertWidget(0, self._chat_bubble)
        self._mission.log_widget = self._chat_bubble
        self._mission._switch_tab(0)
        self._log = self._chat_bubble  # keep _log reference for compatibility
        self._chat_bubble.command_submitted.connect(self._send)

        # Build assets page content
        assets_inner = QWidget()
        assets_inner.setStyleSheet("background: transparent;")
        al = QVBoxLayout(assets_inner)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(6)
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        al.addWidget(self._drop_zone)
        self._file_hint = QLabel("No file loaded — drop or click above")
        self._file_hint.setFont(QFont("Courier New", 7))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._file_hint.setWordWrap(True)
        al.addWidget(self._file_hint)
        self._mission.set_assets_widget(assets_inner)

        lay.addWidget(self._mission, stretch=1)

        # Hidden combo for voice tracking (not shown — controls are in CommandBar)
        self._voice_combo = QComboBox()
        self._voice_combo.hide()
        for label, value in VOICE_OPTIONS:
            self._voice_combo.addItem(label, value)
        self._voice_combo.setCurrentIndex(0)
        self._voice_combo.currentTextChanged.connect(self._on_voice_changed)

        return w

    def _build_footer(self) -> QWidget:
        w = QWidget()
        self._dock_frame = w
        w.setObjectName("JarvisCommandRail")
        w.setAccessibleName("JARVIS command rail")
        w.setFixedHeight(72)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(12)

        # ── Rail anchor: identity plus real application state ────────────────
        anchor = QWidget(w)
        anchor.setObjectName("CommandRailAnchor")
        anchor.setFixedWidth(174)
        anchor_lay = QVBoxLayout(anchor)
        anchor_lay.setContentsMargins(0, 1, 0, 1)
        anchor_lay.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(7)
        self._rail_status_dot = QLabel("●")
        self._rail_status_dot.setFont(QFont(TECH_FONT, 7, QFont.Weight.Medium))
        self._rail_status_dot.setAccessibleName("JARVIS status indicator")
        title_row.addWidget(self._rail_status_dot)
        self._command_title_lbl = QLabel("COMMAND RAIL")
        self._command_title_lbl.setFont(QFont(UI_FONT, 9, QFont.Weight.DemiBold))
        title_row.addWidget(self._command_title_lbl)
        title_row.addStretch()
        anchor_lay.addLayout(title_row)

        self._rail_mode_lbl = QLabel("LOCAL  /  LISTENING")
        self._rail_mode_lbl.setFont(QFont(TECH_FONT, 7, QFont.Weight.Medium))
        self._rail_mode_lbl.setAccessibleName("JARVIS current state")
        anchor_lay.addWidget(self._rail_mode_lbl)
        lay.addWidget(anchor)

        sep2 = QFrame(w)
        sep2.setObjectName("CommandRailDivider")
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setFixedSize(1, 34)
        self._rail_divider = sep2
        lay.addWidget(sep2)

        lay.addStretch(1)

        # ── One continuous control track, not a row of unrelated cards ──────
        track = QFrame(w)
        track.setObjectName("CommandControlTrack")
        track.setAccessibleName("Command controls")
        self._rail_control_track = track
        track_lay = QHBoxLayout(track)
        track_lay.setContentsMargins(2, 2, 2, 2)
        track_lay.setSpacing(0)

        def _ctrl_btn(txt, width):
            b = QPushButton(txt)
            b.setFixedSize(width, 40)
            b.setFont(QFont(UI_FONT, 8, QFont.Weight.DemiBold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            return b

        def _track_separator():
            separator = QFrame(track)
            separator.setObjectName("CommandRailDivider")
            separator.setFrameShape(QFrame.Shape.VLine)
            separator.setFixedSize(1, 24)
            return separator

        self._mute_btn = QPushButton("MIC  ·  ON")
        self._mute_btn.setFixedSize(112, 40)
        self._mute_btn.setFont(QFont(UI_FONT, 8, QFont.Weight.DemiBold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.setToolTip("Toggle microphone (F4)")
        self._mute_btn.setAccessibleName("Microphone active")
        self._mute_btn.clicked.connect(self._toggle_mute)
        track_lay.addWidget(self._mute_btn)
        track_lay.addWidget(_track_separator())

        self._tts_btn = _ctrl_btn("VOICE", 142)
        self._tts_btn.setToolTip("Change JARVIS voice")
        self._tts_btn.setAccessibleName("Change JARVIS voice")
        self._tts_btn.clicked.connect(self._show_tts_select)
        self._update_tts_btn()
        track_lay.addWidget(self._tts_btn)
        track_lay.addWidget(_track_separator())

        self._name_btn = _ctrl_btn("NAME", 142)
        self._name_btn.setToolTip("Change operator name")
        self._name_btn.setAccessibleName("Change operator name")
        self._name_btn.clicked.connect(self._show_name_signin)
        self._update_name_btn()
        track_lay.addWidget(self._name_btn)
        track_lay.addWidget(_track_separator())

        # Theme cycle button
        self._theme_btn = _ctrl_btn("THEME", 176)
        self._theme_btn.setToolTip("Cycle JARVIS theme")
        self._theme_btn.setAccessibleName("Cycle JARVIS theme")
        self._theme_btn.clicked.connect(self._cycle_theme)
        track_lay.addWidget(self._theme_btn)
        lay.addWidget(track)

        lay.addStretch(1)

        # Hidden shortcut mirrors retain existing keyboard-accessible functions
        # without adding visual noise to the rail.
        shortcut_mirrors = QWidget(w)
        shortcut_mirrors.hide()
        mirror_lay = QHBoxLayout(shortcut_mirrors)
        mirror_lay.setContentsMargins(0, 0, 0, 0)

        fs_btn = QPushButton("FULLSCREEN", shortcut_mirrors)
        fs_btn.setAccessibleName("Toggle fullscreen")
        fs_btn.setToolTip("Toggle fullscreen (F11)")
        fs_btn.clicked.connect(self._toggle_fullscreen)
        mirror_lay.addWidget(fs_btn)

        compact_btn = QPushButton("COMPACT", shortcut_mirrors)
        compact_btn.setToolTip("Compact Mode (Ctrl+M)")
        compact_btn.setAccessibleName("Toggle compact mode")
        compact_btn.clicked.connect(self._toggle_compact_mode)
        mirror_lay.addWidget(compact_btn)

        help_btn = QPushButton("SHORTCUTS", shortcut_mirrors)
        help_btn.setToolTip("Keyboard Shortcuts (Ctrl+/)")
        help_btn.setAccessibleName("Show keyboard shortcuts")
        help_btn.clicked.connect(self._toggle_shortcuts_overlay)
        mirror_lay.addWidget(help_btn)

        self._quit_btn = QPushButton("QUIT")
        self._quit_btn.setObjectName("JarvisQuitButton")
        self._quit_btn.setAccessibleName("Quit JARVIS")
        self._quit_btn.setToolTip("Quit JARVIS")
        self._quit_btn.setFixedSize(78, 44)
        self._quit_btn.setFont(QFont(UI_FONT, 8, QFont.Weight.DemiBold))
        self._quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._quit_btn.clicked.connect(self._request_quit)
        lay.addWidget(self._quit_btn)
        self._update_theme_btn()
        self._style_command_controls()

        return w

    def _build_maker_signature(self) -> QWidget:
        """Build the quiet, persistent creator signature beneath the shell."""
        strip = QWidget()
        self._maker_signature = strip
        strip.setObjectName("JarvisMakerSignature")
        strip.setAccessibleName("JARVIS creator trademark")
        strip.setFixedHeight(20)

        lay = QHBoxLayout(strip)
        lay.setContentsMargins(14, 0, 14, 1)
        lay.setSpacing(0)
        lay.addStretch(1)

        self._maker_signature_lbl = QLabel("amd.creationz™", strip)
        self._maker_signature_lbl.setFont(QFont(TECH_FONT, 7, QFont.Weight.Medium))
        self._maker_signature_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._maker_signature_lbl.setAccessibleName("amd.creationz trademark")
        self._maker_signature_lbl.setToolTip("JARVIS interface by amd.creationz")
        lay.addWidget(self._maker_signature_lbl)

        self._style_maker_signature()
        return strip


    def _show_left_panel_popup(self):
        """Show left panel content as a popup."""
        # Gather information from left panel components
        info_lines = ["LEFT PANEL INFORMATION"]

        # Add system metrics if available
        if hasattr(self, '_bar_cpu'):
            cpu_val = getattr(self._bar_cpu, 'value', 0)
            info_lines.append(f"CPU Usage: {cpu_val:.0f}%")
        if hasattr(self, '_bar_mem'):
            mem_val = getattr(self._bar_mem, 'value', 0)
            info_lines.append(f"Memory Usage: {mem_val:.0f}%")
        if hasattr(self, '_bar_net'):
            net_val = getattr(self._bar_net, 'value', 0)
            info_lines.append(f"Network: {net_val:.0f}%")
        if hasattr(self, '_bar_gpu'):
            gpu_val = getattr(self._bar_gpu, 'value', 0)
            info_lines.append(f"GPU: {gpu_val:.0f}%")

        # Add agent info if available
        if hasattr(self, '_agent_grid'):
            # AgentGridWidget has a fixed number of agents
            agent_count = len(self._agent_grid._AGENTS)
            info_lines.append(f"Active Agents: {agent_count}")

        info_lines.append("")
        info_lines.append("Press L again to refresh this information")

        # Join lines and show as popup
        message = "\n".join(info_lines)
        self._popup_manager.show_popup(
            message,
            PopupType.INFORMATION
        )

    def _show_right_panel_popup(self):
        """Show right panel content as a popup."""
        # Gather information from right panel components
        info_lines = ["RIGHT PANEL INFORMATION"]

        # Add mission info
        info_lines.append("Mission Control Panel")

        # Add chat status
        if hasattr(self, '_mission'):
            if hasattr(self._mission, 'log_widget'):
                info_lines.append("Chat Status: Active")
            else:
                info_lines.append("Chat Status: Inactive")

            # Add current tab info
            if hasattr(self._mission, '_stack'):
                current_index = self._mission._stack.currentIndex()
                tab_names = ["COMMS", "INTEL", "FILES", "ASSETS", "TOOLS", "MEMORY"]
                if 0 <= current_index < len(tab_names):
                    current_tab = tab_names[current_index]
                    info_lines.append(f"Current Tab: {current_tab}")

        info_lines.append("")
        info_lines.append("Press R again to refresh this information")

        # Join lines and show as popup
        message = "\n".join(info_lines)
        self._popup_manager.show_popup(
            message,
            PopupType.INFORMATION
        )

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"{icon}  {p.name}  ·  {size}  ·  Tell JARVIS what to do with it")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _get_selected_voice(self) -> str:
        idx = self._voice_combo.currentIndex()
        voice_name = self._voice_combo.itemData(idx)
        if isinstance(voice_name, str) and voice_name:
            return voice_name
        label = self._voice_combo.currentText().strip().lower()
        return VOICE_LABEL_TO_VALUE.get(label, "puck")

    def _on_voice_changed(self, _voice_label: str):
        """Handle voice selection change"""
        voice_name = self._get_selected_voice()
        self._log.append_log(f"SYS: Voice changed to {voice_name}.")
        os.environ["GEMINI_VOICE_NAME"] = voice_name
        try:
            cfg = json.loads(API_FILE.read_text(encoding="utf-8")) if API_FILE.exists() else {}
        except Exception:
            cfg = {}
        cfg.pop("gemini_api_key", None)
        cfg["voice_name"] = voice_name
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            API_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
        except Exception as e:
            self._log.append_log(f"SYS: Could not save voice selection: {e}")
        if self.on_voice_change:
            try:
                self.on_voice_change(voice_name)
            except Exception as e:
                self._log.append_log(f"SYS: Voice callback error: {e}")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("MIC  ·  MUTED")
            self._mute_btn.setAccessibleName("Microphone muted")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.RED_BG};
                    color: {C.MUTED_C};
                    border: 1px solid {C.RED};
                    border-radius: 5px;
                    padding: 0 12px;
                }}
                QPushButton:hover {{
                    background: {C.DARK2};
                    border: 1px solid {C.MUTED_C};
                }}
            """)
        else:
            self._mute_btn.setText("MIC  ·  ON")
            self._mute_btn.setAccessibleName("Microphone active")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.GREEN_BG};
                    color: {C.GREEN};
                    border: 1px solid {C.GREEN_D};
                    border-radius: 5px;
                    padding: 0 12px;
                }}
                QPushButton:hover {{
                    background: {C.DARK2};
                    border: 1px solid {C.GREEN};
                }}
            """)

    def _send(self, txt: str = ""):
        """Handle command submission. Called via ChatBubbleWidget signal."""
        try:
            txt = str(txt or "").strip()
            if not txt:
                return
            self._log.append_log(f"You: {txt}")
            normalized = re.sub(r"[^a-z ]+", " ", txt.lower())
            normalized = " ".join(normalized.split())
            if normalized in {
                "open command center", "open the command center", "show command center",
                "show the command center", "open command ceneter", "open the command ceneter",
            }:
                self._set_command_center(True)
                self._log.append_log("JARVIS: Command Center is open.")
                return
            if normalized in {
                "close command center", "close the command center", "hide command center",
                "hide the command center", "return to focus view",
            }:
                self._set_command_center(False)
                self._log.append_log("JARVIS: Returning to focus view.")
                return
            if self.on_text_command:
                def _dispatch():
                    try:
                        self.on_text_command(txt)
                    except Exception as exc:
                        self._log_sig.emit(f"ERR: Message processing failed: {exc}")
                threading.Thread(target=_dispatch, daemon=True).start()
        except Exception as exc:
            # Exceptions escaping a PyQt signal handler can abort the process.
            # Keep JARVIS alive and surface the problem in its own log instead.
            try:
                self._log.append_log(f"ERR: Message processing failed: {exc}")
            except Exception:
                print(f"[JARVIS] Message processing failed: {exc}")

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")
        if hasattr(self, "_rail_mode_lbl"):
            self._rail_mode_lbl.setText(f"LOCAL  /  {state}")
            rail_color = {
                "MUTED": C.RED,
                "THINKING": C.ACC2,
                "PROCESSING": C.ACC2,
                "SPEAKING": C.PRI,
                "LISTENING": C.GREEN,
            }.get(state, C.TEXT_DIM)
            self._rail_mode_lbl.setStyleSheet(
                f"color: {rail_color}; background: transparent; letter-spacing: 1px;"
            )
            if hasattr(self, "_rail_status_dot"):
                self._rail_status_dot.setStyleSheet(
                    f"color: {rail_color}; background: transparent;"
                )
        if hasattr(self, "_header_mode_lbl"):
            self._header_mode_lbl.setText(state)
            state_color = {
                "MUTED": C.RED,
                "THINKING": C.ACC2,
                "PROCESSING": C.ACC2,
                "SPEAKING": C.PRI,
                "LISTENING": C.TEXT_MED,
            }.get(state, C.TEXT_MED)
            self._header_mode_lbl.setStyleSheet(
                f"color: {state_color}; background: transparent; letter-spacing: 1px;"
            )
        # Sync AI canvas state
        if hasattr(self, "_ai_canvas"):
            self._ai_canvas.state = state
            # Map state to canvas mode if no specific context is set
            if state == "SPEAKING":
                self._ai_canvas.set_mode("speaking")
            elif state == "THINKING":
                self._ai_canvas.set_mode(getattr(self, "_context_mode", "thinking"))
            elif state == "LISTENING":
                self._ai_canvas.set_mode("listening")

        # Sync compact mode widget state
        if self._compact_widget:
            self._compact_widget.set_state(state)

        # Show toast for state transitions
        if state == "THINKING":
            self._show_toast("JARVIS is thinking...", "info")
        elif state == "PROCESSING":
            self._show_toast("Processing request...", "info")

    def _parse_log_for_context(self, text: str):
        """Detect context from log messages and feed task/tool widgets."""
        tl = text.lower()

        # Context mode detection
        if any(k in tl for k in ("code_helper", "dev_agent", "coding", "python", "javascript")):
            mode = "coding"
        elif any(k in tl for k in ("file_processor", "screen_process", "analyzing", "document")):
            mode = "analyzing"
        elif any(k in tl for k in ("web_search", "flight_finder", "weather", "research")):
            mode = "researching"
        elif "thinking" in tl or "🔧" in text:
            mode = "thinking"
        else:
            mode = getattr(self, "_context_mode", "idle")

        if mode != getattr(self, "_context_mode", "idle"):
            self._context_mode = mode
            try:
                self._mode_sig.emit(mode)
            except Exception:
                pass

        # Task queue feeding
        TOOL_MAP = {
            "open_app": "Open Application",
            "web_search": "Web Search",
            "deep_research": "Deep Research",
            "weather_report": "Weather Report",
            "browser_control": "Browser Control",
            "file_controller": "File Controller",
            "send_message": "Send Message",
            "reminder": "Set Reminder",
            "youtube_video": "YouTube",
            "screen_process": "Vision Analysis",
            "computer_settings": "System Settings",
            "desktop_control": "Desktop Control",
            "email_control": "Email",
            "media_control": "Media Control",
            "code_helper": "Code Assistant",
            "dev_agent": "Dev Agent",
            "web_search": "Web Search",
            "file_processor": "File Processor",
            "computer_control": "Computer Control",
            "game_updater": "Game Updater",
            "flight_finder": "Flight Finder",
        }
        for key, label in TOOL_MAP.items():
            if key in tl:
                if "📞" in text or "🔧" in text:
                    status = "calling"
                    # Show tool progress indicator
                    if hasattr(self, "_tool_progress"):
                        self._tool_progress.show_tool(label)
                elif "→" in text or "done" in tl or "✓" in text:
                    status = "done"
                    # Hide tool progress indicator
                    if hasattr(self, "_tool_progress"):
                        self._tool_progress.hide_tool()
                elif "❌" in text or "error" in tl or "fail" in tl:
                    status = "error"
                    if hasattr(self, "_tool_progress"):
                        self._tool_progress.hide_tool()
                    self._show_toast(f"Tool error: {label}", "error")
                else:
                    status = "active"
                try:
                    self._task_sig.emit(label, status)
                except Exception:
                    pass
                break

        # Tool log feeding — forward SYS/tool lines to tool widget
        if any(text.startswith(p) for p in ("SYS:", "ERR:", "FILE:")):
            try:
                self._tool_sig.emit(text)
            except Exception:
                pass
        elif "🔧" in text or "📞" in text or "→" in text:
            try:
                self._tool_sig.emit(text)
            except Exception:
                pass

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            if "gemini_api_key" in d:
                d.pop("gemini_api_key", None)
                try:
                    API_FILE.write_text(json.dumps(d, indent=4), encoding="utf-8")
                except Exception:
                    pass
            return bool(d.get("os_system"))
        except Exception:
            return False

    def _load_saved_voice(self):
        try:
            if API_FILE.exists() and hasattr(self, '_voice_combo'):
                d = json.loads(API_FILE.read_text(encoding="utf-8"))
                voice_name = d.get("voice_name", "puck")
                if isinstance(voice_name, str):
                    voice_name = voice_name.strip().lower()
                if voice_name not in VOICE_VALUE_TO_LABEL:
                    voice_name = "puck"
                index = self._voice_combo.findData(voice_name)
                if index >= 0:
                    self._voice_combo.setCurrentIndex(index)
                else:
                    self._voice_combo.setCurrentIndex(0)
                os.environ["GEMINI_VOICE_NAME"] = self._get_selected_voice()
        except Exception:
            pass

    def _sync_voice_combo(self, voice_name: str):
        self._voice_combo.blockSignals(True)
        idx = self._voice_combo.findData(voice_name.lower())
        if idx >= 0:
            self._voice_combo.setCurrentIndex(idx)
        self._voice_combo.blockSignals(False)

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 420
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str, remember_key: bool):
        from core.api_key_validator import normalize_gemini_api_key

        normalized_key = normalize_gemini_api_key(key)
        verified_key = getattr(self._overlay, "_verified_key", "") if self._overlay else ""
        if not normalized_key or normalized_key != verified_key:
            self._log.append_log("ERR: Setup blocked because the Gemini API key was not verified.")
            self._ready = False
            return
        key = normalized_key

        # Persist API key only if user explicitly opts in.
        if remember_key and isinstance(key, str) and key.strip():
            try:
                store = get_secret_store()
                store.set("gemini_api_key", key.strip())
                self._log.append_log("SYS: Gemini API key saved to OS keychain.")
            except Exception as e:
                self._log.append_log(f"SYS: Could not save key to keychain: {e}")

        os.makedirs(CONFIG_DIR, exist_ok=True)

        try:
            cfg = json.loads(API_FILE.read_text(encoding="utf-8")) if API_FILE.exists() else {}
        except Exception:
            cfg = {}
        # Do NOT persist the Gemini API key to disk. Keep it in-memory for this session only.
        cfg.pop("gemini_api_key", None)
        cfg["os_system"] = os_name
        API_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
        try:
            # Set the API key for this running process only. This ensures the key is
            # available to modules that read `os.environ['GEMINI_API_KEY']` without
            # writing it to disk — the user must re-enter it on next start.
            if isinstance(key, str) and key.strip():
                os.environ["GEMINI_API_KEY"] = key.strip()
                self._log.append_log("SYS: Gemini API key set for this session (not saved).")
        except Exception:
            pass
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._log.append_log("SYS: INITIATING SYSTEMS, SIR...")
        self._log.append_log("SYS: CORE SYSTEMS ONLINE")
        self._log.append_log("SYS: NEURAL NETWORK ACTIVE  [OK]")
        self._log.append_log("SYS: PARALLAX UI COMPLETE   [OK]")
        self._log.append_log("SYS: VOICE SYNTHESIS READY  [OK]")
        self._log.append_log(f"SYS: PLATFORM {os_name.upper()} DETECTED")
        self._log.append_log("SYS: JARVIS MARK XXXIX - ALL SYSTEMS NOMINAL")
        # After setup: show voice popup first, then name popup
        self._show_voice_select_then_name()

    def _check_and_show_name_signin(self):
        """Show the name sign-in overlay only if no name is saved in memory."""
        try:
            from memory.memory_manager import load_memory
            memory = load_memory()
            name_entry = memory.get("identity", {}).get("name")
            name = None
            if isinstance(name_entry, dict):
                name = name_entry.get("value")
            elif isinstance(name_entry, str):
                name = name_entry
            if name and name.strip():
                # Name already known — no need to ask
                return
        except Exception:
            pass
        self._show_name_signin()

    def _show_voice_select_then_name(self):
        """Show voice popup only on first boot (no saved voice); then chain name popup."""
        # Check if a voice has already been saved
        voice_already_set = False
        try:
            if API_FILE.exists():
                d = json.loads(API_FILE.read_text(encoding="utf-8"))
                if d.get("voice_name"):
                    voice_already_set = True
        except Exception:
            pass

        if voice_already_set:
            # Voice already chosen — skip straight to name check
            self._check_and_show_name_signin()
            return

        current = self._get_selected_voice()
        ov = VoiceSelectOverlay(self.centralWidget(), current_voice=current)
        cw = self.centralWidget()
        ow, oh = 420, 380
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )

        def _voice_done(voice_value: str):
            ov.hide()
            self._voice_overlay = None
            # Apply and persist the chosen voice
            idx = self._voice_combo.findData(voice_value)
            if idx >= 0:
                self._voice_combo.setCurrentIndex(idx)
            self._update_voice_btn()
            # Chain into name popup
            self._check_and_show_name_signin()

        ov.done.connect(_voice_done)
        ov.show()
        self._voice_overlay = ov

    def _show_name_signin(self):
        # Pre-fill with existing name if one is saved
        existing_name = ""
        try:
            from memory.memory_manager import load_memory
            memory = load_memory()
            name_entry = memory.get("identity", {}).get("name")
            if isinstance(name_entry, dict):
                existing_name = name_entry.get("value", "")
            elif isinstance(name_entry, str):
                existing_name = name_entry
        except Exception:
            pass

        # Don't open a second copy if already visible
        if self._name_overlay and self._name_overlay.isVisible():
            self._name_overlay.raise_()
            return

        ov = NameSignInOverlay(self.centralWidget(), existing_name=existing_name)
        cw = self.centralWidget()
        ow, oh = 420, 310
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_name_done)
        ov._close_btn.clicked.disconnect()
        ov._close_btn.clicked.connect(self._close_name_overlay)
        ov.show()
        self._name_overlay = ov

    def _close_name_overlay(self):
        if self._name_overlay:
            self._name_overlay.hide()
            self._name_overlay.deleteLater()
            self._name_overlay = None

    def _on_name_done(self, name: str):
        if self._name_overlay:
            self._name_overlay.hide()
            self._name_overlay.deleteLater()
            self._name_overlay = None
        if not name or not name.strip():
            # X button was pressed — don't overwrite saved name
            return
        # Always save — "Sir" is the default when skipped
        save_name = name.strip() if name.strip() else "Sir"
        try:
            from memory.memory_manager import update_memory
            update_memory({"identity": {"name": {"value": save_name}}})
            self._log.append_log(f"SYS: Identity set — {save_name}.")
            self._log.append_log(f"JARVIS: The workshop is now at your disposal, {save_name}.")
            self._log.append_log("JARVIS: All systems nominal. How may I assist you today?")
        except Exception as e:
            self._log.append_log(f"SYS: Could not save name: {e}")
        # Notify JarvisLive so it can update the running session immediately
        if self.on_name_change:
            try:
                self.on_name_change(save_name)
            except Exception as e:
                self._log.append_log(f"SYS: Name callback error: {e}")
        # Refresh the button label to show current name
        self._update_name_btn()

    def _update_name_btn(self):
        """Update the Change Name button to show the currently saved name."""
        if not hasattr(self, "_name_btn"):
            return
        try:
            from memory.memory_manager import load_memory
            memory = load_memory()
            name_entry = memory.get("identity", {}).get("name")
            name = None
            if isinstance(name_entry, dict):
                name = name_entry.get("value")
            elif isinstance(name_entry, str):
                name = name_entry
            if name and name.strip() and name.strip().lower() not in ("sir", "madam"):
                display_name = name.strip()
                if len(display_name) > 14:
                    display_name = display_name[:13].rstrip() + "…"
                self._name_btn.setText(f"NAME  ·  {display_name}")
            else:
                self._name_btn.setText("NAME  ·  SET")
        except Exception:
            self._name_btn.setText("NAME")

    def _show_voice_select(self):
        if self._voice_overlay and self._voice_overlay.isVisible():
            self._voice_overlay.raise_()
            return

        current = self._get_selected_voice()

        ov = VoiceSelectorOverlay(
            self.centralWidget(),
            current_provider="gemini",
            current_voice_id=current,
            current_api_key="",
        )
        cw = self.centralWidget()
        ow, oh = 520, 540
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_tts_select_done)
        ov._close_btn.clicked.disconnect()
        ov._close_btn.clicked.connect(self._close_voice_overlay)
        ov.show()
        self._voice_overlay = ov

    def _close_voice_overlay(self):
        if self._voice_overlay:
            self._voice_overlay.hide()
            self._voice_overlay.deleteLater()
            self._voice_overlay = None

    def _on_voice_select_done(self, voice_value: str):
        if hasattr(self, "_voice_overlay") and self._voice_overlay:
            self._voice_overlay.hide()
            self._voice_overlay.deleteLater()
            self._voice_overlay = None
        # Sync the hidden combo so existing voice-change logic fires
        idx = self._voice_combo.findData(voice_value)
        if idx >= 0:
            self._voice_combo.setCurrentIndex(idx)
        self._update_voice_btn()

    def _update_voice_btn(self):
        """Update the voice button label to show the currently selected voice."""
        if not hasattr(self, "_voice_btn"):
            return
        try:
            voice_val = self._get_selected_voice()
            label = VOICE_VALUE_TO_LABEL.get(voice_val, voice_val.title())
            self._voice_btn.setText(f"◈  {label.upper()}  ·  CHANGE VOICE")
        except Exception:
            self._voice_btn.setText("◈  CHANGE VOICE")

    # ------------------------------------------------------------------
    # TTS Provider overlay
    # ------------------------------------------------------------------

    def _load_saved_tts(self):
        """Read saved TTS config and update the button label."""
        self._update_tts_btn()

    def _show_tts_select(self):
        # Don't open a second copy if already visible
        if self._tts_overlay and self._tts_overlay.isVisible():
            self._tts_overlay.raise_()
            return

        # Read current provider + voice from JSON; API key from keychain
        provider = "gemini"
        voice_id = "orus"
        api_key  = ""
        try:
            if API_FILE.exists():
                d = json.loads(API_FILE.read_text(encoding="utf-8"))
                provider = d.get("tts_provider", "gemini")
                voice_id = d.get("tts_voice_id", "orus")
        except Exception:
            pass
        try:
            api_key = get_secret_store().get("tts_api_key") or ""
        except Exception:
            pass

        ov = VoiceSelectorOverlay(
            self.centralWidget(),
            current_provider=provider,
            current_voice_id=voice_id,
            current_api_key=api_key,
        )
        cw = self.centralWidget()
        ow, oh = 520, 540
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_tts_select_done)
        # Wire X button to also clear the reference
        ov._close_cb = self._close_tts_overlay
        ov._close_btn.clicked.disconnect()
        ov._close_btn.clicked.connect(self._close_tts_overlay)
        ov.show()
        self._tts_overlay = ov

    def _close_tts_overlay(self):
        if self._tts_overlay:
            self._tts_overlay.hide()
            self._tts_overlay.deleteLater()
            self._tts_overlay = None

    def _on_tts_select_done(self, provider: str, voice_id: str, api_key: str):
        if self._tts_overlay:
            self._tts_overlay.hide()
            self._tts_overlay.deleteLater()
            self._tts_overlay = None

        # Save API key to OS keychain — never written to disk in plain text
        if api_key:
            try:
                get_secret_store().set("tts_api_key", api_key)
            except Exception as e:
                self._log.append_log(f"SYS: Could not save TTS key to keychain: {e}")

        # Persist only non-sensitive fields to JSON
        try:
            cfg = json.loads(API_FILE.read_text(encoding="utf-8")) if API_FILE.exists() else {}
        except Exception:
            cfg = {}
        cfg["tts_provider"] = provider
        cfg["tts_voice_id"] = voice_id
        cfg.pop("tts_api_key",  None)   # scrub any legacy plain-text key
        cfg.pop("tts_preset",   None)   # scrub old preset field
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            API_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
        except Exception as e:
            self._log.append_log(f"SYS: Could not save TTS config: {e}")

        # Derive a friendly label for the log
        from actions.tts_engine import PROVIDER_VOICES
        label = voice_id
        for lbl, vid in PROVIDER_VOICES.get(provider, []):
            if vid == voice_id:
                label = lbl
                break
        self._log.append_log(f"SYS: Voice → {label}  ({provider})")
        self._update_tts_btn()

        # Keep the hidden Gemini combo in sync (used by on_voice_change callback)
        if provider == "gemini" and hasattr(self, "_voice_combo"):
            idx = self._voice_combo.findData(voice_id)
            if idx >= 0:
                # Block the signal so we don't double-fire on_voice_change
                self._voice_combo.blockSignals(True)
                self._voice_combo.setCurrentIndex(idx)
                self._voice_combo.blockSignals(False)

        if self.on_tts_provider_change:
            try:
                self.on_tts_provider_change(provider, api_key, voice_id)
            except Exception as e:
                self._log.append_log(f"SYS: TTS callback error: {e}")

    def _update_tts_btn(self):
        """Update the TTS button label to show the active voice name."""
        if not hasattr(self, "_tts_btn"):
            return
        try:
            provider = "gemini"
            voice_id = "orus"
            if API_FILE.exists():
                d = json.loads(API_FILE.read_text(encoding="utf-8"))
                provider = d.get("tts_provider", "gemini")
                voice_id = d.get("tts_voice_id", "orus")
            from actions.tts_engine import PROVIDER_VOICES
            label = voice_id.title()
            for lbl, vid in PROVIDER_VOICES.get(provider, []):
                if vid == voice_id:
                    label = lbl
                    break
            self._tts_btn.setText(f"VOICE  ·  {label}")
        except Exception:
            self._tts_btn.setText("VOICE")


class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class JarvisUI:

    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._closing = False
        self._app.aboutToQuit.connect(self._mark_closing)

        self._win = MainWindow(face_path)
        self._win.show()
        self.root = _RootShim(self._app)

    def _mark_closing(self):
        self._closing = True

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def _voice_combo(self):
        """Expose the legacy selector used by the live engine voice fallback."""
        return self._win._voice_combo

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_voice_change(self):
        return self._win.on_voice_change

    @on_voice_change.setter
    def on_voice_change(self, cb):
        self._win.on_voice_change = cb

    @property
    def on_name_change(self):
        return self._win.on_name_change

    @on_name_change.setter
    def on_name_change(self, cb):
        self._win.on_name_change = cb

    @property
    def on_tts_provider_change(self):
        return self._win.on_tts_provider_change

    @on_tts_provider_change.setter
    def on_tts_provider_change(self, cb):
        self._win.on_tts_provider_change = cb

    def set_state(self, state: str):
        if self._closing:
            return
        value = str(state or "IDLE")
        try:
            self._win._state_sig.emit(value)
        except RuntimeError:
            # The live session runs on a worker thread and can report one last
            # state while Qt is tearing down the main window.
            from PyQt6 import sip

            if not self._closing and not sip.isdeleted(self._win):
                raise

    def write_log(self, text: str):
        self._win._log_sig.emit(text)
        self._win._parse_log_for_context(text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")

    def sync_voice_display(self, voice_name: str):
        self._win._voice_sig.emit(voice_name)

    def show_subtitle(self, text: str):
        """Thread-safe subtitle display request."""
        try:
            # Don't show subtitles when muted.
            if getattr(self._win, "_muted", False) or (
                getattr(self._win, "hud", None) is not None and getattr(self._win.hud, "muted", False)
            ):
                return
            self._win._sub_sig.emit(text)
        except Exception:
            pass

    def clear_subtitle(self):
        """Thread-safe subtitle clear."""
        try:
            self._win._sub_clear_sig.emit()
        except Exception:
            pass

    def start_subtitle_hold(self):
        """Thread-safe: start the subtitle fade-out hold timer (call when JARVIS finishes talking)."""
        try:
            self._win._sub_hold_sig.emit()
        except Exception:
            pass

    def show_vision_preview(self, source: str = "screen"):
        """Show a thread-safe draggable live screen or camera preview."""
        self._win._vision_preview_sig.emit(source)

    def hide_vision_preview(self, delay_ms: int = 1800):
        """Mark vision complete and close its preview after a short hold."""
        self._win._vision_preview_hide_sig.emit(max(0, int(delay_ms)))

    def show_research_progress(self, question: str):
        """Open the compact background Deep Research status surface."""
        self._win._research_progress_sig.emit({
            "question": str(question or "Deep research"),
            "percent": 0,
            "phase": "Queued",
            "artifacts": [],
            "warnings": [],
        })

    def update_research_progress(
        self,
        question: str,
        percent: int,
        phase: str,
        artifacts: list[str] | None = None,
        warnings: list[str] | None = None,
    ):
        """Thread-safe update for real research phases and artifacts."""
        self._win._research_progress_sig.emit({
            "question": str(question or "Deep research"),
            "percent": max(0, min(100, int(percent or 0))),
            "phase": str(phase or "Researching"),
            "artifacts": list(artifacts or []),
            "warnings": list(warnings or []),
        })

    def finish_research_progress(self, state: str, detail: str):
        """Show a visible research failure or cancellation without crashing the UI."""
        self._win._research_progress_finish_sig.emit(str(state), str(detail))

    def hide_research_progress(self):
        self._win._research_progress_hide_sig.emit()

    def show_presentation_progress(self, title: str, visible: bool = False):
        """Show the chosen foreground or background presentation status surface."""
        self._win._presentation_progress_sig.emit({
            "question": str(title or "Presentation"),
            "percent": 0,
            "phase": "Queued",
            "artifacts": [],
            "warnings": [],
            "visible": bool(visible),
        })

    def update_presentation_progress(
        self,
        title: str,
        percent: int,
        phase: str,
        visible: bool = False,
        artifacts: list[str] | None = None,
        warnings: list[str] | None = None,
    ):
        """Thread-safe update using real presentation build phases."""
        self._win._presentation_progress_sig.emit({
            "question": str(title or "Presentation"),
            "percent": max(0, min(100, int(percent or 0))),
            "phase": str(phase or "Building presentation"),
            "artifacts": list(artifacts or []),
            "warnings": list(warnings or []),
            "visible": bool(visible),
        })

    def finish_presentation_progress(self, state: str, detail: str):
        """Display cancellation or failure without leaving stale task state."""
        self._win._presentation_progress_finish_sig.emit(str(state), str(detail))

    def hide_presentation_progress(self):
        self._win._presentation_progress_hide_sig.emit()

    # ── New UI enhancement methods ───────────────────────────────────────

    def show_toast(self, message: str, toast_type: str = "info"):
        """Thread-safe toast notification. Types: info, success, warning, error."""
        try:
            self._win._show_toast(message, toast_type)
        except Exception:
            pass

    def show_tool_progress(self, tool_name: str):
        """Show tool execution progress indicator."""
        try:
            self._win._tool_progress.show_tool(tool_name)
        except Exception:
            pass

    def hide_tool_progress(self):
        """Hide tool execution progress indicator."""
        try:
            self._win._tool_progress.hide_tool()
        except Exception:
            pass

    def set_theme(self, theme_key: str):
        """Thread-safe theme change requested by JARVIS."""
        self._win._theme_sig.emit(theme_key)

    def set_graphics_quality(self, quality: str):
        """Thread-safe graphics quality change requested by JARVIS."""
        self._win._graphics_sig.emit(quality)

    def handle_ui_command(self, action: str):
        """Thread-safe access to secondary interface utilities."""
        self._win._ui_command_sig.emit(action)

    def toggle_compact_mode(self):
        """Toggle compact/mini mode."""
        self._win._toggle_compact_mode()
