# tests/test_kline.py
import json
import pytest
from unittest.mock import patch, MagicMock

from scripts.lib import kline


# ---- 符号映射 ----

def test_symbol_sh_codes():
    assert kline._symbol("600519") == "sh600519"
    assert kline._symbol("688017") == "sh688017"
    assert kline._symbol("515980") == "sh515980"
    assert kline._symbol("562500") == "sh562500"
    assert kline._symbol("588430") == "sh588430"


def test_symbol_sz_codes():
    assert kline._symbol("000001") == "sz000001"
    assert kline._symbol("002044") == "sz002044"
    assert kline._symbol("300750") == "sz300750"
    assert kline._symbol("159140") == "sz159140"


def test_symbol_bj_and_prefixed():
    assert kline._symbol("830799") == "bj830799"
    assert kline._symbol("sh600519") == "sh600519"
    assert kline._symbol("SZ159792") == "sz159792"


def test_symbol_invalid_raises():
    for bad in ["", "abc", "12345", "1234567", "AAPL", "hk00700"]:
        with pytest.raises(ValueError):
            kline._symbol(bad)


# ---- 候选源解析 ----

def _mock_resp(text_or_bytes):
    m = MagicMock()
    m.read.return_value = text_or_bytes.encode("utf-8") if isinstance(text_or_bytes, str) else text_or_bytes
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=None)
    return m


def test_try_tencent_parses_bars():
    # 腾讯响应：data.<sym>.qfqday = [[date,open,close,high,low,vol], ...]
    payload = {"data": {"sh515980": {"qfqday": [
        ["2026-07-19", "1.05", "1.07", "1.08", "1.04", "100000"],
        ["2026-07-21", "1.07", "1.06", "1.09", "1.02", "200000"],
    ]}}}
    with patch("scripts.lib.kline.urllib.request.urlopen", return_value=_mock_resp(json.dumps(payload))):
        bars = kline._try_tencent("515980", 320)
    assert len(bars) == 2
    # 行格式 [date, open, close, high, low, volume] —— close 是 index 2
    assert bars[0] == {"date": "2026-07-19", "open": 1.05, "close": 1.07,
                       "high": 1.08, "low": 1.04, "volume": 100000.0}
    assert bars[1]["close"] == 1.06 and bars[1]["high"] == 1.09


def test_try_tencent_empty_returns_empty():
    with patch("scripts.lib.kline.urllib.request.urlopen", return_value=_mock_resp(json.dumps({"data": {"sh000001": {}}}))):
        assert kline._try_tencent("000001", 320) == []


def test_try_tencent_invalid_code_returns_empty():
    assert kline._try_tencent("INVALID", 320) == []


def test_try_baidu_parses_bars():
    # 百度响应：Result.newMarketData.{keys, marketData}
    payload = {"Result": {"newMarketData": {
        "keys": ["time", "open", "close", "high", "low", "volume"],
        "marketData": "2026-07-19,1.05,1.07,1.08,1.04,100000;2026-07-21,1.07,1.06,1.09,1.02,200000",
    }}}
    with patch("scripts.lib.kline.urllib.request.urlopen", return_value=_mock_resp(json.dumps(payload))):
        bars = kline._try_baidu("600519", 320)
    assert len(bars) == 2
    assert bars[0]["close"] == 1.07 and bars[1]["close"] == 1.06


def test_try_baidu_empty_result_returns_empty():
    with patch("scripts.lib.kline.urllib.request.urlopen", return_value=_mock_resp(json.dumps({"Result": {"newMarketData": {}}}))):
        assert kline._try_baidu("600519", 320) == []


def test_try_baidu_network_error_returns_empty():
    with patch("scripts.lib.kline.urllib.request.urlopen", side_effect=Exception("timeout")):
        assert kline._try_baidu("600519", 320) == []


# ---- 优先链持久化 ----

def test_load_save_chain_roundtrip(tmp_path, monkeypatch):
    chain_file = tmp_path / "kline_chain.json"
    monkeypatch.setattr(kline, "_CHAIN_FILE", chain_file)
    assert kline.load_chain() == []
    kline.save_chain(["tencent", "baidu"])
    assert chain_file.exists()
    assert kline.load_chain() == ["tencent", "baidu"]


