"""Bounded structural patches; identity, permission and receipts stay server-owned."""

import json
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from short_drama.schemas.base import Identifier, InputModel, NonnegativeVersion, parse_identifier
from short_drama.schemas.creation.assets.management.assets import AssetPatch
from short_drama.schemas.creation.assets.management.library import AssetLibraryCreate
from short_drama.schemas.creation.sound.tracks.sound import Dialogue
from short_drama.schemas.creation.sound.voices.voices import NativeDialogueDocument, NativeLine
from short_drama.schemas.creation.storyboard.management.script import ShotSizeEnum, ShotTitle
from short_drama.schemas.creation.storyboard.management.storyboard import (
    ShotImageSettings,
    ShotScriptText,
    ShotVideoSettings,
    StoryboardUpdate,
)


def object_reference(value: str) -> str:
    if not value.startswith("$"):
        parse_identifier(value)
    return value


LocalKey = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")]
ObjectReference = Annotated[
    str,
    Field(pattern=r"^(?:[1-9][0-9]{0,19}|\$[A-Za-z][A-Za-z0-9_-]{0,39})$"),
    AfterValidator(object_reference),
]


class CreateStandardAsset(InputModel):
    action: Literal["create_asset"] = Field(description="动作分支标识；必须是 create_asset。")
    key: LocalKey = Field(description="本批创建素材的临时键；后续动作使用 $<key> 引用它。")
    values: AssetLibraryCreate = Field(description="新素材字段；不得传媒体、模型、状态或版本身份。")


class UpdateStandardAsset(InputModel):
    action: Literal["update_asset"] = Field(description="动作分支标识；必须是 update_asset。")
    asset: ObjectReference = Field(description="已授权素材 ID，或本批更早 create_asset 的 $key。")
    patch: AssetPatch = Field(description="要修改的素材字段及读取时的 row_version。")


class StandardShotValues(InputModel):
    title: ShotTitle = Field(default="", description="新分场/镜头标题；不是素材名称字段 name。")
    shot_size: ShotSizeEnum | None = Field(default=None, description="可选景别枚举；未指定时为空。")
    script: ShotScriptText = Field(
        default="", description="分场（ShotScript）正文；并非 ProjectScript 整篇正文。"
    )
    duration_ms: int = Field(
        default=3000,
        strict=True,
        ge=1000,
        le=10000,
        description="分场建议时长，单位毫秒；10 秒传 10000。",
    )
    image_settings: ShotImageSettings = Field(
        default_factory=ShotImageSettings, description="该分场下一次图片生成设置。"
    )
    video_prompt: Annotated[
        str, Field(max_length=16000, description="该分场下一次视频生成的补充提示词。")
    ] = ""
    video_settings: ShotVideoSettings = Field(
        default_factory=ShotVideoSettings, description="该分场下一次视频生成设置。"
    )


class CreateStandardShot(InputModel):
    action: Literal["create_shot"] = Field(
        description="动作分支标识；必须是 create_shot，不是 type。"
    )
    key: LocalKey = Field(description="本批新镜头临时键；后续动作使用 $<key> 引用。")
    values: StandardShotValues = Field(
        description="新分场/镜头字段；正文使用 script，标题使用 title，时长使用毫秒 duration_ms。"
    )
    assets: Annotated[
        list[ObjectReference],
        Field(max_length=100, description="关联素材 ID 或本批较早创建的 $key。"),
    ] = Field(default_factory=list)


class UpdateStandardShot(InputModel):
    action: Literal["update_shot"] = Field(description="动作分支标识；必须是 update_shot。")
    shot: ObjectReference = Field(description="已授权镜头 ID，或本批更早 create_shot 的 $key。")
    patch: StoryboardUpdate = Field(
        description="镜头变化及读取时 row_version；素材关联需用 set_shot_assets 动作。"
    )

    @model_validator(mode="after")
    def separate_associations(self):
        if "asset_ids" in self.patch.model_fields_set:
            raise ValueError("use set_shot_assets to change associations")
        if self.patch.model_fields_set == {"row_version"}:
            raise ValueError("shot patch must contain a change")
        if any(
            getattr(self.patch, field) is None
            for field in self.patch.model_fields_set - {"shot_size"}
        ):
            raise ValueError("shot patch fields cannot be null except shot_size")
        return self


