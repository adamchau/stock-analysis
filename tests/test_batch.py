# tests/test_batch.py
import json
from unittest.mock import patch, MagicMock
from datetime import date

from scripts import batch


# ---- signal() 纯函数 ----

def test_signal_bullish_buy():
    ind = {"last_close": 10.0, "ma5": 9.5, "ma10": 9.0, "ma20": 8.5,
           "rsi14": 55, "bias_ma5": 2.0, "volume_ratio": 1.2}
    bars = [{"close": 9.0}, {"close": 9.2}, {"close": 9.5}, {"close": 10.0}]
    act, score, note = batch.signal(ind, bars)
    # 多头排列 ma5>ma10>ma20 且 close>ma5，RSI<70，乖离<5 → 买入
    assert act == "买入"
    assert score >= 60
    assert "多头" in note


def test_signal_bearish_reduce():
    ind = {"last_close": 8.0, "ma5": 9.0, "ma10": 9.5, "ma20": 10.0,
           "rsi14": 25, "bias_ma5": -11.0, "volume_ratio": 0.5}
    bars = [{"close": 9.5}, {"close": 9.0}, {"close": 8.5}, {"close": 8.0}]
    act, score, note = batch.signal(ind, bars)
    assert act == "减仓"
    assert score < 40
    assert "空头" in note


def test_signal_overbought_bull_hold():
    # 多头但 RSI>70 → 持有（不追高）
    ind = {"last_close": 12.0, "ma5": 10.0, "ma10": 9.0, "ma20": 8.0,
           "rsi14": 78, "bias_ma5": 20.0, "volume_ratio": 1.0}
    bars = [{"close": 8}, {"close": 9}, {"close": 10}, {"close": 12}]
    act, score, note = batch.signal(ind, bars)
    assert act == "持有"
    assert "超买" in note and "多头" in note


def test_signal_no_data():
    assert batch.signal(None, []) == ("—", 0, "")
    assert batch.signal({"last_close": None}, []) == ("—", 0, "")


# ---- news_sentiment ----

def test_news_sentiment_empty():
    assert batch.news_sentiment([]) == ("无新闻", "0", "")


def test_news_sentiment_bullish():
    items = [{"title": "茅台大涨创新高 净流入居首"}, {"title": "利好回购"}]
    tag, cnt, top = batch.news_sentiment(items)
    assert tag == "偏多"
    assert cnt == "2"
    assert "茅台大涨创新高" in top


def test_news_sentiment_bearish():
    items = [{"title": "减持利空 大跌"}, {"title": "退市问询"}]
    tag, cnt, _ = batch.news_sentiment(items)
    assert tag == "偏空"


def test_news_sentiment_neutral():
    items = [{"title": "公司发布年报"}]
    tag, _, _ = batch.news_sentiment(items)
    assert tag == "中性"


# ---- qt_batch_quote 解析 ----

def _qt_response(codes_fields):
    """构造 qt.gtimg.cn 响应。codes_fields: {code: [vals]} 模拟。"""
    lines = []
    for code, vals in codes_fields.items():
        sym = f"sh{code}" if code.startswith(("6", "9")) else f"sz{code}"
        lines.append(f'v_{sym}="{"~".join(str(v) for v in vals)}"')
    return ";".join(lines).encode("gbk")


def test_qt_batch_quote_parses_stock(monkeypatch):
    # 53 字段的股票行情
    stock_fields = ["1"] + ["x"] * 38 + ["0.34"] + ["x"] * 4 + ["16429"] + ["x"] + ["7.06"] + ["x"] * 6
    # 索引: name=1, price=3, last_close=4, open=5, change_pct=32, turnover=38, pe=39, mcap=44, pb=46
    stock_fields[1] = "贵州茅台"; stock_fields[3] = "1314.29"; stock_fields[4] = "1327.5"
    stock_fields[32] = "-1.0"; stock_fields[38] = "0.34"; stock_fields[39] = "19.86"
    stock_fields[44] = "16429.7"; stock_fields[46] = "7.06"
    while len(stock_fields) < 53: stock_fields.append("x")
    resp = _qt_response({"600519": stock_fields})
    m = MagicMock(); m.read.return_value = resp; m.__enter__ = MagicMock(return_value=m); m.__exit__ = MagicMock(return_value=None)
    with patch("scripts.batch.urllib.request.urlopen", return_value=m):
        out = batch.qt_batch_quote(["600519"])
    assert "600519" in out
    assert out["600519"]["name"] == "贵州茅台"
    assert out["600519"]["price"] == 1314.29
    assert out["600519"]["pe_ttm"] == 19.86
    assert out["600519"]["mcap_yi"] == 16429.7
    assert out["600519"]["pb"] == 7.06


