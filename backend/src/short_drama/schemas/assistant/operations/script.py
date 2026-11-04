from typing import Annotated

from pydantic import Field, model_validator

from short_drama.schemas.base import Identifier, InputModel, ReadModel
from short_drama.schemas.creation.writing.script.documents import WritingText


class ScriptReference(InputModel):
    project_id: Identifier = Field(description="剧本所属项目 ID；来自当前获准项目目标。")
    script_id: Identifier | None = Field(
        description=(
            "ProjectScript 身份；使用读取目标返回的 ID。无当前原稿时按契约传 null，不得改用 "
            "ShotScript ID。"
        )
    )
    content_version: Identifier = Field(
        description="ProjectScript 正文版本；必须来自当前剧本读取结果，用于冻结本次读取版本。"
    )
    start: int | None = Field(
        default=None, ge=0, description="可选选区起始字符偏移，包含该位置；须与 end 同时提供。"
    )
    end: int | None = Field(
        default=None, ge=0, description="可选选区结束字符偏移，不包含该位置；须大于 start。"
    )

    @model_validator(mode="after")
    def bounded_selection(self):
        if (self.start is None) != (self.end is None):
            raise ValueError("A selection requires both boundaries")
        if self.start is not None and self.end <= self.start:
            raise ValueError("Selection end must follow start")
        return self


class ReadCurrentScript(ScriptReference):
    offset: Annotated[
        int, Field(ge=0, description="当前整篇正文或选区片段的分页字符偏移，从 0 开始。")
    ] = 0
    limit: Annotated[
        int,
        Field(
            ge=1,
            le=6000,
            description="本次最多读取的字符数；省略时为 4000。继续读取时使用返回的 next offset。",
        ),
    ] = 4000


class ProposeScriptPatch(InputModel):
    content_version: Identifier = Field(
        description="原稿当前正文版本；必须与最近一次读取的版本完全一致。"
    )
    script_id: Identifier | None = Field(
        description="原稿 ProjectScript ID；使用读取结果中的身份，未建稿时依契约传 null。"
    )
    start: Annotated[
        int, Field(ge=0, description="替换区间起始字符偏移，包含该位置；来自最近读取的原稿选区。")
    ]
    end: Annotated[
        int, Field(ge=0, description="替换区间结束字符偏移，不包含该位置；必须大于 start。")
    ]
    replacement: WritingText = Field(
        description="用于替换所选区间的新文本；这会创建待审建议，不直接修改当前正文。"
    )


class ScriptProposalRead(ReadModel):
    operation_id: Identifier
    project_id: Identifier
    content_version: Identifier
    script_id: Identifier | None
    start: int
    end: int
    before: str
    after: str
    applied_version: Identifier | None = None


class ScriptProposalApplied(ReadModel):
    project_id: Identifier
    content_version: Identifier
