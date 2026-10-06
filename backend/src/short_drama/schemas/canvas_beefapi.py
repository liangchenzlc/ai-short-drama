"""企业设备授权的公开状态与上游私有响应合同。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .canvas_generation import CanvasGenerationProtocol

ConnectionState = Literal[
    "disconnected",
    "pending",
    "connected",
    "expired",
    "cancelled",
    "rejected",
    "store_error",
    "catalog_failed",
    "revoked",
]


class BeefAPIAccount(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    username: str | None = Field(None, max_length=512)
    display_name: str | None = Field(None, max_length=512)
    email: str | None = Field(None, max_length=512)

    @field_validator("id", mode="before")
    @classmethod
    def wire_id(cls, value):
        if type(value) not in (int, str):
            raise ValueError("invalid enterprise identity")
        text = str(value).strip()
        if not text.isascii() or not text.isdecimal() or not 0 < int(text) < 2**63:
            raise ValueError("invalid enterprise identity")
        return str(int(text))


class BeefAPIToken(BaseModel):
    model_config = ConfigDict(extra="ignore")
    api_key: SecretStr
    base_url: str
    market: Literal["enterprise"]
    group: Literal["enterprise"]
    account: BeefAPIAccount
    token_id: str
    key_name: str = Field(default="", max_length=512)

    _token_id = field_validator("token_id", mode="before")(BeefAPIAccount.wire_id.__func__)


class CanvasBeefAPISummary(CanvasGenerationProtocol):
    state: ConnectionState
    enterprise_origin: str
    has_credential: bool
    user_code: str | None = None
    verification_uri: str | None = None
    expires_at: str | None = None
    account: BeefAPIAccount | None = None
    key_name: str | None = None
    token_id: str | None = None
    market: str | None = None
    wallet_url: str | None = None
    balance: Literal["unknown", "zero", "available"] = "unknown"
    catalog_failed: bool = False
    error_reason: str | None = None
    connected_at: str | None = None
    credential_ref: str | None = None


class CanvasBeefAPIWallet(CanvasGenerationProtocol):
    enterprise_origin: str
    wallet_url: str