def test_load_chain_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(kline, "_CHAIN_FILE", tmp_path / "nope.json")
    assert kline.load_chain() == []


def test_load_chain_corrupt_returns_empty(tmp_path, monkeypatch):
    f = tmp_path / "kline_chain.json"
    f.write_text("not json")
    monkeypatch.setattr(kline, "_CHAIN_FILE", f)
    assert kline.load_chain() == []


# ---- fetch_bars 发现 + 持久化 ----

def _bars(code, close=1.0):
    return [{"date": "2026-07-21", "open": close, "close": close, "high": close, "low": close, "volume": 100}]


def test_fetch_bars_first_run_discovers_and_saves(tmp_path, monkeypatch):
    """首次（无链）→ 探 baidu(空) → tencent(命中) → 存链 [tencent,baidu] + 返 tencent bars。"""
    monkeypatch.setattr(kline, "_CHAIN_FILE", tmp_path / "kline_chain.json")
    monkeypatch.setattr(kline, "_try_baidu", lambda c, l: [])
    monkeypatch.setattr(kline, "_try_tencent", lambda c, l: _bars(c, 1.06))
    monkeypatch.setattr(kline, "_CANDIDATE_NAMES", ["baidu", "tencent"])
    monkeypatch.setattr(kline, "_last_working", [None])
    bars = kline.fetch_bars("515980")
    assert len(bars) == 1 and bars[0]["close"] == 1.06
    assert kline.load_chain() == ["tencent", "baidu"]   # 命中源置首
    assert kline._last_working[0] == "tencent"


def test_fetch_bars_uses_saved_chain_skips_dead(tmp_path, monkeypatch):
    """保存链 [tencent,baidu] → 直接命中 tencent，不探 baidu。"""
    chain_file = tmp_path / "kline_chain.json"
    chain_file.write_text(json.dumps({"chain": ["tencent", "baidu"]}))
    monkeypatch.setattr(kline, "_CHAIN_FILE", chain_file)
    called = []
    monkeypatch.setattr(kline, "_try_tencent", lambda c, l: (called.append("tencent"), _bars(c))[1])
    monkeypatch.setattr(kline, "_try_baidu", lambda c, l: (called.append("baidu"), [])[1])
    monkeypatch.setattr(kline, "_CANDIDATE_NAMES", ["baidu", "tencent"])
    monkeypatch.setattr(kline, "_last_working", [None])
    bars = kline.fetch_bars("600519")
    assert len(bars) == 1
    assert called == ["tencent"]   # baidu 未被探（链 tencent 命中即停）


def test_fetch_bars_saved_chain_all_fail_self_heals(tmp_path, monkeypatch):
    """保存链 [tencent,baidu]，两源全失败 → _discover_and_save 重新探、覆盖存链、返 []。"""
    chain_file = tmp_path / "kline_chain.json"
    chain_file.write_text(json.dumps({"chain": ["tencent", "baidu"]}))
    monkeypatch.setattr(kline, "_CHAIN_FILE", chain_file)
    monkeypatch.setattr(kline, "_try_tencent", lambda c, l: [])
    monkeypatch.setattr(kline, "_try_baidu", lambda c, l: [])
    monkeypatch.setattr(kline, "_CANDIDATE_NAMES", ["baidu", "tencent"])
    monkeypatch.setattr(kline, "_last_working", [None])
    assert kline.fetch_bars("600519") == []
    # 自愈后链为空（全无命中）
    assert kline.load_chain() == []


def test_fetch_bars_all_dead_first_run_saves_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(kline, "_CHAIN_FILE", tmp_path / "kline_chain.json")
    monkeypatch.setattr(kline, "_try_tencent", lambda c, l: [])
    monkeypatch.setattr(kline, "_try_baidu", lambda c, l: [])
    monkeypatch.setattr(kline, "_CANDIDATE_NAMES", ["baidu", "tencent"])
    monkeypatch.setattr(kline, "_last_working", [None])
    assert kline.fetch_bars("600519") == []
    assert kline.load_chain() == []   # 记空链避免反复探测


