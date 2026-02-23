"""
backtest/engine.py
==================
Vector-aligned event-driven backtester.

Simulates the Runtime environment:
1. Feeds historical OHLCV to StrategyEngine.
2. Feeds DailyDecisions + Prices to TradeManager.
3. Simulates execution (slippage, commission) and fills.
4. Tracks portfolio equity and FIFO trade records.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from backtest.config import BacktestConfig
from backtest.report import BacktestReport, TradeRecord
from strategy_engine import StrategyEngine
from trade_manager import OrderIntent, OrderSide, TradeManager, TradeManagerState

log = logging.getLogger(__name__)


class Backtester:
    """
    Orchestrates the backtest simulation.

    Usage
    -----
    >>> bt = Backtester(BacktestConfig())
    >>> report = bt.run(soxx_df, soxl_df, soxs_df)
    """

    def __init__(self, config: BacktestConfig, strategy=None) -> None:
        self.cfg = config
        self.strategy = strategy or StrategyEngine()
        self.tm = TradeManager()

    def run(
        self,
        soxx: pd.DataFrame,
        soxl: pd.DataFrame,
        soxs: pd.DataFrame
    ) -> BacktestReport:
        """
        Run the backtest.

        Parameters
        ----------
        soxx : pd.DataFrame
            Signal asset OHLCV (must contain 'close').
        soxl : pd.DataFrame
            Bull execution asset OHLCV.
        soxs : pd.DataFrame
            Bear execution asset OHLCV.

        Returns
        -------
        BacktestReport
        """
        # 1. Pre-calculate Strategy Decisions (Vectorized/Fast)
        #    StrategyEngine is deterministic and only depends on SOXX.
        log.info("Computing strategy decisions...")
        decisions = self.strategy.run(soxx)
        decision_map = {d.date: d for d in decisions}

        # 2. Align Data Indices
        #    Intersection of all available data points where we have a decision.
        common_dates = soxx.index.intersection(soxl.index).intersection(soxs.index).sort_values()
        valid_dates = [d for d in common_dates if d in decision_map]

        if not valid_dates:
            raise RuntimeError("No overlapping dates found between SOXX/SOXL/SOXS and strategy outputs.")

        # 3. Simulation Loop
        cash = self.cfg.initial_capital
        holdings = {"SOXL": 0, "SOXS": 0}
        tm_state = TradeManagerState()
        
        # FIFO Lot Tracking: symbol -> deque of [date, price, qty]
        lots: Dict[str, deque] = {"SOXL": deque(), "SOXS": deque()}
        
        equity_curve_data = {}
        closed_trades: List[TradeRecord] = []

        log.info(f"Starting simulation on {len(valid_dates)} trading days...")

        for date in valid_dates:
            decision = decision_map[date]
            px_soxl = float(soxl.loc[date, "close"])
            px_soxs = float(soxs.loc[date, "close"])
            
            # Mark-to-Market Equity
            equity = cash + (holdings["SOXL"] * px_soxl) + (holdings["SOXS"] * px_soxs)
            
            # Generate Intents
            intents, tm_state = self.tm.process_day(
                decision, px_soxl, px_soxs, equity, tm_state
            )

            # Execute Intents
            # TradeManager sorts intents by priority, but we should process SELLS before BUYS
            # to free up cash, although TradeManager usually prioritizes sells.
            
            # We'll process in the order provided by TradeManager, assuming it handles priority.
            # (TradeManager puts sells (priority < 60) before buys (priority >= 60))
            
            for intent in intents:
                symbol = intent.symbol
                price = px_soxl if symbol == "SOXL" else px_soxs
                
                if intent.side == OrderSide.SELL:
                    # --- EXECUTE SELL ---
                    qty_to_sell = intent.qty
                    if qty_to_sell > holdings[symbol]:
                        qty_to_sell = holdings[symbol]  # Cap at actual holding
                    
                    if qty_to_sell > 0:
                        # Apply Slippage (Sell lower)
                        exec_price = price * (1 - self.cfg.slippage_pct)
                        proceeds = qty_to_sell * exec_price
                        comm = proceeds * self.cfg.commission_pct
                        net_proceeds = proceeds - comm
                        
                        cash += net_proceeds
                        holdings[symbol] -= qty_to_sell
                        
                        # FIFO Accounting & Trade Records
                        realized_pnl_for_vampire = self._process_fifo_sell(
                            lots, symbol, qty_to_sell, exec_price, date, closed_trades
                        )
                        
                        # Update TradeManager State (Fill)
                        tm_state = self.tm.apply_fill(
                            symbol, OrderSide.SELL, qty_to_sell, exec_price, date, tm_state
                        )
                        
                        # Vampire Rebalance Hook
                        # If we sold SOXS for a profit, notify TradeManager to potentially inject budget
                        if symbol == "SOXS" and realized_pnl_for_vampire > 0:
                            tm_state = self.tm.on_realized_pnl(
                                "SOXS", realized_pnl_for_vampire, decision.effective_state, px_soxl, tm_state
                            )

                elif intent.side == OrderSide.BUY:
                    # --- EXECUTE BUY ---
                    # Calculate Qty from Notional if needed
                    qty_to_buy = intent.qty
                    if qty_to_buy == 0 and intent.notional > 0:
                        # Apply Slippage estimate for sizing (Buy higher)
                        est_price = price * (1 + self.cfg.slippage_pct)
                        if est_price > 0:
                            qty_to_buy = int(intent.notional / est_price)
                    
                    if qty_to_buy > 0:
                        # Check Cash
                        est_cost = qty_to_buy * price * (1 + self.cfg.slippage_pct)
                        if est_cost * (1 + self.cfg.commission_pct) > cash:
                            # Insufficient cash - scale down (simple logic)
                            scale = cash / (est_cost * (1 + self.cfg.commission_pct))
                            qty_to_buy = int(qty_to_buy * scale)
                        
                        if qty_to_buy > 0:
                            exec_price = price * (1 + self.cfg.slippage_pct)
                            cost = qty_to_buy * exec_price
                            comm = cost * self.cfg.commission_pct
                            total_cost = cost + comm
                            
                            cash -= total_cost
                            holdings[symbol] += qty_to_buy
                            
                            # Add to FIFO lots
                            lots[symbol].append([date, exec_price, qty_to_buy])
                            
                            # Update TradeManager State (Fill)
                            tm_state = self.tm.apply_fill(
                                symbol, OrderSide.BUY, qty_to_buy, exec_price, date, tm_state
                            )

            # End of Day Reporting
            final_equity = cash + (holdings["SOXL"] * px_soxl) + (holdings["SOXS"] * px_soxs)
            equity_curve_data[date] = final_equity

        # 4. Compile Report
        equity_series = pd.Series(equity_curve_data)
        
        total_ret = (equity_series.iloc[-1] / self.cfg.initial_capital) - 1.0
        
        # CAGR
        days = (equity_series.index[-1] - equity_series.index[0]).days
        years = days / 365.25
        cagr = ((equity_series.iloc[-1] / self.cfg.initial_capital) ** (1 / years)) - 1.0 if years > 0 else 0.0
        
        # MDD
        rolling_max = equity_series.cummax()
        drawdown = (equity_series - rolling_max) / rolling_max
        mdd = drawdown.min()
        
        # Sharpe
        daily_rets = equity_series.pct_change().dropna()
        excess_rets = daily_rets - (self.cfg.risk_free_rate / 252)
        sharpe = (excess_rets.mean() / excess_rets.std()) * np.sqrt(252) if not excess_rets.empty and excess_rets.std() > 0 else 0.0
        
        # Win Rate
        wins = sum(1 for t in closed_trades if t.pnl > 0)
        win_rate = wins / len(closed_trades) if closed_trades else 0.0

        return BacktestReport(
            initial_capital=self.cfg.initial_capital,
            final_capital=equity_series.iloc[-1],
            total_return=total_ret,
            cagr=cagr,
            mdd=mdd,
            sharpe_ratio=sharpe,
            win_rate=win_rate,
            total_trades=len(closed_trades),
            equity_curve=equity_series,
            trades=closed_trades
        )

    def _process_fifo_sell(
        self,
        lots: Dict[str, deque],
        symbol: str,
        qty_sold: int,
        sell_price: float,
        sell_date: pd.Timestamp,
        trade_list: List[TradeRecord]
    ) -> float:
        """Process a sell against open lots using FIFO. Returns total realized PnL."""
        remaining = qty_sold
        total_pnl = 0.0
        
        while remaining > 0 and lots[symbol]:
            # Peek at oldest lot: [date, price, qty]
            lot = lots[symbol][0]
            lot_date, lot_entry_px, lot_qty = lot[0], lot[1], lot[2]
            
            matched_qty = min(remaining, lot_qty)
            
            # Calculate PnL
            pnl = (sell_price - lot_entry_px) * matched_qty
            total_pnl += pnl
            
            # Record Trade
            ret_pct = (sell_price / lot_entry_px) - 1.0
            hold_days = (sell_date - lot_date).days
            
            trade_list.append(TradeRecord(
                symbol=symbol,
                side="LONG",
                entry_date=lot_date,
                exit_date=sell_date,
                entry_price=lot_entry_px,
                exit_price=sell_price,
                qty=matched_qty,
                pnl=pnl,
                return_pct=ret_pct,
                holding_days=hold_days
            ))
            
            # Update Lot
            if matched_qty == lot_qty:
                lots[symbol].popleft()  # Fully consumed
            else:
                lot[2] -= matched_qty   # Partially consumed
            
            remaining -= matched_qty
            
        return total_pnl