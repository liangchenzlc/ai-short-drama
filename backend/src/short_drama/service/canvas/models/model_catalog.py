"""原版渠道目录原子保存；执行配置由服务端绑定，不信任客户端 logicalModelId。"""

import json
from copy import deepcopy

from short_drama.ai.adapters.catalog import capability_fingerprint
from short_drama.ai.adapters.text.chat_protocols import COMPATIBLE_CHAT_PROTOCOLS
from short_drama.ai.model_identity import model_credential_identity
from short_drama.ai.types import GenerationError
from short_drama.core.exceptions import ConfigurationError, NotFound, WorkflowError
from short_drama.dao.canvas.models.model_catalog import CanvasModelCatalogDAO
from short_drama.domain import AIModelConfig, CanvasChannelModel, CanvasModelCatalog
from short_drama.schemas.canvas.models.model_catalog import (
    MAX_CATALOG_BYTES,
    CanvasModelChannelInput,
)
from short_drama.service.ai_config.management.models import AIModelConfigService
from short_drama.service.ai_config.management.runtime import (
    PROTOCOL_ADAPTERS,
    decrypt_runtime_credentials,
    encrypt_runtime_credentials,
    legacy_runtime_profile,
    refresh_runtime_model,
)
from short_drama.service.base import BaseService, utcnow
from short_drama.utils.snowflake import next_id


def protocol_adapter(channel: dict, profile: dict) -> str | None:
    return PROTOCOL_ADAPTERS.get(profile.get("protocol") or channel.get("interfaceType"))


def model_capability_cache(config: AIModelConfig, channel: dict, profile: dict) -> dict | None:
    """仅从已保存渠道与当前执行配置派生缓存，不读取本次任务的浏览器能力。"""
    adapter = protocol_adapter(channel, profile)
    if not adapter or not config.base_url:
        return None
    if adapter == "gemini_generate_content.v1" and channel.get("apiFormat") != "openai":
        return None
    identity = model_credential_identity(config)
    snapshot = {
        "base_url": config.base_url,
        "model_key": config.model_key,
        "service_type": config.service_type,
    }
    cache = {"adapter": adapter, "fingerprint": capability_fingerprint(snapshot, identity)}
    from short_drama.service.canvas.generation.video.video_admission import VIDEO_ADAPTERS

    capability = profile.get("capabilityConfig") or {}
    if adapter in VIDEO_ADAPTERS:
        cache["canvas_channel_key"] = channel["id"]
        video = capability.get("video")
        if isinstance(video, dict):
            cache["canvas_video_capability"] = deepcopy(video)
        defaults = profile.get("defaultOptions") or {}
        if "variants" in defaults:
            cache["canvas_video_variants"] = deepcopy(defaults["variants"])
    if config.service_type == "text" and isinstance(capability.get("text"), dict):
        cache["canvas_text_capability"] = deepcopy(capability["text"])
    if adapter in {"claude_messages.v1", "gemini_generate_content.v1"}:
        cache["canvas_text_defaults"] = deepcopy(profile.get("defaultOptions") or {})
    protocol = profile.get("protocol") or channel.get("interfaceType")
    if protocol in COMPATIBLE_CHAT_PROTOCOLS:
        cache["canvas_text_protocol"] = protocol
        cache["canvas_text_defaults"] = deepcopy(profile.get("defaultOptions") or {})
    return cache


