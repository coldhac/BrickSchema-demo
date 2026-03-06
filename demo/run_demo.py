"""
Demo 入口：一键运行 Brick 语义层升级三阶段管线

使用方式：
    cd BrickSchema
    python demo/run_demo.py
"""

import sys
import os
import argparse
import warnings
import time
from pathlib import Path

# 确保终端输出 UTF-8（解决 Windows 中文乱码）
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# 屏蔽 brickschema 的 sqlalchemy 警告
warnings.filterwarnings("ignore", message="sqlalchemy not installed")

sys.path.insert(0, str(Path(__file__).parent))

SEPARATOR = "=" * 65


def run(reasoner: str = "owlrl"):
    print(SEPARATOR)
    print("  Brick 语义层升级 Demo")
    print("  三阶段混合管线：规则匹配 → LLM分类 → RDF图构建+校验")
    print(SEPARATOR)

    total_start = time.time()

    # ---------------------------------------------------------------
    # Step 1: 提取点位清单
    # ---------------------------------------------------------------
    print("\n--- Step 1: 从 Excel 提取点位清单 ---")
    import parse_points as s1
    points = s1.main()

    # ---------------------------------------------------------------
    # Step 2: Stage 1 规则匹配
    # ---------------------------------------------------------------
    print("\n--- Step 2: Stage 1 规则匹配引擎 ---")
    import stage1_rule_engine as s2
    matched, unmatched = s2.main()

    # ---------------------------------------------------------------
    # Step 3: Stage 2 LLM 分类（Mock）
    # ---------------------------------------------------------------
    print("\n--- Step 3: Stage 2 LLM 分类（Mock 模式） ---")
    import stage2_llm_classifier as s3
    classifications, unresolved = s3.main()

    # ---------------------------------------------------------------
    # Step 4: Stage 3 图构建、OWL推理、SHACL校验
    # ---------------------------------------------------------------
    print("\n--- Step 4: Stage 3 图构建 + OWL推理 + SHACL校验 ---")
    import stage3_graph_builder as s4
    g, shacl_report = s4.main(reasoner=reasoner)

    # ---------------------------------------------------------------
    # Step 5: 导出 SiteProfile
    # ---------------------------------------------------------------
    print("\n--- Step 5: 导出 SiteProfile ---")
    import siteprofile_exporter as s5
    siteprofile = s5.main()

    # ---------------------------------------------------------------
    # 汇总
    # ---------------------------------------------------------------
    elapsed = time.time() - total_start
    total_points = len(points)
    matched_count = len(matched)
    llm_count = len(classifications)
    unresolved_count = len(unresolved)

    print(f"\n{SEPARATOR}")
    print("  管线运行摘要")
    print(SEPARATOR)
    print(f"  点位总数         : {total_points}")
    print(f"  Stage 1 规则命中 : {matched_count}  ({matched_count/total_points*100:.1f}%)")
    print(f"  Stage 2 LLM 分类 : {llm_count}  (Mock 模式)")
    print(f"  未解析点位       : {unresolved_count}  (待人工审核)")
    shacl_status = "PASS" if shacl_report["conforms"] else f"FAIL ({shacl_report['violation_count']} 条违规)"
    print(f"  SHACL 校验       : {shacl_status}")
    print(f"  scope_graph 节点 : {len(siteprofile['scope_graph']['nodes'])}")
    print(f"  resource_domains : {len(siteprofile['resource_domains'])} 个")
    print(f"  总耗时           : {elapsed:.1f}s")
    print(SEPARATOR)
    print(f"\n  产物目录：{Path(__file__).parent / 'output'}")
    print("  - point_list.json      点位清单")
    print("  - stage1_results.json  规则匹配结果")
    print("  - stage2_results.json  LLM分类结果（Mock）")
    print("  - model.ttl            Brick RDF 图（Turtle）")
    print("  - model.jsonld         JSON-LD 格式（REST API 用）")
    print("  - shacl_report.json    SHACL 校验报告")
    print("  - siteprofile.json     SiteProfile（下游消费接口）")
    print("  - point_mapping.json   点位映射表")
    print(SEPARATOR)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Brick 语义层升级 Demo")
    parser.add_argument(
        "--reasoner",
        choices=["owlrl", "reasonable"],
        default="owlrl",
        help="OWL 推理引擎（默认: owlrl，可选: reasonable，需安装 brickschema[reasonable]）",
    )
    args = parser.parse_args()
    run(reasoner=args.reasoner)
