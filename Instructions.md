# Brick Schema 构建操作指南

## 目录

1. [整体架构与流程](#1-整体架构与流程)
2. [环境准备](#2-环境准备)
3. [输入数据规范](#3-输入数据规范)
4. [Stage 1：规则匹配引擎](#4-stage-1规则匹配引擎)
5. [Stage 2：LLM 分类](#5-stage-2llm-分类)
6. [Stage 3：图构建、推理与校验](#6-stage-3图构建owl-推理与-shacl-校验)
7. [SHACL 校验规则编写](#7-shacl-校验规则编写)
8. [SiteProfile 导出](#8-siteprofile-导出)
9. [输出产物规范](#9-输出产物规范)
10. [质量门与验收流程](#10-质量门与验收流程)
11. [版本管理](#11-版本管理)

---

## 1. 整体架构与流程

### 1.1 数据流

```
原始 BAS 数据（CSV/Excel）
        │
        ▼
  [Step 0] 点位提取
  解析列名 → point_list.json
        │
        ▼
  [Stage 1] 规则匹配引擎（目标覆盖 50%+ 点位）
  正则关键词 + 单位交叉验证 → 高置信度结果
        │
    ┌───┴───┐
    │       │
  已匹配   未匹配/低置信度
    │       │
    │       ▼
    │ [Stage 2] LLM 分类
    │ Pydantic 约束输出 + 本体接地
    │       │
    └───┬───┘
        │
        ▼
  [Stage 3] 图构建 + OWL 推理 + SHACL 校验
  写入 RDF 三元组 → reasonable 推理 → 校验
        │
        ▼
  [Step 4] SiteProfile 导出
  SPARQL 查询 → scope_graph / resource_domains / point_mapping
        │
        ▼
  产物：model.ttl / model.jsonld / siteprofile.json / shacl_report.json
```

### 1.2 核心原则

- **Stage 1 宁可漏分，不可错分。** 只在完全确定时输出高置信度（≥0.95）结果，歧义点位一律交给 Stage 2。
- **LLM 必须本体接地。** Stage 2 的 LLM 只能从预定义的 Brick 类列表中选择，不能自由生成类名，否则幻觉率极高。
- **SiteProfile 接口不变。** 下游裁决层、护栏层消费的 JSON 结构与现有方案完全一致，Brick 升级对下游透明。

---

## 2. 环境准备

### 2.1 安装依赖

**标准安装（推荐用于生产）：**

```bash
pip install "brickschema[reasonable]"  # Brick Python 库 + Rust 推理引擎（38x 加速）
pip install rdflib>=7.0
pip install brickllm>=1.3              # LLM 生成 Brick RDF 的专用库
pip install buildingmotif>=1.0         # NREL 模板化校验工具
pip install pydantic>=2.0
pip install openai                     # DeepSeek / Qwen 均兼容 OpenAI SDK
pip install pandas>=2.0 openpyxl>=3.1
```

> **Demo 与生产的差异：**
>
> - Demo 使用 `owlrl`（纯 Python）替代 `reasonable`（Rust），原因是 Demo 运行时遇到兼容性问题。生产环境应使用 `brickschema[reasonable]`，速度快 38 倍。
> - Demo 的 Stage 2 是 Mock（硬编码映射），生产环境必须接入真实大模型 API（推荐 DeepSeek 或 Qwen）。
> - Demo 未使用 `brickllm` 和 `buildingmotif`，生产环境推荐集成。

### 2.2 验证安装

```python
from brickschema import Graph
g = Graph(load_brick=True)
g.expand(profile="owlrl", backend="reasonable")  # 验证 reasonable 可用
print("reasonable 推理引擎可用")
```

### 2.3 项目目录结构

```
project_root/
  data/                       # 原始 BAS 数据（CSV/Excel），不纳入 Git
  brick/
    model.ttl                 # Brick RDF 图（Git 版本管理，主要产物）
    model.jsonld              # JSON-LD 导出（由 CI 自动生成，禁止手动修改）
    custom_shapes.ttl         # 项目专属 SHACL 校验规则（Git 版本管理）
  mapping/
    point_mapping.json        # 点位映射表
    coverage_report.json      # 覆盖率报告
  siteprofile/
    siteprofile.json          # SiteProfile（由 CI 从 TTL 自动导出）
  audit/
    shacl_validation_report.json  # SHACL 校验报告
    mapping_audit_report.md       # 人工抽检报告
```

---

## 3. 输入数据规范

### 3.1 点位清单（point_list.json）

每个点位需包含以下字段：

```json
{
  "point_id": "ACC.冷冻供水温度",    // 唯一标识，格式：{系统}.{点位名}
  "name": "冷冻供水温度",            // 点位显示名称（用于规则匹配）
  "source_file": "某项目数据.xlsx",  // 来源文件
  "sheet": "ACC_raw",               // Sheet 名
  "system": "ACC",                  // 所属系统
  "device_hint": "chiller_plant"    // 推断的设备归属（可为 null）
}
```

### 3.2 设备台账（equipment_ledger）

设备台账定义项目中的物理设备，需包含：

```python
# 示例结构（可从 CMMS/BMS 导出为 JSON，或在代码中硬编码）
EQUIPMENT_LEDGER = {
    "building": [
        {"id": "building_A", "brick_class": "Building", "label": "某项目建筑"},
    ],
    "chiller_plant": [
        {"id": "chiller_plant_01", "brick_class": "Chiller_Plant",
         "label": "冷水机房", "isPartOf": "building_A"},
    ],
    "chillers": [
        {"id": "chiller_01", "brick_class": "Chiller",
         "label": "冷水主机1号", "isPartOf": "chiller_plant_01"},
        # ... 其余主机
    ],
    "pumps": [
        {"id": "pump_01", "brick_class": "Chilled_Water_Pump",
         "label": "冷冻泵1号", "isPartOf": "chiller_plant_01"},
        # ... 其余泵
    ],
}
```

**`brick_class` 必须使用 Brick 1.4 的精确类名**，不加 `brick:` 前缀（代码构建时加）。常用类名：

| 设备 | Brick 类名 |
|------|-----------|
| 建筑 | `Building` |
| 冷水机房 | `Chiller_Plant` |
| 冷水主机 | `Chiller` |
| 冷冻泵 | `Chilled_Water_Pump` |
| 冷却塔 | `Cooling_Tower` |
| 空调机组 | `AHU` |
| 风机盘管 | `FCU` |
| 楼层 | `Floor` |
| 区域 | `Zone` |
| 房间 | `Room` |

### 3.3 命名空间规范

所有项目实体使用统一命名空间：

```
实体 URI：  urn:proj/{entity_id}
元数据：    urn:aihvac:meta:{field_name}
```

示例：`urn:proj/chiller_01`、`urn:proj/ACC_冷冻供水温度`

---

## 4. Stage 1：规则匹配引擎

### 4.1 规则表结构

规则表是核心配置，直接决定覆盖率。每条规则的格式：

```python
{
    "patterns": [r"正则表达式1", r"正则表达式2"],  # 任一匹配即生效
    "brick_class": "brick:Brick类名",             # 目标 Brick 类（含 brick: 前缀）
    "unit_hint": "℃",                            # 期望单位（用于交叉验证）
    "confidence": 0.98,                           # 基础置信度
}
```

### 4.2 规则编写原则

**关键词选取：**

- 优先匹配**中文语义关键词**（国内 BAS 数据中文命名覆盖率高）
- 同时提供**英文/缩写**备选（应对中英混合命名）
- 使用 `|` 分隔同义词：`r"送风温度|SAT|supply.?air.?temp"`

**置信度设定：**

| 场景 | 置信度 |
|------|--------|
| 名称完全明确（如"冷冻供水温度"） | 0.97-0.98 |
| 高度明确但存在少量歧义 | 0.93-0.96 |
| 名称明确但需要上下文确认 | 0.90-0.92 |
| 低于 0.90 的匹配 | 不设规则，交给 Stage 2 |

**单位交叉验证：**

- 当点位有单位字段时，与 `unit_hint` 对比
- 单位匹配：置信度保持
- 单位不匹配：置信度 × 0.7（自动降级至可能触发 Stage 2）

### 4.3 常用 Brick 类对照

点位类型覆盖参考（按冷水机房场景）：

| 点位名称模式 | Brick 类 | 单位 |
|-------------|---------|------|
| 冷冻供水温度、供水温度 | `brick:Chilled_Water_Supply_Temperature_Sensor` | ℃ |
| 冷冻回水温度、回水温度 | `brick:Chilled_Water_Return_Temperature_Sensor` | ℃ |
| 室外温度、outdoor temp | `brick:Outside_Air_Temperature_Sensor` | ℃ |
| 湿球温度、wet bulb | `brick:Air_Wet_Bulb_Temperature_Sensor` | ℃ |
| 冷冻水流量 | `brick:Chilled_Water_Supply_Flow_Sensor` | m³/h |
| 主机功率、总功率 | `brick:Electric_Power_Sensor` | kW |
| 相对湿度、RH | `brick:Relative_Humidity_Sensor` | % |
| 泵运行频率 | `brick:Frequency_Sensor` | Hz |
| 送风温度、SAT | `brick:Supply_Air_Temperature_Sensor` | ℃ |
| 回风温度、RAT | `brick:Return_Air_Temperature_Sensor` | ℃ |
| 室温、Zone Temp | `brick:Zone_Air_Temperature_Sensor` | ℃ |
| 温度设定点、SP | `brick:Zone_Air_Temperature_Setpoint` | ℃ |
| 阀位、valve pos | `brick:Valve_Position_Command` | % |
| 风机启停状态 | `brick:Fan_On_Off_Status` | — |
| CO2、二氧化碳 | `brick:CO2_Level_Sensor` | ppm |

> **注意：** Brick 1.4 中无专用 COP/EER 类。对于制冷 COP、瞬时 EER 等效率指标，使用通用类 `brick:Sensor` 并通过 `brick:label` 保留语义。

### 4.4 匹配流程代码模板

```python
import re
from dataclasses import dataclass

CONFIDENCE_THRESHOLD = 0.90  # 低于此值交给 Stage 2

@dataclass
class RuleMatchResult:
    point_id: str
    name: str
    device_hint: str | None
    brick_class: str
    confidence: float
    evidence: list[str]
    source: str = "rule"

KEYWORD_RULES: list[dict] = [
    # 按项目定制填写
    {
        "patterns": [r"冷冻供水温度|供水温度"],
        "brick_class": "brick:Chilled_Water_Supply_Temperature_Sensor",
        "unit_hint": "℃",
        "confidence": 0.98,
    },
    # ... 更多规则
]

def rule_match(point: dict) -> RuleMatchResult | None:
    name = point["name"]
    unit = point.get("unit", "")
    text = f"{name} {point.get('description', '')}".lower()

    for rule in KEYWORD_RULES:
        for pattern in rule["patterns"]:
            if re.search(pattern, text, re.IGNORECASE):
                conf = rule["confidence"]
                evidence = [f"名称匹配规则: '{pattern}'"]
                # 单位交叉验证
                if rule.get("unit_hint") and unit:
                    if unit.strip() == rule["unit_hint"]:
                        evidence.append(f"单位匹配: {unit}")
                    else:
                        conf *= 0.7
                        evidence.append(f"单位不匹配: 期望 {rule['unit_hint']}, 实际 {unit}")
                return RuleMatchResult(
                    point_id=point["point_id"],
                    name=name,
                    device_hint=point.get("device_hint"),
                    brick_class=rule["brick_class"],
                    confidence=conf,
                    evidence=evidence,
                )
    return None

def run_stage1(points: list[dict]) -> tuple[list[dict], list[dict]]:
    matched, unmatched = [], []
    for point in points:
        result = rule_match(point)
        if result and result.confidence >= CONFIDENCE_THRESHOLD:
            matched.append(result.__dict__)
        else:
            unmatched.append(point)
    return matched, unmatched
```

---

## 5. Stage 2：LLM 分类

### 5.1 核心设计要求

Stage 2 只处理 Stage 1 未覆盖或低置信度的点位。**必须遵守以下约束：**

1. **本体接地（Ontology Grounding）**：LLM 只能从 `ALLOWED_CLASSES` 列表中选择 Brick 类，不能自由生成。这是防止幻觉的关键，幻觉率可从 ~63% 降至 ~1.7%。
2. **分设备类型批处理**：按设备类型分组（AHU 一批、FCU 一批、Chiller 一批），每批只注入对应设备类型的 Brick 类子集，减少 token 和误分类。
3. **Few-shot 来自 Stage 1**：将 Stage 1 已确认的同类映射作为示例注入 Prompt，提供项目内命名上下文。

### 5.2 Pydantic 输出 Schema

输出格式必须通过 Pydantic 约束，确保 LLM 返回结构化 JSON：

```python
from pydantic import BaseModel, Field

class PointClassification(BaseModel):
    point_id: str
    name: str
    device_hint: str | None
    brick_class: str = Field(
        description="必须是 ALLOWED_CLASSES 列表中的一个 Brick 类 URI，格式如 brick:Supply_Air_Temperature_Sensor"
    )
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(
        description="分类依据，至少包含一条说明"
    )
    unit_inferred: str | None = Field(
        description="推断的标准单位，如 ℃、%、ppm，无单位时为 null"
    )
    source: str = "llm"

class BatchResult(BaseModel):
    classifications: list[PointClassification]
    unresolved: list[str] = Field(
        default_factory=list,
        description="无法分类的点位 ID 列表，需要人工审核"
    )
```

### 5.3 Prompt 模板

```python
CLASSIFICATION_PROMPT = """你是一个建筑自动化系统（BAS）的点位语义标注专家，熟悉 Brick Schema 本体标准。

## 任务
将以下 BAS 点位分类为 Brick Schema 标准类。你只能从下方的 ALLOWED_CLASSES 列表中选择类名。

## 约束
- 每个点位必须分类为 ALLOWED_CLASSES 中的一个类，或标记为 unresolved
- confidence 必须反映你的真实确信度：
  - 0.95+：名称和上下文完全明确
  - 0.80-0.95：高度可能但有少量歧义
  - 0.60-0.80：可能正确但需要人工确认（仍输出，由下游决策）
  - <0.60：猜测，请标记为 unresolved
- evidence 必须说明分类依据（点位名语义、单位、设备上下文等）
- 如果无法确定，将 point_id 加入 unresolved 列表

## ALLOWED_CLASSES（{equipment_type} 相关）
{allowed_classes}

## 已确认的同类映射（参考同项目的命名模式）
{few_shot_examples}

## 待分类点位
{points_to_classify}

请严格按照 JSON Schema 输出，不要输出其他内容。"""
```

### 5.4 调用大模型 API（DeepSeek / Qwen，结构化输出）

```python
from openai import OpenAI
from pydantic import BaseModel
import json, os

# --- 选择供应商（二选一）---

# 方案 A：DeepSeek
client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
)
SCREENING_MODEL = "deepseek-chat"       # DeepSeek-V3，非思考模式
REVIEW_MODEL    = "deepseek-reasoner"   # DeepSeek-R1

# 方案 B：Qwen（阿里云灵积）
# client = OpenAI(
#     api_key=os.environ["DASHSCOPE_API_KEY"],
#     base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
# )
# SCREENING_MODEL = "qwen-turbo"
# REVIEW_MODEL    = "qwen-max"

def classify_batch(
    equipment_type: str,
    points: list[dict],
    confirmed_mappings: list[dict],
    allowed_classes: list[str],
    use_review_model: bool = False,
) -> BatchResult:
    prompt = CLASSIFICATION_PROMPT.format(
        equipment_type=equipment_type,
        allowed_classes="\n".join(f"- {cls}" for cls in allowed_classes),
        few_shot_examples=format_few_shots(confirmed_mappings[:10]),
        points_to_classify=json.dumps(points, ensure_ascii=False, indent=2),
    )
    model = REVIEW_MODEL if use_review_model else SCREENING_MODEL
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},  # 强制 JSON 输出
        max_tokens=4096,
    )
    raw_json = response.choices[0].message.content
    result = BatchResult.model_validate_json(raw_json)
    return result
```

**批处理策略：**

| 参数 | 推荐值 |
|------|--------|
| 批大小 | 50-80 点/次 |
| 初筛模型 | DeepSeek-V3（`deepseek-chat`）或 Qwen-Turbo（`qwen-turbo`） |
| 低置信度复核（confidence < 0.80） | DeepSeek-R1（`deepseek-reasoner`）或 Qwen-Max（`qwen-max`） |
| Prompt 缓存 | 启用（DeepSeek/Qwen 均支持 context caching） |
| API 模式 | 批处理异步提交（成本再降 50%） |
| 重试策略 | unresolved 点位最多重试 3 次，每次微调 Prompt |

### 5.5 按设备类型划分 ALLOWED_CLASSES

不同设备类型对应不同的 Brick 类子集，示例：

```python
DEVICE_CLASS_SUBSETS = {
    "Chiller": [
        "brick:Chilled_Water_Supply_Temperature_Sensor",
        "brick:Chilled_Water_Return_Temperature_Sensor",
        "brick:Chilled_Water_Supply_Flow_Sensor",
        "brick:Electric_Power_Sensor",
        "brick:Thermal_Power_Sensor",
        "brick:Frequency_Sensor",
        "brick:Sensor",  # 兜底
    ],
    "AHU": [
        "brick:Supply_Air_Temperature_Sensor",
        "brick:Return_Air_Temperature_Sensor",
        "brick:Mixed_Air_Temperature_Sensor",
        "brick:Fan_On_Off_Status",
        "brick:Valve_Position_Command",
        "brick:Supply_Air_Flow_Sensor",
        "brick:CO2_Level_Sensor",
    ],
    "FCU": [
        "brick:Supply_Air_Temperature_Sensor",
        "brick:Zone_Air_Temperature_Sensor",
        "brick:Zone_Air_Temperature_Setpoint",
        "brick:Valve_Position_Command",
        "brick:Fan_On_Off_Status",
    ],
    # ... 其他设备类型
}
```

---

## 6. Stage 3：图构建、OWL 推理与 SHACL 校验

### 6.1 构建 RDF 图

```python
from brickschema import Graph as BrickGraph
from rdflib import URIRef, RDF, Literal, Namespace

BRICK = Namespace("https://brickschema.org/schema/Brick#")
AIHVAC = Namespace("urn:aihvac:meta:")
PROJ = Namespace("urn:proj/")

def build_graph(stage1_results, stage2_results, equipment_ledger) -> BrickGraph:
    g = BrickGraph(load_brick=True)  # 自动加载 Brick 1.4 本体

    # Step 1：写入设备拓扑
    for equip in flatten_ledger(equipment_ledger):
        uri = PROJ[equip["id"]]
        g.add((uri, RDF.type, BRICK[equip["brick_class"]]))
        g.add((uri, BRICK.label, Literal(equip["label"])))
        if equip.get("isPartOf"):
            g.add((uri, BRICK.isPartOf, PROJ[equip["isPartOf"]]))

    # Step 2：写入点位
    for r in stage1_results + stage2_results:
        brick_local = r["brick_class"].replace("brick:", "")
        point_uri = PROJ[sanitize_id(r["point_id"])]
        g.add((point_uri, RDF.type, BRICK[brick_local]))
        g.add((point_uri, BRICK.label, Literal(r["name"])))
        # 自定义元数据（不影响 Brick 语义）
        g.add((point_uri, AIHVAC["source"], Literal(r.get("source", "unknown"))))
        g.add((point_uri, AIHVAC["confidence"], Literal(r.get("confidence", 0.0))))
        # 关联设备
        if r.get("device_hint"):
            g.add((point_uri, BRICK.isPointOf, PROJ[r["device_hint"]]))

    return g

def sanitize_id(raw_id: str) -> str:
    """将点位 ID 中的非法 URI 字符替换为下划线"""
    return raw_id.replace(".", "_").replace(" ", "_").replace("/", "_")
```

### 6.2 OWL 推理

**生产环境（推荐）：**

```python
# reasonable：Rust 引擎，速度是 OWLRL 的 38 倍
g.expand(profile="owlrl", backend="reasonable")
```

**推理效果：**
- `isPartOf` 传递闭包自动补全：`pump_01 isPartOf chiller_plant`、`chiller_plant isPartOf building_A` → 自动推导出 `pump_01 isPartOf building_A`
- Brick 类层次继承：`Chilled_Water_Supply_Temperature_Sensor` → `Temperature_Sensor` → `Sensor` → `Point`（全部自动推导）
- `feeds`/`serves` 的传递性

推理后三元组数量通常增长 30-50%，属于正常现象。

### 6.3 SHACL 校验

```python
import pyshacl
from rdflib import Graph

def run_shacl_validation(project_g: BrickGraph, shapes_path: str) -> dict:
    shapes_g = Graph()
    shapes_g.parse(shapes_path, format="turtle")

    # 只提取项目数据三元组，避免 Brick 本体 shapes 的噪音
    data_g = Graph()
    for s, p, o in project_g:
        if str(s).startswith("urn:proj/") or str(s).startswith("urn:aihvac:"):
            data_g.add((s, p, o))

    conforms, report_graph, report_text = pyshacl.validate(
        data_g,
        shacl_graph=shapes_g,
        inference="none",    # 推理已在上一步完成
        abort_on_first=False,
    )
    return parse_shacl_report(conforms, report_text)
```

> **为什么不用 `graph.validate()`？**
> `brickschema` 的内置 `validate()` 会同时运行 Brick 内置 shapes，产生大量针对本体本身的噪音违规。用 `pyshacl` 独立调用，可以精确控制只校验我们的项目数据。

### 6.4 导出产物

```python
# Turtle 格式（Git 版本管理的主要产物）
g.serialize(destination="brick/model.ttl", format="turtle")

# JSON-LD 格式（供 REST API 使用，自动生成，禁止手动修改）
g.serialize(destination="brick/model.jsonld", format="json-ld")
```

---

## 7. SHACL 校验规则编写

### 7.1 文件结构

```turtle
@prefix sh:    <http://www.w3.org/ns/shacl#> .
@prefix brick: <https://brickschema.org/schema/Brick#> .
@prefix xsd:   <http://www.w3.org/2001/XMLSchema#> .
@prefix proj:  <urn:proj:shapes#> .

# 每个规则写一个 sh:NodeShape
proj:SomeRuleShape a sh:NodeShape ;
    sh:targetClass brick:SomeClass ;
    sh:property [
        sh:path brick:someProperty ;
        sh:minCount 1 ;
        sh:message "违规时显示的错误消息" ;
    ] .
```

### 7.2 常用规则模板

**规则类型1：设备必须归属于某个父级（替代"无孤立节点"检查）**

```turtle
proj:ChillerPartOfShape a sh:NodeShape ;
    sh:targetClass brick:Chiller ;
    sh:property [
        sh:path brick:isPartOf ;
        sh:minCount 1 ;
        sh:message "冷水主机必须通过 isPartOf 归属于某个冷水机房" ;
    ] .
```

**规则类型2：设备必须有特定类型的传感器**

```turtle
proj:ChillerHasPowerShape a sh:NodeShape ;
    sh:targetClass brick:Chiller ;
    sh:property [
        sh:path [ sh:inversePath brick:isPointOf ] ;  # 反向路径：找到 isPointOf 指向该设备的点位
        sh:qualifiedValueShape [ sh:class brick:Electric_Power_Sensor ] ;
        sh:qualifiedMinCount 1 ;
        sh:message "冷水主机缺少电力功率传感器" ;
    ] .
```

**规则类型3：闭环房间必须有温控点位**

```turtle
proj:ClosedLoopRoomShape a sh:NodeShape ;
    sh:targetClass brick:Room ;
    sh:property [
        sh:path [ sh:inversePath brick:isPointOf ] ;
        sh:qualifiedValueShape [ sh:class brick:Temperature_Sensor ] ;
        sh:qualifiedMinCount 1 ;
        sh:message "闭环控制房间缺少温度传感器" ;
    ] ;
    sh:property [
        sh:path [ sh:inversePath brick:isPointOf ] ;
        sh:qualifiedValueShape [ sh:class brick:Temperature_Setpoint ] ;
        sh:qualifiedMinCount 1 ;
        sh:message "闭环控制房间缺少温度设定点" ;
    ] .
```

**规则类型4：设备必须 feeds 某个目标**

```turtle
proj:FCUFeedsShape a sh:NodeShape ;
    sh:targetClass brick:FCU ;
    sh:property [
        sh:path brick:feeds ;
        sh:minCount 1 ;
        sh:message "FCU 未关联任何服务房间" ;
    ] .
```

### 7.3 校验规则设计原则

- **只校验项目数据层面的约束**，不重复 Brick 本体已有的语义约束
- **错误消息要明确**，指出缺少什么、应该怎样修正
- **Severity 分级**：
  - `sh:Violation`（默认）：必须修复，阻止合入主分支
  - `sh:Warning`：建议修复，不阻止合入
- **版本化**：`custom_shapes.ttl` 变更需要团队审批，影响所有项目

---

## 8. SiteProfile 导出

### 8.1 scope_graph 导出

scope_graph 描述空间拓扑和设备归属关系，通过 SPARQL 从 RDF 图中查询：

```python
SCOPE_GRAPH_QUERY = """
PREFIX brick: <https://brickschema.org/schema/Brick#>
SELECT DISTINCT ?space ?type ?parent WHERE {
    ?space a ?type .
    VALUES ?type {
        brick:Building
        brick:Chiller_Plant
        brick:Floor
        brick:Zone
        brick:Room
    }
    FILTER(STRSTARTS(STR(?space), "urn:proj/"))
    OPTIONAL {
        ?space brick:isPartOf ?parent .
        FILTER(STRSTARTS(STR(?parent), "urn:proj/"))
    }
}
"""

def export_scope_graph(g) -> dict:
    nodes = []
    for row in g.query(SCOPE_GRAPH_QUERY):
        class_name = str(row.type).split("#")[-1]
        scope_type = BRICK_CLASS_TO_SCOPE_TYPE.get(class_name, class_name.lower())
        nodes.append({
            "id": str(row.space).split("/")[-1],
            "type": scope_type,
            "level": SCOPE_LEVELS.get(scope_type, 99),
            "parent": str(row.parent).split("/")[-1] if row.parent else None,
        })
    # ... 同样查询 edges
    return {"nodes": nodes, "edges": edges}
```

OWL 推理已自动补全 `isPartOf` 传递闭包，SPARQL 查询会自动覆盖全部层级，无需手动遍历。

### 8.2 resource_domains 导出

resource_domains 描述每台设备及其关联点位：

```python
RESOURCE_DOMAINS_QUERY = """
PREFIX brick: <https://brickschema.org/schema/Brick#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT DISTINCT ?equip ?point ?point_type WHERE {
    ?point brick:isPointOf ?equip .
    ?point a ?point_type .
    FILTER(STRSTARTS(STR(?equip), "urn:proj/"))
    FILTER(STRSTARTS(STR(?point), "urn:proj/"))
    # 只取最具体的子类（过滤 OWL 推理产生的父类型）
    FILTER NOT EXISTS {
        ?point a ?subtype .
        ?subtype rdfs:subClassOf ?point_type .
        FILTER(?subtype != ?point_type)
        FILTER(STRSTARTS(STR(?subtype), "https://brickschema.org/schema/Brick#"))
    }
}
"""
```

> **"只取最具体子类"过滤器**是关键。OWL 推理后，一个 `Chilled_Water_Supply_Temperature_Sensor` 会同时被推导为 `Temperature_Sensor`、`Sensor`、`Point` 等父类。不加过滤器会导致每个点位出现多条重复记录。

### 8.3 最终 SiteProfile 结构

```json
{
    "schema_version": "1.0",
    "project_id": "proj_001",
    "project_name": "某项目",
    "scope_graph": {
        "nodes": [
            {"id": "building_A", "type": "building", "level": 0, "parent": null},
            {"id": "chiller_plant_01", "type": "chiller_plant", "level": 1, "parent": "building_A"}
        ],
        "edges": [
            {"from": "chiller_01", "to": "chiller_plant_01", "relation": "isPartOf"}
        ]
    },
    "resource_domains": {
        "chiller_01": {
            "domain_type": "cooling_resource",
            "source_equipment": "chiller_01",
            "brick_class": "Chiller",
            "points": [
                {"point_id": "ACC_冷冻供水温度", "brick_class": "Chilled_Water_Supply_Temperature_Sensor"}
            ]
        }
    }
}
```

此结构与现有方案完全一致，下游无需任何修改。

---

## 9. 输出产物规范

### 9.1 产物清单

| 文件 | 格式 | 生成方式 | Git 管理 |
|------|------|---------|---------|
| `brick/model.ttl` | Turtle | Stage 3 直接输出 | **是（主产物）** |
| `brick/model.jsonld` | JSON-LD | 由 CI 从 TTL 自动生成 | 否（自动生成） |
| `brick/custom_shapes.ttl` | Turtle | 人工编写 | **是** |
| `mapping/point_mapping.json` | JSON | Stage 4 SPARQL 导出 | 是 |
| `mapping/coverage_report.json` | JSON | 统计 Stage 1/2 结果 | 是 |
| `siteprofile/siteprofile.json` | JSON | Stage 4 SPARQL 导出 | 是 |
| `audit/shacl_validation_report.json` | JSON | Stage 3 校验输出 | 是 |

### 9.2 JSON-LD 格式说明

JSON-LD 是 W3C 标准的 RDF 序列化格式，语法上就是 JSON，任何 JSON 解析器可直接读取。示例：

```json
{
    "@context": {
        "brick": "https://brickschema.org/schema/Brick#",
        "aihvac": "urn:aihvac:meta:",
        "isPartOf": "brick:isPartOf",
        "isPointOf": "brick:isPointOf"
    },
    "@graph": [
        {
            "@id": "urn:proj/chiller_01",
            "@type": "brick:Chiller",
            "isPartOf": {"@id": "urn:proj/chiller_plant_01"},
            "aihvac:source": "ledger"
        },
        {
            "@id": "urn:proj/ACC_冷冻供水温度",
            "@type": "brick:Chilled_Water_Supply_Temperature_Sensor",
            "isPointOf": {"@id": "urn:proj/chiller_01"},
            "aihvac:source": "rule",
            "aihvac:confidence": 0.98
        }
    ]
}
```

`aihvac:source` 和 `aihvac:confidence` 是我们的自定义元数据命名空间，不影响 Brick 语义，但可以追溯每个三元组的来源。

---

## 10. 质量门与验收流程

### 10.1 四层质量门

| 层次 | 检查方式 | 覆盖 | 人工参与 |
|------|---------|------|---------|
| L1 SHACL 自动校验 | `pyshacl.validate()` | 100% 三元组 | 无 |
| L2 BuildingMOTIF 模板校验 | 模板实例化检查 | 100% 设备 | 无 |
| L3 业务规则校验 | 覆盖率、置信度分布、单位一致性 | 100% 映射 | 无 |
| L4 人工抽检 | 抽查 ≥5% 点位、≥20 个房间/设备 | 抽样 | **必须** |

L1-L3 全部通过后，再进入 L4 人工抽检。L4 只需关注"语义是否正确"，结构完整性问题已被自动拦截。

### 10.2 L3 业务规则检查项

在导出 SiteProfile 之前，验证以下指标：

```python
def check_coverage_report(stage1, stage2, all_points):
    total = len(all_points)
    s1_count = len(stage1)
    s2_count = len(stage2)
    covered = s1_count + s2_count

    # 覆盖率
    assert covered / total >= 0.95, f"覆盖率不足 95%: {covered}/{total}"

    # 置信度分布
    low_conf = [r for r in stage2 if r["confidence"] < 0.70]
    assert len(low_conf) == 0, f"{len(low_conf)} 个点位置信度低于 0.70，需人工审核"

    # 未解析点位
    unresolved = [p for p in all_points if p["point_id"] not in covered_ids]
    if unresolved:
        print(f"警告：{len(unresolved)} 个点位未解析，需人工处理")
```

### 10.3 CI/CD 集成

在 GitHub Actions 或 GitLab CI 中添加以下检查：

```yaml
# .github/workflows/brick-validate.yml
- name: SHACL 校验
  run: |
    python -c "
    import pyshacl
    from rdflib import Graph
    data_g = Graph(); data_g.parse('brick/model.ttl')
    shapes_g = Graph(); shapes_g.parse('brick/custom_shapes.ttl')
    conforms, _, text = pyshacl.validate(data_g, shacl_graph=shapes_g)
    if not conforms:
        print(text)
        exit(1)
    "
- name: 重新生成 JSON-LD
  run: python scripts/export_jsonld.py  # 从 TTL 自动生成
```

**规则：** TTL 文件必须通过 SHACL 校验后才能合入主分支。

---

## 11. 版本管理

### 11.1 版本号规则

沿用现有方案的语义化版本：

- **主版本（Major）**：本体结构重大变更（如引入新的设备体系）
- **次版本（Minor）**：新增设备类型或 SHACL rules
- **补丁（Patch）**：修正单个点位的 Brick 类分配

在 TTL 文件中记录版本元数据：

```turtle
@prefix aihvac: <urn:aihvac:meta:> .
@prefix proj: <urn:proj/> .

proj:model
    aihvac:version "1.2.0" ;
    aihvac:project_id "proj_001" ;
    aihvac:created "2026-03-06" ;
    aihvac:description "冷水机房 Brick 语义图" .
```

### 11.2 变更规则

| 操作 | 要求 |
|------|------|
| 修改 `model.ttl` | 需通过 SHACL 校验 CI 检查 |
| 修改 `custom_shapes.ttl` | **需要团队审批**，影响所有项目 |
| 修改 `model.jsonld` | **禁止手动修改**，由 CI 从 TTL 自动生成 |
| 修改 `siteprofile.json` | **禁止手动修改**，由 CI 从 TTL 自动导出 |

---

Demo 可以运行（`python demo/run_demo.py`）来验证完整流程的输入输出格式，但不代表生产实现标准。
