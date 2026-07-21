# scripts/lib/kline.py
"""K线 source —— 发现 + 持久化，不写死兜底链路。

候选源（peer，无写死优先级；首次运行无保存链时按默认序探，命中即记录）：
  - baidu   (a-stock-data §1.3，finance.pae.baidu.com HTTP，纯标准库)
  - tencent (web.ifzq.gtimg.cn appstock HTTP，前复权 qfq，纯标准库)
  - mootdx  (a-stock-data §1.1，通达信 TCP，需 mootdx 库；**opt-in**：仅 KLINE_TRY_MOOTDX=1 时加入)

**发现 + 持久化**：首次运行（无 `storage/kline_chain.json`）→ 按默认序探候选，
命中第一个返回数据的源即停，把命中源置首 + 其余候选按默认序追加，写入
`storage/kline_chain.json` 作为「K线源优先链参考」。后续读保存链按序取，首个命中即返；
保存链全失败则重新探测所有候选、重建并覆盖保存链（自愈）。无写死兜底——优先级由发现机制决定，
不同机器/网络会发现不同命中源（本机命中 tencent，他机可能命中 baidu/mootdx）。

覆盖：A 股（沪 6/9、深 0/1/3、北 8）+ A 股 ETF（5xxxxx/1xxxxx）。不支持港美台。
纯标准库（urllib + json），mootdx 为可选依赖（缺失自动跳过该候选）。
"""
from __future__ import annotations
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_TIMEOUT = 10
_DEFAULT_LOOKBACK = 320  # 足够算 MA20/RSI14
_SKILL_ROOT = Path(__file__).resolve().parent.parent.parent
_CHAIN_FILE = _SKILL_ROOT / "storage" / "kline_chain.json"


# ---- A 股符号映射 ----
def _symbol(code: str) -> str:
    """A 股代码 → sh/sz/bj 前缀符号。"""
    raw = code.strip()
    low = raw.lower()
    if low.startswith(("sh", "sz", "bj")):
        return low
    c = raw.upper()
    if not c.isdigit() or len(c) != 6:
        raise ValueError(f"非法 A 股代码: {code}")
    first = c[0]
    if first in ("5", "6", "9"):      # 沪：主板 6、科创 688、ETF 51/56/58
        return f"sh{c}"
    if first in ("0", "1", "3"):      # 深：主板 000、中小 002、创业 300/301、ETF 15/16
        return f"sz{c}"
    if first == "8":                   # 北交所
        return f"bj{c}"
    raise ValueError(f"无法映射 A 股代码: {code}")


# ---- 候选源 ----
def _try_baidu(code: str, lookback: int) -> list[dict]:
    """a-stock-data §1.3 百度股市通 K线（HTTP）。返空 Result/异常 → []。"""
    url = "https://finance.pae.baidu.com/selfselect/getstockquotation"
    headers = {"User-Agent": _UA, "Accept": "application/vnd.finance-web.v1+json",
               "Origin": "https://gushitong.baidu.com", "Referer": "https://gushitong.baidu.com/"}
    for fmt in (code, f"SH{code}", f"SZ{code}", f"sh{code}", f"sz{code}"):
        params = {"all": "1", "isIndex": "false", "isBk": "false", "isBlock": "false",
                  "isFutures": "false", "isStock": "true", "newFormat": "1",
                  "group": "quotation_kline_ab", "finClientType": "pc",
                  "code": fmt, "start_time": "", "ktype": "1"}
        try:
            req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers=headers)
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                d = json.loads(r.read().decode("utf-8", "ignore"))
        except Exception:
            continue
        md = (d.get("Result") or {}).get("newMarketData") or {}
        keys = md.get("keys") or []
        rows = md.get("marketData", "")
        if not rows:
            continue
        bars = []
        for row in rows.split(";"):
            if not row:
                continue
            vals = row.split(",")
            if len(vals) < len(keys):
                continue
            m = dict(zip(keys, vals))
            try:
                bars.append({"date": str(m.get("time", ""))[:10], "open": float(m["open"]),
                              "close": float(m["close"]), "high": float(m["high"]),
                              "low": float(m["low"]), "volume": float(m.get("volume") or 0)})
            except (KeyError, ValueError, TypeError):
                continue
        if bars:
            return bars
    return []


def _try_tencent(code: str, lookback: int) -> list[dict]:
    """腾讯 appstock fqkline（HTTP，前复权）。行格式 [date,open,close,high,low,volume]。"""
    try:
        sym = _symbol(code)
    except ValueError:
        return []
    try:
        param = f"{sym},day,,,{lookback},qfq"
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={urllib.parse.quote(param)}"
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
        blk = (d.get("data") or {}).get(sym) or {}
        kls = blk.get("qfqday") or blk.get("day") or []
        bars = []
        for k in kls:
            if len(k) < 6:
                continue
            try:
                bars.append({"date": str(k[0])[:10], "open": float(k[1]), "close": float(k[2]),
                             "high": float(k[3]), "low": float(k[4]), "volume": float(k[5] or 0)})
            except (ValueError, TypeError):
                continue
        return bars
    except Exception:
        return []


