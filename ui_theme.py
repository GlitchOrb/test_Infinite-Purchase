"""
ui_theme.py
===========
Design system for Alpha Predator v4.0 desktop application.

Light fintech theme — Bloomberg meets Notion.
White background, navy headers, green/red/orange accents.
"""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QFrame, QLabel, QPushButton, QWidget, QGraphicsDropShadowEffect,
    QVBoxLayout, QHBoxLayout, QSizePolicy,
)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QColor, QFont, QPalette


# ======================================================================= #
#  Color palette
# ======================================================================= #

class C:
    """Color constants — use as ``C.NAVY``, ``C.BG``, etc."""
    BG         = "#FFFFFF"
    BG_CARD    = "#F8F9FC"
    BG_HOVER   = "#F0F2F8"
    BORDER     = "#E2E5ED"

    NAVY       = "#1B2A4A"
    NAVY_LIGHT = "#2D4373"
    NAVY_TEXT  = "#354764"
    TEXT       = "#2C3E50"
    TEXT_SUB   = "#7F8CA3"
    TEXT_MUTED = "#A0AABB"

    GREEN      = "#00B37E"
    GREEN_BG   = "#E6F9F1"
    GREEN_DARK = "#009966"

    RED        = "#E74C3C"
    RED_BG     = "#FDF0EE"
    RED_DARK   = "#C0392B"

    ORANGE     = "#F39C12"
    ORANGE_BG  = "#FFF8E1"

    BLUE       = "#3498DB"
    BLUE_BG    = "#EBF5FB"

    KILL_RED   = "#DC2626"
    KILL_BG    = "#FEE2E2"


# ======================================================================= #
#  Fonts
# ======================================================================= #

class F:
    """Font factories."""

    @staticmethod
    def title() -> QFont:
        f = QFont("Segoe UI", 18)
        f.setBold(True)
        return f

    @staticmethod
    def heading() -> QFont:
        f = QFont("Segoe UI", 13)
        f.setBold(True)
        return f

    @staticmethod
    def body() -> QFont:
        return QFont("Segoe UI", 10)

    @staticmethod
    def body_bold() -> QFont:
        f = QFont("Segoe UI", 10)
        f.setBold(True)
        return f

    @staticmethod
    def mono() -> QFont:
        return QFont("Consolas", 10)

    @staticmethod
    def small() -> QFont:
        return QFont("Segoe UI", 9)

    @staticmethod
    def badge() -> QFont:
        f = QFont("Segoe UI", 9)
        f.setBold(True)
        return f


# ======================================================================= #
#  Global stylesheet
# ======================================================================= #

GLOBAL_STYLE = f"""
    * {{
        font-family: 'Segoe UI', sans-serif;
    }}
    QMainWindow, QWidget {{
        background-color: {C.BG};
        color: {C.TEXT};
    }}
    QScrollArea {{
        border: none;
        background: {C.BG};
    }}
    QToolTip {{
        background: {C.NAVY};
        color: white;
        border: none;
        padding: 6px 10px;
        border-radius: 4px;
        font-size: 10px;
    }}
"""


# ======================================================================= #
#  Reusable styled widgets
# ======================================================================= #

def make_card(parent: QWidget | None = None, padding: int = 16) -> QFrame:
    """White rounded card with subtle drop shadow."""
    card = QFrame(parent)
    card.setStyleSheet(f"""
        QFrame {{
            background: {C.BG_CARD};
            border: 1px solid {C.BORDER};
            border-radius: 10px;
        }}
    """)
    shadow = QGraphicsDropShadowEffect()
    shadow.setBlurRadius(18)
    shadow.setXOffset(0)
    shadow.setYOffset(2)
    shadow.setColor(QColor(0, 0, 0, 22))
    card.setGraphicsEffect(shadow)

    layout = QVBoxLayout(card)
    layout.setContentsMargins(padding, padding, padding, padding)
    layout.setSpacing(8)
    return card


def make_badge(text: str, bg: str, fg: str = "#FFFFFF") -> QLabel:
    """Pill-shaped badge label."""
    badge = QLabel(text)
    badge.setFont(F.badge())
    badge.setAlignment(Qt.AlignCenter)
    badge.setFixedHeight(26)
    badge.setMinimumWidth(70)
    badge.setStyleSheet(f"""
        QLabel {{
            background: {bg};
            color: {fg};
            border-radius: 13px;
            padding: 0 14px;
            letter-spacing: 1px;
        }}
    """)
    return badge


