"""原审美工具与已有分镜节点的批准合同；数据库身份与版本均为十进制字符串。"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base import Identifier, NonnegativeVersion
from .canvas import CanvasKey, SourceKey
from .canvas_generation import CanvasGenerationProtocol
from .canvas_task_runtime import CanvasRuntimeTaskCreate

Owner = Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9:_-]+$")]


class CanvasToolRunState(CanvasGenerationProtocol):
    kind: Literal["art-critique"]
    node_id: SourceKey
    source_node_id: SourceKey
    source_fingerprint: Annotated[str, Field(pattern=r"^v1-[0-9a-f]{8}$")]
    model: Annotated[str, Field(min_length=1, max_length=512)]
    logical_model_id: Identifier


class CanvasStoryboardRunState(CanvasGenerationProtocol):
    kind: Literal["storyboard"]
    node_id: SourceKey
    source_node_id: SourceKey
    model: Annotated[str, Field(min_length=1, max_length=512)]
    logical_model_id: Identifier

    @model_validator(mode="after")
    def script_source(self):
        if self.source_node_id != self.node_id:
            raise ValueError("Storyboard source must be its script node")
        return self


RunState = Annotated[CanvasToolRunState | CanvasStoryboardRunState, Field(discriminator="kind")]


class CanvasToolRunCreate(CanvasGenerationProtocol):
    client_key: Annotated[
        str,
        Field(
            min_length=12, max_length=120, pattern=r"^(art-critique|storyboard):[A-Za-z0-9:_-]+$"
        ),
    ]
    canvas_id: CanvasKey
    state: RunState

    @model_validator(mode="after")
    def kind_matches_key(self):
        if not self.client_key.startswith(self.state.kind + ":"):
            raise ValueError("Run key must match its state kind")
        return self


class CanvasToolGuard(CanvasGenerationProtocol):
    execution_epoch: NonnegativeVersion
    owner: Owner


class CanvasToolClaim(CanvasGenerationProtocol):
    expected_epoch: NonnegativeVersion
    owner: Owner


class CanvasToolPrepare(CanvasToolGuard):
    item_key: Annotated[str, Field(min_length=1, max_length=160)]
    request: CanvasRuntimeTaskCreate


class CanvasToolApprove(CanvasToolGuard):
    submission_ids: Annotated[list[Identifier], Field(min_length=1, max_length=9)]

    @model_validator(mode="after")
    def unique_ids(self) -> "CanvasToolApprove":
        if len(set(self.submission_ids)) != len(self.submission_ids):
            raise ValueError("duplicate stage approvals")
        return self


class CanvasToolExecute(CanvasToolGuard):
    submission_id: Identifier


class CanvasToolRunRead(CanvasGenerationProtocol):
    id: Identifier
    user_id: Identifier
    canvas_id: CanvasKey
    revision: Identifier
    execution_epoch: NonnegativeVersion
    execution_owner: str
    lease_expires_at: str | None = None
    status: Literal["idle", "running"]
    state: RunState
    created_at: str
    updated_at: str


class CanvasToolExecutionRead(CanvasGenerationProtocol):
    model: str
    config_hash: str


class CanvasToolSubmissionRead(CanvasGenerationProtocol):
    id: Identifier
    run_id: Identifier
    item_key: str
    request_hash: str
    execution: CanvasToolExecutionRead
    approved_at: str | None = None
    task_id: Identifier | None = None


class CanvasToolRunDetail(CanvasGenerationProtocol):
    run: CanvasToolRunRead
    submissions: list[CanvasToolSubmissionRead]


class CanvasToolRunList(CanvasGenerationProtocol):
    runs: list[CanvasToolRunRead]


class CanvasToolApprovalsRead(CanvasGenerationProtocol):
    submissions: list[CanvasToolSubmissionRead]


class CanvasToolHeartbeatRead(CanvasGenerationProtocol):
    lease_expires_at: str


class CanvasToolReleaseRead(CanvasGenerationProtocol):
    released: bool
