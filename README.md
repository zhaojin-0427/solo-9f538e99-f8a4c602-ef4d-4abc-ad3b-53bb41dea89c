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
| package-builder | 8001 | `POST /build`，文档 `/docs` | 输入新旧区号及各自覆盖街块，输出 JSON 交接包 |
| converter | 8002 | `POST /convert`，文档 `/docs` | 输入交接包与本次有数的旧区人数，输出新区人数 |

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

## 目录结构

```
app/
  rationals.py      # 精确有理线性代数（RREF、零空间、线性方程组）
  expressions.py    # 分数与算式格式化
  models.py         # Pydantic 请求/响应模型与校验
  engine.py         # 交接包生成与换算的纯领域逻辑
  builder_app.py    # 实例 1：/build
  converter_app.py  # 实例 2：/convert
tests/              # 14 个端到端测试（TestClient）
Dockerfile
docker-compose.yml
```