def test_qt_batch_quote_etf_short_fields():
    # ETF 字段少于 53，best-effort 取有的
    etf_fields = ["1", "人工智能ETF", "515980", "1.06", "1.063", "1.07"]  # 仅 6 字段
    resp = _qt_response({"515980": etf_fields})
    m = MagicMock(); m.read.return_value = resp; m.__enter__ = MagicMock(return_value=m); m.__exit__ = MagicMock(return_value=None)
    with patch("scripts.batch.urllib.request.urlopen", return_value=m):
        out = batch.qt_batch_quote(["515980"])
    assert out["515980"]["name"] == "人工智能ETF"
    assert out["515980"]["price"] == 1.06
    assert out["515980"]["pe_ttm"] is None  # 字段不够
    assert out["515980"]["pb"] is None


def test_qt_batch_quote_network_error_returns_empty():
    with patch("scripts.batch.urllib.request.urlopen", side_effect=Exception("timeout")):
        assert batch.qt_batch_quote(["600519"]) == {}


def test_qt_batch_quote_empty_input():
    assert batch.qt_batch_quote([]) == {}


# ---- extract_date / _within_window ----

def test_extract_date_formats():
    assert batch.extract_date("2026年7月21日 收盘") == "2026-07-21"
    assert batch.extract_date("2026-07-21 10:00") == "2026-07-21"
    assert batch.extract_date("无日期") == ""


def test_within_window():
    today = date(2026, 7, 21); ws = date(2026, 7, 14)
    assert batch._within_window("2026-07-20", ws, today) is True
    assert batch._within_window("2026-07-10", ws, today) is False
    assert batch._within_window("", ws, today) is True


# ---- run_tier1（mock fetch_quote + qt_batch_quote + compute_all） ----

def test_run_tier1_concurrent_and_signal(monkeypatch):
    items = [("600519", "贵州茅台"), ("515980", "人工智能ETF")]
    def fake_fetch_quote(c):
        return {"date": "2026-07-21", "open": 1.0, "high": 1.1, "low": 0.9,
                "close": 1.05, "volume": 100, "pct_chg": 1.0, "data_source": "test",
                "bars": [{"close": 1.0, "volume": 100}, {"close": 1.05, "volume": 120}]}
    monkeypatch.setattr(batch, "warm_chain", lambda: ["tencent", "baidu"])  # 跳过真实发现
    monkeypatch.setattr(batch, "fetch_quote", fake_fetch_quote)
    monkeypatch.setattr(batch, "qt_batch_quote", lambda codes: {"600519": {"pe_ttm": 19.86, "mcap_yi": 16429}})
    rows = batch.run_tier1(items, workers=2)
    assert len(rows) == 2
    assert rows[0]["code"] == "600519"
    assert rows[0]["has_quote"] is True
    assert rows[0]["pe_ttm"] == 19.86
    assert rows[0]["indicators"] is not None
    assert rows[0]["action"] in ("买入", "持有", "观望", "减仓", "—")


def test_run_tier1_handles_failed_fetch(monkeypatch):
    items = [("880952", "芯片")]
    monkeypatch.setattr(batch, "warm_chain", lambda: [])
    monkeypatch.setattr(batch, "fetch_quote", lambda c: None)
    monkeypatch.setattr(batch, "qt_batch_quote", lambda codes: {})
    rows = batch.run_tier1(items)
    assert rows[0]["has_quote"] is False
    assert rows[0]["action"] == "—"
    assert rows[0]["score"] == 0
