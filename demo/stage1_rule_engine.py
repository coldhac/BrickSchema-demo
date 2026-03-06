"""
Step 2: Stage 1 规则匹配引擎
输入：demo/output/point_list.json
输出：demo/output/stage1_results.json（已匹配）
      demo/output/stage2_input.json（未匹配，交给Stage 2）
"""

import re
import json
from dataclasses import dataclass, asdict
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "output"

CONFIDENCE_THRESHOLD = 0.90  # 低于此值的匹配交给Stage 2


@dataclass
class RuleMatchResult:
    point_id: str
    name: str
    device_hint: str | None
    brick_class: str
    confidence: float
    evidence: list[str]
    source: str = "rule"


# -----------------------------------------------------------------------
# 规则表：patterns 为正则列表（任一匹配即生效），unit_hint 用于交叉验证置信度
# -----------------------------------------------------------------------
KEYWORD_RULES: list[dict] = [
    # --- 温度类 ---
    {
        "patterns": [r"冷冻供水温度|供水温度"],
        "brick_class": "brick:Chilled_Water_Supply_Temperature_Sensor",
        "unit_hint": "℃", "confidence": 0.98,
    },
    {
        "patterns": [r"冷冻回水温度|回水温度"],
        "brick_class": "brick:Chilled_Water_Return_Temperature_Sensor",
        "unit_hint": "℃", "confidence": 0.98,
    },
    {
        "patterns": [r"室外温度|outdoor.?temp"],
        "brick_class": "brick:Outside_Air_Temperature_Sensor",
        "unit_hint": "℃", "confidence": 0.97,
    },
    {
        "patterns": [r"湿球温度|wet.?bulb"],
        "brick_class": "brick:Air_Wet_Bulb_Temperature_Sensor",
        "unit_hint": "℃", "confidence": 0.98,
    },
    # --- 流量 ---
    {
        "patterns": [r"冷冻水流量|chilled.?water.?flow"],
        "brick_class": "brick:Chilled_Water_Flow_Sensor",
        "unit_hint": "m3/h", "confidence": 0.98,
    },
    # --- 功率/电力 ---
    {
        "patterns": [r"主机\d+功率"],
        "brick_class": "brick:Electric_Power_Sensor",
        "unit_hint": "kW", "confidence": 0.97,
    },
    {
        "patterns": [r"ACC总功率|冷水机房总功率|冷水机房调整后的功率"],
        "brick_class": "brick:Electric_Power_Sensor",
        "unit_hint": "kW", "confidence": 0.97,
    },
    {
        "patterns": [r"风机机房功率|风机机房调整后的功率"],
        "brick_class": "brick:Electric_Power_Sensor",
        "unit_hint": "kW", "confidence": 0.95,
    },
    # --- 效率 ---
    {
        "patterns": [r"制冷瞬时COP|瞬时COP"],
        "brick_class": "brick:Sensor",  # Brick 1.4 无专用 COP/EER 类，用 Sensor + label 保留语义
        "unit_hint": None, "confidence": 0.98,
    },
    {
        "patterns": [r"制冷瞬时EER|瞬时EER"],
        "brick_class": "brick:Sensor",  # Brick 1.4 无专用 COP/EER 类，用 Sensor + label 保留语义
        "unit_hint": None, "confidence": 0.98,
    },
    # --- 环境 ---
    {
        "patterns": [r"相对湿度|relative.?humidity"],
        "brick_class": "brick:Relative_Humidity_Sensor",
        "unit_hint": "%", "confidence": 0.97,
    },
    # --- 泵频率 ---
    {
        "patterns": [r"冷冻泵\d+运行频率|泵.{0,4}运行频率"],
        "brick_class": "brick:Frequency_Sensor",
        "unit_hint": "Hz", "confidence": 0.97,
    },
]


def rule_match(point: dict) -> RuleMatchResult | None:
    name = point["name"]
    for rule in KEYWORD_RULES:
        for pattern in rule["patterns"]:
            if re.search(pattern, name, re.IGNORECASE):
                evidence = [f"名称匹配规则: '{pattern}'"]
                conf = rule["confidence"]
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
    matched: list[dict] = []
    unmatched: list[dict] = []

    for point in points:
        result = rule_match(point)
        if result and result.confidence >= CONFIDENCE_THRESHOLD:
            matched.append(asdict(result))
        else:
            unmatched.append(point)

    return matched, unmatched


def main():
    with open(OUTPUT_DIR / "point_list.json", encoding="utf-8") as f:
        points = json.load(f)

    matched, unmatched = run_stage1(points)

    with open(OUTPUT_DIR / "stage1_results.json", "w", encoding="utf-8") as f:
        json.dump(matched, f, ensure_ascii=False, indent=2)
    with open(OUTPUT_DIR / "stage2_input.json", "w", encoding="utf-8") as f:
        json.dump(unmatched, f, ensure_ascii=False, indent=2)

    total = len(points)
    rate = len(matched) / total * 100 if total else 0
    print(f"[02] Stage 1 规则匹配：{len(matched)}/{total} 点位命中（覆盖率 {rate:.1f}%）")
    print(f"     {len(unmatched)} 个点位交给 Stage 2 LLM 分类")

    if unmatched:
        print("     未命中点位：", [p["name"] for p in unmatched])

    return matched, unmatched


if __name__ == "__main__":
    main()
