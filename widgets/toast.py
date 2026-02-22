"""토스트 위젯 — 화면 하단 슬라이드-인 알림.

기능:
 • 성공(✅ 녹색) / 오류(❌ 빨강) / 정보(🔵 파랑) 3종 스타일
 • 아래에서 위로 슬라이드-인 → 3초 후 페이드-아웃
 • 아이콘 자동 표시
 • 과한 애니메이션 없이 부드러운 전환
"""

from __future__ import annotations

from PyQt5.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QPoint,
    QTimer,
    Qt,
)
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QLabel, QWidget


_PALETTE = {
    "success": ("#32A85C", "#FFFFFF", "✅"),
    "error":   ("#E05A5A", "#FFFFFF", "❌"),
    "info":    ("#3182F6", "#FFFFFF", "ℹ️"),
}


class Toast(QWidget):
    """화면 하단 슬라이드 인/아웃 토스트 알림."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setObjectName("toastLabel")

        # Opacity effect for fade-out
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)

        # Slide animation (position)
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(300)
        self._slide.setEasingCurve(QEasingCurve.OutCubic)

        # Fade animation (opacity)
        self._fade = QPropertyAnimation(self.opacity_effect, b"opacity", self)
        self._fade.setDuration(280)
        self._fade.setEasingCurve(QEasingCurve.InOutCubic)
        self._fade.finished.connect(self._on_fade_finished)

        self.hide()

    # ─── public ───
    def show_message(
        self,
        text: str,
        kind: str = "info",
        timeout_ms: int = 3000,
    ) -> None:
        parent = self.parentWidget()
        if not parent:
            return

        bg, fg, icon = _PALETTE.get(kind, _PALETTE["info"])
        display_text = f"  {icon}  {text}  "

        self.label.setText(display_text)
        self.label.setStyleSheet(
            f"background: {bg};"
            f" color: {fg};"
            f" border-radius: 14px;"
            f" padding: 12px 20px;"
            f" font-weight: 600;"
            f" font-size: 13px;"
        )
        self.adjustSize()

        width = min(
            max(280, self.label.sizeHint().width() + 16),
            max(320, parent.width() - 48),
        )
        height = self.label.sizeHint().height() + 8
        self.resize(width, height)

        # Slide start: off-screen below parent
        x = (parent.width() - self.width()) // 2
        y_end = parent.height() - self.height() - 28
        y_start = parent.height() + 10

        self._slide.stop()
        self._fade.stop()

        self.opacity_effect.setOpacity(1.0)
        self.move(x, y_start)
        self.show()
        self.raise_()

        # Slide in
        self._slide.setStartValue(QPoint(x, y_start))
        self._slide.setEndValue(QPoint(x, y_end))
        self._slide.start()

        # Schedule fade-out
        QTimer.singleShot(timeout_ms, self._fade_out)

    # ─── internal ───
    def resizeEvent(self, event) -> None:  # type: ignore[override]
        self.label.setGeometry(0, 0, self.width(), self.height())
        super().resizeEvent(event)

    def _fade_out(self) -> None:
        self._fade.stop()
        self._fade.setStartValue(self.opacity_effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        if self.opacity_effect.opacity() <= 0.01:
            self.hide()
