"""Encrypted model settings with optimistic versions and atomic default switching."""

import hashlib
from datetime import datetime

from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from short_drama.core.config import Settings
from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import BusinessError, ConfigurationError, Conflict, NotFound
from short_drama.domain import AIModelConfig
from short_drama.schemas.ai_model_config import (
    AIModelConfigCreate,
    AIModelConfigRead,
    AIModelConfigUpdate,
)
from short_drama.schemas.base import parse_identifier

from .base import BaseService, utcnow


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
        return super()._read(entity).model_copy(update={"has_api_key": bool(entity.apikey)})

    def _active(self, identifier):
        entity = self._get_locked(identifier)
        if entity.is_deleted:
            raise NotFound("Model configuration does not exist")
        return entity

    def create(self, payload):
        values = self._payload(self.create_schema, payload)
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
        from short_drama.ai import capabilities

        with self._transaction():
            entity = self._require(self.model, identifier, for_update=False)
            if entity.is_deleted:
                raise NotFound("Model configuration does not exist")
            snapshot = {
                key: getattr(entity, key)
                for key in ("base_url", "model_key", "service_type", "capability_cache")
            }
            snapshot["credential_identity"] = hashlib.sha256(
                (entity.apikey or "").encode()
            ).hexdigest()
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
            if "apikey" in values:
                secret = values.pop("apikey")
                plaintext = secret.get_secret_value() if secret else ""
                try:
                    old_plaintext = (
                        self._key_cipher().decrypt(entity.apikey) if entity.apikey else ""
                    )
                except ValueError:
                    raise ConfigurationError("Stored API key cannot be decrypted") from None
                if plaintext != old_plaintext:
                    values["apikey"] = self._encrypt_key(secret)
            if values.get("enabled") == 0:
                values["is_default"] = 0
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
