"""
Step 4: Stage 3 图构建、OWL推理与 SHACL 校验
输入：demo/output/stage1_results.json
      demo/output/stage2_results.json
输出：demo/output/model.ttl        （Brick RDF 图，Turtle 格式）
      demo/output/model.jsonld     （JSON-LD 格式，供 REST API 使用）
      demo/output/shacl_report.json（校验报告）

推理引擎说明：
  技术方案推荐使用 reasonable（Rust-based，38x 加速），
  但该引擎仅支持 Linux/macOS。本 Demo 使用 owlrl 直接调用作为替代。
"""

import json
import warnings
import owlrl
import pyshacl
from pathlib import Path
from rdflib import Graph, URIRef, RDF, Literal, Namespace
from brickschema import Graph as BrickGraph

OUTPUT_DIR = Path(__file__).parent / "output"
SHAPES_DIR = Path(__file__).parent.parent / "shapes"

BRICK = Namespace("https://brickschema.org/schema/Brick#")
AIHVAC = Namespace("urn:aihvac:meta:")
PROJ = Namespace("urn:proj/")

# -----------------------------------------------------------------------
# 设备台账（本项目固定设备，实际项目从 CMMS/BMS 导出）
# -----------------------------------------------------------------------
EQUIPMENT_LEDGER = {
    "building": [
        {"id": "building_A", "brick_class": "Building", "label": "某项目建筑"},
    ],
    "chiller_plant": [
        {"id": "chiller_plant", "brick_class": "Chiller_Plant", "label": "冷水机房",
         "isPartOf": "building_A"},
    ],
    "chillers": [
        {"id": f"chiller_{i:02d}", "brick_class": "Chiller",
         "label": f"冷水主机{i}号", "isPartOf": "chiller_plant"}
        for i in range(1, 10)  # 主机 1-9
    ],
    "pumps": [
        {"id": f"pump_{i:02d}", "brick_class": "Chilled_Water_Pump",
         "label": f"冷冻泵{i}号", "isPartOf": "chiller_plant"}
        for i in range(1, 6)  # 冷冻泵 1-5
    ],
}

# Brick 类名修正：部分类在 Brick 1.4 中的确切名称
BRICK_CLASS_ALIASES = {
    "brick:Thermal_Power_Sensor": "Thermal_Power_Sensor",  # Brick 1.4 有此类
    "brick:Chilled_Water_Flow_Sensor": "Chilled_Water_Supply_Flow_Sensor",
}


def resolve_brick_class(raw_class: str, g: BrickGraph) -> str:
    """
    将 'brick:ClassName' 解析为合法的 Brick URI。
    如果类不在本体中，尝试已知别名，最后 fallback 到 brick:Sensor。
    """
    local_name = raw_class.replace("brick:", "")
    uri = BRICK[local_name]
    if (uri, RDF.type, None) in g or (uri, None, None) in g:
        return local_name

    # 尝试别名
    alias = BRICK_CLASS_ALIASES.get(raw_class)
    if alias:
        alias_local = alias.replace("brick:", "")
        if (BRICK[alias_local], None, None) in g:
            return alias_local

    # fallback
    print(f"     [警告] 未找到 Brick 类 '{raw_class}'，fallback 到 brick:Sensor")
    return "Sensor"


def build_graph(stage1_results: list[dict], stage2_results: list[dict]) -> BrickGraph:
    """构建 Brick RDF 图，写入设备拓扑和所有点位。"""
    g = BrickGraph(load_brick=True)

    # --- Step 1: 写入空间和设备实体 ---
    all_equipment = (
        EQUIPMENT_LEDGER["building"]
        + EQUIPMENT_LEDGER["chiller_plant"]
        + EQUIPMENT_LEDGER["chillers"]
        + EQUIPMENT_LEDGER["pumps"]
    )
    for equip in all_equipment:
        uri = PROJ[equip["id"]]
        g.add((uri, RDF.type, BRICK[equip["brick_class"]]))
        g.add((uri, BRICK.label, Literal(equip["label"])))
        if "isPartOf" in equip:
            g.add((uri, BRICK.isPartOf, PROJ[equip["isPartOf"]]))

    # --- Step 2: 写入点位 ---
    all_points = stage1_results[:]
    # stage2 可能是列表或包含 classifications 的 BatchResult 格式
    if stage2_results and isinstance(stage2_results, list):
        if stage2_results and "brick_class" in stage2_results[0]:
            all_points += stage2_results
        elif stage2_results and "classifications" in stage2_results[0]:
            all_points += stage2_results[0]["classifications"]

    for r in all_points:
        raw_class = r.get("brick_class", "brick:Sensor")
        brick_local = resolve_brick_class(raw_class, g)

        safe_id = r["point_id"].replace(".", "_").replace(" ", "_").replace("/", "_")
        point_uri = PROJ[safe_id]
        g.add((point_uri, RDF.type, BRICK[brick_local]))
        g.add((point_uri, BRICK.label, Literal(r["name"])))

        # 元数据（自定义命名空间，不影响 Brick 语义）
        g.add((point_uri, AIHVAC["source"], Literal(r.get("source", "unknown"))))
        g.add((point_uri, AIHVAC["confidence"], Literal(r.get("confidence", 0.0))))

        # 关联设备
        device_id = r.get("device_hint")
        if device_id:
            device_uri = PROJ[device_id]
            g.add((point_uri, BRICK.isPointOf, device_uri))

    print(f"[04] 写入 RDF 图：{len(all_points)} 个点位 + {len(all_equipment)} 个设备实体")
    print(f"     图中三元组总数（含 Brick 本体）：{len(g)}")
    return g


