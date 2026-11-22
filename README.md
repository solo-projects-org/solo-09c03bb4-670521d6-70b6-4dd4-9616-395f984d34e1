# 考古编年核对系统

每层位取一个整数年；测年报告给出的若干**候选年代闭区间**作为该层位的可选年份（并集，
区间之间的空档保留），地层叠压记录化为**严格早于**约束 `x < y`；编年员登记的拟采用区间
作为额外约束（与候选域取交），可更换、可撤回。系统对“最新入库状态”核对：一次提交里的
全部依据和登记必须能**同时成立**，否则整批驳回并指出冲突层位与依据，原编年表照常可查。

## 年份约定

整数表示年份：公元前 N 年记为 `-N`（如公元前 1200 年 = `-1200`），公元后直接写正数。

## 启动（Docker Compose）

```bash
docker compose up --build
# 打开 http://localhost:8000
```

SQLite 数据库持久化在宿主机 `./data/chrono.db`（compose 已挂载卷）。

## 离线/无 Docker 环境自测

本仓库业务代码仅依赖 Python 标准库；`tests/stdlib_server.py` 是与 FastAPI 路由同契约的
标准库 HTTP 垫片，用于没有网络无法安装 FastAPI 的环境：

```bash
python3 -I tests/test_solver.py     # 求解器：4000 组随机实例 vs 暴力枚举 + 定向用例
python3 -I tests/test_services.py  # 服务层：事务、登记/更正交错、冲突回滚
python3 -I tests/test_proof.py     # 校样：重算、CSV、冲突、依据更正/撤回前后的下载资格
CHRONO_DB_PATH=/tmp/t.db PORT=8000 python3 -I tests/stdlib_server.py &
python3 -I tests/test_http.py      # HTTP 级：与前端相同的 API 契约
bash tests/run_all.sh
```

## 数据模型与 API

### 表（SQLite）

| 表 | 说明 |
|---|---|
| `layer` | 层位（唯一编号 code） |
| `evidence` | 依据：`datum` 测年候选闭区间；`superposition` 严格早于（layer_id 早于 layer2_id），可撤回 |
| `registration` | 拟采用区间登记：`active` / `replaced` / `withdrawn`，更换保留替换链 |
| `submission` | 提交流水，通过与驳回均留痕（驳回带冲突详情） |
| `proof` | 外发校样留档（内部可见）：外发层位、所用依据快照（含版本号）、重算区间；不参与编年约束 |

### 校样（外发）规则

- 网页“④ 外发校样”：先选**外发层位**，再勾选**可引用的有效（active）测年 / 叠压依据**。
- 叠压依据涉及的**两个层位都须入选**——入选层位 = 外发层位 + 已勾选测年依据所属层位；
  勾选叠压时若另一端层位尚未入选，该叠压不可勾选，服务端也会以 `422` 拒绝。
- 只用所选依据按**与编年表相同的规则**重算（测年候选取并集保留空档、叠压为严格早于）；
  **登记不进入校样**，其改动也不影响校样结果。
- 所选依据无法同时成立时，网页指出冲突层位与依据，**本次停止出件**（不生成校样留档）。
- 校样可下载为 CSV（UTF-8 BOM，便于表格软件）：列出所选层位（外发层位及其它含所选测年的层位）
  的编号与可行年份区间。
- **下载资格**：生成时把所用每条依据的 `version` 记入快照；下载前按最新入库状态核对——
  只要其中任一依据被更正或撤回（`version` 前进，**即使后来改回原值**），该校样即不可下载（`409`）。
  其它依据的更正/撤回、登记的任何改动都不影响下载。生成与下载都在单次读内完成，保证用同一份已入库台账。


### HTTP API

- `GET /api/chronology`：当前编年表，每层 `feasible_intervals` 为可行年份闭区间并集（保留空档）
- `GET /api/state`：层位/依据/登记/流水全量台账 + 编年表
- `GET /api/submissions`：提交流水
- `POST /api/submissions`：提交一批动作，整体核对；冲突返回 `409` + 冲突诊断，校验错误 `422`
- `GET /api/proofs`：校样留档列表 + 按最新入库状态现算的下载资格
- `POST /api/proofs`：`{"outbound_layer_id": 1, "evidence_ids": [2, 5]}` 生成校样；
  所选依据冲突返回 `409`（`error.code=unsatisfiable` + `conflict`），依据不可选/规则不满足返回 `422`
- `GET /api/proofs/{id}/csv`：下载校样 CSV；所引依据已更正/撤回后返回 `409`（`proof_ineligible`）

### 动作类型（提交体 `{"actions": [...]}`，同批可用负 id 引用本批新建对象，从 `-1` 起）

| action | 关键字段 |
|---|---|
| `add_layer` | `code`, `name?` |
| `add_dating` | `layer_id`, `lower_year`, `upper_year`, `note?` |
| `add_superposition` | `earlier_layer_id`, `later_layer_id`, `note?` |
| `correct_dating` | `evidence_id`, `lower_year`, `upper_year`, `note?` |
| `correct_superposition` | `evidence_id`, `earlier_layer_id`, `later_layer_id`, `note?` |
| `withdraw_evidence` | `evidence_id` |
| `add_registration` | `layer_id`, `lower_year`, `upper_year`, `note?` |
| `replace_registration` | `registration_id`, `lower_year`, `upper_year`, `note?` |
| `withdraw_registration` | `registration_id` |
| `withdraw_with` | `evidence_id`, `registration_ids[]`，并可带更正字段或 `withdraw_evidence:true` |

`withdraw_with` 用于“更正测年/叠压记录时一并撤回失效登记”：更正与撤回在同一事务内
整体核对；若不撤回登记会导致无解，单独的更正提交将被驳回（测试 [H5]/[7] 覆盖正反两例）。

## 求解方法

- 测年候选区间 → 变量域（区间并集，保留空档）；登记 → 域交集；严格早于 → `x_j ≥ x_i + 1`
- 先做有向环检测（含自环），再在区间并集域上跑有向弧 AC-3 传播；
  对该单调约束语言，弧一致后的非空域即全部可行取值（随机穷举对照验证）
- 无解诊断给出首个被清空的层位、关联依据 id；环冲突额外给出环路径

## 目录

```
app/solver.py    求解器（标准库）
app/db.py        SQLite 表结构与读写
app/services.py  动作校验、事务、快照、CSP 装配、冲突诊断
app/main.py      FastAPI 路由
app/static/      单页前端（原生 JS）
tests/           四级测试 + 标准库 HTTP 垫片
```
