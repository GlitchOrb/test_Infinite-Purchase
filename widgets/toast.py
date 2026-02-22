from __future__ import annotations

from PyQt5.QtCore import QPropertyAnimation, QTimer, Qt
from PyQt5.QtWidgets import QLabel, QWidget


class Toast(QLabel):
    def __init__(self, parent: QWidget, text: str, level: str = "info") -> None:
        super().__init__(parent)
        self.setObjectName(f"toast-{level}")
        self.setText(text)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("")

        self.adjustSize()
        w = min(max(220, self.width() + 24), max(260, parent.width() - 48))
        h = self.height() + 16
        self.resize(w, h)
        self.move((parent.width() - w) // 2, parent.height() - h - 24)

        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(180)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

        QTimer.singleShot(2200, self._fade_out)

    def _fade_out(self) -> None:
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(220)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self.deleteLater)
        self._anim.start()


def show_toast(parent: QWidget, text: str, level: str = "info") -> None:
    toast = Toast(parent, text, level=level)
    toast.show()
