# Changelog

本文件记录 stock-analysis skill 的版本变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [1.0.0] - 2026-07-21

首版发布。薄编排 skill——编排 + 指标 + K线（发现+持久化）+ 存储，其余 A 股数据全委托 sibling skill [a-stock-data](https://github.com/simonlin1212/a-stock-data)。

### 新功能

- **薄编排架构**：7 步分析流程（输入归一化 → K线 → 基本面 → 指标 → 新闻 → context pack → LLM 仪表盘 → 落盘），编排与 prompt 内置，见 `references/orchestration.md`、`references/prompts.md`。
- **K线 发现+持久化** `scripts/lib/kline.py`：候选源 = a-stock-data §1.3 百度股市通 K线（HTTP）/ §1.1 mootdx（通达信 TCP，`KLINE_TRY_MOOTDX=1` opt-in）/ 腾讯 appstock（HTTP 前复权），peer 无写死优先级。首次运行（无 `storage/kline_chain.json`）按默认序探测、命中即停、存优先链；后续按链取、全失败自愈重探。不同环境发现不同命中源（本机 mootdx TDX 死 + 百度返空 → 命中 tencent）。`fetch_bars` / `fetch_quote` 归一化 bars 喂指标计算，`data_source` 为命中的源名。覆盖 A 股（沪 6/9、深 0/1/3、北 8）+ A 股 ETF（5xxxxx/1xxxxx）。
- **技术指标** `scripts/lib/indicators.py`：`compute_all(bars)` 算 MA5/10/20、乖离率、量比、RSI14，纯标准库。
- **多股分层批量 runner** `scripts/batch.py`：Tier1 K线并发 + qt.gtimg.cn 批量 PE/PB/市值 + 指标 → 决策信号总表（30 只 ~1.3s）；Tier2 `--news` 选定几只加东财个股新闻（em_get 1s 串行限流防封）。
- **LLM 决策仪表盘** `references/prompts.md`：SYSTEM_PROMPT + 评分/动作口径（0-100 分 → 买入/持有/观望/减仓/卖出）+ 决策仪表盘 JSON schema（core_conclusion / battle_plan / phase_decision / signal_attribution 等）。
- **报告落盘** `references/storage.md`：file 模式（`storage/reports/{code}/{date}.json` + `.md` + `index.json`）或 sqlite 模式（`analysis.db` 两表），纯标准库 snippets。
- **全数据委托 a-stock-data**：PE/PB/市值、基本面（季报/F10/三表）、新闻（东财/财联社）、资金流/融资融券/龙虎榜/解禁、研报、公告、打板、ETF 期权、舆情互动——agent 运行时按 a-stock-data 端点路由速查表取数并归一化。

### 设计决策

- **K线 发现+持久化（无写死兜底）**：K线 候选含 a-stock-data 百度/mootdx + 腾讯，发现机制决定命中源、持久化优先链，不同环境发现不同源；不写死兜底链路。
- **纯 A 股**：a-stock-data 不覆盖港美台，本 skill `kline.py` 也只映射 A 股符号，故仅支持 A 股 + A 股 ETF。
- **零 pip 依赖**（本 skill 自身）：`kline.py` / `indicators.py` / `batch.py` 均纯 Python 标准库（urllib + concurrent + json + re + datetime + sqlite3）。a-stock-data 的依赖（mootdx/requests/pandas/stockstats）为委托数据层所需（mootdx 可选，缺失自动跳过该 K线 候选）。

### 测试

- 43 单元测试：`test_kline.py`（22）+ `test_indicators.py`（5）+ `test_batch.py`（16），覆盖符号映射、腾讯/百度候选解析、优先链 load/save + 损坏兜底、首次发现+存链、保存链跳死源、全失败自愈、技术信号、新闻情绪、qt 批量解析、日期提取、recency 过滤、Tier1 并发+失败兜底。

### 致谢

- 数据层与设计契约深度感谢 [a-stock-data](https://github.com/simonlin1212/a-stock-data) by Simon 林。
- 分析主干语义参考 [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)。

---

## 版本规则

后续按 SemVer 维护：

- **patch**（1.0.x）：bug 修复、文档订正。
- **minor**（1.x.0）：新增端点委托/能力/测试，向后兼容。
- **major**（x.0.0）：破坏性变更（如编排流程、prompt schema、存储格式改）。
