"""Encrypted model settings with optimistic versions and atomic default switching."""

from copy import deepcopy
from datetime import datetime

from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from short_drama.ai.adapters.model_protocols import (
    interface_options,
    interface_profile,
    public_interface_type,
)
from short_drama.ai.model_identity import model_credential_identity
from short_drama.ai.types import GenerationError
from short_drama.core.config import Settings
from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import (
    BusinessError,
    ConfigurationError,
    Conflict,
    GenerationRequestError,
    NotFound,
    WorkflowError,
)
from short_drama.core.identity import require_actor
from short_drama.dao.ai_config.management.models import AIModelConfigDAO
from short_drama.domain import AIModelConfig, CanvasChannelModel
from short_drama.schemas.ai_config.management.models import (
    AIModelConfigCreate,
    AIModelConfigRead,
    AIModelConfigUpdate,
)
from short_drama.schemas.ai_config.management.runtime_profile import ModelRuntimeProfile
from short_drama.schemas.base import parse_identifier
from short_drama.service.ai_config.management.runtime import (
    decrypt_runtime_credentials,
    encrypt_runtime_credentials,
    model_runtime_public,
)
from short_drama.service.base import BaseService, utcnow
from short_drama.utils.json_digest import digest


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
        self.dao = AIModelConfigDAO(session)

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
            {
                **super()._read(entity).model_dump(),
                "has_api_key": bool(entity.apikey),
                "interface_type": public_interface_type(
                    (entity.runtime_profile or {}).get("protocol", "auto"), entity.service_type
                ),
                **public,
            }
        )

    def _runtime_values(self, values, entity=None):
        if "interface_type" in values:
            selected = values.pop("interface_type")
            if selected is not None:
                kind = values.get("service_type") or entity.service_type
                try:
                    profile = interface_profile(selected, kind)
                except GenerationError as error:
                    raise GenerationRequestError(error.code) from None
                values["runtime_profile"] = {
                    **deepcopy(
                        values.get("runtime_profile")
                        or (entity.runtime_profile if entity else None)
                        or {}
                    ),
                    **profile,
                }
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

    def create(self, payload, key=None):
        return self.create_once(payload, key)[0]

    def create_once(self, payload, key):
        values = self._payload(self.create_schema, payload)
        values["name"] = values.get("name", "").strip() or values["model_key"]
        original = deepcopy(values)
        for field in ("apikey", "secret_key"):
            if original.get(field) is not None:
                original[field] = original[field].get_secret_value()
        if original.get("headers") is not None:
            original["headers"] = [
                {"name": item["name"], "value": item["value"].get_secret_value()}
                for item in original["headers"]
            ]
        request_hash = digest(original)
        values = self._runtime_values(values)
        if "apikey" in values:
            values["apikey"] = self._encrypt_key(values["apikey"])
        with self._transaction():
            if key is not None:
                owner = require_actor(self.session).user_id
                self.dao.lock_owner(owner)
                previous = self.dao.created_request(owner, key)
                if previous is not None:
                    if previous.create_hash != request_hash:
                        raise Conflict("相同创建标识不能更改模型配置")
                    if previous.is_deleted:
                        raise Conflict("原次创建的模型已删除，请重新开始创建")
                    return self._read(previous), False
                values.update(create_key=key, create_hash=request_hash)
            return self._read(self.dao.create(self._creation_audit(values))), True

    def options(self, service_type):
        return interface_options(service_type)

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
        from short_drama.service.ai_config.discovery.catalog import (
            ModelDiscoveryError,
            normalize_base_url,
        )

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
        from short_drama.service.ai_config.management.runtime import (
            resolved_runtime_capability_cache,
        )

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
                try:
                    snapshot["capability_cache"] = resolved_runtime_capability_cache(
                        entity, binding.channel_key if binding else ""
                    )
                except GenerationError:
                    return capabilities({})
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
            values = self._runtime_values(values, entity)
            if "name" in values:
                values["name"] = values["name"].strip() or values.get("model_key", entity.model_key)
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
