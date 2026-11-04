"""Explicit project reads; identity and granted project IDs stay server-owned."""

from typing import Annotated

from pydantic import Field

from short_drama.schemas.base import Identifier, InputModel


class ListProjects(InputModel):
    offset: Annotated[
        int, Field(ge=0, description="结果页起始偏移，从 0 开始。此值是列表位置，不是项目 ID。")
    ] = 0
    limit: Annotated[
        int, Field(ge=1, le=100, description="本页最多返回的项目数；省略时使用默认值 20。")
    ] = 20
    query: Annotated[
        str, Field(max_length=120, description="可选的项目名称搜索词；空字符串表示不按名称筛选。")
    ] = ""


class ReadProject(InputModel):
    project_id: Identifier = Field(
        description="要读取的项目 ID；使用用户指定或项目列表返回的十进制 ID。"
    )


class ReadProjectCollection(ReadProject):
    offset: Annotated[int, Field(ge=0, description="结果页起始偏移，从 0 开始。")] = 0
    limit: Annotated[
        int, Field(ge=1, le=100, description="本页最多返回的项目对象数；省略时使用默认值 20。")
    ] = 20
