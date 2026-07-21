# Orchestration: Stock Analysis Flow（薄编排 · 全数据委托 a-stock-data）

本文件描述 stock-analysis skill 的完整分析编排流程。本 skill 是**薄编排层**：只做输入归一化、技术指标计算（`scripts/lib/indicators.py`，纯标准库）、上下文合并、LLM prompt、报告存储。**所有数据获取（行情/基本面/新闻/资金流/研报/公告/打板/期权/舆情）委托 sibling skill `a-stock-data`**（v3.4.0，43 端点）在运行时由 agent 调用，不在本 skill 内维护任何 fetcher/channel。

> 提炼自 `src/core/pipeline.py:analyze_stock` @ commit b326ae27 的主干语义。

> **覆盖范围**：a-stock-data 是纯 A 股 skill，故本 skill 仅支持 **A 股 + A 股 ETF**（`600519`/`515980`/`002044` 等）。港股/美股/台股不支持——如需可另行加 yfinance 委托层（当前不提供）。

> **K线 发现+持久化**：K线 由本 skill `scripts/lib/kline.py` 取，候选源 = a-stock-data §1.3 百度K线 / §1.1 mootdx(opt-in) + 腾讯 appstock，无写死兜底。首次运行探测命中源、存优先链 `storage/kline_chain.json`，后续按保存链取、全失败自愈重探。本机 mootdx TDX 死 + 百度返空 → 发现命中 tencent；他机可能命中百度/mootdx。其余数据（基本面/新闻/资金流/研报/公告/打板/期权/舆情）委托 a-stock-data。

---

## 步骤总览

| 步骤 | 名称 | 产出 | 由谁 |
|------|------|------|------|
| 1 | 输入准备 | `stock_code` / `stock_name` / `report_type` / `language` / `days` | 本 skill |
| 2 | 行情获取 | quote bars（多日 OHLCV）+ pct_chg | **本 skill** `kline.py`（发现+持久化：候选 百度/mootdx/腾讯，命中源存 `storage/kline_chain.json`） |
| 2b | 基本面获取 | fundamental bundle（growth/earnings/分红/主力资金/行业+估值） | **委托 a-stock-data §6/§3**（季报/F10/新浪三表/东财资金流） |
| 3 | 技术指标 | MA5/10/20、量比、RSI14、乖离率 | **本 skill** `scripts/lib/indicators.py` |
| 4 | 新闻搜索 | news 列表（标题/摘要/时间/媒体/URL） | **委托 a-stock-data §5**（东财个股新闻 / 财联社电报） |
| 5 | 构建分析上下文 | context pack dict | 本 skill |
| 6 | LLM 分析 | 决策仪表盘 JSON | 本 skill（读 `references/prompts.md`） |
| 7 | 报告生成与存储 | `storage/reports/{code}/{date}.json` + `.md` 或 sqlite 行 | 本 skill（`references/storage.md` 纯标准库 snippet） |

---

## 步骤 1：输入准备

**输入**：用户请求（自然语言或结构化参数）。

**操作**：从请求中解析并归一化：

| 字段 | 含义 | 取值 / 示例 |
|------|------|-------------|
| `stock_code` | 股票代码 | A 股 `600519`、ETF `515980`、`002044` |
| `stock_name` | 股票名称 | `贵州茅台` / `人工智能ETF华富`；缺失时用代码占位，步骤 2 拿到行情后回填 |
| `report_type` | 报告类型 | `simple` / `detailed`，默认 `detailed` |
| `language` | 报告语言 | `zh` / `en`，默认 `zh` |
| `days` | 新闻回溯天数 | 整数，默认 `7` |

**输出**：`{"stock_code": "600519", "stock_name": "贵州茅台", "report_type": "detailed", "language": "zh", "days": 7}`

**衔接下一步**：`stock_code` 传步骤 2；`stock_name` 传步骤 4 作新闻关键词；`days` 传步骤 4。

---

## 步骤 2：行情获取（quote）— 本 skill `kline.py`（发现 + 持久化）

**输入**：步骤 1 的 `stock_code`。

**操作**：调本 skill 的 `scripts/lib/kline.py`：

```python
import sys; sys.path.insert(0,'scripts')
from lib.kline import fetch_quote
q = fetch_quote('515980')   # 返回 dict 或 None
```

`kline.py` 候选源（peer，无写死优先级）：**百度股市通 K线**（a-stock-data §1.3，`finance.pae.baidu.com` HTTP）/ **mootdx**（a-stock-data §1.1，通达信 TCP，`KLINE_TRY_MOOTDX=1` opt-in）/ **腾讯 appstock**（`web.ifzq.gtimg.cn`，HTTP 前复权 qfq）。

> **发现 + 持久化，无写死兜底**：首次运行（无 `storage/kline_chain.json`）按默认序探测候选，命中第一个返回数据的源即停，把命中源置首 + 其余候选按默认序写入 `storage/kline_chain.json` 作为「K线源优先链参考」。后续读保存链按序取、命中即返；保存链全失败则重新探测所有候选、覆盖重建链（自愈）。不同机器/网络会发现不同命中源（本机 mootdx TDX 死 + 百度返空 → 命中 tencent；他机可能命中百度/mootdx）。用户删 `storage/kline_chain.json` 可强制重新发现。