def regime_badge(state: str) -> QLabel:
    """Create a badge colored by regime state."""
    colors = {
        "BULL_ACTIVE":  (C.GREEN, "#FFFFFF"),
        "BEAR_ACTIVE":  (C.RED, "#FFFFFF"),
        "TRANSITION":   (C.ORANGE, "#FFFFFF"),
        "NEUTRAL":      (C.NAVY_LIGHT, "#FFFFFF"),
    }
    label = state.replace("_ACTIVE", "")
    bg, fg = colors.get(state, (C.TEXT_MUTED, "#FFFFFF"))
    return make_badge(label, bg, fg)


def make_kv_row(key: str, value: str = "—",
                value_color: str | None = None) -> tuple[QLabel, QLabel]:
    """Key-value row: muted key on left, bold value on right."""
    k = QLabel(key)
    k.setFont(F.small())
    k.setStyleSheet(f"color: {C.TEXT_SUB}; background: transparent; border: none;")

    v = QLabel(value)
    v.setFont(F.body_bold())
    color = value_color or C.TEXT
    v.setStyleSheet(f"color: {color}; background: transparent; border: none;")
    v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return k, v


def make_header_label(text: str) -> QLabel:
    """Navy section header inside a card."""
    lbl = QLabel(text)
    lbl.setFont(F.heading())
    lbl.setStyleSheet(f"color: {C.NAVY}; background: transparent; border: none;")
    return lbl


def make_kill_button() -> QPushButton:
    """Big red kill-switch toggle button."""
    btn = QPushButton("KILL SWITCH")
    btn.setCheckable(True)
    btn.setFixedHeight(36)
    btn.setMinimumWidth(130)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setStyleSheet(f"""
        QPushButton {{
            background: {C.BG_CARD};
            color: {C.KILL_RED};
            border: 2px solid {C.KILL_RED};
            border-radius: 8px;
            font-weight: bold;
            font-size: 11px;
            padding: 0 16px;
            letter-spacing: 1px;
        }}
        QPushButton:hover {{
            background: {C.KILL_BG};
        }}
        QPushButton:checked {{
            background: {C.KILL_RED};
            color: white;
            border-color: {C.RED_DARK};
        }}
    """)
    return btn


def make_primary_button(text: str) -> QPushButton:
    """Navy primary action button."""
    btn = QPushButton(text)
    btn.setFixedHeight(34)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setStyleSheet(f"""
        QPushButton {{
            background: {C.NAVY};
            color: white;
            border: none;
            border-radius: 8px;
            font-weight: bold;
            font-size: 11px;
            padding: 0 20px;
        }}
        QPushButton:hover {{
            background: {C.NAVY_LIGHT};
        }}
        QPushButton:pressed {{
            background: #152238;
        }}
        QPushButton:disabled {{
            background: {C.TEXT_MUTED};
        }}
    """)
    return btn


def make_secondary_button(text: str) -> QPushButton:
    """Outlined secondary button."""
    btn = QPushButton(text)
    btn.setFixedHeight(32)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setStyleSheet(f"""
        QPushButton {{
            background: transparent;
            color: {C.NAVY};
            border: 1.5px solid {C.BORDER};
            border-radius: 7px;
            font-size: 10px;
            padding: 0 14px;
        }}
        QPushButton:hover {{
            background: {C.BG_HOVER};
            border-color: {C.NAVY_LIGHT};
        }}
    """)
    return btn


# ======================================================================= #
#  Emergency overlay
# ======================================================================= #

def make_emergency_overlay(parent: QWidget) -> QLabel:
    """Semi-transparent red banner shown when kill switch is active."""
    overlay = QLabel("EMERGENCY MODE — ALL ORDERS BLOCKED", parent)
    overlay.setAlignment(Qt.AlignCenter)
    overlay.setFont(F.title())
    overlay.setStyleSheet(f"""
        QLabel {{
            background: rgba(220, 38, 38, 0.88);
            color: white;
            border: none;
            letter-spacing: 2px;
        }}
    """)
    overlay.setFixedHeight(52)
    overlay.hide()
    return overlay
