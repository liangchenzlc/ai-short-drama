"""原版渠道目录原子保存；执行配置由服务端绑定，不信任客户端 logicalModelId。"""

import hashlib
import json
from copy import deepcopy

from short_drama.ai.adapters import capability_fingerprint
from short_drama.core.exceptions import ConfigurationError, NotFound, WorkflowError
from short_drama.dao.canvas_model_catalog_dao import CanvasModelCatalogDAO
from short_drama.domain import AIModelConfig, CanvasChannelModel, CanvasModelCatalog
from short_drama.schemas.canvas_model_catalog import MAX_CATALOG_BYTES, CanvasModelChannelInput
from short_drama.utils.snowflake import next_id

from .ai_model_config_service import AIModelConfigService
from .base import BaseService, utcnow

PROTOCOL_ADAPTERS = {
    "chat-completion": "openai_chat.v1",
    "openai-response": "openai_responses.v1",
    "openai-image": "openai_images.v1",
    "volcengine-ark-image": "ark_images.v1",
    "qwen-image": "dashscope_images.v1",
    "dashscope-qwen-image": "dashscope_images.v1",
    "openai-audio": "openai_speech.v1",
    "volcengine-ark-video": "ark_video.v1",
    "qwen-video": "dashscope_video.v1",
    "dashscope-wan-video": "dashscope_video.v1",
    "newapi": "canvas_openai_videos.v1",
    "openai-video": "canvas_openai_videos.v1",
    "openai-videos": "canvas_openai_videos.v1",
    "newapi-channel-2": "canvas_newapi_video_generations.v1",
}


def protocol_adapter(channel: dict, profile: dict) -> str | None:
    if channel.get("id") == "beefapi" and profile.get("capability") == "video":
        from short_drama.ai.canvas_video_adapters import is_seedance_model

        if is_seedance_model(profile["model"]):
            return "canvas_beefapi_seedance_video.v1"
    return PROTOCOL_ADAPTERS.get(profile.get("protocol") or channel.get("interfaceType"))


