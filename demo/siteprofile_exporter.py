"""
Step 5: 从 Brick RDF 图导出 SiteProfile
输入：demo/output/model.ttl
输出：demo/output/siteprofile.json

说明：
  现有方案中 build_scope_graph() / build_resource_domains() 是手写 Python 遍历。
  本模块用 SPARQL 查询替代，逻辑声明式，且 OWL 推理已补全传递关系。
  最终 SiteProfile 的 JSON 结构与现有方案完全一致（对下游透明）。
"""

import json
from pathlib import Path
from rdflib import Graph, Namespace

OUTPUT_DIR = Path(__file__).parent / "output"
BRICK = Namespace("https://brickschema.org/schema/Brick#")
PROJ = Namespace("urn:proj/")

# 空间类型到 scope_type 的映射
BRICK_CLASS_TO_SCOPE_TYPE = {
    "Building": "building",
    "Chiller_Plant": "chiller_plant",
    "Floor": "floor",
    "Zone": "zone",
    "Room": "room",
}

# 空间层级（数字越小越高）
SCOPE_LEVELS = {
    "building": 0,
    "chiller_plant": 1,
    "floor": 1,
    "zone": 2,
    "room": 3,
}

# 设备类型到资源域类型的映射
EQUIPMENT_TO_RESOURCE_TYPE = {
    "Chiller": "cooling_resource",
    "Chilled_Water_Pump": "cooling_distribution",
    "Chiller_Plant": "cooling_plant",
}


def extract_id(uri) -> str:
    """从 URIRef 提取本地 ID。"""
    s = str(uri)
    return s.split("/")[-1]


def brick_class_name(uri) -> str:
    """从 Brick 类 URI 提取类名。"""
    s = str(uri)
    return s.split("#")[-1]


