"""Public creation contracts; ownership, ordering and audit stay server-owned."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from .base import InputModel


class ProjectCreateRequest(InputModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    aspect: Literal["16:9", "9:16"]
    synopsis: Annotated[str, Field(max_length=2000)] = ""
    style: Annotated[str, Field(max_length=255)] = ""


class EpisodeCreateRequest(InputModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    synopsis: Annotated[str, Field(max_length=500)] = ""
    # An omitted field inherits the project value. Explicit null is invalid.
    aspect: Literal["16:9", "9:16"] = None
    style: Annotated[str, Field(max_length=255)] = None


class ProjectPatchRequest(InputModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)] = (
        None
    )
    aspect: Literal["16:9", "9:16"] = None
    synopsis: Annotated[str, Field(max_length=2000)] = None
    style: Annotated[str, Field(max_length=255)] = None


class EpisodePatchRequest(InputModel):
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ] = None
    synopsis: Annotated[str, Field(max_length=500)] = None
    aspect: Literal["16:9", "9:16"] = None
    style: Annotated[str, Field(max_length=255)] = None
