"""作品创建与重命名；归属、版本与审计信息由服务端管理。"""

from typing import Annotated, Literal

from pydantic import StringConstraints

from short_drama.schemas.base import Identifier, InputModel


class ProjectCreateRequest(InputModel):
    workspace_mode: Literal["standard", "infinite_canvas"] = "standard"
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class ProjectPatchRequest(InputModel):
    row_version: Identifier
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
