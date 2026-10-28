from typing import Annotated

from pydantic import Field, model_validator

from short_drama.schemas.base import Identifier, InputModel, ReadModel
from short_drama.schemas.creation.writing.script.documents import WritingText


class ScriptReference(InputModel):
    project_id: Identifier
    script_id: Identifier | None
    content_version: Identifier
    start: Annotated[int, Field(ge=0)] | None = None
    end: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def bounded_selection(self):
        if (self.start is None) != (self.end is None):
            raise ValueError("A selection requires both boundaries")
        if self.start is not None and self.end <= self.start:
            raise ValueError("Selection end must follow start")
        return self


class ReadCurrentScript(InputModel):
    offset: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=6000)] = 4000


class ProposeScriptPatch(InputModel):
    content_version: Identifier
    script_id: Identifier | None
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=0)]
    replacement: WritingText


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
