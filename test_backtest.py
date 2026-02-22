"""
test_backtest.py
================
Tests for the backtest framework (backtest.py).

Run with:  pytest test_backtest.py -v
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest import (
    BacktestMetrics,
    BacktestResult,
    SweepResult,
    WalkForwardResult,
    _compute_metrics,
    build_vbt_signals,
    export_report,
    export_sweep_report,
    export_walkforward_report,
    generate_sample_data,
    print_metrics,
    run_single_backtest,
    sweep_parameters,
    walk_forward_quarterly,
)
from trade_manager import TradeManagerConfig


# ======================================================================= #
#  Fixtures
# ======================================================================= #

@pytest.fixture(scope="module")
def sample_data():
    """Generate sample data once for the module (expensive)."""
    soxx, soxl, soxs = generate_sample_data(years=5, seed=42)
    return soxx, soxl, soxs


@pytest.fixture
def output_dir(tmp_path):
    """Temporary output directory for exports."""
    return str(tmp_path / "bt_out")


# ======================================================================= #
#  1) Synthetic data generator
# ======================================================================= #

class TestGenerateSampleData:

    def test_shape(self):
        soxx, soxl, soxs = generate_sample_data(years=2, seed=0)
        assert len(soxx) == 2 * 252
        assert len(soxl) == 2 * 252
        assert len(soxs) == 2 * 252

    def test_columns(self):
        soxx, _, _ = generate_sample_data(years=1)
        assert set(soxx.columns) >= {"open", "high", "low", "close", "volume"}

    def test_index_is_datetime(self):
        soxx, _, _ = generate_sample_data(years=1)
        assert isinstance(soxx.index, pd.DatetimeIndex)

    def test_soxl_3x_leverage(self):
        """SOXL daily return should be approx 3x SOXX daily return."""
        soxx, soxl, _ = generate_sample_data(years=2, seed=99)
        soxx_ret = soxx["close"].pct_change().dropna()
        soxl_ret = soxl["close"].pct_change().dropna()
        # Ratio should be approximately 3
        ratio = (soxl_ret / soxx_ret).replace([np.inf, -np.inf], np.nan).dropna()
        median_ratio = ratio.median()
        assert abs(median_ratio - 3.0) < 0.1, f"Median leverage ratio was {median_ratio}"

    def test_deterministic(self):
        soxx1, _, _ = generate_sample_data(years=1, seed=42)
        soxx2, _, _ = generate_sample_data(years=1, seed=42)
        pd.testing.assert_frame_equal(soxx1, soxx2)


# ======================================================================= #
#  2) Single backtest
# ======================================================================= #

class TestSingleBacktest:

    def test_returns_result(self, sample_data):
        soxx, soxl, soxs = sample_data
        result = run_single_backtest(soxx, soxl, soxs, initial_capital=100_000)
        assert isinstance(result, BacktestResult)

    def test_equity_curve_length(self, sample_data):
        soxx, soxl, soxs = sample_data
        result = run_single_backtest(soxx, soxl, soxs)
        assert len(result.equity_curve) > 0

    def test_equity_starts_at_capital(self, sample_data):
        soxx, soxl, soxs = sample_data
        result = run_single_backtest(soxx, soxl, soxs, initial_capital=50_000)
        assert result.equity_curve.iloc[0] == pytest.approx(50_000, rel=0.01)

    def test_decisions_not_empty(self, sample_data):
        soxx, soxl, soxs = sample_data
        result = run_single_backtest(soxx, soxl, soxs)
        assert not result.decisions.empty

    def test_has_sharpe_ratio(self, sample_data):
        soxx, soxl, soxs = sample_data
        result = run_single_backtest(soxx, soxl, soxs)
        assert isinstance(result.metrics.sharpe_ratio, float)

    def test_custom_trade_config(self, sample_data):
        soxx, soxl, soxs = sample_data
        cfg = TradeManagerConfig(soxl_max_slices=10)
        result = run_single_backtest(soxx, soxl, soxs, trade_config=cfg)
        assert isinstance(result, BacktestResult)

    def test_deterministic(self, sample_data):
        soxx, soxl, soxs = sample_data
        r1 = run_single_backtest(soxx, soxl, soxs, initial_capital=100_000)
        r2 = run_single_backtest(soxx, soxl, soxs, initial_capital=100_000)
        pd.testing.assert_series_equal(r1.equity_curve, r2.equity_curve)
        assert r1.metrics.sharpe_ratio == r2.metrics.sharpe_ratio

    def test_drawdown_series(self, sample_data):
        soxx, soxl, soxs = sample_data
        result = run_single_backtest(soxx, soxl, soxs)
        assert len(result.drawdown_series) == len(result.equity_curve)
        assert result.drawdown_series.max() <= 0  # drawdown is <= 0

    def test_entry_exit_arrays(self, sample_data):
        soxx, soxl, soxs = sample_data
        result = run_single_backtest(soxx, soxl, soxs)
        assert result.soxl_entries.dtype == bool
        assert result.soxl_exits.dtype == bool
        assert result.soxs_entries.dtype == bool
        assert result.soxs_exits.dtype == bool

    def test_empty_data(self):
        """Empty input should return empty result without crashing."""
        empty = pd.DataFrame({"close": []}, index=pd.DatetimeIndex([]))
        result = run_single_backtest(empty, empty, empty)
        assert result.metrics.total_trades == 0


# ======================================================================= #
#  3) Metrics computation
# ======================================================================= #

class TestMetrics:

    def test_positive_return(self):
        equity = pd.Series([100, 105, 110, 115, 120],
                           index=pd.bdate_range("2024-01-01", periods=5))
        m = _compute_metrics(equity, pd.DataFrame(), pd.DataFrame(), 100)
        assert m.total_return_pct > 0
        assert m.sharpe_ratio > 0

    def test_max_drawdown(self):
        equity = pd.Series([100, 110, 90, 95, 100],
                           index=pd.bdate_range("2024-01-01", periods=5))
        m = _compute_metrics(equity, pd.DataFrame(), pd.DataFrame(), 100)
        # Peak was 110, trough was 90 -> dd = -18.18%
        assert m.max_drawdown_pct == pytest.approx(-18.1818, rel=0.01)

    def test_trade_stats(self):
        trades = pd.DataFrame({
            "pnl": [100, -50, 200, -30, 150],
            "symbol": ["SOXL"] * 5,
        })
        m = _compute_metrics(
            pd.Series([100, 105], index=pd.bdate_range("2024-01-01", periods=2)),
            trades, pd.DataFrame(), 100,
        )
        assert m.total_trades == 5
        assert m.win_rate_pct == 60.0
        assert m.avg_trade_pnl > 0

    def test_regime_day_counts(self):
        decisions = pd.DataFrame({
            "effective_state": ["BULL_ACTIVE", "BULL_ACTIVE", "BEAR_ACTIVE",
                               "NEUTRAL", "NEUTRAL", "NEUTRAL"],
        }, index=pd.bdate_range("2024-01-01", periods=6))
        m = _compute_metrics(
            pd.Series([100] * 6, index=decisions.index),
            pd.DataFrame(), decisions, 100,
        )
        assert m.bull_days == 2
        assert m.bear_days == 1
        assert m.neutral_days == 3

    def test_empty_equity(self):
        m = _compute_metrics(pd.Series(dtype=float), pd.DataFrame(), pd.DataFrame(), 100)
        assert m.sharpe_ratio == 0
        assert m.total_trades == 0


# ======================================================================= #
#  4) VectorBT signal generation
# ======================================================================= #

class TestVbtSignals:

    def test_signal_dict_keys(self, sample_data):
        soxx, soxl, soxs = sample_data
        signals = build_vbt_signals(soxx, soxl, soxs)
        expected_keys = {"soxl_entries", "soxl_exits", "soxs_entries",
                         "soxs_exits", "soxl_close", "soxs_close", "decisions_df"}
        assert set(signals.keys()) == expected_keys

    def test_signals_are_boolean(self, sample_data):
        soxx, soxl, soxs = sample_data
        signals = build_vbt_signals(soxx, soxl, soxs)
        assert signals["soxl_entries"].dtype == bool
        assert signals["soxs_entries"].dtype == bool

    def test_entries_and_exits_same_length(self, sample_data):
        soxx, soxl, soxs = sample_data
        signals = build_vbt_signals(soxx, soxl, soxs)
        assert len(signals["soxl_entries"]) == len(signals["soxl_exits"])
        assert len(signals["soxs_entries"]) == len(signals["soxs_exits"])


# ======================================================================= #
#  5) Parameter sweep
# ======================================================================= #

class TestSweep:

    def test_sweep_runs(self, sample_data):
        soxx, soxl, soxs = sample_data
        grid = {
            "soxl_max_slices": [25, 35],
            "soxs_take_profit": [0.06, 0.10],
        }
        result = sweep_parameters(soxx, soxl, soxs, param_grid=grid)
        assert isinstance(result, SweepResult)
        assert len(result.results) == 4  # 2 x 2
        assert len(result.summary) == 4

    def test_summary_has_sharpe(self, sample_data):
        soxx, soxl, soxs = sample_data
        grid = {"soxl_max_slices": [25, 35]}
        result = sweep_parameters(soxx, soxl, soxs, param_grid=grid)
        assert "sharpe_ratio" in result.summary.columns
        assert "total_return_pct" in result.summary.columns
        assert "calmar_ratio" in result.summary.columns

    def test_best_metrics_populated(self, sample_data):
        soxx, soxl, soxs = sample_data
        grid = {"soxl_max_slices": [25, 35]}
        result = sweep_parameters(soxx, soxl, soxs, param_grid=grid)
        assert isinstance(result.best_sharpe, BacktestMetrics)
        assert isinstance(result.best_calmar, BacktestMetrics)


# ======================================================================= #
#  6) Walk-forward
# ======================================================================= #

class TestWalkForward:

    def test_walkforward_produces_windows(self, sample_data):
        soxx, soxl, soxs = sample_data
        wf = walk_forward_quarterly(
            soxx, soxl, soxs,
            is_quarters=4, oos_quarters=1,
            param_grid={"soxl_max_slices": [35]},
        )
        assert isinstance(wf, WalkForwardResult)
        assert len(wf.windows) > 0

    def test_walkforward_oos_equity_not_flat(self, sample_data):
        soxx, soxl, soxs = sample_data
        wf = walk_forward_quarterly(
            soxx, soxl, soxs,
            is_quarters=4, oos_quarters=1,
            param_grid={"soxl_max_slices": [35]},
        )
        if not wf.oos_equity.empty:
            assert wf.oos_equity.iloc[0] != wf.oos_equity.iloc[-1]

    def test_insufficient_data_warns(self):
        soxx, soxl, soxs = generate_sample_data(years=1)  # too short
        with pytest.warns(match="Not enough data"):
            wf = walk_forward_quarterly(soxx, soxl, soxs, is_quarters=8)
        assert len(wf.windows) == 0


# ======================================================================= #
#  7) Report export
# ======================================================================= #

class TestExport:

    def test_export_creates_files(self, sample_data, output_dir):
        soxx, soxl, soxs = sample_data
        result = run_single_backtest(soxx, soxl, soxs)
        paths = export_report(result, output_dir=output_dir, prefix="test")
        for label, path in paths.items():
            assert Path(path).exists(), f"{label} file not found"

    def test_metrics_json_valid(self, sample_data, output_dir):
        soxx, soxl, soxs = sample_data
        result = run_single_backtest(soxx, soxl, soxs)
        paths = export_report(result, output_dir=output_dir)
        with open(paths["metrics"]) as f:
            data = json.load(f)
        assert "sharpe_ratio" in data
        assert "total_return_pct" in data

    def test_sweep_export(self, sample_data, output_dir):
        soxx, soxl, soxs = sample_data
        grid = {"soxl_max_slices": [25, 35]}
        sw = sweep_parameters(soxx, soxl, soxs, param_grid=grid)
        paths = export_sweep_report(sw, output_dir=output_dir)
        assert Path(paths["summary_csv"]).exists()
        assert Path(paths["best_json"]).exists()

    def test_walkforward_export(self, sample_data, output_dir):
        soxx, soxl, soxs = sample_data
        wf = walk_forward_quarterly(
            soxx, soxl, soxs,
            param_grid={"soxl_max_slices": [35]},
        )
        if wf.windows:
            paths = export_walkforward_report(wf, output_dir=output_dir)
            assert Path(paths["metrics"]).exists()


# ======================================================================= #
#  8) Print metrics (smoke test)
# ======================================================================= #

class TestPrintMetrics:

    def test_print_no_crash(self, capsys):
        m = BacktestMetrics(
            total_return_pct=10.5, cagr_pct=5.2, sharpe_ratio=1.1,
            sortino_ratio=1.5, max_drawdown_pct=-15.3,
            max_drawdown_duration_days=42, calmar_ratio=0.34,
            total_trades=50, win_rate_pct=55.0, avg_trade_pnl=100.0,
            avg_win=250.0, avg_loss=-120.0, profit_factor=1.5,
            bull_days=200, bear_days=50, neutral_days=100, transition_days=3,
        )
        print_metrics(m)
        captured = capsys.readouterr()
        assert "Sharpe" in captured.out
        assert "10.50" in captured.out
