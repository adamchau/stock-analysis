# 存储双模式：本地文件 / sqlite

> 被 `references/orchestration.md` 步骤 7 引用。stock-analysis skill 的报告落盘支持两种模式，由 `STORAGE_MODE` 环境变量切换。两种模式均只依赖 Python 标准库，不依赖项目运行时、项目数据库或项目存储模块。所有路径均相对 skill 根目录下的 `storage/`。

---

## 模式选择

| 变量 | 取值 | 行为 |
|------|------|------|
| `STORAGE_MODE` | `file`（默认） | 写 `storage/reports/{code}/{date}.json` + `.md` + `storage/reports/index.json` |
| `STORAGE_MODE` | `sqlite` | 写 `storage/analysis.db`，`analysis_history` + `context_snapshot` 两表 |
| `STORAGE_DIR` | 任意路径 | 覆盖 `storage/` 根目录（两种模式通用） |

未设置 `STORAGE_MODE` 时默认 `file`。`STORAGE_DIR` 缺失时根目录为 skill 内 `storage/`。agent 不写死绝对路径，运行时以 `STORAGE_DIR` 或默认 `storage/` 为根。

---

## 默认本地文件模式（`STORAGE_MODE=file`，零依赖）

### 目录结构

```
storage/
└── reports/
    ├── index.json
    ├── 600519/
    │   ├── 2024-03-28.json
    │   └── 2024-03-28.md
    └── hk00700/
        ├── 2024-03-28.json
        └── 2024-03-28.md
```

### JSON 报告结构

`storage/reports/{code}/{date}.json` 对应 `references/prompts.md` 的决策仪表盘输出 schema，顶层含 `meta` + `analysis` + `context_pack` 三段。

```json
{
  "meta": {
    "query_id": "20240328-600519-a1b2c3",
    "stock_code": "600519",
    "stock_name": "贵州茅台",
    "report_type": "detailed",
    "language": "zh",
    "created_at": "2024-03-28T18:05:00+08:00",
    "model_used": "gpt-4o"
  },
  "analysis": {
    "stock_name": "贵州茅台",
    "sentiment_score": 75,
    "trend_prediction": "看多",
    "operation_advice": "加仓",
    "decision_type": "buy",
    "action": "add",
    "guardrail_reason": "",
    "confidence_level": "高",
    "dashboard": { "...": "见 prompts.md 决策仪表盘 schema" },
    "analysis_summary": "100字综合分析摘要"
  },
  "context_pack": {
    "quote": {"close": 1722.3, "...": "步骤5 context pack"},
    "fundamental": {"growth": {"roe": 0.30}, "industry": "食品饮料", "...": "可选，akshare 缺失时为 null"},
    "indicators": {"ma5": 1710.0, "rsi14": 58.2},
    "news": [{"title": "...", "published_date": "..."}],
    "data_gap": []
  }
}
```

`meta` 段固定字段：`query_id`（本轮唯一 id）、`stock_code`、`stock_name`、`report_type`、`language`、`created_at`（ISO8601）、`model_used`。`analysis` 段即步骤 6 LLM 产出的决策仪表盘 JSON。`context_pack` 段即步骤 5 产出的上下文，供回填行情/指标摘要与 `data_gap`。

### Markdown 报告渲染

`storage/reports/{code}/{date}.md` 按 `meta.language` 渲染人读报告，固定结构：

- 标题：`# {stock_name}({code}) 分析报告 {date}`
- 摘要段：`analysis.analysis_summary` + `sentiment_score` / `action` / `confidence_level`
- 策略点位段：`dashboard.core_conclusion` 一句话结论 + 信号类型 + 时间敏感度 + 持仓建议
- 详情段：`analysis.trend_analysis` / `technical_analysis` / `ma_analysis` / `volume_analysis` / `pattern_analysis` / `fundamental_analysis`

`language=en` 时渲染英文对应模板，标题改为 `# {stock_name}({code}) Analysis Report {date}`。

### 索引文件

`storage/reports/index.json` 为列表，每条对应一次分析记录，供历史查询与列表展示：

```json
[
  {
    "code": "600519",
    "name": "贵州茅台",
    "date": "2024-03-28",
    "sentiment_score": 75,
    "action": "add"
  },
  {
    "code": "hk00700",
    "name": "腾讯控股",
    "date": "2024-03-28",
    "sentiment_score": 62,
    "action": "hold"
  }
]
```

追加规则：每轮分析写入后，向 `index.json` 追加一条；同 `code` + `date` 视为覆盖（先删后追加）。

### 纯标准库读写片段

**写入 JSON 报告**：

```python
import json
from pathlib import Path

def write_report_json(storage_dir: Path, code: str, date: str, report: dict) -> Path:
    code_dir = storage_dir / "reports" / code
    code_dir.mkdir(parents=True, exist_ok=True)
    path = code_dir / f"{date}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
```

**写入 Markdown 报告**：

```python
def write_report_md(storage_dir: Path, code: str, date: str, name: str,
                     analysis: dict, language: str = "zh") -> Path:
    code_dir = storage_dir / "reports" / code
    code_dir.mkdir(parents=True, exist_ok=True)
    path = code_dir / f"{date}.md"
    title = f"# {name}({code}) 分析报告 {date}" if language == "zh" \
            else f"# {name}({code}) Analysis Report {date}"
    lines = [title, "", analysis.get("analysis_summary", ""), ""]
    dash = analysis.get("dashboard", {}) or {}
    core = dash.get("core_conclusion", {}) or {}
    if core:
        lines += ["## 策略点位", "", core.get("one_sentence", ""), ""]
    for key in ("trend_analysis", "technical_analysis", "fundamental_analysis"):
        if analysis.get(key):
            lines += [f"## {key}", "", analysis[key], ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
```

