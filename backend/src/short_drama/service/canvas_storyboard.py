"""冻结已保存分镜上下文，校验模型结构并分配可重放的镜头身份。"""

import json
import re
from copy import deepcopy

from pydantic import ValidationError

from short_drama.ai.types import GenerationError
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.canvas_storyboard import CanvasStoryboardResult

from .canvas_document import content_hash

CINEMATIC_COLUMNS = [
    "shotNumber",
    "durationSeconds",
    "videoMotionPrompt",
    "dialogue",
    "assets",
    "shotSize",
    "narrativeIntent",
    "viewerPOV",
    "performanceBlocking",
    "camera",
    "motion",
    "timeBeats",
    "lightingAndAtmosphere",
    "continuityOut",
    "negativePrompt",
]


def compact_asset_text(value: str | None, limit: int) -> str:
    if value is not None and not isinstance(value, str):
        raise WorkflowError(
            "canvas_storyboard_context_invalid", "已保存资产的标题、标签或提示词类型无效", 422
        )
    normalized = re.sub(r"\s+", " ", value or "").strip()
    return normalized[:limit] + "…" if len(normalized) > limit else normalized


def _character_names(characters: list[dict]) -> dict[str, dict]:
    names = {}
    for character in characters:
        previous = names.get(character["name"])
        if previous is not None and previous["nodeId"] != character["nodeId"]:
            raise ValueError("same character name refers to different identities")
        names[character["name"]] = character
    return names


def storyboard_characters(document: dict) -> list[dict]:
    """Build character context exclusively from saved nodes in this canvas."""
    characters = []
    for node in document["nodes"]:
        metadata = node.get("metadata") or {}
        if metadata.get("workflowKind") != "character":
            continue
        characters.append(
            {
                "nodeId": node["id"],
                "name": (metadata.get("characterName") or node["title"]).strip(),
                "definition": deepcopy(
                    metadata["characterDefinition"]
                    if metadata.get("characterDefinition") is not None
                    else {"prompt": metadata.get("characterPrompt") or ""}
                ),
            }
        )
    try:
        _character_names(characters)
    except ValueError:
        raise WorkflowError(
            "canvas_storyboard_character_ambiguous",
            "同名项目角色对应不同作品身份，请调整角色名称后重新生成分镜",
            422,
        ) from None
    return characters


def storyboard_snapshot(document: dict, node_id: str) -> dict:
    node = next((item for item in document["nodes"] if item["id"] == node_id), None)
    style = next(
        (
            item
            for item in document["nodes"]
            if (item.get("metadata") or {}).get("workflowKind") == "styleboard"
        ),
        None,
    )
    if node is None or node["type"] != "script" or style is None:
        raise WorkflowError("canvas_tool_source_changed", "请先保存分镜节点及项目画风", 409)
    metadata = style.get("metadata") or {}
    prompt = str(metadata.get("content") or metadata.get("prompt") or "").strip()
    preset = str(metadata.get("stylePresetId") or "").strip()
    if not prompt or not preset:
        raise WorkflowError("canvas_tool_source_changed", "请先设置并保存项目画风", 409)
    characters = storyboard_characters(document)
    project_style = {
        "presetId": preset,
        "title": re.sub(r"^(?:项目)?画风\s*[·：:]?\s*", "", style["title"]).strip()
        or style["title"],
        "prompt": prompt,
    }
    if metadata.get("styleProfileJson") is not None:
        project_style["profileJson"] = metadata["styleProfileJson"]
    assets = []
    for item in document["nodes"]:
        fields = item.get("metadata") or {}
        kind = (
            "character"
            if fields.get("workflowKind") == "character"
            else "image"
            if item["type"] == "drawing"
            else item["type"]
        )
        if (
            kind not in {"image", "video", "audio", "character"}
            or fields.get("workflowKind") in {"shot", "action_board", "final"}
            or kind != "character"
            and not (fields.get("content") or fields.get("storageKey") or fields.get("assetId"))
        ):
            continue
        asset = {
            "id": item["id"],
            "title": compact_asset_text(item["title"], 120) or "未命名资产",
            "type": kind,
            "tags": list(
                dict.fromkeys(
                    filter(
                        None, (compact_asset_text(tag, 64) for tag in fields.get("assetTags") or [])
                    )
                )
            )[:12],
            "prompt": compact_asset_text(
                fields.get("prompt")
                or fields.get("workflowDescription")
                or fields.get("characterPrompt"),
                600,
            ),
        }
        for target, source in (("category", "assetCategory"),):
            if fields.get(source) is not None:
                asset[target] = fields[source]
        assets.append(asset)
        if len(assets) == 60:
            break
    return {
        "nodeId": node_id,
        "rows": deepcopy((node.get("metadata") or {}).get("storyboard", {}).get("rows", [])),
        "projectStyle": project_style,
        "characters": characters,
        "canvasAssets": assets,
    }


def check_storyboard_context(request: dict, snapshot: dict) -> None:
    inputs = request["input"]
    if any(
        content_hash(inputs.get(key)) != content_hash(snapshot[key])
        for key in ("projectStyle", "characters", "canvasAssets")
    ):
        raise WorkflowError(
            "canvas_tool_source_changed", "分镜画风、角色或资产已变化，请重新读取", 409
        )