**输出**（`fetch_quote` 返回，`QUOTE_FIELDS` 风格；`data_source` 为命中的源名）：

```json
{"code":"515980","date":"2026-07-21","open":1.07,"high":1.09,"low":1.02,"close":1.06,
 "volume":2197069,"amount":null,"pct_chg":-0.28,"data_source":"kline/tencent",
 "bars":[{"date":"...","open":...,"close":...,"high":...,"low":...,"volume":...}, ...]}
```

`fetch_quote` 返回 None（代码无匹配/网络错）→ 记 `data_gap: ["quote"]`，步骤 3 指标无法算，LLM 知情降级。覆盖：A 股（沪 6/9、深 0/1/3、北 8）+ A 股 ETF（5xxxxx/1xxxxx）；港美台不支持。

**衔接下一步**：`q["bars"]` 传步骤 3；单日 OHLCV 并入步骤 5 context pack。PE/PB/市值等估值字段如需，agent 可另委托 a-stock-data §1.2 `tencent_quote()`（qt.gtimg.cn，注意 ETF 字段少可能被其 `<53` 过滤丢弃，股票 OK）。

---

## 步骤 2b：基本面获取（fundamental，可选）— 委托 a-stock-data §6/§3

**输入**：步骤 1 的 `stock_code`。

**操作**：agent 调 a-stock-data 取基本面 bundle（多端点 fail-open，任一成功即用）：

| 块 | a-stock-data 端点 |
|----|-------------------|
| 季报 37 字段（EPS/ROE/净利润/主营收入） | §6.1 季报快照（mootdx，本机失效则降级 §6.3 东财/新浪） |
| 财报三表 | §6.4 新浪财报三表（HTTP） |
| F10 公司资料 | §6.2 F10（mootdx，失效则跳过） |
| 主力资金流（分钟/120日） | §3.4 东财 push2（`em_get` 限流） |
| 行业/总股本/市值/上市日期 | §6.3 东财个股信息（push2） |
| 分红送转 | §4 东财 datacenter |

**归一化**为 `fundamental` dict（`growth`/`earnings`/`capital_flow`/`industry`/`valuation`/`source_chain`/`errors`），多端点探测部分成功 + 来源链，永不抛异常。ETF/指数基本返回 `not_supported`，属正常降级。

**衔接下一步**：并入步骤 5 context pack `fundamental` 字段。

---

## 步骤 3：技术指标 — 本 skill

**输入**：步骤 2 归一化的 `bars`（每条含 `close` 和 `volume`）。

**操作**：调本 skill 的 `scripts/lib/indicators.py` `compute_all(bars)`：

```bash
python -c "import sys,json; sys.path.insert(0,'scripts'); from lib.indicators import compute_all; bars=json.load(open('bars.json')); print(json.dumps(compute_all(bars), ensure_ascii=False))"
```

`compute_all` 纯标准库，不触网、不依赖 a-stock-data。输入 list[dict]，每条至少 `close` + `volume`。

**输出**：

```json
{"last_close":1680.5,"ma5":1672.3,"ma10":1655.1,"ma20":1640.0,
 "bias_ma5":0.49,"bias_ma10":1.53,"volume_ratio":1.18,"rsi14":58.32}
```

| 字段 | 含义 | 不足时 |
|------|------|--------|
| `last_close` | 末页收盘 | `null` |
| `ma5`/`ma10`/`ma20` | 5/10/20 日均线 | `null` |
| `bias_ma5`/`bias_ma10` | 乖离率 % | `null` |
| `volume_ratio` | 量比 | `null` |
| `rsi14` | 14 日 RSI | `null` |

**衔接下一步**：整份指标 dict 作 `indicators` 字段并入步骤 5。

---

## 步骤 4：新闻搜索 — 委托 a-stock-data §5

**输入**：步骤 1 的 `stock_name`（中文名搜索质量高于纯代码）与 `days`。

**操作**：agent 调 a-stock-data 新闻层：

| 数据 | a-stock-data 端点 | 说明 |
|------|-------------------|------|
| 个股新闻 | §5.1 `eastmoney_stock_news(code)`（search-api-web JSONP，`em_get` 限流） | per-stock、字段齐，主源 |
| 全市场电报 | §5.2 `cls_telegraph()`（cls.cn v1 + 本地签名零 key） | 大盘情绪，非个股 |
| 全球资讯 | §5.3 东财 np-weblist（7×24） | 与财联社互备 |

**归一化**为 `news` 列表，每条含 `NEWS_FIELDS`：`title`/`summary`/`url`/`published_date`/`source`。东财 `date` 字段 "YYYY-MM-DD HH:MM:SS" 取前 10 位；缺失则从标题/摘要正则提取 `YYYY年M月D日`/`YYYY-MM-DD`。超出 `days` 窗口的条目丢弃；百科/字典域名（baike/zhidao/cidian）黑名单过滤。

