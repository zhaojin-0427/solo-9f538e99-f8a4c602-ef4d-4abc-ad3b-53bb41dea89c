# 统计区改划 · 新旧区人数精确换算服务

区划调整后，历年按旧区公布的人数需要换算到新区。本服务用两个独立的
FastAPI 实例完成这件事，全程使用有理数精确运算（`fractions.Fraction`），
不使用浮点数。

## 数学模型

设街块人数为未知量 x，0/1 关联矩阵：

- `A`（街块 × 旧区）：旧区人数 `p = A·x`
- `B`（街块 × 新区）：新区人数 `q = B·x`
- 同一街块可同时属于多个区（矩阵列允许重叠）

由此得到两类精确代数关系：

1. **旧区之间的等式**：`ker(A)` 中的每个向量 k 满足 `k·p = 0`；
2. **新区对旧区的表达式**：对每个新区列 bⱼ 解 `A·c = bⱼ`，
   有解则 `qⱼ = c·p`，系数为精确分数（如 `1/2`、`-3`）。

## 两个实例

| 实例 | 端口 | 接口 | 职责 |
|---|---|---|---|
| package-builder | 8001 | `POST /build`，`POST /analysis/build`，文档 `/docs` | 输入新旧区号及各自覆盖街块，输出 JSON 交接包或分析包 |
| converter | 8002 | `POST /convert`，`POST /analysis/convert`，文档 `/docs` | 输入交接包/分析包与本次有数人数，输出新区人数或三项差额 |

### 交接包内容（不含任何街块编号或逐块归属）

```json
{
  "schema_version": 1,
  "old_districts": ["O1", "O2"],
  "new_districts": ["N1"],
  "old_relations": [{"O1": 1, "O2": -1}],
  "new_district_relations": [
    {"code": "N1", "derivable": true, "coefficients": {"O1": "1/2", "O2": "1/2"}}
  ]
}
```

- `old_relations`：旧区人数必须满足的精确线性等式（加权和为 0），
  整数系数，已约分为本原向量；
- `new_district_relations`：每个新区一个条目，`derivable=false` 表示
  覆盖关系无法用旧区精确表达，`coefficients` 为 `"p/q"` 形式的精确分数
  （整数写作 `"2"`）。

### 换算规则（converter）

- **可算范围只按本次提交的旧区数据判定**：仅当新区表达式能（借助交接包
  内的旧区关系）改写为只含本次有数旧区时才计算，其余新区列入 `missing`，
  并注明 `not_expressible_from_old_districts` 或
  `insufficient_known_old_districts`；
- 本次有数旧区所支撑的每条旧区等式都会实际校验，结果放在
  `checked_relations`；等式涉及无数据旧区时自动跳过；
- 旧区人数必须是非负整数（请求体 schema 强制）；
- **整次失败**（HTTP 400，只返回错误体）的情形：
  - 旧区人数违背覆盖关系推出的等式；
  - 任一新区换算结果为负数；
  - 任一新区换算结果不是整数（分数系数导致的半整数等）。

成功响应示例：

```json
{
  "available_old_districts": ["O1", "O2"],
  "checked_relations": [],
  "computed": [
    {"code": "N1", "status": "computed", "population": 8,
     "coefficients": {"O1": "1/2", "O2": "1/2"},
     "expression": "1/2*O1 + 1/2*O2", "missing_reason": null}
  ],
  "missing": [],
  "results": [ /* computed 与 missing 的完整列表，顺序与新区一致 */ ]
}
```

## 县界重划影响分析包（v2）

年鉴需要把县界重划造成的人口增减单列。分析包在 v1 之上增加一组入口，
原 `/build`、`/convert` 与 v1 包（`schema_version: 1`）保持不变。

### 分析包入口（builder：`POST /analysis/build`）

输入原有新旧区街块清单，外加改划前后各一组区号：

- `before_districts`：**旧区**区号组，其街块取并集构成基期旧县界；
- `after_districts`：**新区**区号组，其街块取并集构成报告期新县界。

两组内部都**按街块去重**：同一街块被组内多个区覆盖只计一次，因此县界
人数是街块人数的线性泛函，而不是各区人数的简单相加。组内重复区号也
只计一次。区号组可写作 `["O1","O2"]`、`[{"code":"O1"},...]` 或
`{"districts": [...]}`。

返回的包（`schema_version: 2`）可离线复用，只含区号与精确有理系数，
**不含任何街块编号或逐块归属**：

```json
{
  "schema_version": 2,
  "kind": "boundary_analysis",
  "old_districts": ["O1", "O2"],
  "new_districts": ["N1", "N2"],
  "old_relations": [{"O1": 1, "O2": -1}],
  "new_relations": [],
  "old_boundary":            {"districts": ["O1"], "derivable": true, "coefficients": {"O1": "1"}},
  "new_boundary_baseline":   {"districts": ["N1"], "derivable": true, "coefficients": {"O1": "1"}},
  "new_boundary_report":     {"districts": ["N1"], "derivable": true, "coefficients": {"N1": "1"}},
  "boundary_effect":         {"derivable": true, "coefficients": {"O2": "1"}}
}
```

