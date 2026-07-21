# scripts/lib/indicators.py
"""技术指标，纯标准库。提炼自项目 trend_analyzer @ commit b326ae27。

输入 OHLCV 序列，输出 MA/量比/RSI/乖离率。非数据获取，不在 DataFetcher 抽象层。
"""
from __future__ import annotations
from typing import List, Optional


def compute_ma(values: List[float], period: int) -> Optional[float]:
    n = len(values)
    if n < period or period <= 0:
        return None
    return sum(values[-period:]) / period


def compute_volume_ratio(volumes: List[float], period: int = 5) -> Optional[float]:
    n = len(volumes)
    if n < period + 1:
        return None
    today = volumes[-1]
    avg_prev = sum(volumes[-(period + 1):-1]) / period
    if not avg_prev:
        return None
    return today / avg_prev


def compute_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    n = len(closes)
    if n < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_bias(price: float, ma: float) -> Optional[float]:
    if not ma:
        return None
    return (price - ma) / ma * 100


def compute_all(bars: list) -> dict:
    """从日线 bars（[{close, volume, ...}]）算全套指标。"""
    closes = [float(b["close"]) for b in bars if b.get("close") is not None]
    volumes = [float(b["volume"]) for b in bars if b.get("volume") is not None]
    last_close = closes[-1] if closes else None
    ma5 = compute_ma(closes, 5)
    ma10 = compute_ma(closes, 10)
    ma20 = compute_ma(closes, 20)
    return {
        "last_close": last_close,
        "ma5": ma5, "ma10": ma10, "ma20": ma20,
        "bias_ma5": compute_bias(last_close, ma5) if last_close and ma5 else None,
        "bias_ma10": compute_bias(last_close, ma10) if last_close and ma10 else None,
        "volume_ratio": compute_volume_ratio(volumes),
        "rsi14": compute_rsi(closes, 14),
    }
