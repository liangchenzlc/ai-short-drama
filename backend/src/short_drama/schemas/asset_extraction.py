"""Text-only extraction candidates and explicit adoption contracts."""

import json
import re
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import AfterValidator, Field, model_validator

from .asset import Name
from .asset_library import AssetLibraryCreate
from .base import Identifier, InputModel, nonblank

CandidateId = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
RequiredText = Annotated[str, Field(max_length=8000), AfterValidator(nonblank)]


class ExtractionDraft(AssetLibraryCreate):
    description: RequiredText
    prompt: RequiredText


class ExtractedAsset(ExtractionDraft):
    aliases: list[Name] = Field(default_factory=list, max_length=20)
    evidence: Annotated[str, Field(max_length=500), AfterValidator(nonblank)]


class ExtractionOutput(InputModel):
    schema_version: Literal[1]
    items: list[ExtractedAsset] = Field(max_length=100)


class CandidateEdit(InputModel):
    candidate_id: CandidateId
    draft: ExtractionDraft


class ExtractionPatch(InputModel):
    result_version: Identifier
    items: list[CandidateEdit] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_items(self):
        if len({item.candidate_id for item in self.items}) != len(self.items):
            raise ValueError("Duplicate candidates")
        return self


class CandidateAdoption(InputModel):
    candidate_id: CandidateId
    action: Literal["create", "reuse"]
    asset_id: Identifier | None = None
    expected_row_version: Identifier | None = None
    confirm_duplicate: bool = False

    @model_validator(mode="after")
    def reuse_target(self):
        if self.action == "reuse":
            if self.asset_id is None or self.expected_row_version is None:
                raise ValueError("Reuse requires an asset and its version")
        elif self.asset_id is not None or self.expected_row_version is not None:
            raise ValueError("Create cannot specify a target")
        return self


class ExtractionApply(InputModel):
    result_version: Identifier
    content_version: Identifier
    items: list[CandidateAdoption] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_items(self):
        if len({item.candidate_id for item in self.items}) != len(self.items):
            raise ValueError("Duplicate candidates")
        return self


def parse_extraction_result(content, snapshot):
    if len(content.encode("utf-8")) > 1048576:
        raise ValueError("Structured output exceeds 1 MiB")
    stripped = content.strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", stripped)
    parsed = ExtractionOutput.model_validate(json.loads(fence.group(1) if fence else stripped))
    if len(parsed.items) > snapshot.get("max_candidates", 100):
        raise ValueError("Too many candidates")
    items = []
    for item in parsed.items:
        if item.kind not in snapshot["extraction"]["kinds"]:
            raise ValueError("Unrequested asset kind")
        if item.evidence not in snapshot["content"]:
            raise ValueError("Unverified script evidence")
        original = item.model_dump(mode="json")
        draft = {
            key: value for key, value in original.items() if key not in {"aliases", "evidence"}
        }
        items.append(
            {
                "candidate_id": uuid4().hex,
                "original": original,
                "draft": draft,
                "applied": None,
            }
        )
    return {
        "kind": "script_assets",
        "schema_version": 1,
        "result_version": "1",
        "items": items,
        "receipts": {},
    }
