"""
Step 1: 从 Excel 文件提取点位清单
输入：data/ 目录下的三个 Excel 文件
输出：demo/output/point_list.json
"""

import re
import json
import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# 数据源定义：(文件名, sheet名或None, 所属系统)
SOURCES = [
    ("某项目-456月历史数据.xlsx", "ACC_raw", "ACC"),
    ("某项目-456月历史数据.xlsx", "WCC_raw", "WCC"),
    ("某项目-分钟级数据.xlsx",    None,       "综合"),
]

# 设备归属推断规则（从点位名提取设备ID）
DEVICE_PATTERNS = [
    (r"主机(\d+)", "chiller_{n}"),
    (r"冷冻泵(\d+)", "pump_{n}"),
]


def infer_device(point_name: str) -> str | None:
    for pattern, template in DEVICE_PATTERNS:
        m = re.search(pattern, point_name)
        if m:
            return template.format(n=m.group(1).zfill(2))
    # 机房级点位
    if re.search(r"冷水机房|ACC总|制冷站", point_name):
        return "chiller_plant"
    if re.search(r"风机机房", point_name):
        return "fan_room"
    return None


def parse_points() -> list[dict]:
    seen_names: set[str] = set()
    points: list[dict] = []

    for filename, sheet, system in SOURCES:
        filepath = DATA_DIR / filename
        df = pd.read_excel(filepath, sheet_name=sheet if sheet else 0, nrows=0)  # 只读列名

        for col in df.columns:
            col_str = str(col).strip()
            # 跳过时间列和无名列
            if col_str in ("时间",) or col_str.startswith("Unnamed"):
                continue
            # 跨文件去重（以点位名为key）
            if col_str in seen_names:
                continue
            seen_names.add(col_str)

            points.append({
                "point_id": f"{system}.{col_str}",
                "name": col_str,
                "source_file": filename,
                "sheet": sheet or "Sheet1",
                "system": system,
                "device_hint": infer_device(col_str),
            })

    return points


def main():
    points = parse_points()
    out_path = OUTPUT_DIR / "point_list.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(points, f, ensure_ascii=False, indent=2)

    print(f"[01] 点位清单提取完成：共 {len(points)} 个唯一点位 -> {out_path}")
    return points


if __name__ == "__main__":
    main()
