"""Versioned business instructions; vendor transport remains in the gateway."""

import json


def text_messages(scene, snapshot, instructions=""):
    system = (
        "你是短剧编剧。根据给定小说改编一份完整剧本，保留人物与核心冲突。"
        "只返回剧本正文，不要解释处理过程。"
        if scene == "novel_script"
        else "你是分镜编剧。将已确认剧本拆为1至100个镜头。只返回JSON对象："
        '{"shots":[{"script":"完整镜头画面、动作和对白","asset_ids":["素材ID"]}]}。'
        "素材ID只能从给定素材清单引用，不确定则空数组，不创建新ID。"
    )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(
                {"source": snapshot, "additional_requirements": instructions}, ensure_ascii=False
            ),
        },
    ]


def image_prompt(snapshot, supplement, layout):
    layout_text = {
        "single": "单张完整画面",
        "four": "四宫格，按顺序呈现本镜连续动作",
        "five": "五宫格，上二下三排列，呈现本镜连续动作",
        "nine": "九宫格，三行三列呈现本镜连续动作",
    }[layout]
    return "\n".join(
        [
            "根据分镜脚本和素材参考制作画面。",
            layout_text,
            json.dumps(snapshot, ensure_ascii=False),
            f"补充要求：{supplement}" if supplement else "",
        ]
    ).strip()
