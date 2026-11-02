"""Offline configuration check; --live explicitly sends two public demo texts."""
import argparse
import json

from .adapters import AdapterError, planner_available, planner_config
from .parameters import apply_patch, default_params
from .planner import plan_initial, plan_revision


def main():
    parser = argparse.ArgumentParser(description="检查 LLM 配置；默认不联网、不发送任何描述")
    parser.add_argument("--live", action="store_true", help="明确调用最多两次配置的收费文字 API，仅发送内置演示文本与参数")
    args = parser.parse_args()
    try:
        config = planner_config()
    except AdapterError as error:
        print(str(error))
        return 2
    print(json.dumps({"planner": config, "available": planner_available(), "live": args.live}, ensure_ascii=False))
    if not args.live:
        print("仅检查配置，没有发送 API 请求。加 --live 可用内置演示文本测试最多两次调用。")
        return 0
    if not planner_available():
        print("缺少有效配置或密钥，未调用 API。")
        return 2
    initial = plan_initial("演示测试：持续低沉的嗡鸣，来源不确定。", kind="hum", use_model=True)
    print(json.dumps({"initial": initial}, ensure_ascii=False))
    if initial["mode"] != "model" or initial.get("clarification"):
        return 2
    params = apply_patch(default_params(initial["kind"]), initial["patch"])
    revision = plan_revision("更低沉", params, locked_fields=["gain"], use_model=True)
    print(json.dumps({"revision": revision}, ensure_ascii=False))
    return 0 if revision["mode"] == "model" and not revision.get("clarification") else 2


if __name__ == "__main__":
    raise SystemExit(main())
