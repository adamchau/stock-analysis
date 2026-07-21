---
name: stock-analysis
description: 可移植自包含的股票分析薄编排 skill。行情/基本面/新闻/资金流等全部数据委托 sibling skill a-stock-data（A股，43端点）运行时取数；本 skill 只做输入归一化、技术指标(纯标准库)、上下文合并、LLM 决策仪表盘 prompt、报告存储。覆盖 A 股 + A 股 ETF（港美台不支持）。编排流程与 prompt 内置，任何支持 skill 的智能体项目都能用。存储可选本地文件或 sqlite。
version: 1.0.0
data_sources: 委托 a-stock-data（百度K线/腾讯/东财/同花顺/巨潮/财联社 HTTP）
storage: file | sqlite
---

# Stock Analysis Skill（薄编排 · 全数据委托）

A 股分析**薄编排 skill**：只负责输入归一化、技术指标计算（纯标准库）、上下文合并、LLM 决策仪表盘 prompt、报告落盘。**所有数据获取委托 sibling skill `a-stock-data`**（43 端点 / 15 数据源）在运行时由 agent 调用——本 skill 不维护任何 fetcher/channel/dispatcher。

## 1. 概述

本 skill 是编排层 + 指标库 + prompt + 存储；数据层全部委托 a-stock-data。K线 由本 skill `kline.py` 取——候选源含 a-stock-data §1.3 百度K线 / §1.1 mootdx(opt-in) + 腾讯 appstock，发现机制探测命中源、持久化优先链到 `storage/kline_chain.json`（无写死兜底，不同环境发现不同命中源）。**仅支持 A 股 + A 股 ETF**（a-stock-data 是纯 A 股 skill，港美台不支持）。

## 2. 触发条件

满足任一即激活：

- 用户请求分析 A 股个股 / A 股 ETF（`600519`/`515980`/`002044` 等）。
- 用户要生成 A 股决策仪表盘报告或回顾历史分析。
- 用户要 A 股行情 / 新闻 / 资金流 / 基本面数据（取数委托 a-stock-data）。

非触发：纯持仓管理、交易记录、回测 → `portfolio-manager` skill。港股/美股/台股 → 不支持（a-stock-data 不覆盖）。

## 3. 依赖与分工

| 层 | 由谁 | 说明 |
|----|------|------|
| 编排（步骤1/5/6/7）+ 指标（步骤3） | **本 skill** | `scripts/lib/indicators.py` 纯标准库；`references/prompts.md`、`storage.md` |
| **K线/行情（步骤2）** | **本 skill** `scripts/lib/kline.py` | 候选源：a-stock-data §1.3 百度K线 / §1.1 mootdx(opt-in) + 腾讯 appstock；发现机制探测命中源、存优先链 `storage/kline_chain.json`，无写死兜底 |
| 数据（步骤2b/4：基本面/新闻/资金流/研报/公告/打板/期权/舆情） | **委托 a-stock-data skill** | agent 运行时按 a-stock-data 端点路由速查表取代码段执行，归一化后喂本 skill |

**a-stock-data 依赖**（项目 `.venv` 已装）：`mootdx`/`stockstats`/`requests`/`pandas`。本 skill 自身零 pip 依赖（indicators + kline 均纯标准库 urllib）。

**本 skill 不 import a-stock-data**（它是单文件 Skill，非 Python 模块）。"委托" = agent 运行时调 a-stock-data skill 取数 + 归一化；K线 由本 skill `kline.py` 取（候选含 a-stock-data 百度/mootdx + 腾讯，发现机制决定命中源，非写死兜底）。

## 4. 工具索引表

