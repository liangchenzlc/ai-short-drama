"""Encrypted model settings with optimistic versions and atomic default switching."""

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from short_drama.ai.model_identity import model_credential_identity
from short_drama.core.config import Settings
from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import (
    BusinessError,
    ConfigurationError,
    Conflict,
    NotFound,
    WorkflowError,
)
from short_drama.domain import AIModelConfig, CanvasChannelModel
from short_drama.schemas.ai_model_config import (
    AIModelConfigCreate,
    AIModelConfigRead,
    AIModelConfigUpdate,
)
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.model_runtime_profile import ModelRuntimeProfile

from .base import BaseService, utcnow
from .model_runtime_config import (
    decrypt_runtime_credentials,
    encrypt_runtime_credentials,
    model_runtime_public,
)


class _RetryDefault(Exception):
    pass


class AIModelConfigService(BaseService):
    model = AIModelConfig
    create_schema = AIModelConfigCreate
    update_schema = AIModelConfigUpdate
    read_schema = AIModelConfigRead

    def __init__(self, session, cipher=None, settings=None):
        super().__init__(session)
        self.cipher = cipher
        self.settings = settings

    def _key_cipher(self):
        if self.cipher is None:
            try:
                key = (self.settings or Settings()).encryption_key
                self.cipher = KeyCipher(key.get_secret_value() if key else None)
            except ValueError:
                raise ConfigurationError("API key encryption is not configured correctly") from None
        return self.cipher

    def _encrypt_key(self, secret):
        if secret is None or not secret.get_secret_value():
            return None
        envelope = self._key_cipher().encrypt(secret.get_secret_value())
        if len(envelope.encode("utf-8")) > 65535:
            raise BusinessError("Encrypted API key is too long")
        return envelope

    def _read(self, entity):
        binding = self.session.scalar(
            select(CanvasChannelModel).where(CanvasChannelModel.model_config_id == entity.id)
        )
        cipher = self._key_cipher() if entity.runtime_credentials_cipher else None
        public = model_runtime_public(entity, cipher, binding)
        public.pop("runtime_profile")
        return self.read_schema.model_validate(
            {**super()._read(entity).model_dump(), "has_api_key": bool(entity.apikey), **public}
        )

    def _runtime_values(self, values, entity=None):
        if "runtime_profile" in values and values["runtime_profile"] is not None:
            values["runtime_profile"] = ModelRuntimeProfile.model_validate(
                values["runtime_profile"]
            ).model_dump(mode="json", exclude_none=True)
        secret_present = "secret_key" in values
        headers_present = "headers" in values
        secret = values.pop("secret_key", None)
        headers = values.pop("headers", None)
        if not secret_present and not headers_present:
            return values
        if secret_present and (secret is None or not secret.get_secret_value()) and headers == []:
            if entity is not None and entity.runtime_credentials_cipher is not None:
                values["runtime_credentials_cipher"] = None
            return values
        previous = (
            decrypt_runtime_credentials(entity, self._key_cipher())
            if entity is not None and entity.runtime_credentials_cipher
            else {"secretKey": "", "headers": {}}
        )
        updated = deepcopy(previous)
        if secret_present:
            updated["secretKey"] = secret.get_secret_value() if secret is not None else ""
        if headers_present and headers is not None:
            old = {name.lower(): value for name, value in previous["headers"].items()}
            updated["headers"] = {}
            for item in headers:
                value = item["value"].get_secret_value()
                updated["headers"][item["name"]] = value or old.get(item["name"].lower(), "")
        if updated != previous:
            try:
                values["runtime_credentials_cipher"] = encrypt_runtime_credentials(
                    updated["secretKey"], updated["headers"], self._key_cipher()
                )
            except ValueError:
                raise WorkflowError(
                    "model_runtime_credentials_invalid",
                    "合并后的模型请求头不符合限制：最多 32 项、每项 4 KiB、总计 16 KiB",
                    422,
                ) from None
        return values

    def _active(self, identifier):
        entity = self._get_locked(identifier)
        if entity.is_deleted:
            raise NotFound("Model configuration does not exist")
        return entity

    def create(self, payload):
        values = self._payload(self.create_schema, payload)
        values = self._runtime_values(values)
        if "apikey" in values:
            values["apikey"] = self._encrypt_key(values["apikey"])
        with self._transaction():
            return self._read(self.dao.create(self._creation_audit(values)))

    def get(self, identifier):
        identifier = parse_identifier(identifier)
        with self._transaction():
            entity = self._require(self.model, identifier, for_update=False)
            if entity.is_deleted:
                raise NotFound("Model configuration does not exist")
            return self._read(entity)

    def list(self, offset=0, limit=20, filters=None):
        return super().list(offset, limit, {**(filters or {}), "is_deleted": 0})

    def key_for_discovery(self, identifier, base_url):
        from .model_discovery_service import ModelDiscoveryError, normalize_base_url

        with self._transaction():
            entity = self._require(self.model, parse_identifier(identifier), for_update=False)
            if entity.is_deleted:
                raise NotFound("Model configuration does not exist")
            if not entity.apikey:
                return None
            if normalize_base_url(entity.base_url) != base_url:
                raise ModelDiscoveryError("key_address_changed", 400)
            try:
                return (
                    SecretStr(self._key_cipher().decrypt(entity.apikey)) if entity.apikey else None
                )
            except ValueError:
                raise ConfigurationError("Stored API key cannot be decrypted") from None

    def capabilities(self, identifier):
        from short_drama.ai import GenerationError, capabilities

        from .model_runtime_config import refresh_runtime_model

        with self._transaction():
            entity = self._require(self.model, identifier, for_update=False)
            if entity.is_deleted:
                raise NotFound("Model configuration does not exist")
            snapshot = {
                key: getattr(entity, key)
                for key in ("base_url", "model_key", "service_type", "capability_cache")
            }
            snapshot["credential_identity"] = model_credential_identity(entity)
            if entity.runtime_profile is not None:
                binding = self.session.scalar(
                    select(CanvasChannelModel).where(
                        CanvasChannelModel.model_config_id == entity.id
                    )
                )
                derived = SimpleNamespace(
                    **snapshot,
                    runtime_profile=deepcopy(entity.runtime_profile),
                    apikey=entity.apikey,
                    runtime_credentials_cipher=entity.runtime_credentials_cipher,
                )
                try:
                    refresh_runtime_model(derived, binding.channel_key if binding else "")
                except GenerationError:
                    return capabilities({})
                snapshot["capability_cache"] = derived.capability_cache
            return capabilities(snapshot)

    def _versioned_update(self, entity, values, expected_version):
        if entity.row_version != expected_version:
            raise Conflict("Model configuration version is stale")
        changes = {key: value for key, value in values.items() if getattr(entity, key) != value}
        if not changes:
            return entity
        if expected_version == 2**64 - 1:
            raise Conflict("Model configuration version is exhausted")
        changes.update(
            row_version=expected_version + 1,
            updated_at=max(utcnow(), entity.created_at or datetime.min),
            updated_by=None,
        )
        result = self.session.execute(
            update(self.model)
            .where(self.model.id == entity.id, self.model.row_version == expected_version)
            .values(**changes)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise Conflict("Model configuration version is stale")
        self.session.refresh(entity)
        return entity

    def update(self, identifier, payload):
        values = self._payload(self.update_schema, payload)
        expected = values.pop("row_version")
        with self._transaction():
            entity = self._active(identifier)
            if entity.row_version != expected:
                raise Conflict("Model configuration version is stale")
            binding = self.session.scalar(
                select(CanvasChannelModel).where(CanvasChannelModel.model_config_id == entity.id)
            )
            if binding is not None and binding.channel_key == "beefapi":
                for key in ("base_url", "model_key", "runtime_profile"):
                    proposed = values.get(key)
                    if key == "runtime_profile" and proposed is not None:
                        proposed = ModelRuntimeProfile.model_validate(proposed).model_dump(
                            mode="json", exclude_none=True
                        )
                    if key in values and proposed != getattr(entity, key):
                        raise BusinessError("BeefAPI 模型连接与协议由企业授权目录维护")
                if any(key in values for key in ("apikey", "secret_key")):
                    raise BusinessError("BeefAPI 凭据由企业授权维护")
            values = self._runtime_values(values, entity)
            if "apikey" in values:
                secret = values.pop("apikey")
                plaintext = secret.get_secret_value() if secret else ""
                if not plaintext:
                    # Explicit clearing must also work when the old key is unreadable.
                    values["apikey"] = None
                else:
                    cipher = self._key_cipher()
                    old_plaintext = None
                    if entity.apikey:
                        try:
                            old_plaintext = cipher.decrypt(entity.apikey)
                        except ValueError:
                            # An explicit replacement repairs an unreadable credential.
                            # Missing/invalid master-key configuration still fails above.
                            pass
                    if plaintext != old_plaintext:
                        values["apikey"] = self._encrypt_key(secret)
            if values.get("enabled") == 0:
                values["is_default"] = 0
            if any(
                key in values and values[key] != getattr(entity, key)
                for key in (
                    "runtime_profile",
                    "base_url",
                    "model_key",
                    "apikey",
                    "runtime_credentials_cipher",
                )
            ):
                values["capability_cache"] = None
            return self._read(self._versioned_update(entity, values, expected))

    def delete(self, identifier, row_version):
        """Soft-delete a configuration using the caller's last observed version."""
        expected = parse_identifier(row_version)
        with self._transaction():
            entity = self._active(identifier)
            self._versioned_update(entity, {"is_deleted": 1, "is_default": 0}, expected)

    def set_default(self, identifier, row_version):
        """Switch within one service type; retry the complete transaction up to three times."""
        identifier, expected = parse_identifier(identifier), parse_identifier(row_version)
        for attempt in range(3):
            try:
                with self._transaction():
                    try:
                        service_type = self.session.scalar(
                            select(self.model.service_type).where(self.model.id == identifier)
                        )
                        rows = list(
                            self.session.scalars(
                                select(self.model)
                                .where(self.model.service_type == service_type)
                                .order_by(self.model.id)
                                .with_for_update()
                                .execution_options(populate_existing=True)
                            )
                        )
                        entity = next((row for row in rows if row.id == identifier), None)
                        if entity is None or entity.is_deleted:
                            raise NotFound("Model configuration does not exist")
                        if entity.row_version != expected:
                            raise Conflict("Model configuration version is stale")
                        if not entity.enabled:
                            raise BusinessError("Disabled model cannot be the default")
                        for row in rows:
                            if row.id != identifier and row.is_default:
                                self._versioned_update(row, {"is_default": 0}, row.row_version)
                        return self._read(
                            self._versioned_update(entity, {"is_default": 1}, expected)
                        )
                    except IntegrityError as error:
                        if getattr(error.orig, "args", (None,))[0] != 1062:
                            raise
                        raise _RetryDefault from None
                    except OperationalError as error:
                        if getattr(error.orig, "args", (None,))[0] not in (1205, 1213, 3572):
                            raise
                        raise _RetryDefault from None
            except _RetryDefault:
                if attempt == 2:
                    raise Conflict("Concurrent default switch; retry the operation") from None