class SetStandardShotAssets(InputModel):
    action: Literal["set_shot_assets"] = Field(description="动作分支标识；必须是 set_shot_assets。")
    shot: ObjectReference = Field(description="已授权镜头 ID，或本批更早创建镜头的 $key。")
    row_version: Identifier = Field(
        description="镜头当前 row_version；来自最近一次分镜读取或本批创建回执。"
    )
    assets: Annotated[
        list[ObjectReference],
        Field(max_length=100, description="要设置的完整素材关联列表；空数组表示明确清空。"),
    ]


class ReorderStandardShots(InputModel):
    action: Literal["reorder_shots"] = Field(description="动作分支标识；必须是 reorder_shots。")
    shots: Annotated[
        list[ObjectReference],
        Field(
            min_length=2,
            max_length=500,
            description="按目标新顺序排列的真实镜头 ID 或本批临时 $key。",
        ),
    ]
    versions: dict[ObjectReference, Identifier] = Field(
        description="每个 shots 引用对应的预期 row_version；键集合必须与 shots 完全一致。"
    )

    @model_validator(mode="after")
    def exact_versions(self):
        if len(self.shots) != len(set(self.shots)) or set(self.shots) != set(self.versions):
            raise ValueError("ordered shots must be unique and each requires an expected version")
        return self


class DeleteStandardAsset(InputModel):
    action: Literal["delete_asset"] = Field(description="动作分支标识；必须是 delete_asset。")
    asset: ObjectReference = Field(description="待删除的已授权素材 ID；不能用名称代替 ID。")
    row_version: Identifier = Field(description="该素材当前 row_version；来自最近一次素材读取。")


class DeleteStandardShot(InputModel):
    action: Literal["delete_shot"] = Field(description="动作分支标识；必须是 delete_shot。")
    shot: ObjectReference = Field(description="待归档的已授权镜头 ID；不能用标题代替 ID。")
    row_version: Identifier = Field(description="该镜头当前 row_version；来自最近一次分镜读取。")


class StandardDialogueLine(NativeLine):
    character_id: ObjectReference = Field(
        description="说话角色的已授权素材 ID，或本批较早 create_asset 的 $key。"
    )


class StandardDialogueDocument(NativeDialogueDocument):
    lines: Annotated[
        list[StandardDialogueLine], Field(max_length=20, description="按说话顺序排列的对白行。")
    ] = Field(default_factory=list)


class EditStandardDialogue(InputModel):
    action: Literal["edit_dialogue"] = Field(description="动作分支标识；必须是 edit_dialogue。")
    shot: ObjectReference = Field(description="已授权镜头 ID 或本批新建镜头的临时 $key。")
    row_version: Identifier = Field(
        description="镜头当前 row_version；来自最近分镜读取或批内创建结果。"
    )
    dialogue_version: NonnegativeVersion = Field(
        description="该镜头对白文档版本；未建稿时为 0，来自当前目标读取。"
    )
    document: StandardDialogueDocument = Field(description="要保存的完整对白文档；最多两位角色。")


class StandardSoundRevision(InputModel):
    row_version: NonnegativeVersion = Field(
        description="声音文档 row_version；未建稿时为 0，来自当前声音读取结果。"
    )
    assembly_version: Identifier = Field(description="成片时间线版本；来自最近读取的成片快照。")
    timeline_hash: Annotated[
        str,
        Field(
            pattern=r"^[a-f0-9]{64}$",
            description="同一成片快照返回的时间线 SHA-256；不能自行计算替代。",
        ),
    ]


SoundLineReference = Annotated[
    str, Field(pattern=r"^(?:[a-zA-Z0-9_-]{1,64}|\$[A-Za-z][A-Za-z0-9_-]{0,39})$")
]


class StandardSoundValues(InputModel):
    character: Annotated[
        str, Field(min_length=1, max_length=100, description="台词说话人名称。")
    ] = "旁白"
    text: Annotated[
        str, Field(min_length=1, max_length=4096, description="要加入声音时间线的台词正文。")
    ]
    voice: Annotated[str, Field(max_length=128, description="可选音色名称/说明。")] = ""
    config_id: Identifier | None = Field(
        default=None, description="可选本人音频模型配置 ID；必须来自当前可用模型目录。"
    )

    @model_validator(mode="after")
    def valid_content(self):
        Dialogue(id="validation", **self.model_dump())
        if not self.text.strip() or not self.character.strip():
            raise ValueError("台词正文和角色不能只含空白")
        return self


