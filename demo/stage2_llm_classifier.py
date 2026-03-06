"""
Step 3: Stage 2 LLM 分类（Mock 模式）
输入：demo/output/stage2_input.json
输出：demo/output/stage2_results.json

Mock 模式说明：
  真实实现会调用国产大模型 API（DeepSeek-V3 / Qwen-Turbo），使用 Pydantic 约束输出格式。
  当前 Demo 使用预设的 mock 映射来演示管线结构和输出格式，
  与真实 LLM 输出的 JSON 结构完全一致。
"""

import json
from pathlib import Path
from pydantic import BaseModel, Field

OUTPUT_DIR = Path(__file__).parent / "output"


# -----------------------------------------------------------------------
# Pydantic Schema（与真实 LLM 结构化输出完全一致）
# -----------------------------------------------------------------------
class PointClassification(BaseModel):
    point_id: str
    name: str
    device_hint: str | None
    brick_class: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str]
    source: str = "llm_mock"


class BatchResult(BaseModel):
    classifications: list[PointClassification]
    unresolved: list[str] = Field(default_factory=list)


# -----------------------------------------------------------------------
# Mock 映射表（模拟 LLM 对语义模糊点位的分类判断）
# 真实场景中，这些点位会被发送到国产大模型 API（DeepSeek / Qwen），并附带：
#   - Brick 类层次子集（按设备类型裁剪）
#   - 已确认的同类映射作为 few-shot 示例
#   - Pydantic Schema 约束输出格式
# -----------------------------------------------------------------------
MOCK_CLASSIFICATIONS: dict[str, dict] = {
    "瞬时制冷量": {
        "brick_class": "brick:Thermal_Power_Sensor",
        "confidence": 0.88,
        "evidence": [
            "LLM: '瞬时制冷量'语义为当前制冷功率，对应 Brick 热功率传感器",
            "LLM: 制冷系统上下文支持此分类",
        ],
        "device_hint": "chiller_plant",
    },
    "总负荷": {
        "brick_class": "brick:Thermal_Power_Sensor",
        "confidence": 0.82,
        "evidence": [
            "LLM: '总负荷'在制冷站上下文中指总冷负荷",
            "LLM: 与冷水机房功率点位同属系统级测量",
        ],
        "device_hint": "chiller_plant",
    },
    "制冷瞬时冷量": {
        "brick_class": "brick:Thermal_Power_Sensor",
        "confidence": 0.90,
        "evidence": [
            "LLM: '制冷瞬时冷量'直接描述制冷量，等价于 Cooling Thermal Power",
        ],
        "device_hint": "chiller_plant",
    },
    "冷负荷": {
        "brick_class": "brick:Thermal_Power_Sensor",
        "confidence": 0.92,
        "evidence": [
            "LLM: '冷负荷'为制冷站典型用语，即制冷热功率需求",
        ],
        "device_hint": "chiller_plant",
    },
    "昨天同时刻的冷负荷": {
        "brick_class": "brick:Thermal_Power_Sensor",
        "confidence": 0.75,
        "evidence": [
            "LLM: 与'冷负荷'同类但含时间偏移语义，归类为同一 Brick 类",
            "LLM: 低置信度，建议人工确认是否应归入派生点位类别",
        ],
        "device_hint": "chiller_plant",
    },
    "上一时刻的冷负荷": {
        "brick_class": "brick:Thermal_Power_Sensor",
        "confidence": 0.75,
        "evidence": [
            "LLM: 与'昨天同时刻的冷负荷'同类，含时间偏移",
            "LLM: 低置信度，建议人工确认",
        ],
        "device_hint": "chiller_plant",
    },
}


def run_stage2_mock(unmatched_points: list[dict]) -> tuple[list[dict], list[str]]:
    classifications = []
    unresolved = []

    for point in unmatched_points:
        name = point["name"]
        if name in MOCK_CLASSIFICATIONS:
            mock = MOCK_CLASSIFICATIONS[name]
            cls = PointClassification(
                point_id=point["point_id"],
                name=name,
                device_hint=mock.get("device_hint") or point.get("device_hint"),
                brick_class=mock["brick_class"],
                confidence=mock["confidence"],
                evidence=mock["evidence"],
            )
            classifications.append(cls.model_dump())
        else:
            unresolved.append(point["point_id"])

    return classifications, unresolved


def main():
    with open(OUTPUT_DIR / "stage2_input.json", encoding="utf-8") as f:
        unmatched = json.load(f)

    if not unmatched:
        print("[03] Stage 2：无需处理（Stage 1 已全部覆盖）")
        with open(OUTPUT_DIR / "stage2_results.json", "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        return [], []

    classifications, unresolved = run_stage2_mock(unmatched)

    result = BatchResult(classifications=[PointClassification(**c) for c in classifications],
                         unresolved=unresolved)

    with open(OUTPUT_DIR / "stage2_results.json", "w", encoding="utf-8") as f:
        json.dump(result.model_dump(), f, ensure_ascii=False, indent=2)

    print(f"[03] Stage 2 LLM 分类（Mock）：{len(classifications)}/{len(unmatched)} 点位分类成功")
    if unresolved:
        print(f"     {len(unresolved)} 个点位无法解析（待人工审核）：{unresolved}")

    return classifications, unresolved


if __name__ == "__main__":
    main()