| 想做什么 | 用什么 |
|----------|--------|
| 跑完整分析流程 | 读 `references/orchestration.md`（步骤含委托 a-stock-data 的取数点） |
| 拿分析 SYSTEM_PROMPT 与输出 schema | 读 `references/prompts.md` |
| 拿 A 股 K线/行情（bars + OHLCV） | `python -c "import sys; sys.path.insert(0,'scripts'); from lib.kline import fetch_quote; print(fetch_quote('515980'))"` |
| 算技术指标（MA/RSI/量比/乖离） | `python -c "import sys; sys.path.insert(0,'scripts'); from lib.indicators import compute_all; ..."`（喂 kline.fetch_bars 的 bars） |
| **多股批量信号总表（Tier1，快）** | `python scripts/batch.py [--codes "600519,515980,..." --names "..."] [--news NAMES] [--json]` | K线并发+qt批量+指标，30 只~1-2s |
| 查报告落盘目录结构与 sqlite 表 | 读 `references/storage.md` |
| 取 A 股基本面/新闻/资金流/研报等 | 调 sibling skill `a-stock-data`（其 SKILL.md 端点路由速查表） |

## 5. 环境变量

| 变量 | 用途 | 缺失行为 |
|------|------|----------|
| `STORAGE_MODE` | `file`（默认） / `sqlite` | 缺省 file |
| `STORAGE_DIR` | 覆盖 `storage/` 根目录 | 缺省 skill 内 `storage/` |

数据层环境变量见 a-stock-data SKILL.md（`OPENWEBSEARCH_*` 不再用；东财限流 `EM_MIN_INTERVAL` 等）。

## 6. 快速开始

1. **依赖**：a-stock-data 的 `.venv` 依赖已装（`mootdx`/`stockstats`/`requests`/`pandas`）；本 skill 指标库零依赖。

2. **冒烟（指标）**：
   ```bash
   python -c "import sys,json; sys.path.insert(0,'scripts'); from lib.indicators import compute_all; print(compute_all([{'close':10,'volume':100},{'close':11,'volume':120}]))"
   ```

3. **跑完整分析**：按 `references/orchestration.md` 步骤——步骤 2/2b/4 调 a-stock-data 取数并归一化，步骤 3 算指标，步骤 5 合并 context pack，步骤 6 读 `prompts.md` 生成决策仪表盘，步骤 7 按 `storage.md` 落盘。

## 7. 允许的自动操作

- 运行 `scripts/lib/indicators.py`（compute_all，纯标准库）。
- 调 sibling skill `a-stock-data` 取 A 股数据（按其端点路由速查表）。
- 读 `references/*.md`。
- 写 `storage/reports/` 下报告文件（file 模式）或 `storage/analysis.db`（sqlite 模式）。
- 单股或 ≤5 只批量分析。

## 8. 需确认操作

- 批量分析 > 5 只：先列清单与预计取数次数，等用户确认。
- `force_refresh` / 绕过缓存重新拉取：确认是否真需要。
- `STORAGE_DIR` 指向 skill 外路径。
- 任何写入 skill 目录外的操作。

## 9. 已知限制

- **纯 A 股**：港股/美股/台股不支持（a-stock-data 不覆盖，kline.py 也只映射 A 股/ETF 符号）。如需港美台，可另加 yfinance 委托层（当前不提供）。
- **K线 发现+持久化**：K线 由 `kline.py` 取，候选源 a-stock-data §1.3 百度 / §1.1 mootdx(opt-in, `KLINE_TRY_MOOTDX=1`) + 腾讯 appstock；首次探测命中源、存优先链 `storage/kline_chain.json`（无写死兜底）。本机 mootdx TDX 死 + 百度返空，发现命中 tencent；他机可能命中百度/mootdx。保存链全失败则自愈重探。mootdx 独有的五档/逐笔/F10 不可用。
- **委托开销**：基本面/新闻/资金流等每次取数需 agent 加载 a-stock-data SKILL.md 对应章节并 exec 内嵌 Python，比直接 HTTP 调用慢且 token 重；批量按端点路由速查表局部读取控 token。
- **无程序化数据 CLI（除 K线/指标/批量）**：K线/指标有 `scripts/lib/{kline,indicators}.py` 直接 import 入口，多股批量有 `scripts/batch.py`（Tier1 信号总表，K线并发+qt批量，30只~1-2s）；基本面/新闻等只能 agent 委托 a-stock-data。
