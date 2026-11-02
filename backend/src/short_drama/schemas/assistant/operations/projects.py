"""Explicit project reads; identity and granted project IDs stay server-owned."""

from typing import Annotated

from pydantic import Field

from short_drama.schemas.base import Identifier, InputModel


class ListProjects(InputModel):
    offset: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    query: Annotated[str, Field(max_length=120)] = ""


class ReadProject(InputModel):
    project_id: Identifier


class ReadProjectCollection(ReadProject):
    offset: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 20
