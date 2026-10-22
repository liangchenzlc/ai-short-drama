"""Bounded structural patches; identity, permission and receipts stay server-owned."""

import json
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from short_drama.schemas.asset import AssetPatch
from short_drama.schemas.asset_library import AssetLibraryCreate
from short_drama.schemas.base import Identifier, InputModel, NonnegativeVersion, parse_identifier
from short_drama.schemas.native_voice import NativeDialogueDocument, NativeLine
from short_drama.schemas.project_sound import Dialogue
from short_drama.schemas.project_storyboard import (
    ShotImageSettings,
    ShotScriptText,
    ShotVideoSettings,
    StoryboardUpdate,
)
from short_drama.schemas.shot_script import ShotSizeEnum, ShotTitle


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
    action: Literal["create_asset"]
    key: LocalKey
    values: AssetLibraryCreate


class UpdateStandardAsset(InputModel):
    action: Literal["update_asset"]
    asset: ObjectReference
    patch: AssetPatch


class StandardShotValues(InputModel):
    title: ShotTitle = ""
    shot_size: ShotSizeEnum | None = None
    script: ShotScriptText = ""
    duration_ms: int = Field(default=3000, strict=True, ge=1000, le=10000)
    image_settings: ShotImageSettings = Field(default_factory=ShotImageSettings)
    video_prompt: Annotated[str, Field(max_length=16000)] = ""
    video_settings: ShotVideoSettings = Field(default_factory=ShotVideoSettings)


class CreateStandardShot(InputModel):
    action: Literal["create_shot"]
    key: LocalKey
    values: StandardShotValues
    assets: Annotated[list[ObjectReference], Field(max_length=100)] = Field(default_factory=list)


class UpdateStandardShot(InputModel):
    action: Literal["update_shot"]
    shot: ObjectReference
    patch: StoryboardUpdate

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
    action: Literal["set_shot_assets"]
    shot: ObjectReference
    row_version: Identifier
    assets: Annotated[list[ObjectReference], Field(max_length=100)]


class ReorderStandardShots(InputModel):
    action: Literal["reorder_shots"]
    shots: Annotated[list[ObjectReference], Field(min_length=2, max_length=500)]
    versions: dict[ObjectReference, Identifier]

    @model_validator(mode="after")
    def exact_versions(self):
        if len(self.shots) != len(set(self.shots)) or set(self.shots) != set(self.versions):
            raise ValueError("ordered shots must be unique and each requires an expected version")
        return self


class DeleteStandardAsset(InputModel):
    action: Literal["delete_asset"]
    asset: ObjectReference
    row_version: Identifier


class DeleteStandardShot(InputModel):
    action: Literal["delete_shot"]
    shot: ObjectReference
    row_version: Identifier


class StandardDialogueLine(NativeLine):
    character_id: ObjectReference


class StandardDialogueDocument(NativeDialogueDocument):
    lines: Annotated[list[StandardDialogueLine], Field(max_length=20)] = Field(default_factory=list)


class EditStandardDialogue(InputModel):
    action: Literal["edit_dialogue"]
    shot: ObjectReference
    row_version: Identifier
    dialogue_version: NonnegativeVersion
    document: StandardDialogueDocument


class StandardSoundRevision(InputModel):
    row_version: NonnegativeVersion
    assembly_version: Identifier
    timeline_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


SoundLineReference = Annotated[
    str, Field(pattern=r"^(?:[a-zA-Z0-9_-]{1,64}|\$[A-Za-z][A-Za-z0-9_-]{0,39})$")
]


class StandardSoundValues(InputModel):
    character: Annotated[str, Field(min_length=1, max_length=100)] = "旁白"
    text: Annotated[str, Field(min_length=1, max_length=4096)]
    voice: Annotated[str, Field(max_length=128)] = ""
    config_id: Identifier | None = None

    @model_validator(mode="after")
    def valid_content(self):
        Dialogue(id="validation", **self.model_dump())
        if not self.text.strip() or not self.character.strip():
            raise ValueError("台词正文和角色不能只含空白")
        return self


class StandardSoundPatch(InputModel):
    character: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    text: Annotated[str, Field(min_length=1, max_length=4096)] | None = None
    voice: Annotated[str, Field(max_length=128)] | None = None
    config_id: Identifier | None = None

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
    action: Literal["create_sound_line"]
    key: LocalKey
    values: StandardSoundValues


class UpdateStandardSoundLine(InputModel):
    action: Literal["update_sound_line"]
    line: SoundLineReference
    patch: StandardSoundPatch


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
    storyboard_version: Identifier | None = None
    sound_revision: StandardSoundRevision | None = None
    actions: Annotated[list[StandardAction], Field(min_length=1, max_length=50)]

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