class StandardSoundPatch(InputModel):
    character: str | None = Field(
        default=None, min_length=1, max_length=100, description="要更新的说话人名称。"
    )
    text: str | None = Field(
        default=None, min_length=1, max_length=4096, description="要替换的台词正文。"
    )
    voice: str | None = Field(default=None, max_length=128, description="要更新的音色说明。")
    config_id: Identifier | None = Field(
        default=None, description="音频模型配置 ID；显式 null 清除模型，省略表示不修改。"
    )

    @model_validator(mode="after")
    def has_changes(self):
        if not self.model_fields_set:
            raise ValueError("台词修改须包含至少一个字段")
        for field in self.model_fields_set - {"config_id"}:
            value = getattr(self, field)
            if value is None or field in {"text", "character"} and not value.strip():
                raise ValueError("台词正文和角色须非空，非模型字段不能为 null")
        return self


class CreateStandardSoundLine(InputModel):
    action: Literal["create_sound_line"] = Field(
        description="动作分支标识；必须是 create_sound_line。"
    )
    key: LocalKey = Field(description="本批声音台词临时键；后续动作可用 $<key> 引用。")
    values: StandardSoundValues = Field(description="新增声音时间线台词的内容与可选音色。")


class UpdateStandardSoundLine(InputModel):
    action: Literal["update_sound_line"] = Field(
        description="动作分支标识；必须是 update_sound_line。"
    )
    line: SoundLineReference = Field(
        description="声音时间线中的真实台词行 ID，或本批较早创建的 $key。"
    )
    patch: StandardSoundPatch = Field(description="至少提供一个需要更新的台词字段。")


StandardAction = Annotated[
    CreateStandardAsset
    | UpdateStandardAsset
    | CreateStandardShot
    | UpdateStandardShot
    | SetStandardShotAssets
    | ReorderStandardShots
    | DeleteStandardAsset
    | DeleteStandardShot
    | EditStandardDialogue
    | CreateStandardSoundLine
    | UpdateStandardSoundLine,
    Field(discriminator="action"),
]


class StandardPatch(InputModel):
    storyboard_version: Identifier | None = Field(
        default=None,
        description=(
            "项目分镜集合 storyboard_version；包含镜头动作时必填，来自 "
            "runtime__describe_creation_target。"
        ),
    )
    sound_revision: StandardSoundRevision | None = Field(
        default=None, description="声音动作的文档/成片预期版本；不包含声音动作时省略。"
    )
    actions: Annotated[
        list[StandardAction],
        Field(
            min_length=1,
            max_length=50,
            description="按顺序原子执行的素材、分镜、对白或声音动作；每项由 action 判别。",
        ),
    ]

    @model_validator(mode="after")
    def validate_batch(self):
        known: dict[str, str] = {}
        for item in self.actions:
            if item.action in {"create_asset", "create_shot", "create_sound_line"}:
                if item.key in known:
                    raise ValueError("temporary keys must be unique across object kinds")
                known[item.key] = item.action.removeprefix("create_")
            references = []
            if hasattr(item, "asset"):
                references.append((item.asset, "asset"))
            if hasattr(item, "shot"):
                references.append((item.shot, "shot"))
            if hasattr(item, "line"):
                references.append((item.line, "sound_line"))
            references.extend((ref, "shot") for ref in getattr(item, "shots", []))
            references.extend((ref, "asset") for ref in getattr(item, "assets", []))
            if item.action == "edit_dialogue":
                references.extend((line.character_id, "asset") for line in item.document.lines)
            for ref, kind in references:
                if ref.startswith("$") and known.get(ref[1:]) != kind:
                    raise ValueError(
                        "temporary reference must identify an earlier object of its kind"
                    )
            if hasattr(item, "assets") and len(item.assets) != len(set(item.assets)):
                raise ValueError("asset references must be unique")
        if (
            any(item.action in {"create_sound_line", "update_sound_line"} for item in self.actions)
            and self.sound_revision is None
        ):
            raise ValueError("sound text changes require sound_revision")
        if any(
            item.action.endswith("shot")
            or item.action in {"set_shot_assets", "reorder_shots", "edit_dialogue"}
            for item in self.actions
        ):
            if self.storyboard_version is None:
                raise ValueError("shot changes require storyboard_version")
        if (
            len(json.dumps(self.model_dump(mode="json"), ensure_ascii=False).encode("utf-8"))
            > 1048576
        ):
            raise ValueError("structural patch exceeds 1 MiB")
        return self


class ApplyStandardPatch(InputModel):
    project_id: Identifier = Field(description="当前获准标准项目 ID；使用任务目标中的真实 ID。")
    patch: StandardPatch = Field(description="包含最新版本和一个或多个结构化动作的原子写入批次。")
