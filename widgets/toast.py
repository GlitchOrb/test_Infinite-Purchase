from __future__ import annotations

from PyQt5.QtCore import QEasingCurve, QPropertyAnimation, QTimer, Qt
from PyQt5.QtWidgets import QLabel, QGraphicsOpacityEffect, QWidget


class Toast(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setObjectName("toastLabel")

        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)

        self._fade = QPropertyAnimation(self.opacity_effect, b"opacity", self)
        self._fade.setDuration(260)
        self._fade.setEasingCurve(QEasingCurve.InOutCubic)
        self._fade.finished.connect(self._on_fade_finished)

        self.hide()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        self.label.setGeometry(0, 0, self.width(), self.height())
        super().resizeEvent(event)

    def show_message(self, text: str, kind: str = "info", timeout_ms: int = 2200) -> None:
        if not self.parentWidget():
            return

        palette = {
            "success": ("#16A34A", "#FFFFFF"),
            "error": ("#E5484D", "#FFFFFF"),
            "info": ("#2A65EA", "#FFFFFF"),
        }
        bg, fg = palette.get(kind, palette["info"])
        self.label.setText(f"  {text}  ")
        self.label.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:14px; padding:10px 14px; font-weight:600;"
        )
        self.adjustSize()

        parent = self.parentWidget()
        width = min(max(260, self.label.sizeHint().width() + 12), max(300, parent.width() - 48))
        self.resize(width, self.label.sizeHint().height() + 6)
        self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - 24)

        self._fade.stop()
        self.opacity_effect.setOpacity(0.0)
        self.show()
        self.raise_()

        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        QTimer.singleShot(timeout_ms, self._fade_out)

    def _fade_out(self) -> None:
        self._fade.stop()
        self._fade.setStartValue(self.opacity_effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        if self.opacity_effect.opacity() <= 0.01:
            self.hide()
