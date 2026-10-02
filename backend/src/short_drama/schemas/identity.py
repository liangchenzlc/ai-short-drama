import re
from typing import Annotated

from pydantic import Field, field_validator

from .base import Identifier, InputModel


def normalize_email(value: str) -> str:
    value = value.strip().casefold()
    if len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("Invalid email address")
    return value


class Registration(InputModel):
    username: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{3,32}$")]
    display_name: Annotated[str, Field(min_length=1, max_length=80)]
    email: str
    password: Annotated[str, Field(min_length=12, max_length=128)]

    @field_validator("username")
    @classmethod
    def canonical_username(cls, value):
        return value.lower()

    @field_validator("email")
    @classmethod
    def canonical_email(cls, value):
        return normalize_email(value)

    @field_validator("display_name")
    @classmethod
    def name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Display name is required")
        return value


class Login(InputModel):
    username: Annotated[str, Field(min_length=1, max_length=32)]
    password: Annotated[str, Field(min_length=1, max_length=128)]


class EmailRequest(InputModel):
    email: str

    @field_validator("email")
    @classmethod
    def canonical_email(cls, value):
        return normalize_email(value)


class Proof(InputModel):
    challenge_id: Identifier
    code: Annotated[str, Field(pattern=r"^[0-9]{6}$")]


class PasswordReset(Proof):
    password: Annotated[str, Field(min_length=12, max_length=128)]


class InvitationCreate(InputModel):
    target_user_id: Identifier | None = None
    target_email: str | None = None


class PreferenceUpdate(InputModel):
    context_key: Annotated[str, Field(min_length=1, max_length=128)]
    config_id: Identifier | None = None