class CanvasModelCatalogService(BaseService):
    model = CanvasModelCatalog

    def __init__(self, session, settings=None, cipher=None):
        super().__init__(session)
        self.catalog_dao = CanvasModelCatalogDAO(session)
        self.configs = AIModelConfigService(session, settings=settings, cipher=cipher)

    @property
    def actor_id(self) -> int:
        actor = self.session.info.get("actor")
        if actor is None:
            raise WorkflowError("authentication_required", "请先登录", 401)
        return actor.user_id

    def _credentials(self, catalog: CanvasModelCatalog | None) -> dict:
        if catalog is None or catalog.credentials_cipher is None:
            return {}
        try:
            return json.loads(self.configs._key_cipher().decrypt(catalog.credentials_cipher))
        except (ValueError, TypeError):
            raise ConfigurationError("画布渠道凭据无法解密，请检查加密配置") from None

    def discovery_credentials(self, channel_id, credential_ref, base_url):
        from short_drama.service.ai_config.discovery.catalog import (
            ModelDiscoveryError,
            normalize_base_url,
        )

        with self._transaction(read_only=True):
            if credential_ref and not credential_ref.startswith("host:"):
                raise NotFound("本人渠道凭据不存在")
            referenced = credential_ref.removeprefix("host:") if credential_ref else None
            key = channel_id or (
                f"host-{referenced}" if referenced and referenced.isdecimal() else referenced
            )
            if not key:
                return None
            if key.startswith("host-"):
                identifier = key.removeprefix("host-")
                if not identifier.isdecimal() or referenced and referenced != identifier:
                    raise NotFound("本人模型配置不存在")
                config = self._require(AIModelConfig, identifier, for_update=False)
                if config.is_deleted:
                    raise NotFound("本人模型配置不存在")
                if normalize_base_url(config.base_url) != normalize_base_url(base_url):
                    raise ModelDiscoveryError("key_address_changed", 400)
                try:
                    return {
                        "apiKey": self.configs._key_cipher().decrypt(config.apikey)
                        if config.apikey
                        else "",
                        "headers": decrypt_runtime_credentials(config, self.configs._key_cipher())[
                            "headers"
                        ],
                    }
                except ValueError:
                    raise ConfigurationError("本人模型凭据无法解密") from None
            if credential_ref:
                raise WorkflowError(
                    "canvas_model_legacy_credential_reference",
                    "旧渠道凭据引用已停用，请使用宿主 AI 配置中的模型身份",
                    409,
                )
            return None

    @staticmethod
    def _secret(channel, field, previous):
        alias = {"api_key": "apiKey", "secret_key": "secretKey"}[field]
        value = getattr(channel, field)
        if alias in channel.clear_credentials:
            return ""
        if value is not None and value.get_secret_value():
            return value.get_secret_value()
        return previous.get(alias, "")

    def _channel(self, channel, previous):
        secrets = {
            "apiKey": self._secret(channel, "api_key", previous),
            "secretKey": self._secret(channel, "secret_key", previous),
            "headers": {},
        }
        old_headers = (
            previous.get("headers", {}) if "headers" not in channel.clear_credentials else {}
        )
        old_headers = {name.lower(): value for name, value in old_headers.items()}
        for header in channel.headers:
            secrets["headers"][header.name] = header.value.get_secret_value() or old_headers.get(
                header.name.lower(), ""
            )
        public = channel.model_dump(mode="json", by_alias=True, exclude_none=True)
        for key in ("apiKey", "secretKey", "clearCredentials"):
            public.pop(key, None)
        public.update(
            apiKey="",
            secretKey="",
            hasApiKey=bool(secrets["apiKey"]),
            hasSecretKey=bool(secrets["secretKey"]),
            credentialRef=f"host:{channel.source_key}",
            headers=[{"name": item.name, "value": ""} for item in channel.headers],
        )
        return public, secrets

    def _update_model(self, channel, profile, secret, config):
        existing = config is not None
        if existing:
            return config
        changed = False
        values = {
            "name": profile.get("displayName") or profile["model"][:120],
            "model_key": profile["model"],
            "service_type": profile["capability"],
            "provider": (
                profile.get("protocol") or channel.get("interfaceType") or channel["apiFormat"]
            ),
            "base_url": channel["baseUrl"],
            "enabled": int(channel["enabled"]),
            "is_deleted": 0,
        }
        if config is None:
            config = AIModelConfig(
                id=next_id(),
                owner_user_id=self.actor_id,
                apikey=None,
                is_default=0,
                row_version=1,
                created_at=utcnow(),
                updated_at=utcnow(),
                created_by=self.actor_id,
                updated_by=self.actor_id,
                **values,
            )
            self.session.add(config)
        plaintext = None
        if config.apikey:
            try:
                plaintext = self.configs._key_cipher().decrypt(config.apikey)
            except ValueError:
                plaintext = None
        if secret["apiKey"] != (plaintext or ""):
            from pydantic import SecretStr

            config.apikey = self.configs._encrypt_key(SecretStr(secret["apiKey"]))
            changed = True
        runtime = legacy_runtime_profile(channel, profile)
        changed = changed or config.runtime_profile != runtime
        config.runtime_profile = runtime
        if not existing:
            extended = encrypt_runtime_credentials(
                secret.get("secretKey", ""),
                secret.get("headers", {}),
                self.configs._key_cipher()
                if secret.get("secretKey") or secret.get("headers")
                else None,
            )
            changed = changed or config.runtime_credentials_cipher != extended
            config.runtime_credentials_cipher = extended
        cache = model_capability_cache(config, channel, profile)
        changed = changed or config.capability_cache != cache
        config.capability_cache = cache
        if changed and config not in self.session.new:
            config.row_version += 1
            config.updated_at, config.updated_by = utcnow(), self.actor_id
        return config

    def save_locked(self, channels: list[CanvasModelChannelInput]) -> bool:
        catalog = self.catalog_dao.catalog(self.actor_id, lock=True)
        previous = self._credentials(catalog)
        public_channels, credentials = [], {}
        for channel in channels:
            public, secret = self._channel(channel, previous.get(channel.source_key, {}))
            public_channels.append(public)
            credentials[channel.source_key] = secret
        return self._persist_locked(public_channels, credentials)

    def _persist_locked(self, public_channels: list[dict], credentials: dict) -> bool:
        catalog = self.catalog_dao.catalog(self.actor_id, lock=True)
        previous = self._credentials(catalog)
        bindings = self.catalog_dao.bindings(self.actor_id, lock=True)
        bound = {(item.channel_key, item.model_key): item for item in bindings}
        configs = self.catalog_dao.models([item.model_config_id for item in bindings], lock=True)
        for public in public_channels:
            secret = credentials.get(public["id"])
            for profile in public["modelProfiles"]:
                if profile["model"] not in public["models"] or not profile.get("capability"):
                    profile.pop("logicalModelId", None)
                    continue
                key = (public["id"], profile["model"])
                binding = bound.get(key)
                config = self._update_model(
                    public,
                    profile,
                    secret,
                    configs.get(binding.model_config_id) if binding else None,
                )
                if binding is None:
                    binding = CanvasChannelModel(
                        id=next_id(),
                        user_id=self.actor_id,
                        channel_key=key[0],
                        model_key=key[1],
                        model_config_id=config.id,
                        runtime_migrated_at=utcnow(),
                        created_at=utcnow(),
                        updated_at=utcnow(),
                        created_by=self.actor_id,
                        updated_by=self.actor_id,
                    )
                    self.session.add(binding)
                profile["logicalModelId"] = str(config.id)
        merged_size = len(
            json.dumps(
                {"channels": public_channels, "credentials": credentials},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if merged_size > MAX_CATALOG_BYTES:
            raise WorkflowError("canvas_model_catalog_too_large", "保存后的模型目录超过 2 MiB", 422)
        changed = (
            catalog is None
            and bool(public_channels)
            or catalog is not None
            and (catalog.channels_json != public_channels or previous != credentials)
        )
        if changed:
            ciphertext = (
                self.configs._key_cipher().encrypt(json.dumps(credentials, separators=(",", ":")))
                if credentials
                else None
            )
            if catalog is None:
                catalog = CanvasModelCatalog(
                    id=next_id(),
                    user_id=self.actor_id,
                    channels_json=public_channels,
                    credentials_cipher=ciphertext,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                    created_by=self.actor_id,
                    updated_by=self.actor_id,
                )
                self.session.add(catalog)
            else:
                catalog.channels_json, catalog.credentials_cipher = public_channels, ciphertext
                catalog.updated_at, catalog.updated_by = utcnow(), self.actor_id
        return bool(changed)

    def refresh_runtime_model_locked(self, config: AIModelConfig) -> bool:
        """新准入只读取宿主真值；旧目录不能复活用户清除的运行配置。"""
        binding = self.catalog_dao.binding(config.id)
        if (
            config.owner_user_id != self.actor_id
            or config.is_deleted
            or not config.enabled
            or binding is not None
            and binding.user_id != self.actor_id
        ):
            raise NotFound("本人模型不可用")
        if getattr(config, "runtime_profile", None) is not None:
            try:
                channel_key = (
                    binding.channel_key
                    if binding
                    else (config.capability_cache or {}).get("canvas_channel_key", "")
                )
                changed = refresh_runtime_model(config, channel_key)
            except GenerationError:
                raise WorkflowError(
                    "canvas_generation_protocol_unsupported",
                    "该模型协议尚未接通，未发送供应商请求",
                    422,
                ) from None
            if changed:
                config.row_version += 1
                config.updated_at, config.updated_by = utcnow(), self.actor_id
            return changed
        return False

    def require_protocol_locked(self, model_id: int, adapter: str) -> None:
        binding = self.catalog_dao.binding(model_id)
        config = self.catalog_dao.models([model_id], lock=True).get(model_id)
        if config is not None and getattr(config, "runtime_profile", None) is not None:
            from short_drama.service.ai_config.management.runtime import runtime_protocol_adapter

            if (
                runtime_protocol_adapter(
                    config.runtime_profile,
                    binding.channel_key
                    if binding
                    else (config.capability_cache or {}).get("canvas_channel_key", ""),
                    config.model_key,
                    config.service_type,
                )
                != adapter
            ):
                raise WorkflowError(
                    "canvas_generation_protocol_unsupported", "模型协议与执行适配器不一致", 422
                )
            return

    def runtime_credentials_locked(self, model_id: int, adapter: str) -> dict | None:
        """仅在已授权准入事务中读取本人渠道执行凭据，不能作为 HTTP 输出。"""
        self.require_protocol_locked(model_id, adapter)
        binding = self.catalog_dao.binding(model_id)
        config = self.catalog_dao.models([model_id], lock=True).get(model_id)
        if config is not None:
            cipher = self.configs._key_cipher()
            extended = decrypt_runtime_credentials(config, cipher)
            try:
                key = cipher.decrypt(config.apikey) if config.apikey else ""
            except ValueError:
                raise ConfigurationError("模型凭据无法解密，请检查配置") from None
            return {"apiKey": key, **extended}
        if binding is not None:
            raise NotFound("本人模型执行配置不存在")
        return None
