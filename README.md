# Stock Analysis Skill

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Tests: 43](https://img.shields.io/badge/tests-43-brightgreen.svg)](#测试)
[![Skill v1.0.0](https://img.shields.io/badge/skill-v1.0.0-purple.svg)](#版本维护)

A 股分析**薄编排 skill**——只做输入归一化、技术指标计算、上下文合并、LLM 决策仪表盘 prompt、报告落盘；**所有 A 股原始数据获取委托 sibling skill [a-stock-data](https://github.com/simonlin1212/a-stock-data)（43 端点 / 15 数据源）**。单文件可移植、零项目运行时依赖、任何支持 skill 的智能体项目都能加载即用。

> 设计参考自 [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) 的 `src/core/pipeline.py` 分析主干语义，提取为可移植的自包含 skill。

> **v1.0.0（首版 · 薄编排 · K线发现+持久化 · 全数据委托）**：本 skill 只保留「编排 + 指标 + K线 + 存储」薄层，其余 A 股数据（基本面/新闻/资金流/研报/公告/打板/期权/舆情）全委托 a-stock-data。K线 候选源（a-stock-data §1.3 百度 / §1.1 mootdx opt-in + 腾讯 appstock）发现机制 + 持久化优先链 `storage/kline_chain.json`，无写死兜底（不同环境发现不同命中源）；多股批量 `batch.py` 30 只 Tier1 **1.3s**。

---

## 目录结构

```
stock-analysis/
├── SKILL.md                        # skill 入口（frontmatter + 触发条件 + 编排契约）
├── README.md                       # 本文件
├── requirements.txt                # 依赖说明（本 skill 自身零 pip 依赖）
├── scripts/
│   ├── batch.py                    # 多股分层批量 runner（Tier1 信号总表 + Tier2 选定新闻）
│   └── lib/
│       ├── kline.py                # K线 发现+持久化（候选 百度/mootdx/腾讯，命中源存 storage/kline_chain.json，纯标准库）
│       ├── indicators.py           # 技术指标 compute_all（MA/RSI/量比/乖离，纯标准库）
│       └── __init__.py
├── references/
│   ├── orchestration.md            # 7 步分析编排流程（每步标注由谁：本 skill vs 委托 a-stock-data）
│   ├── prompts.md                  # LLM SYSTEM_PROMPT + 决策仪表盘 JSON schema
│   └── storage.md                  # 报告落盘（file/sqlite 双模式，纯标准库 snippets）
├── tests/
│   ├── test_kline.py               # 22 条
│   ├── test_indicators.py          # 5 条
│   └── test_batch.py               # 16 条
└── storage/                        # 报告产物（file 模式 reports/，sqlite 模式 analysis.db）
```

---

## 架构

```
stock-analysis · 薄编排 · v1.0.0
│  （本 skill 只做编排 + 指标 + K线 + 存储；其余数据全委托 a-stock-data）
├── 步骤1 输入准备        本 skill        stock_code/name/report_type/language/days 归一化
├── 步骤2 行情 K线        本 skill kline.py   候选 百度/mootdx/腾讯，发现命中源+存优先链（无写死兜底）
├── 步骤2b 基本面         委托 a-stock-data    季报/F10/新浪三表/东财资金流（agent 运行时取）
├── 步骤3 技术指标        本 skill indicators.py   MA5/10/20 · 量比 · RSI14 · 乖离率
├── 步骤4 新闻            委托 a-stock-data    东财个股新闻 / 财联社电报 / 全球资讯
├── 步骤5 context pack    本 skill        合并 quote/fundamental/indicators/news + data_gap
├── 步骤6 LLM 仪表盘       本 skill        读 prompts.md 生成决策仪表盘 JSON（sentiment/action/dashboard）
└── 步骤7 报告存储        本 skill        storage/reports/{code}/{date}.json + .md 或 sqlite
```

> **K线 发现+持久化（无写死兜底）**：K线 候选源 = a-stock-data §1.3 百度K线 / §1.1 mootdx(opt-in) + 腾讯 appstock。首次运行探测命中源、存优先链 `storage/kline_chain.json`，后续按保存链取、全失败自愈重探。不同环境发现不同命中源（本机 mootdx TDX 死 + 百度返空 → 命中 tencent；他机可能命中百度/mootdx）。用户删 `storage/kline_chain.json` 可强制重新发现。详见 [已知限制](#已知限制)。

---

## 安装

### 方式 1：作为 Claude Code / OpenClaw / Codex skill 安装

```bash
# 1. 复制 skill 到 skills 目录
cp -r stock-analysis ~/.claude/skills/stock-analysis

# 2. 安装 sibling skill a-stock-data（数据层，必装）
mkdir -p ~/.claude/skills/a-stock-data
curl -o ~/.claude/skills/a-stock-data/SKILL.md \
  https://raw.githubusercontent.com/simonlin1212/a-stock-data/main/SKILL.md

# 3. 安装 a-stock-data 的依赖（本 skill 自身零 pip 依赖）
pip install mootdx requests pandas stockstats
```

启动 Claude Code，说「分析一下贵州茅台」自动激活。

> **Codex / OpenClaw 用户**：把 `SKILL.md` 内容贴入系统 prompt 或项目上下文文件，内嵌 Python 可直接执行。

### 方式 2：作为独立 Python 模块使用（不走 skill 机制）

```bash
git clone https://github.com/adamchau/stock-analysis.git
cd stock-analysis
pip install mootdx requests pandas stockstats   # 仅 a-stock-data 委托层需要；kline/indicators/batch 零依赖

# K线 + 指标（本 skill，零依赖）
python -c "import sys; sys.path.insert(0,'scripts'); from lib.kline import fetch_quote; from lib.indicators import compute_all; q=fetch_quote('515980'); print(compute_all(q['bars']))"

# 多股批量信号总表（本 skill，零依赖，30 只 ~1.3s）
python scripts/batch.py
```

### 方式 3：智能体一键安装（复制提示词直接装）

把下面这段贴给你的 AI 助手（Claude Code / Codex / OpenClaw 等），它会把 skill + 数据层 sibling skill + 依赖一次装好，并跑个冒烟验证：

```text
请帮我安装 stock-analysis skill 及其数据依赖，步骤：
1. git clone https://github.com/adamchau/stock-analysis.git ~/.claude/skills/stock-analysis
2. 装 sibling skill a-stock-data（数据层）：curl -fsSL -o ~/.claude/skills/a-stock-data/SKILL.md https://raw.githubusercontent.com/simonlin1212/a-stock-data/main/SKILL.md （先 mkdir -p ~/.claude/skills/a-stock-data）
3. pip install mootdx requests pandas stockstats
4. 冒烟验证：python -c "import sys; sys.path.insert(0,'~/.claude/skills/stock-analysis/scripts'); from lib.kline import fetch_quote; q=fetch_quote('600519'); print('600519', q['date'], q['close'], q['data_source'])"
装完说一声，然后我可以直接说「分析一下贵州茅台」激活本 skill。
```

> 单行版（直接粘到终端或让 agent exec）：
> ```bash
> git clone https://github.com/adamchau/stock-analysis.git ~/.claude/skills/stock-analysis && mkdir -p ~/.claude/skills/a-stock-data && curl -fsSL -o ~/.claude/skills/a-stock-data/SKILL.md https://raw.githubusercontent.com/simonlin1212/a-stock-data/main/SKILL.md && pip install mootdx requests pandas stockstats && python -c "import sys; sys.path.insert(0,'$HOME/.claude/skills/stock-analysis/scripts'); from lib.kline import fetch_quote; q=fetch_quote('600519'); print('ok', q['date'], q['close'], q['data_source'])"
> ```

---

## 功能描述

### 本 skill 提供（本地，纯标准库，零依赖）

| 能力 | 入口 | 说明 |
|------|------|------|
| A 股 K线/行情 | `lib.kline.fetch_quote(code)` / `fetch_bars(code)` | 腾讯 appstock，前复权，返回 bars + OHLCV + pct_chg。覆盖 A 股（沪 6/9、深 0/1/3、北 8）+ A 股 ETF（5xxxxx/1xxxxx） |
| 技术指标 | `lib.indicators.compute_all(bars)` | MA5/10/20、乖离率、量比、RSI14，纯标准库 |
| 多股批量信号总表 | `scripts/batch.py` | Tier1：K线并发 + qt.gtimg.cn 批量 PE/PB/市值 + 指标 → action/score 信号总表（30 只 ~1.3s）；Tier2 `--news` 选定几只加东财新闻 |
| LLM 决策仪表盘 | `references/prompts.md` | SYSTEM_PROMPT + 决策仪表盘 JSON schema（sentiment_score/action/decision_type/dashboard/core_conclusion/battle_plan/...） |
| 报告落盘 | `references/storage.md` snippets | file 模式（`storage/reports/{code}/{date}.json` + `.md` + `index.json`）或 sqlite（`analysis.db` 两表），纯标准库 |

### 委托 a-stock-data 提供（agent 运行时按其端点路由速查表取数）

| 能力 | a-stock-data 端点 |
|------|-------------------|
| 实时 PE/PB/市值/换手率/涨跌停 | §1.2 `tencent_quote()`（qt.gtimg.cn） |
| 基本面（季报 37 字段 / F10 / 新浪三表） | §6（mootdx + 东财 + 新浪） |
| 主力资金流 / 融资融券 / 龙虎榜 / 解禁 / 大宗 / 股东户数 / 分红 | §3/§4（东财 datacenter + push2，`em_get` 限流） |
| 个股新闻 / 财联社电报 / 全球资讯 | §5（东财 search-api-web + cls.cn v1 签名零 key） |
| 研报 / 一致预期 EPS / 行业研报 / PDF | §2（东财 reportapi + 同花顺 + iwencai） |
| 公告（沪深北全量） | §7（巨潮 cninfo + 官方备胎） |
| 打板池 / 连板 / 炸板率 / 涨停归因 | §8（东财 push2ex + 同花顺） |
| ETF 期权 T 型报价 / Greeks / IV | §9（新浪 hq.sinajs） |
| 舆情互动（互动易 / 热榜 / 人气榜 / 概念命中） | §10（巨潮 IRM + 同花顺 + 东财） |

---

## 使用示例

跟你的 AI 助手说这些话就能激活：

| 场景 | 说什么 |
|------|--------|
| 单股完整分析 | 「分析一下贵州茅台（600519），生成决策仪表盘」 |
| 看行情 | 「515980 人工智能ETF 现在什么价，技术面怎么样」 |
| 批量信号总表 | 「跑一下这 30 只 A 股 ETF 的决策信号总表」 |
| 选股筛选 | 「按评分排序，哪些 ETF 多头排列可买入」 |
| 深度仪表盘 | 「对信号总表里评分最高的 3 只，加新闻生成完整仪表盘并落盘」 |
| 查历史 | 「看看 600519 上次的报告」 |

### 直接跑批量脚本

```bash
# 默认 30 只观察池，Tier1 信号总表（无 news，1.3s）
python scripts/batch.py

# 自定义代码 + Tier2 加新闻（em_get 串行，~1.3s/只）
python scripts/batch.py --codes "600519,515980,002044" --names "贵州茅台,人工智能ETF华富,美年健康" --news "贵州茅台,人工智能ETF华富" --json
```

### 决策仪表盘输出（per `references/prompts.md` schema）

```json
{
  "stock_name": "贵州茅台", "sentiment_score": 62, "trend_prediction": "看多",
  "operation_advice": "持有", "decision_type": "hold", "action": "hold",
  "guardrail_reason": "多头排列+新闻偏多给62分，但RSI74.5超买，降级为持有不追高",
  "confidence_level": "中",
  "dashboard": {
    "core_conclusion": { "one_sentence": "多头排列但RSI超买，等回踩MA5再加仓", "signal_type": "🟡持有观望", ... },
    "data_perspective": { "trend_status": {...}, "price_position": {...}, "volume_analysis": {...} },
    "battle_plan": { "sniper_points": {"ideal_buy": "...", "stop_loss": "...", "take_profit": "..."}, ... },
    "phase_decision": {...}, "signal_attribution": {...}
  },
  "analysis_summary": "...", "risk_warning": "...", "data_sources": "..."
}
```

---

## 依赖

| 依赖 | 用途 | 是否必需 |
|------|------|----------|
| **[a-stock-data](https://github.com/simonlin1212/a-stock-data)**（sibling skill） | A 股数据层（43 端点）：PE/PB/基本面/新闻/资金流/研报/公告/打板/期权/舆情 | **必需**（K线/指标可独立跑，但基本面/新闻/资金流等全靠它） |
| `mootdx` | a-stock-data 行情层 TCP（通达信 7709） | a-stock-data 依赖（本机可能失效，见下） |
| `requests` | a-stock-data 东财 `em_get` 限流会话 | a-stock-data 依赖 |
| `pandas` | a-stock-data 数据处理 | a-stock-data 依赖 |
| `stockstats` | a-stock-data 指标辅助 | a-stock-data 依赖 |

> **本 skill 自身零 pip 依赖**：`kline.py` / `indicators.py` / `batch.py` 均纯 Python 标准库（urllib + concurrent + json + re + datetime + sqlite3）。装 a-stock-data 的依赖只是为委托数据层；不装也能独立跑 K线+指标+批量信号总表。

```bash
# 一键装齐 a-stock-data 依赖
pip install mootdx requests pandas stockstats
```

---

## 版本维护

- **当前版本**：`v1.0.0`（首版），记在 `SKILL.md` frontmatter 的 `version:` 字段，遵循 [Semantic Versioning](https://semver.org/)。
- **变更日志**：见 `CHANGELOG.md`（扁平格式 `- [类型] 描述`，类型：新功能/改进/修复/文档/测试/chore）。
- **能力**：薄编排架构（编排 + 指标 + K线 + 存储）+ 全数据委托 a-stock-data + K线 发现+持久化（候选 百度/mootdx/腾讯，命中源存 `storage/kline_chain.json`）+ 多股分层批量 runner（30 只 Tier1 1.3s）+ 43 单元测试。

后续版本按 SemVer 维护：patch 修 bug、minor 加端点/能力（向后兼容）、major 破坏性变更。

---

## 测试

```bash
# 纯单元测试（mock 网络，无需联网，~0.1s）
pip install pytest
pytest tests/ -q

# 或用 uv（无需项目虚拟环境）
uv run --with pytest --no-project pytest tests/ -q
```

当前 **36 测试**（15 kline + 5 indicators + 16 batch），覆盖：符号映射、bars 归一化、K线字段契约、网络异常兜底、技术信号（多头/空头/超买/无数据）、新闻情绪分类、qt 批量报价解析（股票 53 字段 / ETF 短字段 best-effort）、日期提取、recency 过滤、run_tier1 并发+失败兜底。

---

## 已知限制

- **纯 A 股**：覆盖 A 股 + A 股 ETF（沪 6/9、深 0/1/3、北 8、ETF 5xxxxx/1xxxxx）。**港股/美股/台股不支持**（a-stock-data 不覆盖，`kline.py` 也只映射 A 股符号）。如需港美台，可另加 yfinance 委托层。
- **K线 发现+持久化（无写死兜底）**：K线 候选源 = a-stock-data §1.3 百度 / §1.1 mootdx(opt-in, `KLINE_TRY_MOOTDX=1`) + 腾讯 appstock；首次探测命中源、存优先链 `storage/kline_chain.json`，后续按链取、全失败自愈重探。本机 mootdx TDX 死 + 百度返空 → 命中 tencent；他机可能命中百度/mootdx。mootdx 独有的五档盘口/逐笔/F10 不可用。删 `storage/kline_chain.json` 可强制重新发现。
- **委托开销**：基本面/新闻/资金流等每次取数需 agent 加载 a-stock-data SKILL.md 对应章节并 exec 内嵌 Python，比直接 HTTP 调用慢且 token 重；批量场景用 `batch.py` Tier1（本地 K线+指标+qt 批量，秒级）+ Tier2（仅选定几只加新闻）规避 30×news 的 em_get 1s 限流地板。
- **qt 批量对 ETF 字段稀疏**：`qt.gtimg.cn` 批量报价对股票（53 字段）齐（PE/PB/市值全），ETF 字段少于 53 → PE/PB/市值 best-effort 取有的（缺失为 None）。信号总表以 K线+指标为主，估值字段为补充。
- **mootdx 库烂尾**：mootdx 最后 commit 2024-07，BESTIP 等 bug 无官方修复；a-stock-data 的 `tdx_client()` helper 已绕开，但本机 TDX 协议仍返 None。a-stock-data 的 HTTP 端点（腾讯/东财 em_get/同花顺/巨潮/财联社）不依赖 mootdx，实测可用。

---

## 致谢

本项目站在巨人的肩膀上，深度感谢以下开源项目：

- **[a-stock-data](https://github.com/simonlin1212/a-stock-data)** by [Simon 林](https://github.com/simonlin1212) —— A 股全栈数据工具包（十层架构 · 43 端点 · 15 数据源）。本 skill 的全部 A 股数据获取（PE/PB/基本面/新闻/资金流/研报/公告/打板/期权/舆情）委托该 sibling skill 在运行时取数；其东财 `em_get` 限流防封、备用源速查降级、端点路由速查表等设计深刻影响了本 skill 的数据层契约。🙏
- **[daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)** —— 本 skill 的设计参考该项目 `src/core/pipeline.py` 的分析主干语义（数据获取 → 技术指标 → 新闻 → 上下文合并 → LLM → 报告存储），提取为可移植的自包含薄编排 skill。🙏

如果这个 skill 帮到了你的投研工作流，欢迎去给上面两个项目点 ⭐。

---

## 免责声明

本项目仅提供数据分析与编排工具，不构成任何投资建议。股市有风险，投资需谨慎。

---

## License

[MIT License](LICENSE) © 2026 adamchau
