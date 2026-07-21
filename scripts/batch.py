#!/usr/bin/env python3
# scripts/batch.py
"""多股分层批量 runner —— v3.0.0 唯一程序化多股入口。

效率优化（相对每只 agent 委托循环串行）：
  - K线 并发（ThreadPoolExecutor, 腾讯 appstock 不封 IP 无限流）→ 30 只 ~16s
  - qt.gtimg.cn 批量单请求取全部 PE/PB/市值 → 1 次请求 ~1s（ETF 字段可能不全，best-effort）
  - 新闻 em_get 1s 串行限流（防东财封 IP，不可并发）→ 仅 Tier2 对选定几只拉

Tier1（默认）：全量 N 只 → K线并发 + qt批量 + indicators → 决策信号总表（action/score/技术面+估值），
  不拉新闻不生成仪表盘，秒级出表。契合 skill §8「批量>5 需先确认」。
Tier2（--news NAMES）：对 Tier1 选出的 ≤几只加东财个股新闻（em_get 串行），输出带新闻的 context pack JSON，
  供 agent 读 prompts.md 生成深度仪表盘 + 落盘。

纯标准库（urllib + concurrent + json + re），零 pip 依赖，与 indicators/kline 同级。
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.kline import fetch_quote, warm_chain  # noqa: E402
from lib.indicators import compute_all  # noqa: E402

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_QT_ENDPOINT = "https://qt.gtimg.cn/q="
_NEWS_ENDPOINT = "https://search-api-web.eastmoney.com/search/jsonp"
_EM_MIN_INTERVAL = 1.0  # 东财防封：两次请求最小间隔
_em_last = [0.0]
import time as _time


# ---- 默认观察池（A 股 ETF + 个股，可被 --codes/--names 覆盖） ----
DEFAULT_WATCHLIST = [
    ("512480", "半导体ETF国联安"), ("588990", "科创芯片ETF博时"), ("880952", "芯片"),
    ("512720", "计算机ETF国泰"), ("515230", "软件ETF国泰"), ("515880", "通信ETF国泰"),
    ("880656", "CPO概念"), ("515980", "人工智能ETF华富"), ("588430", "科创创业AI ETF工银"),
    ("159140", "科创创业AI ETF易方达"), ("562500", "机器人ETF华夏"), ("880703", "人形机器人"),
    ("510150", "消费ETF招商"), ("512690", "酒ETF鹏华"), ("512170", "医疗ETF华宝"), ("002044", "美年健康"),
    ("512070", "证券保险ETF易方达"), ("512880", "证券ETF国泰"), ("512200", "房地产ETF南方"), ("880473", "保险"),
    ("516650", "有色金属ETF华夏"), ("562800", "稀有金属ETF嘉实"), ("516780", "稀土ETF华泰柏瑞"),
    ("516020", "化工ETF华宝"), ("561160", "电池ETF富国"), ("880742", "固态电池"),
    ("512980", "传媒ETF广发"), ("159792", "港股通互联网ETF富国"), ("880548", "商业航天"), ("563230", "卫星ETF富国"),
]


# ---- 技术面决策信号（纯函数，可单测） ----
def signal(ind: Optional[dict], bars: list) -> tuple[str, int, str]:
    """技术面轻量决策信号（无 news/fundamental，confidence=低）。返回 (action, score, note)。"""
    if not ind or ind.get("last_close") is None:
        return ("—", 0, "")
    c = ind["last_close"]; ma5 = ind.get("ma5"); ma10 = ind.get("ma10"); ma20 = ind.get("ma20")
    rsi = ind.get("rsi14") or 0; bias5 = ind.get("bias_ma5") or 0; vr = ind.get("volume_ratio") or 0
    bull = ma5 and ma10 and ma20 and ma5 > ma10 > ma20 and c > ma5
    bear = ma5 and ma10 and ma20 and ma5 < ma10 < ma20 and c < ma5
    closes = [float(b["close"]) for b in bars if b.get("close") is not None] if bars else []
    d5 = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0
    above = sum(1 for m in (ma5, ma10, ma20) if m and c > m)
    below = sum(1 for m in (ma5, ma10, ma20) if m and c < m)
    s = 50
    if bull: s += 15
    if bear: s -= 15
    if above == 3: s += 5
    if below == 3: s -= 5
    if rsi > 70: s -= 8
    if rsi < 30: s += 6
    if bias5 > 5: s -= 6
    if vr > 1.5: s += 3
    if d5 > 10: s -= 4
    s = max(0, min(100, int(round(s))))
    if bull and rsi < 70 and bias5 < 5: act = "买入"
    elif bull: act = "持有"
    elif bear: act = "减仓" if s < 40 else "观望"
    elif above >= 2: act = "持有"
    elif below >= 2: act = "观望"
    else: act = "观望"
    note = f"RSI{rsi:.0f} 乖离{bias5:+.1f}% 量比{vr:.1f} 5日{d5:+.1f}%"
    if bull: note += " 多头"
    if bear: note += " 空头"
    if rsi > 70: note += " 超买"
    if bias5 > 5: note += " 追高"
    return (act, s, note)


def news_sentiment(items: list) -> tuple[str, str, str]:
    """从新闻标题粗分情绪。返回 (tag, count, top3_titles)。"""
    if not items: return ("无新闻", "0", "")
    pos_kw = ("涨", "增长", "净流入", "利好", "突破", "新高", "反弹", "大涨", "超预期", "加仓", "增持", "回购", "中标", "合作", "获批", "放量", "复苏")
    neg_kw = ("跌", "下滑", "净流出", "利空", "破位", "新低", "大跌", "不及预期", "减持", "质押", "违规", "处罚", "退市", "问询", "警示", "停牌", "缩量", "亏损", "终止", "辞任")
    titles = [i.get("title", "") for i in items[:5]]
    text = " ".join(titles)
    p = sum(1 for k in pos_kw if k in text); n = sum(1 for k in neg_kw if k in text)
    tag = "偏多" if (p > n and p > 0) else ("偏空" if (n > p and n > 0) else "中性")
    return (tag, str(len(items)), " | ".join(t[:22] for t in titles[:3]))


# ---- qt.gtimg.cn 批量报价（PE/PB/市值，一次请求多码） ----
def qt_batch_quote(codes: list) -> dict:
    """批量拉腾讯实时报价。返回 {code: {name,price,change_pct,pe_ttm,pb,mcap_yi,...}}。
    ETF 字段可能不全（best-effort，缺失字段 None）。网络错返 {}。"""
    if not codes: return {}
    pref = []
    for c in codes:
        c = c.strip()
        if c.startswith(("6", "9")): pref.append(f"sh{c}")
        elif c.startswith("8"): pref.append(f"bj{c}")
        else: pref.append(f"sz{c}")
    url = _QT_ENDPOINT + ",".join(pref)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read().decode("gbk", errors="ignore")
    except Exception:
        return {}
    out = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line: continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 6: continue
        code = key[2:]
        def g(i):
            try: return float(vals[i]) if len(vals) > i and vals[i] and vals[i] != "" else None
            except (ValueError, IndexError): return None
        out[code] = {
            "name": vals[1] if len(vals) > 1 else "", "price": g(3), "last_close": g(4),
            "open": g(5), "change_pct": g(32) if len(vals) > 32 else None,
            "turnover_pct": g(38) if len(vals) > 38 else None,
            "pe_ttm": g(39) if len(vals) > 39 else None,
            "mcap_yi": g(44) if len(vals) > 44 else None,
            "float_mcap_yi": g(45) if len(vals) > 45 else None,
            "pb": g(46) if len(vals) > 46 else None,
        }
    return out


# ---- 东财个股新闻（a-stock-data §5.1，em_get 1s 串行限流） ----
def _em_get(url, params=None, headers=None, timeout=15):
    """东财统一请求：串行限流防封。urllib 实现。"""
    wait = _EM_MIN_INTERVAL - (_time.time() - _em_last[0])
    if wait > 0: _time.sleep(wait + 0.2)
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=headers or {"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "ignore")
    finally:
        _em_last[0] = _time.time()


def fetch_news(key: str, days: int = 7, max_results: int = 8, page_size: int = 15) -> list:
    """东财个股新闻，recency 过滤后返回 NEWS_FIELDS 风格列表。em_get 1s 串行限流。"""
    inner = json.dumps({"uid": "", "keyword": key, "type": ["cmsArticleWebOld"], "client": "web",
        "clientType": "web", "clientVersion": "curr", "param": {"cmsArticleWebOld": {"searchScope": "default",
        "sort": "default", "pageIndex": 1, "pageSize": page_size, "preTag": "", "postTag": ""}}},
        separators=(",", ":"))
    try:
        text = _em_get(_NEWS_ENDPOINT, params={"cb": "jQuery_news", "param": inner},
                       headers={"User-Agent": _UA, "Referer": "https://so.eastmoney.com/"})
    except Exception:
        return []
    if "(" not in text or ")" not in text: return []
    try:
        d = json.loads(text[text.index("(") + 1: text.rindex(")")])
    except (ValueError, json.JSONDecodeError):
        return []
    arts = (d.get("result") or {}).get("cmsArticleWebOld", []) or []
    today = date.today(); ws = today - timedelta(days=days)
    out = []
    for a in arts:
        title = re.sub(r"<[^>]+>", "", a.get("title", "")).strip()
        if not title: continue
        ds = str(a.get("date", ""))[:10]
        if not ds: ds = extract_date(f"{title} {re.sub(r'<[^>]+>', '', a.get('content', ''))}")
        if not _within_window(ds, ws, today): continue
        out.append({"title": title, "summary": re.sub(r"<[^>]+>", "", a.get("content", "")).strip()[:200],
            "url": a.get("url", ""), "published_date": ds,
            "source": a.get("mediaName", "") or "eastmoney"})
        if len(out) >= max_results: break
    return out


# 日期提取（复用 openwebsearch_fetcher 已删逻辑，内联精简版）
_DATE_PAT = (
    re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"),
    re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})"),
)
_MD_PAT = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日(?!\d)")


def extract_date(text: str) -> str:
    if not text: return ""
    for pat in _DATE_PAT:
        m = pat.search(text)
        if m:
            try: return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%Y-%m-%d")
            except ValueError: continue
    m = _MD_PAT.search(text)
    if not m: return ""
    mo, d = int(m.group(1)), int(m.group(2)); today = date.today()
    for y in (today.year, today.year - 1):
        try:
            c = date(y, mo, d)
            if c <= today: return c.strftime("%Y-%m-%d")
        except ValueError: continue
    return ""


def _within_window(ds: str, ws: date, today: date) -> bool:
    if not ds: return True
    try: dd = datetime.strptime(ds, "%Y-%m-%d").date()
    except ValueError: return True
    return ws <= dd <= today


# ---- Tier1：并发 K线 + qt批量 + 指标 → 信号总表 ----
def run_tier1(items: list, workers: int = 8) -> list:
    """items: [(code, name), ...]。返回 row dicts（含 quote/indicators/signal/valuation）。"""
    codes = [c for c, _ in items]
    # 0. 预热 K线 优先链：发现一次即存盘，并发池全部复用；仅链失效才自愈重探
    warm_chain()
    # 1. K线 并发（命中源已存链，各 worker 直接走命中源，不重复发现）
    quotes = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_quote, c): c for c in codes}
        for f in as_completed(futs):
            c = futs[f]
            try: quotes[c] = f.result()
            except Exception: quotes[c] = None
    # 2. qt 批量报价
    qt = qt_batch_quote(codes)
    # 3. 指标 + 信号
    rows = []
    for code, name in items:
        q = quotes.get(code); bars = (q or {}).get("bars") or []
        try: ind = compute_all(bars) if bars else None
        except Exception: ind = None
        act, score, note = signal(ind, bars)
        closes = [float(b["close"]) for b in bars if b.get("close") is not None] if bars else []
        prev = closes[-2] if len(closes) >= 2 else None
        d1 = (closes[-1] - prev) / prev * 100 if prev else None
        rt = qt.get(code, {})
        rows.append({"code": code, "name": name, "date": (q or {}).get("date"),
                     "close": (q or {}).get("close"), "d1": d1, "pct_chg": (q or {}).get("pct_chg"),
                     "indicators": ind, "action": act, "score": score, "note": note,
                     "pe_ttm": rt.get("pe_ttm"), "pb": rt.get("pb"), "mcap_yi": rt.get("mcap_yi"),
                     "turnover_pct": rt.get("turnover_pct"),
                     "bars_len": len(bars), "has_quote": q is not None})
    return rows


def print_tier1_table(rows: list):
    rows_sorted = sorted(rows, key=lambda r: r["score"], reverse=True)
    print("\n=== Tier1 决策信号总表（K线并发+qt批量+指标，无news）按评分降序 ===")
    print(f"{'代码':<7}{'名称':<16}{'现价':>9}{'日涨':>7}{'评分':>4} {'动作':<5}{'PE':>7}{'市值亿':>9} 备注")
    for r in rows_sorted:
        if not r["has_quote"] or not r["indicators"] or r["indicators"].get("last_close") is None:
            print(f"{r['code']:<7}{r['name']:<16}{'—':>9}{'—':>7}{'—':>4} {'无数据':<5}{'—':>7}{'—':>9} (880xxx/港股ETF kline不覆盖)")
            continue
        pe = f"{r['pe_ttm']:.1f}" if r["pe_ttm"] else "—"
        mc = f"{r['mcap_yi']:.0f}" if r["mcap_yi"] else "—"
        print(f"{r['code']:<7}{r['name']:<16}{r['close']:>9.3f}{(r['d1'] or 0):>+7.2f}{r['score']:>4} {r['action']:<5}{pe:>7}{mc:>9} {r['note']}")


def main() -> int:
    p = argparse.ArgumentParser(description="多股分层批量 runner（Tier1 信号总表 + Tier2 选定新闻）")
    p.add_argument("--codes", help="逗号分隔代码（默认内置 30 只观察池）")
    p.add_argument("--names", help="逗号分隔名称（与 codes 对应，缺省用代码占位）")
    p.add_argument("--workers", type=int, default=8, help="K线并发数（默认8）")
    p.add_argument("--news", help="Tier2：对逗号分隔的名称拉东财新闻（em_get串行），输出带新闻 context pack")
    p.add_argument("--json", action="store_true", help="输出 JSON 而非表格")
    args = p.parse_args()

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        if args.names:
            names = [n.strip() for n in args.names.split(",")]
        else:
            names = codes[:]
        items = list(zip(codes, names))
    else:
        items = DEFAULT_WATCHLIST

    t0 = time.time()
    rows = run_tier1(items, workers=args.workers)
    t1 = time.time()
    print(f"[tier1] {len(items)} 只 K线并发+qt批量+指标 耗时 {t1-t0:.1f}s", file=sys.stderr)

    if args.news:
        news_names = [n.strip() for n in args.news.split(",") if n.strip()]
        for nm in news_names:
            t0n = time.time()
            nitems = fetch_news(nm, days=7, max_results=8)
            tag, cnt, top = news_sentiment(nitems)
            print(f"[tier2] {nm}: {cnt}条({tag}) {(time.time()-t0n):.1f}s | {top[:60]}", file=sys.stderr)
        # 把新闻并入对应 row
        for r in rows:
            if r["name"] in news_names:
                r["news"] = fetch_news(r["name"], days=7, max_results=8)
                r["news_tag"], _, _ = news_sentiment(r["news"])

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    else:
        print_tier1_table(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