def storyboard_system_prompt(request: dict) -> str:
    inputs = request["input"]
    return "\n".join(
        [
            "将用户剧情拆为可编辑分镜。仅返回一个JSON对象，禁止代码块和解释文字。",
            '顶层仅允许 {"title": string, "rows": object[]}，镜号shotNumber从1连续递增。',
            "每镜durationSeconds为1–60整数，plotDescription为非空画面描述。",
            "characters必须为角色名字符串数组，仅用于绑定上下文已提供的项目角色名，"
            "不记录剧情出现的人物；"
            "上下文characters为空时，每镜characters必须为[]，剧情人物仍写入plotDescription。",
            "assetBindings仅可引用canvasAssets中的节点；上下文canvasAssets为空时必须为[]，"
            "禁止新增ID或媒体关联。",
            "assetBindings的每项必须为对象，禁止使用节点ID字符串；仅允许nodeId、role、priority。"
            "nodeId必须为canvasAssets中已有的id字符串，每镜不得重复绑定同一节点；"
            "role只能为character、environment、wardrobe、prop、weapon、style、motion、audio之一；"
            "priority为0–100整数，可省略，默认0。",
            'assetBindings对象示例：{"assetBindings":'
            '[{"nodeId":"canvasAssets中的节点id","role":"character","priority":0}]}',
            "忠实保留用户剧情的动作顺序及人物持有物，不擅自增加松手、丢弃、交接或相反动作；"
            "各镜描述、表演调度、图像和视频提示词须一致，动作细节只能补充而不能改写剧情。",
            "同一镜头及相邻镜头的物品、位置和状态须连续；涉及到达、停稳、开门、接近等先后动作时，"
            "保持合理顺序，不混入未提供且与当前动作冲突的离开、关门或反向动作。",
            "可选文本字段只能为字符串：dialogue,narrativeIntent,viewerPOV,performanceBlocking,shotSize,emotion,lightingAndAtmosphere,audioEffects,camera,motion,timeBeats,imageGenerationPrompt,videoMotionPrompt,continuityOut,negativePrompt；可省略或为空字符串。",
            "mustHave、optionalDetails、assetBindings只能为数组，可省略或为空数组；mustHave最多3项。",
            "每镜仅允许上述字段和shotNumber、durationSeconds、plotDescription、characters；禁止id、status、durationNumber、shotSizeNote及其他额外字段。",
            "上下文："
            + json.dumps(
                {
                    key: inputs[key]
                    for key in (
                        "projectStyle",
                        "characters",
                        "canvasAssets",
                        "requirements",
                        "shotCount",
                        "shotDurationSeconds",
                    )
                },
                ensure_ascii=False,
            ),
            "shotCount/shotDurationSeconds非0时严格使用该镜数/每镜时长，0表示自动。",
            (
                f"本次镜数：{inputs['shotCount'] or '自动'}；"
                f"每镜秒数：{inputs['shotDurationSeconds'] or '自动'}。"
            ),
            "返回前逐镜核对人物、持有物、位置和动作状态，并将本镜结束状态与下一镜起始状态对齐；"
            "同一时刻不能把同一对象同时写成静止与接近、已经到达与尚未到达，"
            "不得为衔接擅自松手、放下或转移用户指定的持有物。"
            "发现冲突时先修正全部相关字段，只输出修正后的JSON，不输出检查过程。",
            "返回前检查全部花括号和方括号闭合，必须保留顶层JSON对象末尾的右花括号。",
        ]
    )


def canvas_storyboard_result(text: str, request: dict, task_id: str) -> dict:
    def unique_object(items):
        values = {}
        for key, value in items:
            if key in values:
                raise ValueError("duplicate JSON field")
            values[key] = value
        return values

    try:
        result = CanvasStoryboardResult.model_validate(
            json.loads(text, object_pairs_hook=unique_object)
        )
        inputs = request["input"]
        if inputs["shotCount"] and len(result.rows) != inputs["shotCount"]:
            raise ValueError("shot count differs from frozen request")
        names = _character_names(inputs["characters"])
        assets = {item["id"] for item in inputs["canvasAssets"]}
        rows = []
        for index, row in enumerate(result.rows, 1):
            if (
                inputs["shotDurationSeconds"]
                and row.duration_seconds != inputs["shotDurationSeconds"]
            ):
                raise ValueError("shot duration differs from frozen request")
            if any(name not in names for name in row.characters) or len(set(row.characters)) != len(
                row.characters
            ):
                raise ValueError("unknown or duplicate character")
            binding_ids = [item.node_id for item in row.asset_bindings]
            if any(key not in assets for key in binding_ids) or len(set(binding_ids)) != len(
                binding_ids
            ):
                raise ValueError("unknown or duplicate asset binding")
            value = row.model_dump(mode="json", by_alias=True)
            value.update(id=f"shot:{task_id}:{index}", status="idle")
            value["characters"] = [
                {
                    "characterName": name,
                    "characterNodeId": names[name]["nodeId"],
                }
                for name in row.characters
            ]
            rows.append(value)
        return {"title": result.title, "rows": rows}
    except (ValueError, TypeError, KeyError, ValidationError):
        raise GenerationError("invalid_storyboard_result") from None
