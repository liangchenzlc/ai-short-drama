"""The Harness function-calling contract: capabilities, not sentence grants."""

from copy import deepcopy
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from short_drama.agent.context.working_set import RecordObservation
from short_drama.agent.skill.contracts import ReadSkillResource, SearchSkills
from short_drama.agent.task.contract import ConfirmRequirements
from short_drama.agent.tools.contracts import (
    CANVAS_TOOL,
    IMAGE_CONTENT_TOOL,
    IMAGE_PRIMARY_TOOL,
    MEDIA_TOOL,
    MEMORY_WRITE_TOOLS,
    STANDARD_TOOL,
    TOOL_SCHEMAS,
    ReadImageContent,
    ReadTaskResults,
)
from short_drama.schemas.assistant.memory.memory import MemoryKind
from short_drama.schemas.assistant.operations.canvas import AssistantCanvasPatch
from short_drama.schemas.assistant.operations.media import (
    AssistantCanvasMediaRequest,
    AssistantCharacterVoiceRequest,
    AssistantDialogueAudioRequest,
    AssistantImageRequest,
    AssistantShotVideoRequest,
)
from short_drama.schemas.assistant.operations.script import ProposeScriptPatch, ReadCurrentScript
from short_drama.schemas.assistant.operations.standard import StandardPatch
from short_drama.schemas.base import Identifier, InputModel
from short_drama.schemas.media.image_groups.groups import ImagePrimarySet

TOOL_VERSION = 4


class ApplyStandardPatch(InputModel):
    patch: StandardPatch


class ApplyCanvasPatch(InputModel):
    patch: AssistantCanvasPatch


class ReadResource(InputModel):
    ref_id: Annotated[str, Field(min_length=1, max_length=240)]
    purpose: Annotated[str, Field(min_length=1, max_length=300)]
    detail: Literal["low", "high", "original"] = "low"
    crop: tuple[int, int, int, int] | None = None
    offset: Annotated[int, Field(ge=0, le=1048576)] = 0
    limit: Annotated[int, Field(ge=1, le=6000)] = 4000


class ReadPixels(ReadImageContent):
    model_config = ConfigDict(
        json_schema_extra={
            "anyOf": [
                {
                    "required": ["subject_key", "group_id", "item_id"],
                    "properties": {
                        "subject_key": {"type": "string"},
                        "operation_id": {"type": "null"},
                        "group_id": {"not": {"type": "null"}},
                        "item_id": {"not": {"type": "null"}},
                    },
                },
                {
                    "required": ["operation_id"],
                    "properties": {
                        "operation_id": {"not": {"type": "null"}},
                        "subject_key": {"type": "null"},
                    },
                    "anyOf": [
                        {"properties": {"group_id": {"type": "null"}, "item_id": {"type": "null"}}},
                        {
                            "required": ["group_id", "item_id"],
                            "properties": {
                                "group_id": {"not": {"type": "null"}},
                                "item_id": {"not": {"type": "null"}},
                            },
                        },
                    ],
                },
            ],
        }
    )
    purpose: Annotated[str, Field(min_length=1, max_length=300)]
    detail: Literal["low", "high", "original"] = "low"
    crop: tuple[int, int, int, int] | None = None