- `old_boundary` / `new_boundary_baseline`：基期旧县界、基期新县界人数，
  都以**基期旧区人数**为变量（`A c = u`）；
- `new_boundary_report`：报告期新县界人数，以**报告期新区人数**为变量
  （`B d = u`）；
- `boundary_effect`：直接存 `u_new − u_old` 的解，即使两个县界各自都
  算不出，只要未报区号的系数相消，边界差额仍可精确推出。

### 换算入口（converter：`POST /analysis/convert`）

凭包接收两组已报人数（均为非负整数，缺省区号视为无资料）：

- `old_populations`：基期已报的**旧区**人数；
- `new_populations`：报告期已报的**新区**人数。

也接受合并的 `populations`（按包内区号自动归入两期）。

当次给出三项差额与分期分数算式：

| 项目 | 定义 | 变量来源 |
|---|---|---|
| `total_change`（总变动） | 报告期新界 − 基期旧界 | 新区人数 − 旧区人数 |
| `boundary_effect`（边界影响） | 基期新界 − 基期旧界 | 全部为旧区人数 |
| `same_coverage_change`（同口径变化） | 报告期新界 − 基期新界 | 新区人数、基期新界 |

响应要点：

```json
{
  "total_change":          {"status": "computed", "difference": 3,
                            "expression": "N1 - O1 - O2",
                            "formula": "(N1) - (O1 + O2)", "...": "..."},
  "boundary_effect":       {"status": "computed", "difference": -2, "...": "..."},
  "same_coverage_change":  {"status": "missing", "difference": null,
                            "missing_reason": "insufficient_known_new_districts"},
  "differences": {"total_change": 3, "boundary_effect": -2,
                  "same_coverage_change": null},
  "formulas": {"total_change": {"expression": "...", "formula": "..."}, "...": "..."},
  "missing": [{"code": "same_coverage_change", "missing_reason": "..."}],
  "baseline": {"old_boundary": {...}, "new_boundary": {...}},
  "report": {"new_boundary": {...}},
  "checked_relations": [ /* 两期可检验的覆盖等式 */ ],
  "same_scope_change": "...", "like_for_like_change": "..."
}
```

规则：

- **三项各自独立判定**：任一项可由本次数据精确推出就给出，其余标为缺资料；
- 两个县界的基期人数分别算不出时，`boundary_effect` 仍可凭包内直接
  差额关系给出；
- 差额可正可负；
- **整次报错**（HTTP 400，只返回错误体）的情形：
  - 任一期**可检验**的覆盖等式出现矛盾（涉及未报区号的等式自动跳过）；
  - 任何可算差额不是整数；
- 边界分量本身是分数不算错误：只要相减后的三项差额为整数即可；
- 缺资料原因：`not_expressible_from_old_districts`、
  `not_expressible_from_new_districts`、`insufficient_known_old_districts`、
  `insufficient_known_new_districts`。

## Docker Compose 启动

```bash
docker compose up --build
```

- 交接包服务（含 Swagger 交互文档）：<http://localhost:8001/docs>
- 换算服务（含 Swagger 交互文档）：<http://localhost:8002/docs>
- 健康检查：`GET /health`

## 本地运行与测试

```bash
pip install -r requirements.txt
pip install pytest httpx
uvicorn app.builder_app:app --port 8001
uvicorn app.converter_app:app --port 8002
pytest -q
```

## 典型调用流程

1. 在 `http://localhost:8001/docs` 的 `POST /build` 提交新旧区及街块覆盖，
   保存返回的交接包 JSON；
2. 在 `http://localhost:8002/docs` 的 `POST /convert` 提交该交接包与
   当期已有旧区人数（非负整数），当次得到可精确推出的新区人数、
   带分数系数的算式，以及缺资料新区列表。

县界重划影响分析：

1. 在 `http://localhost:8001/docs` 的 `POST /analysis/build` 提交新旧区
   街块清单、基期旧界区号组与报告期新界区号组，保存分析包 JSON；
2. 在 `http://localhost:8002/docs` 的 `POST /analysis/convert` 提交该分析包、
   基期旧区人数与报告期新区人数（均为非负整数），当次得到总变动、
   边界影响、同口径变化三项差额及算式。

## 目录结构

```
app/
  rationals.py       # 精确有理线性代数（RREF、零空间、线性方程组）
  expressions.py     # 分数与算式格式化
  models.py          # Pydantic 请求/响应模型与校验（v1）
  engine.py          # 交接包生成与换算的纯领域逻辑（v1）
  analysis.py        # 县界重划分析包与分析逻辑（v2）
  analysis_models.py # 分析包请求/响应模型（v2）
  builder_app.py     # 实例 1：/build、/analysis/build
  converter_app.py   # 实例 2：/convert、/analysis/convert
tests/
  test_service.py    # v1：14 个端到端测试（TestClient）
  test_analysis.py    # v2：县界重划分析端到端测试
Dockerfile
docker-compose.yml
```