def model_capability_cache(config: AIModelConfig, channel: dict, profile: dict) -> dict | None:
    """仅从已保存渠道与当前执行配置派生缓存，不读取本次任务的浏览器能力。"""
    adapter = protocol_adapter(channel, profile)
    if not adapter or not config.base_url:
        return None
    identity = hashlib.sha256((config.apikey or "").encode()).hexdigest()
    snapshot = {
        "base_url": config.base_url,
        "model_key": config.model_key,
        "service_type": config.service_type,
    }
    cache = {"adapter": adapter, "fingerprint": capability_fingerprint(snapshot, identity)}
    from .canvas_video_admission import VIDEO_ADAPTERS

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

    def read_locked(self) -> list[dict]:
        catalog = self.catalog_dao.catalog(self.actor_id)
        channels = deepcopy(catalog.channels_json) if catalog else []
        if not any(item["id"] == "beefapi" for item in channels):
            from short_drama.ai.canvas_beefapi_client import canonical_origin

            channels.append(
                {
                    "id": "beefapi",
                    "name": "BeefAPI",
                    "baseUrl": canonical_origin(
                        getattr(self.configs.settings, "canvas_beefapi_test_origin", "")
                    ),
                    "apiFormat": "openai",
                    "scope": "user",
                    "pinned": True,
                    "presetVersion": 1,
                    "models": [],
                    "modelProfiles": [],
                    "apiKey": "",
                    "secretKey": "",
                    "headers": [],
                    "enabled": True,
                    "hasApiKey": False,
                    "hasSecretKey": False,
                }
            )
        bindings = self.catalog_dao.bindings(self.actor_id)
        ids = {(item.channel_key, item.model_key): item.model_config_id for item in bindings}
        models = self.catalog_dao.models(list(ids.values()))
        for channel in channels:
            channel.pop("_assistantAuthorizationId", None)
            for profile in channel["modelProfiles"]:
                if profile["model"] not in channel["models"] or not profile.get("capability"):
                    profile.pop("logicalModelId", None)
                    continue
                identifier = ids.get((channel["id"], profile["model"]))
                config = models.get(identifier)
                if config is None:
                    raise ConfigurationError("画布模型目录的执行配置缺失")
                profile["logicalModelId"] = str(identifier)
        return channels

    def _credentials(self, catalog: CanvasModelCatalog | None) -> dict:
        if catalog is None or catalog.credentials_cipher is None:
            return {}
        try:
            return json.loads(self.configs._key_cipher().decrypt(catalog.credentials_cipher))
        except (ValueError, TypeError):
            raise ConfigurationError("画布渠道凭据无法解密，请检查加密配置") from None

    def discovery_credentials(self, channel_id, credential_ref, base_url):
        from .model_discovery_service import ModelDiscoveryError, normalize_base_url

        with self._transaction(read_only=True):
            if channel_id == "beefapi" or credential_ref == "beefapi-enterprise":
                if channel_id not in {None, "beefapi"} or credential_ref not in {
                    None,
                    "beefapi-enterprise",
                }:
                    raise NotFound("本人 BeefAPI 连接不存在")
                return self._beefapi_credentials_locked(base_url)
            if credential_ref and not credential_ref.startswith("host:"):
                raise NotFound("本人渠道凭据不存在")
            referenced = credential_ref.removeprefix("host:") if credential_ref else None
            key = channel_id or referenced
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
                        "headers": {},
                    }
                except ValueError:
                    raise ConfigurationError("本人模型凭据无法解密") from None
            if referenced and referenced != key:
                raise NotFound("本人渠道凭据不存在")
            catalog = self.catalog_dao.catalog(self.actor_id)
            channel = next(
                (item for item in (catalog.channels_json if catalog else []) if item["id"] == key),
                None,
            )
            if channel is None:
                if credential_ref:
                    raise NotFound("本人渠道凭据不存在")
                return None
            if normalize_base_url(channel["baseUrl"]) != normalize_base_url(base_url):
                raise ModelDiscoveryError("key_address_changed", 400)
            secret = self._credentials(catalog).get(key, {})
            return {"apiKey": secret.get("apiKey", ""), "headers": secret.get("headers", {})}

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
        else:
            if config.owner_user_id != self.actor_id:
                raise NotFound("本人渠道执行配置不存在")
            old_service_type = config.service_type
            changed = any(getattr(config, key) != value for key, value in values.items())
            for key, value in values.items():
                setattr(config, key, value)
            if not config.enabled or old_service_type != profile["capability"]:
                config.is_default = 0
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
        managed = next(
            (
                deepcopy(item)
                for item in (catalog.channels_json if catalog else [])
                if item["id"] == "beefapi"
            ),
            None,
        )
        public_channels, credentials = [], {}
        for channel in channels:
            if channel.source_key == "beefapi":
                if managed is None:
                    managed = next(item for item in self.read_locked() if item["id"] == "beefapi")
                _, secret = self._channel(channel, previous.get("beefapi", {}))
                managed["enabled"] = channel.enabled
                managed["headers"] = [{"name": item.name, "value": ""} for item in channel.headers]
                public_channels.append(managed)
                if secret["headers"]:
                    credentials["beefapi"] = {
                        "apiKey": "",
                        "secretKey": "",
                        "headers": secret["headers"],
                    }
            else:
                public, secret = self._channel(channel, previous.get(channel.source_key, {}))
                public_channels.append(public)
                credentials[channel.source_key] = secret
        if managed is not None and not any(item["id"] == "beefapi" for item in public_channels):
            public_channels.insert(0, managed)
            if "beefapi" in previous:
                credentials["beefapi"] = previous["beefapi"]
        return self._persist_locked(public_channels, credentials)

    def _persist_locked(
        self, public_channels: list[dict], credentials: dict, *, managed_secret=None
    ) -> bool:
        catalog = self.catalog_dao.catalog(self.actor_id, lock=True)
        previous = self._credentials(catalog)
        bindings = self.catalog_dao.bindings(self.actor_id, lock=True)
        bound = {(item.channel_key, item.model_key): item for item in bindings}
        configs = self.catalog_dao.models([item.model_config_id for item in bindings], lock=True)
        active = set()
        for public in public_channels:
            secret = credentials.get(public["id"])
            for profile in public["modelProfiles"]:
                if profile["model"] not in public["models"] or not profile.get("capability"):
                    profile.pop("logicalModelId", None)
                    continue
                key = (public["id"], profile["model"])
                binding = bound.get(key)
                if public["id"] == "beefapi" and managed_secret is None:
                    config = configs.get(binding.model_config_id) if binding else None
                    if config is None:
                        raise ConfigurationError("本人 BeefAPI 执行配置缺失")
                    if bool(config.enabled) != public["enabled"]:
                        config.enabled = int(public["enabled"])
                        config.is_default = 0
                        config.row_version += 1
                        config.updated_at, config.updated_by = utcnow(), self.actor_id
                    profile["logicalModelId"] = str(config.id)
                    active.add(key)
                    continue
                config = self._update_model(
                    public,
                    profile,
                    managed_secret if public["id"] == "beefapi" else secret,
                    configs.get(binding.model_config_id) if binding else None,
                )
                if binding is None:
                    binding = CanvasChannelModel(
                        id=next_id(),
                        user_id=self.actor_id,
                        channel_key=key[0],
                        model_key=key[1],
                        model_config_id=config.id,
                        created_at=utcnow(),
                        updated_at=utcnow(),
                        created_by=self.actor_id,
                        updated_by=self.actor_id,
                    )
                    self.session.add(binding)
                profile["logicalModelId"] = str(config.id)
                active.add(key)
        merged_size = len(
            json.dumps(
                {"channels": public_channels, "credentials": credentials},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if merged_size > MAX_CATALOG_BYTES:
            raise WorkflowError("canvas_model_catalog_too_large", "保存后的模型目录超过 2 MiB", 422)
        for key, binding in bound.items():
            if key not in active:
                config = configs.get(binding.model_config_id)
                if config is not None and not config.is_deleted:
                    config.is_deleted, config.enabled, config.is_default = 1, 0, 0
                    config.row_version += 1
                    config.updated_at = utcnow()
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

    def _beefapi_credentials_locked(
        self, base_url=None, *, catalog: CanvasModelCatalog | None = None
    ) -> dict:
        from .canvas_beefapi_service import CanvasBeefAPIService

        service = CanvasBeefAPIService(
            self.session, settings=self.configs.settings, cipher=self.configs._key_cipher()
        )
        credential = service.credentials_locked(base_url=base_url)
        if catalog is None:
            catalog = self.catalog_dao.catalog(self.actor_id)
        credential["headers"] = self._credentials(catalog).get("beefapi", {}).get("headers", {})
        return credential

    def _advance_workspace_locked(self, *, authorization_id=None, preferred="") -> None:
        from short_drama.dao.canvas_workspace_dao import CanvasWorkspaceDAO
        from short_drama.domain.canvas import CanvasWorkspaceUserState

        dao = CanvasWorkspaceDAO(self.session)
        state = dao.preferences(self.actor_id, lock=True)
        if state is None:
            state = CanvasWorkspaceUserState(
                id=next_id(),
                user_id=self.actor_id,
                preferences_json={},
                row_version=1,
                created_at=utcnow(),
                updated_at=utcnow(),
                created_by=self.actor_id,
                updated_by=self.actor_id,
            )
            self.session.add(state)
        else:
            state.row_version += 1
            state.updated_at, state.updated_by = utcnow(), self.actor_id
        if authorization_id and preferred:
            state.preferences_json = {**state.preferences_json, "assistantModel": preferred}

    def apply_beefapi_catalog_locked(
        self,
        models: list[dict],
        credential: dict,
        *,
        account_changed: bool,
        authorization_id: str | None,
    ) -> None:
        from .canvas_beefapi_catalog import catalog_profile, merge_catalog

        catalog = self.catalog_dao.catalog(self.actor_id, lock=True)
        channels = deepcopy(catalog.channels_json) if catalog else []
        current = next((item for item in channels if item["id"] == "beefapi"), None)
        base_url = credential["baseUrl"].removesuffix("/v1")
        if current is None:
            current = {
                "id": "beefapi",
                "name": "BeefAPI",
                "scope": "user",
                "pinned": True,
                "presetVersion": 1,
                "enabled": True,
                "apiFormat": "openai",
                "baseUrl": base_url,
                "apiKey": "",
                "secretKey": "",
                "hasApiKey": True,
                "hasSecretKey": False,
                "credentialRef": "beefapi-enterprise",
                "headers": [],
                "models": [],
                "modelProfiles": [],
            }
            channels.insert(0, current)
        next_channel = merge_catalog(current, models, replace=account_changed)
        next_channel.update(
            baseUrl=base_url,
            apiKey="",
            secretKey="",
            hasApiKey=True,
            hasSecretKey=False,
            credentialRef="beefapi-enterprise",
        )
        new_authorization = bool(
            authorization_id and current.get("_assistantAuthorizationId") != authorization_id
        )
        preferred = next_channel.get("modelAliases", {}).get("gpt-6-astra", "gpt-6-astra")
        fresh_available = any(
            item["id"] == preferred
            and catalog_profile(item)["capability"] == "text"
            and catalog_profile(item)["protocol"]
            in {"chat-completion", "claude-api", "responses", "openai-response"}
            for item in models
        )
        if new_authorization:
            next_channel["_assistantAuthorizationId"] = authorization_id
        channels = [next_channel if item["id"] == "beefapi" else item for item in channels]
        secrets = self._credentials(catalog)
        changed = self._persist_locked(
            channels,
            secrets,
            managed_secret={
                "apiKey": credential["apiKey"],
                "headers": secrets.get("beefapi", {}).get("headers", {}),
                "secretKey": "",
            },
        )
        if changed or new_authorization and fresh_available:
            self._advance_workspace_locked(
                authorization_id=authorization_id
                if new_authorization and fresh_available
                else None,
                preferred="beefapi::" + preferred,
            )

    def clear_beefapi_locked(self) -> None:
        catalog = self.catalog_dao.catalog(self.actor_id, lock=True)
        if catalog is None:
            return
        channels = deepcopy(catalog.channels_json)
        for channel in channels:
            if channel["id"] == "beefapi":
                channel.update(
                    models=[],
                    modelProfiles=[],
                    apiKey="",
                    secretKey="",
                    hasApiKey=False,
                    hasSecretKey=False,
                )
                channel.pop("credentialRef", None)
        secrets = self._credentials(catalog)
        if self._persist_locked(channels, secrets):
            self._advance_workspace_locked()

    def refresh_runtime_model_locked(self, config: AIModelConfig) -> bool:
        """新准入刷新本人绑定的派生缓存；历史任务仍使用原冻结快照。"""
        binding = self.catalog_dao.binding(config.id)
        if binding is None:
            return False
        if (
            config.owner_user_id != self.actor_id
            or binding.user_id != self.actor_id
            or config.is_deleted
            or not config.enabled
            or config.model_key != binding.model_key
        ):
            raise NotFound("本人渠道模型不可用")
        catalog = self.catalog_dao.catalog(self.actor_id, lock=True)
        channel = next(
            (
                item
                for item in (catalog.channels_json if catalog else [])
                if item["id"] == binding.channel_key
            ),
            None,
        )
        profile = next(
            (
                item
                for item in (channel["modelProfiles"] if channel else [])
                if item["model"] == binding.model_key
            ),
            None,
        )
        if (
            not channel
            or not channel["enabled"]
            or binding.model_key not in channel["models"]
            or not profile
            or profile.get("capability") != config.service_type
        ):
            raise NotFound("本人渠道模型不可用")
        cache = model_capability_cache(config, channel, profile)
        if cache is None:
            raise WorkflowError(
                "canvas_generation_protocol_unsupported",
                "该渠道协议尚未接通，未发送供应商请求",
                422,
            )
        if config.capability_cache == cache:
            return False
        config.capability_cache = cache
        config.row_version += 1
        config.updated_at, config.updated_by = utcnow(), self.actor_id
        return True

    def require_protocol_locked(self, model_id: int, adapter: str) -> None:
        binding = self.catalog_dao.binding(model_id)
        if binding is None:
            return
        catalog = self.catalog_dao.catalog(self.actor_id, lock=True)
        channel = next(
            (
                item
                for item in (catalog.channels_json if catalog else [])
                if item["id"] == binding.channel_key
            ),
            None,
        )
        profile = next(
            (
                item
                for item in (channel["modelProfiles"] if channel else [])
                if item["model"] == binding.model_key
            ),
            None,
        )
        if not channel or not profile or not channel["enabled"]:
            raise NotFound("本人渠道模型不可用")
        if protocol_adapter(channel, profile) != adapter:
            raise WorkflowError(
                "canvas_generation_protocol_unsupported",
                "该渠道协议尚未接通，未发送供应商请求",
                422,
            )

    def runtime_credentials_locked(self, model_id: int, adapter: str) -> dict | None:
        """仅在已授权准入事务中读取本人渠道执行凭据，不能作为 HTTP 输出。"""
        self.require_protocol_locked(model_id, adapter)
        binding = self.catalog_dao.binding(model_id)
        if binding is None:
            return None
        catalog = self.catalog_dao.catalog(self.actor_id, lock=True)
        if binding.channel_key == "beefapi":
            config = self.catalog_dao.models([model_id]).get(model_id)
            if config is None:
                raise NotFound("本人 BeefAPI 执行配置不存在")
            return self._beefapi_credentials_locked(config.base_url, catalog=catalog)
        secrets = self._credentials(catalog).get(binding.channel_key)
        if secrets is None:
            raise ConfigurationError("本人画布渠道凭据缺失")
        return deepcopy(secrets)
