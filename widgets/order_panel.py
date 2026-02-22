from __future__ import annotations

from typing import Dict

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class OrderPanel(QWidget):
    order_requested = pyqtSignal(dict)
    cancel_all_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        tabs = QTabWidget()
        tabs.setObjectName("orderTabs")
        root.addWidget(tabs)

        general = QWidget()
        quick = QWidget()
        condition = QWidget()
        tabs.addTab(general, "일반주문")
        tabs.addTab(quick, "간편주문")
        tabs.addTab(condition, "조건주문")

        layout = QVBoxLayout(general)
        layout.setSpacing(12)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.side_box = QComboBox()
        self.side_box.addItems(["BUY", "SELL"])

        self.order_type_box = QComboBox()
        self.order_type_box.addItems(["MARKET", "LIMIT"])
        self.order_type_box.currentTextChanged.connect(self._on_order_type_changed)

        self.qty_mode_box = QComboBox()
        self.qty_mode_box.addItems(["Shares", "$", "%"])

        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 10_000_000)
        self.qty_spin.setValue(1)

        self.limit_price_spin = QDoubleSpinBox()
        self.limit_price_spin.setDecimals(4)
        self.limit_price_spin.setRange(0.0, 1_000_000.0)
        self.limit_price_spin.setEnabled(False)

        form.addRow("Side", self.side_box)
        form.addRow("Order Type", self.order_type_box)
        form.addRow("Quantity Mode", self.qty_mode_box)
        form.addRow("Quantity", self.qty_spin)
        form.addRow("Limit Price", self.limit_price_spin)
        layout.addLayout(form)

        presets_layout = QGridLayout()
        presets_layout.setHorizontalSpacing(8)
        for i, val in enumerate([1, 10, 100, 0]):
            txt = "Max" if val == 0 else str(val)
            btn = QPushButton(txt)
            btn.setObjectName("presetButton")
            btn.clicked.connect(lambda _, v=val: self._set_preset(v))
            presets_layout.addWidget(btn, 0, i)
        layout.addLayout(presets_layout)

        btn_row_1 = QHBoxLayout()
        btn_row_1.setSpacing(8)
        self.btn_curr_buy = QPushButton("현재가 매수")
        self.btn_curr_sell = QPushButton("현재가 매도")
        self.btn_curr_buy.setObjectName("buyButton")
        self.btn_curr_sell.setObjectName("sellButton")
        self.btn_curr_buy.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_curr_sell.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn_row_1.addWidget(self.btn_curr_buy)
        btn_row_1.addWidget(self.btn_curr_sell)
        layout.addLayout(btn_row_1)

        btn_row_2 = QHBoxLayout()
        btn_row_2.setSpacing(8)
        self.btn_mkt_buy = QPushButton("시장가 매수")
        self.btn_mkt_sell = QPushButton("시장가 매도")
        self.btn_mkt_buy.setObjectName("buyButton")
        self.btn_mkt_sell.setObjectName("sellButton")
        self.btn_mkt_buy.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_mkt_sell.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn_row_2.addWidget(self.btn_mkt_buy)
        btn_row_2.addWidget(self.btn_mkt_sell)
        layout.addLayout(btn_row_2)

        self.btn_cancel_all = QPushButton("전체취소")
        self.btn_cancel_all.setObjectName("neutralButton")
        layout.addWidget(self.btn_cancel_all)

        quick_layout = QVBoxLayout(quick)
        quick_layout.addWidget(QLabel("간편주문 프리셋은 일반주문 설정과 동일하게 동작합니다."))
        quick_layout.addStretch(1)

        condition_layout = QVBoxLayout(condition)
        condition_layout.addWidget(QLabel("조건주문은 우측 조건주문 패널과 연동됩니다."))
        condition_layout.addStretch(1)

        self.btn_curr_buy.clicked.connect(lambda: self._emit_order("BUY", "LIMIT_CURRENT"))
        self.btn_curr_sell.clicked.connect(lambda: self._emit_order("SELL", "LIMIT_CURRENT"))
        self.btn_mkt_buy.clicked.connect(lambda: self._emit_order("BUY", "MARKET"))
        self.btn_mkt_sell.clicked.connect(lambda: self._emit_order("SELL", "MARKET"))
        self.btn_cancel_all.clicked.connect(self.cancel_all_requested.emit)

        self.status = QLabel("Ready")
        self.status.setObjectName("secondaryLabel")
        layout.addWidget(self.status)

    def set_manual_enabled(self, enabled: bool) -> None:
        for w in [
            self.side_box,
            self.order_type_box,
            self.qty_mode_box,
            self.qty_spin,
            self.limit_price_spin,
            self.btn_curr_buy,
            self.btn_curr_sell,
            self.btn_mkt_buy,
            self.btn_mkt_sell,
            self.btn_cancel_all,
        ]:
            w.setEnabled(enabled)

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def _on_order_type_changed(self, text: str) -> None:
        self.limit_price_spin.setEnabled(text == "LIMIT")

    def _set_preset(self, val: int) -> None:
        if val == 0:
            self.qty_spin.setValue(self.qty_spin.maximum())
        else:
            self.qty_spin.setValue(val)

    def _emit_order(self, side: str, action_type: str) -> None:
        payload: Dict[str, object] = {
            "side": side,
            "action_type": action_type,
            "order_type": self.order_type_box.currentText(),
            "qty_mode": self.qty_mode_box.currentText(),
            "qty": int(self.qty_spin.value()),
            "limit_price": float(self.limit_price_spin.value()) if self.limit_price_spin.isEnabled() else None,
        }
        self.order_requested.emit(payload)
