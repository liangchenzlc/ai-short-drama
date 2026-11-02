"""Skill tool schemas, limits and activation projections shared by coordinators."""

from typing import Annotated, Literal

from pydantic import Field, field_validator

from short_drama.schemas.base import Identifier, InputModel
from short_drama.schemas.skills.catalog.skill import MAX_RESOURCE_BYTES, resource_path

SKILL_SEARCH = "runtime__search_skills"
SKILL_LOAD = "runtime__load_skill"
SKILL_RESOURCE = "runtime__read_skill_resource"
MAX_ACTIVE = 8
MAX_ACTIVATIONS = 32
MAX_INSTRUCTION_BYTES = 131072
MAX_RESOURCE_CONTEXT_BYTES = 32768


class SearchSkills(InputModel):
    query: Annotated[str, Field(max_length=120)] = ""
    offset: Annotated[int, Field(ge=0, le=100000)] = 0
    limit: Annotated[int, Field(ge=1, le=20)] = 12


class ReadSkillResource(InputModel):
    activation_id: Identifier
    path: Annotated[str, Field(min_length=1, max_length=255)]
    offset: Annotated[int, Field(ge=0, le=MAX_RESOURCE_BYTES)] = 0
    limit: Annotated[int, Field(ge=1, le=4000)] = 4000
    purpose: Annotated[str, Field(min_length=1, max_length=300)] = "读取完成当前任务所需的补充资料"
    detail: Literal["low", "high", "original"] = "low"
    crop: tuple[int, int, int, int] | None = None

    @field_validator("path")
    @classmethod
    def safe_path(cls, value):
        return resource_path(value)


def activations(state):
    return state.get("skill_activations", [])


def activation_summary(item):
    skill = item["skill"]
    return {
        "activation_id": item["id"],
        "skill_id": skill["id"],
        "name": skill["name"],
        "content_version": skill["content_version"],
        "package_checksum": skill.get("package_checksum", ""),
        "source": item["source"],
        "purpose": item["purpose"],
        "active": item["active"],
        "plan_revision": item["plan_revision"],
        "created_at": item["created_at"],
        "issue": item.get("issue"),
    }
