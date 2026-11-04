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
    query: Annotated[
        str,
        Field(
            max_length=120,
            description="描述当前任务需要的方法或具体创作问题；空字符串列出当前模式可用方法。",
        ),
    ] = ""
    offset: Annotated[int, Field(ge=0, le=100000, description="方法目录分页偏移，从 0 开始。")] = 0
    limit: Annotated[int, Field(ge=1, le=20, description="本页最多返回的方法数量。")] = 12


class ReadSkillResource(InputModel):
    activation_id: Identifier = Field(description="当前 scope 中 load_skill 返回的 activation_id。")
    path: Annotated[
        str,
        Field(
            min_length=1,
            max_length=255,
            description="当前激活 Skill manifest 中的相对路径；不得引用其他包。",
        ),
    ]
    offset: Annotated[
        int,
        Field(
            ge=0,
            le=MAX_RESOURCE_BYTES,
            description="文本资源分页偏移，单位字节；使用 next_offset 继续。",
        ),
    ] = 0
    limit: Annotated[
        int, Field(ge=1, le=4000, description="本次最多读取的资源字节数；图片水合不按文本分页。")
    ] = 4000
    purpose: Annotated[
        str, Field(min_length=1, max_length=300, description="说明此附件如何帮助完成当前任务。")
    ] = "读取完成当前任务所需的补充资料"
    detail: Literal["low", "high", "original"] = Field(
        default="low", description="图片资源交付的细节级别。"
    )
    crop: tuple[int, int, int, int] | None = Field(
        default=None, description="可选图片裁剪框 (left, top, right, bottom)，使用图像坐标。"
    )

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