```json
{"title":"贵州茅台成交额达100亿","summary":"7月20日涨6%","url":"http://finance.eastmoney.com/a/...","published_date":"2026-07-20","source":"东方财富Choice数据"}
```

东财间歇风控返回 `passportWeb` 无文章 → `[]`，改用财联社/全球资讯兜底，或记 `data_gap: ["news"]`，不阻断。

**衔接下一步**：`news` 列表并入步骤 5 context pack。

---

## 步骤 5：构建分析上下文（Context Pack）

**输入**：步骤 2 quote、2b fundamental、3 indicators、4 news + 步骤 1 元数据。

**操作**：agent 内存合并为 context pack dict：

```json
{
  "stock_code":"600519","stock_name":"贵州茅台","report_type":"detailed","language":"zh",
  "as_of_date":"2026-07-20","is_index_etf":false,
  "quote":{"code":"600519","date":"2026-07-20","close":1680.5,"pct_chg":0.30,"pe_ttm":30.0,"pb":7.13,"data_source":"a-stock-data"},
  "fundamental":{"code":"600519","status":"partial","growth":{"roe":0.30},"capital_flow":{"main_net_inflow":1.2},"valuation":{"pe_ratio":30.0}},
  "indicators":{"last_close":1680.5,"ma5":1672.3,"ma10":1655.1,"ma20":1640.0,"rsi14":58.32,"volume_ratio":1.18},
  "news":[{"title":"...","published_date":"2026-07-20","source":"东方财富Choice数据"}],
  "data_gap":[]
}
```

任一上游缺失 → 对应字段 `null`/`[]` + 追加 `data_gap`（`"quote"`/`"fundamental"`/`"indicators"`/`"news"`），供步骤 6 知情降级。ETF 标的置 `is_index_etf: true`，触发步骤 6 prompt 的指数/ETF 约束。

**衔接下一步**：序列化填入步骤 6 prompt。

---

## 步骤 6：LLM 分析 — 本 skill

**输入**：步骤 5 context pack。

**操作**：
1. 读 `references/prompts.md`，取 `SYSTEM_PROMPT` + `report_type` 模板。
2. context pack 作 `{{context_pack}}` 注入；`{{stock_name}}`/`{{stock_code}}`/`{{language}}` 替换。
3. agent 以该 prompt 调 LLM，产出决策仪表盘 JSON（schema 见 `prompts.md`）。

**输出**：决策仪表盘 JSON（`sentiment_score`/`action`/`decision_type`/`dashboard`/`analysis_summary`/...）。`confidence`/`score` 反映 `data_gap` 缺口。

**衔接下一步**：JSON 作 `analysis` 字段传步骤 7。

---

## 步骤 7：报告生成与存储 — 本 skill

**输入**：步骤 6 决策仪表盘 JSON + 步骤 5 context pack + 步骤 1 `report_type`/`language`。

**操作**：按 `references/storage.md` 的纯标准库 snippet 落盘（`write_report_json`/`write_report_md`/`update_index`，agent 内联调用）。
- `STORAGE_MODE=file`（默认）：写 `storage/reports/{code}/{YYYY-MM-DD}.json`（`meta`+`analysis`+`context_pack`）+ `.md`（人读）。
- `STORAGE_MODE=sqlite`：写 `analysis_history` + `context_snapshot` 两表（`sqlite3` 标准库）。

`meta` 固定：`query_id`/`stock_code`/`stock_name`/`report_type`/`language`/`created_at`/`model_used`。

**输出**：`{"ok":true,"report_path":"storage/reports/600519/2026-07-20.json","md_path":"...md"}`。失败返回 `{"ok":false,"error":"..."}`。

**衔接下一步**：无（终点）。

---

## 失败与降级约定

- 数据域（quote/fundamental/news）单点失败不阻断：记步骤 5 `data_gap`，LLM 知情降级。a-stock-data 各端点主源被封时按其「备用源速查」降级（不同域名/风控面）。
- 步骤 3 指标在 bars 不足时各子项 `null`，不抛异常。
- 步骤 6 LLM 失败：重试一次仍失败则整轮失败，不写报告。
- 步骤 7 存储失败：内存报告可取回，持久化缺失显式报错。

---

## 委托契约要点

- 本 skill **不 import a-stock-data**（它是单文件 Skill，非 Python 模块）。"委托"= agent 在运行时按 a-stock-data SKILL.md 的端点路由速查表取对应代码段执行，结果归一化后喂本 skill 的 indicators/storage。
- a-stock-data 的 43 端点按需局部读取（不必通读 127KB），常用：§1.2 腾讯报价、§1.3 百度K线、§3.4 资金流、§5.1 个股新闻、§6 基本面。
- 本 skill 只对 `bars`（步骤 2→3）和报告存储（步骤 7）有代码依赖；其余纯 agent 编排。