def run_owl_reasoning(g: BrickGraph, reasoner: str = "owlrl") -> int:
    """
    执行 OWL RL 推理。
    reasoner="owlrl"       使用 owlrl（纯 Python，默认）
    reasoner="reasonable"  使用 reasonable（Rust，约快 38x，需安装 brickschema[reasonable]）
    """
    before = len(g)
    if reasoner == "reasonable":
        try:
            g.expand(profile="owlrl", backend="reasonable")
        except Exception as e:
            raise RuntimeError(
                f"reasonable 推理引擎不可用，请先安装：pip install \"brickschema[reasonable]\"\n原始错误: {e}"
            )
        engine_label = "reasonable (Rust)"
    else:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(g)
        engine_label = "owlrl (Python)"
    after = len(g)
    inferred = after - before
    print(f"[04] OWL 推理（{engine_label}）：新增推断三元组 {inferred} 条（{before} -> {after}）")
    print(f"     示例：isPartOf 传递闭包已自动补全（泵/主机 -> 冷水机房 -> 建筑）")
    return inferred


def run_shacl_validation(project_g: Graph) -> dict:
    """
    使用 pyshacl 对项目数据执行 SHACL 校验。
    只验证项目数据（不混入 Brick 本体的内置 shapes）。
    """
    shapes_path = SHAPES_DIR / "custom_shapes.ttl"

    shapes_g = Graph()
    shapes_g.parse(str(shapes_path), format="turtle")

    # 仅包含项目三元组（URN 命名空间，排除 Brick 本体三元组）
    data_g = Graph()
    for s, p, o in project_g:
        if str(s).startswith("urn:proj/") or str(s).startswith("urn:aihvac:"):
            data_g.add((s, p, o))

    conforms, report_graph, report_text = pyshacl.validate(
        data_g,
        shacl_graph=shapes_g,
        inference="none",
        abort_on_first=False,
    )

    # 解析违规条目
    violations = []
    for line in report_text.splitlines():
        line = line.strip()
        if line.startswith("Message:"):
            violations.append(line.replace("Message:", "").strip())

    report = {
        "conforms": conforms,
        "violation_count": len(violations),
        "violations": violations,
    }

    status = "PASS" if conforms else f"FAIL（{len(violations)} 条违规）"
    print(f"[04] SHACL 校验：{status}")
    if violations:
        for v in violations:
            print(f"     - {v}")

    return report


def export_outputs(g: BrickGraph, shacl_report: dict):
    """导出 TTL、JSON-LD 和校验报告。"""
    # TTL
    ttl_path = OUTPUT_DIR / "model.ttl"
    g.serialize(destination=str(ttl_path), format="turtle")

    # JSON-LD
    jsonld_path = OUTPUT_DIR / "model.jsonld"
    g.serialize(destination=str(jsonld_path), format="json-ld")

    # SHACL 报告
    report_path = OUTPUT_DIR / "shacl_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(shacl_report, f, ensure_ascii=False, indent=2)

    print(f"[04] 产物导出：")
    print(f"     {ttl_path}")
    print(f"     {jsonld_path}")
    print(f"     {report_path}")


def main(reasoner: str = "owlrl"):
    # 加载前两个 Stage 的结果
    with open(OUTPUT_DIR / "stage1_results.json", encoding="utf-8") as f:
        stage1 = json.load(f)

    stage2_path = OUTPUT_DIR / "stage2_results.json"
    if stage2_path.exists():
        with open(stage2_path, encoding="utf-8") as f:
            raw = json.load(f)
        # BatchResult 格式：{"classifications": [...], "unresolved": [...]}
        stage2 = raw.get("classifications", raw) if isinstance(raw, dict) else raw
    else:
        stage2 = []

    # 构建图
    g = build_graph(stage1, stage2)

    # OWL 推理
    run_owl_reasoning(g, reasoner=reasoner)

    # SHACL 校验
    shacl_report = run_shacl_validation(g)

    # 导出
    export_outputs(g, shacl_report)

    return g, shacl_report


if __name__ == "__main__":
    main()