class LoadSkill(InputModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    content_version: Identifier
    purpose: Annotated[str, Field(min_length=1, max_length=240)]


class ReleaseSkill(InputModel):
    activation_id: Identifier


class EnableToolGroup(InputModel):
    group: Literal["standard_write", "canvas_write", "media", "memory_write", "image_adoption"]


class SetImagePrimary(ImagePrimarySet):
    subject_key: Annotated[str, Field(pattern=r"^subject:[a-f0-9]{64}$")]
    group_id: Identifier


class RememberMemory(InputModel):
    scope_type: Literal["user", "conversation", "project"] = "conversation"
    kind: MemoryKind = "setting"
    memory_key: Annotated[str, Field(min_length=1, max_length=120)]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    content: Annotated[str, Field(min_length=1, max_length=12000)]
    source_message_ids: Annotated[list[Identifier], Field(min_length=1, max_length=8)]


class ChangeMemory(InputModel):
    memory_id: Identifier
    content_version: Identifier
    content: Annotated[str, Field(min_length=1, max_length=12000)] | None = None
    source_message_ids: Annotated[list[Identifier], Field(min_length=1, max_length=8)]


class SubmitGenerationBatch(InputModel):
    retry_operation_id: Identifier | None = None
    requests: Annotated[
        list[
            Annotated[
                AssistantImageRequest
                | AssistantCanvasMediaRequest
                | AssistantShotVideoRequest
                | AssistantCharacterVoiceRequest
                | AssistantDialogueAudioRequest,
                Field(discriminator="target_kind"),
            ]
        ],
        Field(min_length=1, max_length=16),
    ]

    @model_validator(mode="after")
    def unique_targets(self):
        if len({(item.target_kind, str(item.target_id)) for item in self.requests}) != len(
            self.requests
        ):
            raise ValueError("A batch must have unique targets")
        if len({item.media_kind for item in self.requests}) != 1:
            raise ValueError("Use one media kind per atomic batch")
        return self


OVERRIDES = {
    "read_current_script": (
        ReadCurrentScript,
        "按本次消息的剧本版本引用读取真实正文或选区；按 next_offset 继续。"
        "未引用时先请用户选入当前剧本。",
    ),
    "propose_script_patch": (
        ProposeScriptPatch,
        "基于刚读取的剧本身份、版本和范围准备修改建议（空稿为 start=end=0）。"
        "用户审阅后才写入，不能声称已保存正文。",
    ),
    "confirm_requirements": (
        ConfirmRequirements,
        "确认服务端已按用户原话给出的待确认交付要求（keys），必要时补充有原文引用的要求。"
        "用户明确改为新数量时，可在 additions 引用原 key、更新的用户消息和新数量修订；"
        "模型不能自行缩减。",
    ),
    "enable_tool_group": (
        EnableToolGroup,
        "需要尚未提供 schema 的能力时，按 tool_catalog 中的 group 启用；"
        "下一决策可自主调用该组工具。",
    ),
    "record_task_observation": (
        RecordObservation,
        "保存本任务需要跨压缩保留的关键观察或推断，引用真实读取操作及原文短句；图片必须已经实际看过。不是长期记忆。",
    ),
    "release_skill": (
        ReleaseSkill,
        "当前阶段完成后释放不再需要的自动方法正文；不移除手选方法，历史回执仍保留。",
    ),
    "read_resource": (
        ReadResource,
        "仅按 resources 附件目录的 ref_id 读取本任务附件，不接受 Skill 资源路径或 MinIO URI。"
        "Skill 示例图等资源使用 read_skill_resource(activation_id, path)。"
        "需要实际像素时填写用途；可裁剪或提高 detail。文本按 next_offset 继续。",
    ),
    "load_skill": (
        LoadSkill,
        "按常驻索引或 search_skills 的 name 和版本加载完成任务所需 SOP，填写用途。重复加载去重。",
    ),
    "search_skills": (SearchSkills, "目录不足时搜索更多兼容方法的名称和描述；不自动加载正文。"),
    "read_skill_resource": (
        ReadSkillResource,
        "按激活方法的冻结路径分段读取资料、模板或示例图片。",
    ),
    IMAGE_CONTENT_TOOL: (
        ReadPixels,
        "按需查看真实像素。已选主体必须同时提供 subject_key、group_id、item_id、media_id；"
        "从 read_image_group 的 read_content_args 复制完整身份，再填写 purpose。"
        "本任务生成成果使用真实 operation_id 和 media_id；标准图片组还须 group_id/item_id。"
        "subject_key 与 operation_id 二选一，不能只传 media_id。可裁剪或提高 detail。",
    ),
    "read_task_results": (
        ReadTaskResults,
        "回读本任务保存后的白名单正文。operation_id 必须取 result_sources 中原始"
        "apply_standard_patch/apply_canvas_patch 保存批次，不能取本次读取或回执查询的 ID。"
        "ids 留空读取该批可读成果，分页原样使用 next。标准模式也支持仍选入对象的本任务修改快照；"
        "画布只支持本任务新建节点。results[].content 是真实回读正文，即使 result_refs 为空。"
        "更早批次用 describe_creation_target(result_before_id,result_limit) 查询。",
    ),
    "describe_creation_target": (
        TOOL_SCHEMAS["describe_creation_target"][0],
        "读取目标身份、版本和能力，不读取正文。result_sources 单列原始保存批次及成果 ID，"
        "不受后续读取操作挤占；更多批次用 result_before_id/result_limit 分页。",
    ),
    STANDARD_TOOL: (
        ApplyStandardPatch,
        "根据用户目标自主保存标准素材、分镜和台词增量。先读取真实目录版本，"
        "只操作已选主体或本任务成果，临时引用用 $key。",
    ),
    CANVAS_TOOL: (
        ApplyCanvasPatch,
        "根据用户目标自主保存有界画布增量。先核对 canvas_revision 和 allowed_nodes "
        "的公共及私人版本，临时引用用 $key。",
    ),
    IMAGE_PRIMARY_TOOL: (
        SetImagePrimary,
        "自主采用已选冻结图片目录中的真实图位，须使用三类可信版本。",
    ),
    MEDIA_TOOL: (
        SubmitGenerationBatch,
        "按用户目标自主提交一个有界媒体批次。模型、目标、版本须来自真实目录，参考限选入或本任务已结算成果；受媒体开关和预算约束。",
    ),
    "remember_memory": (
        RememberMemory,
        "根据用户意图保存有来源的记忆。来源必须是本任务已应用用户消息；选择真实作用域，不恢复已遗忘内容。",
    ),
    **{
        name: (
            ChangeMemory,
            "根据用户意图操作指定记忆的当前版本，提供本任务用户消息来源；不能跨作用域或恢复已遗忘旧来源。",
        )
        for name in MEMORY_WRITE_TOOLS
        if name != "remember_memory"
    },
}
REGISTRY = {**TOOL_SCHEMAS, **OVERRIDES}

TOOL_GROUPS = {
    "standard_write": ({STANDARD_TOOL}, "保存标准素材、分镜和台词"),
    "canvas_write": ({CANVAS_TOOL}, "保存画布节点与关联"),
    "media": ({MEDIA_TOOL}, "提交媒体生成批次"),
    "memory_write": (set(MEMORY_WRITE_TOOLS), "保存、更新及遗忘长期记忆"),
    "image_adoption": ({IMAGE_PRIMARY_TOOL}, "采用已有图片为主图"),
}


def tool_catalog(task, *, media_enabled: bool) -> list[dict]:
    available = set(_available_names(task, media_enabled))
    enabled = set(task.effective_state.get("enabled_tool_groups", []))
    return [
        {
            "group": name,
            "description": description,
            "tools": sorted(names & available),
            "enabled": name in enabled,
        }
        for name, (names, description) in TOOL_GROUPS.items()
        if names & available
    ]


def _available_names(task, media_enabled: bool) -> list[str]:
    mode = (task.execution_target or {}).get("mode")
    return [
        name
        for name in REGISTRY
        if (name != STANDARD_TOOL or mode == "standard")
        and (name not in {"read_current_script", "propose_script_patch"} or mode == "standard")
        and (name != CANVAS_TOOL or mode == "infinite_canvas")
        and (name != IMAGE_PRIMARY_TOOL or mode == "standard")
        and (name != MEDIA_TOOL or bool(mode) and media_enabled)
        and (name != "read_selected_subjects" or task.context_snapshot is not None)
    ]


@lru_cache(maxsize=64)
def _schema_manifest(names: tuple[str, ...]) -> tuple[dict, ...]:
    return tuple(
        {
            "name": name,
            "description": REGISTRY[name][1],
            "parameters": REGISTRY[name][0].model_json_schema(),
        }
        for name in names
    )


def tool_manifest(task, *, media_enabled: bool, progressive: bool = False) -> list[dict]:
    names = _available_names(task, media_enabled)
    if progressive:
        deferred = set().union(*(group for group, _ in TOOL_GROUPS.values()))
        base = [name for name in names if name not in deferred]
        enabled = getattr(task, "effective_state", {}).get("enabled_tool_groups", [])
        # Enabling appends schemas; existing capability order stays stable.
        names = base + [
            name for group in enabled for name in names if name in TOOL_GROUPS[group][0]
        ]
    return deepcopy(list(_schema_manifest(tuple(names))))
