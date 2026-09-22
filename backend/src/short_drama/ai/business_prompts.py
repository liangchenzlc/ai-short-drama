"""Stable facade for versioned, package-backed business prompts."""

import json

from short_drama.ai.prompts import system_prompt


def text_messages(scene, snapshot, instructions=""):
    kinds = snapshot.get("extraction", {}).get("kinds", ())
    return [
        {"role": "system", "content": system_prompt(scene, kinds=kinds)},
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