def export_scope_graph(g: Graph) -> dict:
    """
    用 SPARQL 导出 scope_graph。
    替代原方案的手写 build_scope_graph()，
    利用 OWL 推理已补全的 isPartOf 传递闭包。
    """
    nodes_query = """
    PREFIX brick: <https://brickschema.org/schema/Brick#>
    PREFIX proj: <urn:proj/>
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

    edges_query = """
    PREFIX brick: <https://brickschema.org/schema/Brick#>
    SELECT DISTINCT ?equip ?equip_type ?target WHERE {
        ?equip a ?equip_type .
        VALUES ?equip_type { brick:Chiller brick:Chilled_Water_Pump }
        FILTER(STRSTARTS(STR(?equip), "urn:proj/"))
        ?equip brick:isPartOf ?target .
        FILTER(STRSTARTS(STR(?target), "urn:proj/"))
    }
    """

    nodes = []
    for row in g.query(nodes_query):
        class_name = brick_class_name(row.type)
        scope_type = BRICK_CLASS_TO_SCOPE_TYPE.get(class_name, class_name.lower())
        nodes.append({
            "id": extract_id(row.space),
            "type": scope_type,
            "level": SCOPE_LEVELS.get(scope_type, 99),
            "parent": extract_id(row.parent) if row.parent else None,
        })

    edges = []
    for row in g.query(edges_query):
        edges.append({
            "from": extract_id(row.equip),
            "to": extract_id(row.target),
            "relation": "isPartOf",
            "equip_type": brick_class_name(row.equip_type),
        })

    return {"nodes": nodes, "edges": edges}


def export_resource_domains(g: Graph) -> dict:
    """
    用 SPARQL 导出 resource_domains。
    替代原方案的手写 build_resource_domains()。
    """
    query = """
    PREFIX brick: <https://brickschema.org/schema/Brick#>
    SELECT DISTINCT ?equip ?equip_type WHERE {
        ?equip a ?equip_type .
        VALUES ?equip_type { brick:Chiller brick:Chilled_Water_Pump brick:Chiller_Plant }
        FILTER(STRSTARTS(STR(?equip), "urn:proj/"))
    }
    """

    # 查询每台设备关联的点位（只取最具体的 Brick 类，过滤 OWL 推理产生的父类型）
    points_query = """
    PREFIX brick: <https://brickschema.org/schema/Brick#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT DISTINCT ?equip ?point ?point_type WHERE {
        ?point brick:isPointOf ?equip .
        ?point a ?point_type .
        FILTER(STRSTARTS(STR(?equip), "urn:proj/"))
        FILTER(STRSTARTS(STR(?point), "urn:proj/"))
        FILTER(STRSTARTS(STR(?point_type), "https://brickschema.org/schema/Brick#"))
        FILTER NOT EXISTS {
            ?point a ?subtype .
            ?subtype rdfs:subClassOf ?point_type .
            FILTER(?subtype != ?point_type)
            FILTER(STRSTARTS(STR(?subtype), "https://brickschema.org/schema/Brick#"))
            FILTER(STRSTARTS(STR(?point), "urn:proj/"))
        }
    }
    """

    domains = {}
    for row in g.query(query):
        equip_id = extract_id(row.equip)
        class_name = brick_class_name(row.equip_type)
        domains[equip_id] = {
            "domain_type": EQUIPMENT_TO_RESOURCE_TYPE.get(class_name, "unknown_resource"),
            "source_equipment": equip_id,
            "brick_class": class_name,
            "points": [],
        }

    for row in g.query(points_query):
        equip_id = extract_id(row.equip)
        if equip_id in domains:
            domains[equip_id]["points"].append({
                "point_id": extract_id(row.point),
                "brick_class": brick_class_name(row.point_type),
            })

    return domains


def export_point_mapping(g: Graph) -> list[dict]:
    """
    导出点位映射表（point_mapping.json）。
    格式与现有方案完全一致。
    """
    # 只查 Point 类型（过滤设备/空间实体），且只取最具体的 Brick 类
    query = """
    PREFIX brick: <https://brickschema.org/schema/Brick#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX aihvac: <urn:aihvac:meta:>
    SELECT ?point ?point_type ?device ?source ?confidence WHERE {
        ?point a brick:Point .
        ?point a ?point_type .
        FILTER(STRSTARTS(STR(?point), "urn:proj/"))
        FILTER(STRSTARTS(STR(?point_type), "https://brickschema.org/schema/Brick#"))
        FILTER NOT EXISTS {
            ?point a ?subtype .
            ?subtype rdfs:subClassOf ?point_type .
            FILTER(?subtype != ?point_type)
            FILTER(STRSTARTS(STR(?subtype), "https://brickschema.org/schema/Brick#"))
        }
        OPTIONAL { ?point brick:isPointOf ?device }
        OPTIONAL { ?point aihvac:source ?source }
        OPTIONAL { ?point aihvac:confidence ?confidence }
    }
    """

    mappings = []
    seen = set()
    for row in g.query(query):
        point_id = extract_id(row.point)
        if point_id in seen:
            continue
        class_name = brick_class_name(row.point_type)
        seen.add(point_id)
        mappings.append({
            "point_id": point_id,
            "brick_class": f"brick:{class_name}",
            "device": extract_id(row.device) if row.device else None,
            "source": str(row.source) if row.source else None,
            "confidence": float(row.confidence) if row.confidence else None,
        })

    return mappings


def main():
    ttl_path = OUTPUT_DIR / "model.ttl"
    g = Graph()
    g.parse(str(ttl_path), format="turtle")

    scope_graph = export_scope_graph(g)
    resource_domains = export_resource_domains(g)
    point_mapping = export_point_mapping(g)

    siteprofile = {
        "schema_version": "1.0",
        "project_id": "demo_chiller_plant",
        "project_name": "某项目冷水机房",
        "scope_graph": scope_graph,
        "resource_domains": resource_domains,
    }

    # 写出文件
    sp_path = OUTPUT_DIR / "siteprofile.json"
    with open(sp_path, "w", encoding="utf-8") as f:
        json.dump(siteprofile, f, ensure_ascii=False, indent=2)

    pm_path = OUTPUT_DIR / "point_mapping.json"
    with open(pm_path, "w", encoding="utf-8") as f:
        json.dump(point_mapping, f, ensure_ascii=False, indent=2)

    print(f"[05] SiteProfile 导出：")
    print(f"     scope_graph  - {len(scope_graph['nodes'])} 个节点，{len(scope_graph['edges'])} 条边")
    print(f"     resource_domains - {len(resource_domains)} 个资源域")
    print(f"     point_mapping - {len(point_mapping)} 个点位映射")
    print(f"     -> {sp_path}")
    print(f"     -> {pm_path}")

    return siteprofile


if __name__ == "__main__":
    main()