# ---- fetch_quote ----

def test_fetch_quote_dict_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(kline, "_CHAIN_FILE", tmp_path / "kline_chain.json")
    monkeypatch.setattr(kline, "_try_baidu", lambda c, l: [])
    monkeypatch.setattr(kline, "_try_tencent", lambda c, l: [
        {"date": "2026-07-19", "open": 1.05, "close": 1.07, "high": 1.08, "low": 1.04, "volume": 100},
        {"date": "2026-07-21", "open": 1.07, "close": 1.06, "high": 1.09, "low": 1.02, "volume": 200},
    ])
    monkeypatch.setattr(kline, "_CANDIDATE_NAMES", ["baidu", "tencent"])
    monkeypatch.setattr(kline, "_last_working", [None])
    q = kline.fetch_quote("515980")
    assert q is not None
    assert q["code"] == "515980" and q["date"] == "2026-07-21"
    assert q["close"] == 1.06 and q["open"] == 1.07 and q["high"] == 1.09 and q["low"] == 1.02
    assert q["volume"] == 200.0
    assert round(q["pct_chg"], 4) == round((1.06 - 1.07) / 1.07 * 100, 4)
    assert q["data_source"] == "kline/tencent"
    assert len(q["bars"]) == 2 and q["amount"] is None


def test_fetch_quote_no_data_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(kline, "_CHAIN_FILE", tmp_path / "kline_chain.json")
    monkeypatch.setattr(kline, "_try_baidu", lambda c, l: [])
    monkeypatch.setattr(kline, "_try_tencent", lambda c, l: [])
    monkeypatch.setattr(kline, "_CANDIDATE_NAMES", ["baidu", "tencent"])
    monkeypatch.setattr(kline, "_last_working", [None])
    assert kline.fetch_quote("515980") is None


def test_fetch_quote_single_bar_pct_none(tmp_path, monkeypatch):
    monkeypatch.setattr(kline, "_CHAIN_FILE", tmp_path / "kline_chain.json")
    monkeypatch.setattr(kline, "_try_baidu", lambda c, l: [])
    monkeypatch.setattr(kline, "_try_tencent", lambda c, l: [{"date": "2026-07-21", "open": 1.07, "close": 1.08, "high": 1.09, "low": 1.06, "volume": 100}])
    monkeypatch.setattr(kline, "_CANDIDATE_NAMES", ["baidu", "tencent"])
    monkeypatch.setattr(kline, "_last_working", [None])
    q = kline.fetch_quote("515980")
    assert q is not None and q["pct_chg"] is None  # 仅 1 根无前收盘


# ---- warm_chain ----

def test_warm_chain_skips_when_chain_exists(tmp_path, monkeypatch):
    """已有保存链 → 直接返回，不重复发现（不调 fetch_bars）。"""
    chain_file = tmp_path / "kline_chain.json"
    chain_file.write_text(json.dumps({"chain": ["tencent", "baidu"]}))
    monkeypatch.setattr(kline, "_CHAIN_FILE", chain_file)
    probed = []
    monkeypatch.setattr(kline, "fetch_bars", lambda c, l=320: (probed.append(c), [])[1])
    chain = kline.warm_chain()
    assert chain == ["tencent", "baidu"]
    assert probed == []   # 已有链，不发现


def test_warm_chain_discovers_when_no_chain(tmp_path, monkeypatch):
    """无保存链 → 用 sample 触发一次发现，返回存盘链。"""
    monkeypatch.setattr(kline, "_CHAIN_FILE", tmp_path / "kline_chain.json")
    calls = []
    def fake_fetch_bars(c, l=320):
        calls.append(c)
        # 模拟 _discover_and_save 已存链
        kline.save_chain(["tencent", "baidu"])
        return [{"date":"2026-07-21","open":1,"close":1,"high":1,"low":1,"volume":1}]
    monkeypatch.setattr(kline, "fetch_bars", fake_fetch_bars)
    chain = kline.warm_chain("600519")
    assert calls == ["600519"]   # 只发现一次
    assert chain == ["tencent", "baidu"]
