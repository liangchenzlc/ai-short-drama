"""可恢复的本人设备授权；HTTP 在事务外，所有状态应用必须仍持有租约。"""

import json
import logging
import time
from copy import deepcopy
from datetime import UTC, timedelta
from uuid import uuid4

from short_drama.ai.canvas_beefapi_client import (
    CREDENTIAL_REF,
    CanvasBeefAPIClient,
    CanvasBeefAPIError,
)
from short_drama.core.exceptions import ConfigurationError, NotFound, WorkflowError
from short_drama.core.identity import ActorContext, require_actor
from short_drama.dao.canvas_beefapi_dao import CanvasBeefAPIDAO
from short_drama.domain.canvas_beefapi_connection import CanvasBeefAPIConnection
from short_drama.schemas.canvas_beefapi import CanvasBeefAPISummary
from short_drama.utils.snowflake import next_id

from .base import BaseService, utcnow
from .canvas_model_catalog_service import CanvasModelCatalogService

log = logging.getLogger(__name__)
LEASE_SECONDS = 180


def disconnected_state():
    return {"state": "disconnected", "balance": "unknown", "acked": False, "catalogOk": False}


class CanvasBeefAPIService(BaseService):
    model = CanvasBeefAPIConnection

    def __init__(
        self, session, settings=None, *, client=None, cipher=None, now=utcnow, sleep=time.sleep
    ):
        super().__init__(session)
        self.settings = settings
        self.client = client or CanvasBeefAPIClient(
            test_origin=getattr(settings, "canvas_beefapi_test_origin", "")
        )
        self.cipher = cipher
        self.now, self.sleep = now, sleep
        self.connections = CanvasBeefAPIDAO(session)
        self.catalog = CanvasModelCatalogService(session, settings=settings, cipher=cipher)

    @property
    def actor_id(self):
        return require_actor(self.session).user_id

    def _cipher(self):
        return self.cipher or self.catalog.configs._key_cipher()

    def _secrets(self, row):
        if row is None or not row.secrets_cipher:
            return {}
        try:
            value = json.loads(self._cipher().decrypt(row.secrets_cipher))
            if not isinstance(value, dict) or set(value) - {"apiKey", "deviceCode"}:
                raise ValueError
            if any(not isinstance(item, str) for item in value.values()):
                raise ValueError
            return value
        except (ValueError, TypeError):
            raise ConfigurationError("企业连接凭据无法解密，请检查加密配置") from None

    def _summary(self, state, secrets):
        public = {
            key: deepcopy(state[key])
            for key in (
                "state",
                "balance",
                "account",
                "keyName",
                "tokenId",
                "market",
                "errorReason",
                "connectedAt",
            )
            if key in state
        }
        has_key = bool(secrets.get("apiKey"))
        public.update(
            enterpriseOrigin=self.client.origin,
            hasCredential=has_key,
            catalogFailed=state["state"] == "catalog_failed"
            or (has_key and not state.get("catalogOk") and state["state"] != "pending"),
        )
        if has_key:
            public.update(credentialRef=CREDENTIAL_REF, walletUrl=self.client.wallet_url)
        if state["state"] == "pending" and state.get("device"):
            device = state["device"]
            public.update(
                userCode=device["userCode"],
                verificationUri=device["verificationUri"],
                expiresAt=device["expiresAt"],
            )
        return CanvasBeefAPISummary.model_validate(public).model_dump(
            mode="json", by_alias=True, exclude_none=True
        )

    def status(self, *, recover=False):
        if recover:
            self.recover_one()
        with self._transaction(read_only=True):
            row = self.connections.connection(self.actor_id)
            return self._summary(
                row.state_json if row else disconnected_state(), self._secrets(row)
            )

    def credentials_locked(self, base_url=None):
        """仅供已授权服务端目录/执行准入，不能暴露给 HTTP 输出。"""
        row = self.connections.connection(self.actor_id)
        secrets = self._secrets(row)
        if row is None or not secrets.get("apiKey"):
            raise WorkflowError("canvas_beefapi_not_connected", "请先完成 BeefAPI 连接", 409)
        if row.state_json["state"] == "revoked":
            raise WorkflowError("canvas_beefapi_revoked", "连接已失效，请重新连接", 409)
        origin = row.state_json.get("enterpriseOrigin", self.client.origin)
        if origin != self.client.origin:
            raise ConfigurationError("已保存的企业源与当前配置不同，请重新连接")
        if base_url is not None and base_url.strip().rstrip("/") not in {origin, origin + "/v1"}:
            raise WorkflowError("canvas_beefapi_origin_mismatch", "企业凭据不能用于其他地址", 422)
        return {
            "apiKey": secrets["apiKey"],
            "headers": {},
            "baseUrl": origin,
            "accountId": row.state_json["account"]["id"],
            "tokenId": row.state_json["tokenId"],
        }

    def _save(self, row, state, secrets, *, next_poll=None, release=True):
        row.state_json = deepcopy(state)
        row.secrets_cipher = self._cipher().encrypt(json.dumps(secrets)) if secrets else None
        row.next_poll_at = next_poll
        row.row_version += 1
        row.updated_at, row.updated_by = self.now(), self.actor_id
        if release:
            row.lease_owner = row.lease_until = None

    def _claim(self, *, create=False, force=False):
        now, lease = self.now(), str(uuid4())
        with self._transaction():
            if self.connections.lock_user(self.actor_id) is None:
                raise NotFound("本人账号不存在")
            row = self.connections.connection(self.actor_id, lock=True)
            if row is None:
                if not create:
                    return None
                row = CanvasBeefAPIConnection(
                    id=next_id(),
                    user_id=self.actor_id,
                    state_json=disconnected_state(),
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                    created_by=self.actor_id,
                    updated_by=self.actor_id,
                )
                self.session.add(row)
            if create:
                now_stamp = now.replace(tzinfo=UTC).timestamp()
                attempts = [
                    stamp
                    for stamp in row.state_json.get("startAttempts", [])
                    if stamp > now_stamp - 60
                ]
                if len(attempts) >= 10:
                    raise WorkflowError(
                        "canvas_beefapi_start_rate_limit", "连接请求过于频繁，请稍后重试", 429
                    )
                row.state_json = {**row.state_json, "startAttempts": [*attempts, now_stamp]}
            if row.lease_until and row.lease_until > now:
                return None
            if not force and (row.next_poll_at is None or row.next_poll_at > now):
                return None
            row.lease_owner, row.lease_until = lease, now + timedelta(seconds=LEASE_SECONDS)
            # A committed claim remains recoverable even if the process dies during HTTP.
            row.next_poll_at = now
            self.session.flush()
            return lease, deepcopy(row.state_json), self._secrets(row)

    def _apply(self, lease, state, secrets, *, next_poll=None, release=True, catalog=None):
        with self._transaction():
            if self.connections.lock_user(self.actor_id) is None:
                raise NotFound("本人账号不存在")
            row = self.connections.connection(self.actor_id, lock=True)
            if row is None or row.lease_owner != lease:
                return False
            if catalog is not None:
                credential = {
                    "apiKey": secrets["apiKey"],
                    "baseUrl": self.client.origin,
                    "accountId": state["account"]["id"],
                    "tokenId": state["tokenId"],
                }
                self.catalog.apply_beefapi_catalog_locked(
                    catalog,
                    credential,
                    account_changed=state.get("accountChanged", False),
                    authorization_id=state["tokenId"]
                    if state.get("assistantDefaultPending")
                    else None,
                )
                state = {**state, "assistantDefaultPending": False}
            self._save(row, state, secrets, next_poll=next_poll, release=release)
        return True

    def start(self):
        claimed = self._claim(create=True, force=True)
        if claimed is None:
            return self.status(recover=False)
        lease, state, secrets = claimed
        if secrets.get("apiKey"):
            if state["state"] not in {"expired", "rejected", "revoked"} and (
                not state.get("acked") or not state.get("catalogOk")
            ):
                self._finalize(lease, state, secrets)
            else:
                self._apply(lease, state, secrets, next_poll=self.now() + timedelta(seconds=300))
            return self.status(recover=False)
        device = state.get("device")
        if state["state"] == "pending" and device and self.now() < _parse_time(device["expiresAt"]):
            self._apply(lease, state, secrets, next_poll=self.now())
            return self.status(recover=False)
        try:
            device = self.client.device_code()
        except CanvasBeefAPIError:
            state.update(state="store_error", errorReason="无法开始企业授权")
            self._apply(lease, state, secrets)
            return self.status(recover=False)
        state.update(
            state="pending",
            errorReason="",
            enterpriseOrigin=self.client.origin,
            device={
                "userCode": device["user_code"],
                "verificationUri": device.get("verification_uri_complete")
                or device["verification_uri"],
                "expiresAt": _iso(self.now() + timedelta(seconds=device["expires_in"])),
                "intervalSeconds": device["interval"],
            },
        )
        secrets["deviceCode"] = device["device_code"]
        accepted = self._apply(lease, state, secrets, next_poll=self.now())
        if not accepted:
            self._best_effort(self.client.cancel, device["device_code"])
        return self.status(recover=False)

    def cancel(self):
        with self._transaction():
            if self.connections.lock_user(self.actor_id) is None:
                raise NotFound("本人账号不存在")
            row = self.connections.connection(self.actor_id, lock=True)
            if row is None:
                now = self.now()
                row = CanvasBeefAPIConnection(
                    id=next_id(),
                    user_id=self.actor_id,
                    state_json=disconnected_state(),
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                    created_by=self.actor_id,
                    updated_by=self.actor_id,
                )
                self.session.add(row)
            state, secrets = deepcopy(row.state_json), self._secrets(row)
            device_code = secrets.pop("deviceCode", "")
            if state["state"] == "connected" and state.get("acked") and secrets.get("apiKey"):
                return self._summary(state, secrets)
            state.update(state="cancelled", errorReason="")
            state.pop("device", None)
            if not state.get("acked"):
                secrets.pop("apiKey", None)
                state["catalogOk"] = False
            self._save(row, state, secrets)
        self._best_effort(self.client.cancel, device_code)
        return self.status(recover=False)

    def disconnect(self):
        with self._transaction():
            if self.connections.lock_user(self.actor_id) is None:
                raise NotFound("本人账号不存在")
            row = self.connections.connection(self.actor_id, lock=True)
            key = self._secrets(row).get("apiKey", "")
            self.catalog.clear_beefapi_locked()
            if row:
                self._save(
                    row,
                    {
                        **disconnected_state(),
                        "startAttempts": row.state_json.get("startAttempts", []),
                    },
                    {},
                )
        if key:
            self._best_effort(self.client.revoke, key)
        return self.status(recover=False)

    def wallet(self):
        _ = self.actor_id
        return {"enterpriseOrigin": self.client.origin, "walletUrl": self.client.wallet_url}

    def recover_one(self, *, force=False):
        claimed = self._claim(force=force)
        if claimed is None:
            return False
        lease, state, secrets = claimed
        if secrets.get("apiKey"):
            if state["state"] in {"expired", "rejected", "revoked", "cancelled"}:
                self._apply(lease, state, secrets)
            elif not state.get("acked") or not state.get("catalogOk"):
                self._finalize(lease, state, secrets)
            else:
                try:
                    _, status = self.client.connection(secrets["apiKey"])
                    if status in {401, 403}:
                        state.update(state="revoked", errorReason="连接已失效，请重新连接")
                except CanvasBeefAPIError:
                    pass
                self._apply(
                    lease,
                    state,
                    secrets,
                    next_poll=self.now() + timedelta(seconds=300)
                    if state["state"] == "connected"
                    else None,
                )
            return True
        device = state.get("device")
        if state["state"] != "pending" or not device or not secrets.get("deviceCode"):
            self._apply(lease, state, secrets)
            return True
        if self.now() >= _parse_time(device["expiresAt"]):
            state.update(state="expired", errorReason="授权已过期，请重新连接")
            self._apply(lease, state, secrets)
            return True
        try:
            token, code = self.client.poll_token(secrets["deviceCode"])
        except CanvasBeefAPIError as error:
            if error.code == "token_invalid":
                state.update(state="rejected", errorReason="企业授权响应无效")
                self._apply(lease, state, secrets)
                return True
            token, code = None, "authorization_pending"
        if token is not None:
            key = token.api_key.get_secret_value()
            previous_account = (state.get("account") or {}).get("id")
            state.update(
                state="pending",
                account=token.account.model_dump(exclude_none=True),
                tokenId=token.token_id,
                keyName=token.key_name,
                market="enterprise",
                acked=False,
                catalogOk=False,
                assistantDefaultPending=True,
                errorReason="",
                accountChanged=bool(previous_account and previous_account != token.account.id),
            )
            secrets["apiKey"] = key
            # 先持久化密钥再确认；进程中断后继续确认同一次设备授权。
            if self._apply(lease, state, secrets, next_poll=self.now(), release=False):
                self._finalize(lease, state, secrets)
            else:
                self._best_effort(self.client.revoke, key)
            return True
        if code in {"expired_token", "expired", "access_denied", "denied", "rejected"}:
            expired = code in {"expired_token", "expired"}
            state.update(
                state="expired" if expired else "rejected",
                errorReason="授权已过期，请重新连接" if expired else "授权被拒绝",
            )
            self._apply(lease, state, secrets)
            return True
        if code == "slow_down":
            device["intervalSeconds"] += 5
        self._apply(
            lease,
            state,
            secrets,
            next_poll=self.now() + timedelta(seconds=device["intervalSeconds"]),
        )
        return True

    def _finalize(self, lease, state, secrets):
        if not state.get("acked"):
            device_code = secrets.get("deviceCode")
            if not device_code:
                state.update(state="expired", errorReason="授权已过期，请重新连接")
                self._apply(lease, state, {})
                return
            for attempt in range(5):
                if attempt:
                    self.sleep(1)
                try:
                    self.client.complete(device_code)
                    break
                except CanvasBeefAPIError as error:
                    if error.code in {"ack_expired", "ack_rejected"}:
                        state.update(
                            state="expired" if error.code == "ack_expired" else "rejected",
                            errorReason=str(error),
                            acked=False,
                            catalogOk=False,
                        )
                        for field in ("device", "tokenId", "account"):
                            state.pop(field, None)
                        self._apply(lease, state, {})
                        return
                    if attempt == 4:
                        state.update(state="pending", errorReason="正在完成连接确认")
                        self._apply(
                            lease, state, secrets, next_poll=self.now() + timedelta(seconds=5)
                        )
                        return
            state.update(acked=True, errorReason="")
            state.pop("device", None)
            secrets.pop("deviceCode", None)
            if not self._apply(lease, state, secrets, next_poll=self.now(), release=False):
                return
        try:
            models = self.client.models(secrets["apiKey"])
        except CanvasBeefAPIError as error:
            revoked = error.code == "revoked"
            state.update(
                state="revoked" if revoked else "catalog_failed",
                catalogOk=False,
                errorReason="连接已失效，请重新连接" if revoked else "模型列表读取失败，请重试",
            )
            self._apply(
                lease,
                state,
                secrets,
                next_poll=None if revoked else self.now() + timedelta(seconds=5),
            )
            return
        state.update(state="connected", catalogOk=True, errorReason="")
        state.setdefault("connectedAt", _iso(self.now()))
        # Catalog and connected state commit together, so neither can claim false success.
        self._apply(
            lease, state, secrets, next_poll=self.now() + timedelta(seconds=300), catalog=models
        )

    @staticmethod
    def _best_effort(action, value):
        try:
            action(value)
        except CanvasBeefAPIError:
            log.warning("BeefAPI remote cleanup unavailable; local authorization was cleared")


def _iso(value):
    return value.isoformat(timespec="microseconds") + "Z"


def _parse_time(value):
    from datetime import datetime

    return datetime.fromisoformat(value.removesuffix("Z")).replace(tzinfo=None)


def tick_beefapi_connections(factory, settings, *, limit=10):
    """Scheduler 内部扫描后逐本人 Session 恢复；不会创建新设备授权。"""
    with factory() as session, session.begin():
        users = CanvasBeefAPIDAO(session).due_users(utcnow(), limit)
    processed = 0
    for user_id in users:
        with factory() as session:
            session.info["actor"] = ActorContext(
                user_id, "beefapi-recovery", "", True, 0, "", "beefapi-recovery"
            )
            processed += int(CanvasBeefAPIService(session, settings).recover_one())
    return processed