def _try_mootdx(code: str, lookback: int) -> list[dict]:
    """a-stock-data §1.1 通达信 TCP（mootdx 库）。opt-in，缺失/失效返 []。"""
    try:
        from mootdx.quotes import Quotes
    except ImportError:
        return []
    c = code.strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[2:]
    if not c.isdigit() or len(c) != 6:
        return []
    try:
        client = Quotes.factory(market="std")
        df = client.bars(symbol=c, frequency=9, offset=lookback)
        if df is None or len(df) == 0:
            return []
        bars = []
        for _, row in df.iterrows():
            try:
                dt = str(row.get("datetime") or row.get("date") or "")[:10]
                bars.append({"date": dt, "open": float(row["open"]), "close": float(row["close"]),
                             "high": float(row["high"]), "low": float(row["low"]),
                             "volume": float(row.get("vol") or row.get("volume") or 0)})
            except (KeyError, ValueError, TypeError):
                continue
        return bars
    except Exception:
        return []


# 候选名（默认序；mootdx opt-in 前置）。_candidate(name) 用 globals 查 _try_{name}，
# 便于测试 monkeypatch 单个 _try_ 函数。
_CANDIDATE_NAMES: list[str] = ["baidu", "tencent"]
if os.environ.get("KLINE_TRY_MOOTDX", "").lower() in ("1", "true", "yes"):
    _CANDIDATE_NAMES = ["mootdx"] + _CANDIDATE_NAMES


def _candidate(name: str) -> Optional[Callable]:
    """按名查候选函数（_try_{name}，globals 查找，便于 monkeypatch）。"""
    return globals().get(f"_try_{name}")


# ---- 优先链持久化 ----
def load_chain() -> list[str]:
    """读 storage/kline_chain.json 的优先链；无/损坏返 []。"""
    try:
        if _CHAIN_FILE.exists():
            d = json.loads(_CHAIN_FILE.read_text(encoding="utf-8"))
            chain = d.get("chain") or []
            return [n for n in chain if n]
    except Exception:
        pass
    return []


def save_chain(names: list[str]) -> None:
    """写优先链（命中源置首）。写失败静默（不阻断主流程）。"""
    try:
        _CHAIN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CHAIN_FILE.write_text(json.dumps({"chain": names}, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    except Exception:
        pass


def _safe(f: Callable, code: str, lookback: int) -> list[dict]:
    try:
        return f(code, lookback)
    except Exception:
        return []


# 进程内最近命中源（避免单进程内反复读盘）
_last_working = [None]


def warm_chain(sample: str = "600519") -> list[str]:
    """预热 K线 优先链：若无保存链则用 sample 代码触发一次发现+持久化，返回保存链。

    供 batch 等批量场景在并发池前调用一次，避免并发首波多 worker 同时探测死源（race）。
    命中源是环境级（baidu/tencent/mootdx 对所有 A 股代码同可用性），用任一流动性好的
    sample（默认 600519 贵州茅台）探测即可代表全部。已有保存链时直接返回，不重复探测。
    """
    chain = load_chain()
    if not chain:
        fetch_bars(sample)  # 触发 _discover_and_save，存链 + 设 _last_working
        chain = load_chain()
    return chain


def fetch_bars(code: str, lookback: int = _DEFAULT_LOOKBACK) -> list[dict]:
    """取前复权日 K线 bars（[{date,open,close,high,low,volume}]），供 indicators.compute_all。

    发现 + 持久化：读 storage/kline_chain.json 优先链，按序试，首个命中即返（并确保链已存、
    命中源置首）；无链或全失败则按默认序探测所有候选，命中即记录存链、返回，全无命中返 []。
    无写死兜底——优先级由发现机制决定，不同环境会发现不同命中源。
    """
    chain = load_chain()
    order = chain or _CANDIDATE_NAMES
    for name in order:
        f = _candidate(name)
        if not f:
            continue
        bars = _safe(f, code, lookback)
        if bars:
            _last_working[0] = name
            # 命中源置首 + 其余按默认序，确保链已存且优先命中源
            new_chain = [name] + [n for n in _CANDIDATE_NAMES if n != name]
            if new_chain != chain:
                save_chain(new_chain)
            return bars
    # 全失败：重新探测所有候选（自愈，覆盖保存链）
    return _discover_and_save(code, lookback)


def _discover_and_save(code: str, lookback: int) -> list[dict]:
    """探测所有候选，按默认序取首个命中，存链（命中源置首+其余），返回 bars 或 []。"""
    first: Optional[list[dict]] = None
    hit: Optional[str] = None
    for name in _CANDIDATE_NAMES:
        f = _candidate(name)
        if not f:
            continue
        bars = _safe(f, code, lookback)
        if bars and first is None:
            first, hit = bars, name
    if hit:
        new_chain = [hit] + [n for n in _CANDIDATE_NAMES if n != hit]
        save_chain(new_chain)
        _last_working[0] = hit
    else:
        # 全无命中：覆盖存空链（自愈清空陈旧链，下次按默认序重探；用户删文件亦可强制重探）
        save_chain([])
    return first or []


def fetch_quote(code: str, lookback: int = _DEFAULT_LOOKBACK) -> Optional[dict]:
    """拉取行情：最新日 OHLCV + pct_chg + 多日 bars + data_source（命中的源名）。

    返回 QUOTE_FIELDS 风格 dict 或 None（无数据）。供 orchestration 步骤 2 喂步骤 3 + 步骤 5。
    """
    bars = fetch_bars(code, lookback)
    if not bars:
        return None
    last = bars[-1]
    prev_close = bars[-2]["close"] if len(bars) >= 2 else None
    pct_chg = (last["close"] - prev_close) / prev_close * 100 if prev_close else None
    return {
        "code": code,
        "date": last["date"],
        "open": last["open"],
        "high": last["high"],
        "low": last["low"],
        "close": last["close"],
        "volume": last["volume"],
        "amount": None,
        "pct_chg": pct_chg,
        "data_source": f"kline/{_last_working[0] or 'discovered'}",
        "bars": bars,
    }
