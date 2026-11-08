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


def video_default_prompt(snapshot):
    if snapshot.get("input_mode") == "text_to_video" or snapshot.get("has_shot_reference") is False:
        return "\n".join(
            [
                "人物动作、表演与结束状态：",
                snapshot["shot"]["script"].strip(),
                "运镜：遵循镜头说明；未指定时使用稳定机位，完整呈现主体动作。",
                "场景与光线：依据镜头描述和素材设定安排空间与光线。",
                "动作结束后保持本镜终点，自然停留，不进入下一镜。",
            ]
        )
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
    from short_drama.ai.prompts.registry import shot_video_system_prompt

    parts = [user_prompt.strip() or video_default_prompt(snapshot)]
    shot = snapshot["shot"]
    if user_prompt.strip() and shot.get("script"):
        parts.append(f"镜头内容：{shot['script']}")
    if shot.get("title"):
        parts.append(f"镜头名称：{shot['title']}")
    if shot.get("shot_size"):
        parts.append(f"景别：{shot['shot_size']}")
    details = snapshot["shot"].get("details") or {}
    for field, label in (
        ("camera_angle", "机位角度"),
        ("camera_movement", "运镜"),
        ("dialogue_note", "对白设想"),
        ("sound", "声音设想"),
    ):
        if details.get(field) and not (field == "dialogue_note" and snapshot.get("native_speech")):
            parts.append(f"{label}：{details[field]}")
    kinds = {"character": "人物", "prop": "道具", "scene": "场景"}
    for asset in snapshot["assets"]:
        label = kinds.get(asset["kind"], "素材")
        # Image-generation prompts contain layout instructions, not motion constraints.
        parts.append(f"{label} {asset['name']}：{asset['description']}")
        profile = {key: value for key, value in asset.get("profile", {}).items() if value}
        if profile:
            parts.append(f"{label}资料：{json.dumps(profile, ensure_ascii=False)}")
    aspect = (snapshot.get("output") or {}).get("aspect")
    if aspect:
        parts.append(
            f"视频画幅：{aspect}。"
            if snapshot.get("input_mode") == "text_to_video"
            or snapshot.get("has_shot_reference") is False
            else f"视频画幅：{aspect}，参考图的宫格排版不决定输出画幅。"
        )
    duration = snapshot.get("video_duration_ms", snapshot["shot"]["duration_ms"])
    parts.append(f"本次视频时长：{duration / 1000:g} 秒，按此时长安排动作、运镜与结束停留。")
    has_shot_reference = snapshot.get(
        "has_shot_reference", snapshot.get("input_mode") != "text_to_video"
    )
    system = shot_video_system_prompt(reference=has_shot_reference)
    if snapshot.get("input_mode") == "omni_reference" and not has_shot_reference:
        system += "\n\n使用提供的参考图片保持人物、场景和道具的视觉一致性。"
    user = "\n".join(parts)
    return {"system": system, "user": user, "prompt": f"{system}\n\n{user}"}
