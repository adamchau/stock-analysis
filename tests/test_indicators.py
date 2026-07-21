# tests/test_indicators.py
import pytest
from scripts.lib.indicators import compute_ma, compute_volume_ratio, compute_rsi, compute_bias, compute_all


def test_compute_ma_simple():
    assert compute_ma([10, 20, 30], 3) == 20.0
    assert compute_ma([10, 20], 3) is None  # 数据不足


def test_compute_volume_ratio():
    # 量比 = 今日量 / 过去5日平均量
    r = compute_volume_ratio([100, 100, 100, 100, 100, 200])
    assert abs(r - 2.0) < 0.01


def test_compute_rsi_bounds():
    closes = [44, 44.34, 43.93, 44.08, 43.61, 44.03, 43.56, 41.50, 41.20, 41.50,
              41.38, 41.30, 41.47, 41.78, 42.30, 42.30, 42.10, 42.50, 42.10, 42.30]
    r = compute_rsi(closes, 14)
    assert 0 <= r <= 100


def test_compute_bias():
    assert abs(compute_bias(105, 100) - 5.0) < 0.01


def test_compute_all_structure():
    bars = [{"close": 10 + i, "volume": 100 + i * 10} for i in range(25)]
    r = compute_all(bars)
    assert "ma5" in r and "ma10" in r and "ma20" in r
    assert "volume_ratio" in r and "rsi14" in r
    assert isinstance(r["ma5"], (int, float))