**更新索引**：

```python
def update_index(storage_dir: Path, code: str, name: str, date: str,
                 sentiment_score: int, action: str) -> None:
    idx_path = storage_dir / "reports" / "index.json"
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    if idx_path.exists():
        try:
            entries = json.loads(idx_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            entries = []
    entries = [e for e in entries if not (e.get("code") == code and e.get("date") == date)]
    entries.append({"code": code, "name": name, "date": date,
                    "sentiment_score": sentiment_score, "action": action})
    idx_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
```

**读取历史**：

```python
def list_history(storage_dir: Path, code: str | None = None) -> list[dict]:
    idx_path = storage_dir / "reports" / "index.json"
    if not idx_path.exists():
        return []
    try:
        entries = json.loads(idx_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if code:
        entries = [e for e in entries if e.get("code") == code]
    return entries
```

---

## 可选 sqlite 模式（`STORAGE_MODE=sqlite`）

### DB 路径

`storage/analysis.db`（或 `STORAGE_DIR` 覆盖后的 `{STORAGE_DIR}/analysis.db`）。schema 为简化自包含两表，不带入项目特定字段，纯标准库 `sqlite3` 驱动。

### 建表 SQL

```sql
CREATE TABLE IF NOT EXISTS analysis_history (
  query_id TEXT PRIMARY KEY,
  code TEXT NOT NULL,
  name TEXT,
  report_json TEXT,
  sentiment_score INTEGER,
  action TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS context_snapshot (
  query_id TEXT PRIMARY KEY,
  code TEXT NOT NULL,
  context_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (query_id) REFERENCES analysis_history(query_id)
);
```

- `analysis_history`：每轮分析一行，`query_id` 为主键，`report_json` 存完整决策仪表盘 JSON 串。
- `context_snapshot`：每轮分析的步骤 5 context pack 快照，`query_id` 外键关联 `analysis_history`。
- `sentiment_score` / `action` 从 `analysis` 顶层字段提取冗余存储，便于历史查询直接 ORDER BY / WHERE，无需解析 JSON。

### 纯标准库读写片段

**初始化连接 + 建表**：

```python
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis_history (
  query_id TEXT PRIMARY KEY,
  code TEXT NOT NULL,
  name TEXT,
  report_json TEXT,
  sentiment_score INTEGER,
  action TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS context_snapshot (
  query_id TEXT PRIMARY KEY,
  code TEXT NOT NULL,
  context_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (query_id) REFERENCES analysis_history(query_id)
);
"""

def get_conn(storage_dir: Path) -> sqlite3.Connection:
    storage_dir.mkdir(parents=True, exist_ok=True)
    db_path = storage_dir / "analysis.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn
```

**写入分析记录 + 上下文快照**：

```python
import json

def save_analysis(conn: sqlite3.Connection, query_id: str, code: str, name: str,
                  report: dict, context_pack: dict, created_at: str) -> None:
    analysis = report.get("analysis", {}) or {}
    sentiment_score = analysis.get("sentiment_score")
    action = analysis.get("action")
    report_json = json.dumps(report, ensure_ascii=False)
    context_json = json.dumps(context_pack, ensure_ascii=False)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO analysis_history "
            "(query_id, code, name, report_json, sentiment_score, action, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (query_id, code, name, report_json, sentiment_score, action, created_at),
        )
        conn.execute(
            "INSERT OR REPLACE INTO context_snapshot "
            "(query_id, code, context_json, created_at) VALUES (?, ?, ?, ?)",
            (query_id, code, context_json, created_at),
        )
```

**查询历史（按 code）**：

```python
def query_history(conn: sqlite3.Connection, code: str, limit: int = 50) -> list[dict]:
    cur = conn.execute(
        "SELECT query_id, code, name, sentiment_score, action, created_at "
        "FROM analysis_history WHERE code = ? ORDER BY created_at DESC LIMIT ?",
        (code, limit),
    )
    return [dict(zip([d[0] for d in cur.description], row)) for row in cur.fetchall()]
```

**取完整报告 JSON**：

```python
def load_report(conn: sqlite3.Connection, query_id: str) -> dict | None:
    cur = conn.execute(
        "SELECT report_json FROM analysis_history WHERE query_id = ?",
        (query_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return json.loads(row[0])
```

---

## 返回契约

步骤 7 存储完成后返回：

```json
{"ok": true, "report_path": "storage/reports/600519/2024-03-28.json", "md_path": "storage/reports/600519/2024-03-28.md"}
```

sqlite 模式下 `report_path` 改为 `storage/analysis.db`，`md_path` 为对应 md 路径（若仍生成 md）或省略。存储失败返回 `{"ok": false, "error": "..."}`，不回滚已生成的内存报告（见 orchestration.md 失败与降级约定）。

---

## 模式选择建议

- **file 模式**：默认，零依赖，人读报告与结构化 JSON 并存，适合单机/调试/小规模历史。
- **sqlite 模式**：需要按 `code` / `created_at` 查询历史、批量统计或并发写入时启用，schema 简化自包含，纯标准库驱动，无需额外依赖。

两种模式不互转：切换 `STORAGE_MODE` 后历史不自动迁移，agent 如需历史请读旧模式产物。
