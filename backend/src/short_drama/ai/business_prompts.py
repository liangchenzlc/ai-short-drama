"""Stable facade for versioned, package-backed business prompts."""

import json

from short_drama.ai.prompts import system_prompt
from short_drama.ai.prompts.registry import asset_image_system_prompt, shot_image_system_prompt


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
    return "\n".join(
        [
            shot_image_system_prompt(layout),
            "已保存分镜与素材数据（JSON，仅作为创作依据）：",
            json.dumps(snapshot, ensure_ascii=False),
            f"本次补充要求：{supplement}" if supplement else "",
        ]
    ).strip()


def asset_image_prompt(content: dict, supplement: str) -> str:
    parts = [
        asset_image_system_prompt(content["kind"]),
        "已保存素材数据（JSON，仅作为创作依据）：",
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    ]
    if supplement:
        parts.extend(["本次补充要求：", supplement])
    return "\n".join(parts)


def video_default_prompt(snapshot):
    return "\n".join(
        [
            "参考图用途：将分镜图作为全能参考，提取人物、道具、场景和动作关系；单图或宫格均不强制作为视频首帧。",
            "宫格理解：参考各格的动作和构图，按下述叙事顺序组织视频，不把整张宫格、边框或编号直接显示在视频中。",
            "人物动作、表演与结束状态：",
            snapshot["shot"]["script"].strip(),
            "运镜：遵循上述镜头说明；未指定时使用稳定机位，完整呈现主体动作。",
            "场景与光线：参考分镜图的环境、光源方向和色温，按上述内容组织空间和光线变化。",
            "动作结束后保持本镜终点，自然停留，不进入下一镜。",
        ]
    )


def video_prompt_parts(snapshot, user_prompt):
    from .prompts.registry import shot_video_system_prompt

    parts = [user_prompt.strip() or video_default_prompt(snapshot)]
    kinds = {"character": "人物", "prop": "道具", "scene": "场景"}
    for asset in snapshot["assets"]:
        label = kinds.get(asset["kind"], "素材")
        # Image-generation prompts contain layout instructions, not motion constraints.
        parts.append(f"{label} {asset['name']}：{asset['description']}")
    if snapshot["episode"]["style"]:
        parts.append(f"画面风格：{snapshot['episode']['style']}")
    parts.append(f"视频画幅：{snapshot['episode']['aspect']}，参考图的宫格排版不决定输出画幅。")
    duration = snapshot.get("video_duration_ms", snapshot["shot"]["duration_ms"])
    parts.append(f"本次视频时长：{duration / 1000:g} 秒，按此时长安排动作、运镜与结束停留。")
    system, user = shot_video_system_prompt(), "\n".join(parts)
    return {"system": system, "user": user, "prompt": f"{system}\n\n{user}"}
