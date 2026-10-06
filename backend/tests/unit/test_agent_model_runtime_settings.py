"""宿主已保存协议与加密请求头在 Agent 消费路径中保持一致。"""

import asyncio
import base64
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from short_drama.agent.artifacts import candidate_secret, public_content
from short_drama.agent.model_gateway import AgentCapabilityEvidence, AgentSegmentResult
from short_drama.agent.runtime import AgentRuntime, Claim
from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import WorkflowError
from short_drama.service.agent_model_service import AgentModelService, model_snapshot, read_model
from short_drama.service.model_runtime_config import encrypt_runtime_credentials


def model_fixture(*, profile=None, headers=True):
    master = base64.b64encode(b"r" * 32).decode()
    cipher = KeyCipher(master)
    row = SimpleNamespace(
        id=17,
        name="Local model",
        row_version=3,
        service_type="text",
        model_key="test",
        provider="local",
        base_url="https://provider.example/v1/chat/completions",
        capability_cache={},
        apikey=cipher.encrypt("private-api-key"),
        runtime_profile=profile,
        runtime_credentials_cipher=encrypt_runtime_credentials(
            "unused-signing-secret", {"X-Account": "private-header"}, cipher
        )
        if headers
        else None,
        enabled=1,
        is_deleted=0,
    )
    return row, SimpleNamespace(encryption_key=SecretStr(master), agent_enabled=True)


@pytest.mark.parametrize(
    "profile,expected",
    [
        (None, "openai_chat.v1"),
        (
            {"version": 1, "api_format": "openai", "protocol": "openai-response"},
            "openai_responses.v1",
        ),
        ({"version": 1, "api_format": "openai", "protocol": "plugin/unknown"}, None),
        ({"version": 1, "api_format": "claude", "protocol": "chat-completion"}, None),
    ],
)
def test_private_snapshot_keeps_saved_profile_and_cipher_without_mutating_model(profile, expected):
    row, _ = model_fixture(profile=profile)
    before = deepcopy(vars(row))
    frozen = model_snapshot(row)
    public = read_model(row)
    assert frozen["runtime_profile"] == profile
    assert frozen["runtime_credentials_cipher"] == row.runtime_credentials_cipher
    assert public.protocol == expected
    if expected != "openai_chat.v1":
        assert public.input_capabilities.audio is False
    assert "private-header" not in repr(frozen)
    assert "cipher" not in public.model_dump_json()
    assert "runtime_profile" not in public.model_dump_json()
    if profile:
        row.runtime_profile["protocol"] = "changed-after-freeze"
        assert frozen["runtime_profile"] == before["runtime_profile"]
        row.runtime_profile = before["runtime_profile"]
    assert vars(row) == before


def probe_service(row, settings, gateway):
    service = AgentModelService(SimpleNamespace(scalar=lambda _: row), settings, gateway=gateway)

    @contextmanager
    def transaction():
        yield

    service._transaction = transaction
    service._actor = lambda: SimpleNamespace(user_id=1)
    service._probe_identity = lambda *_, **__: False
    service.available = lambda *_, **__: row
    service.preferred_id = lambda: None
    return service


def test_model_verify_passes_frozen_saved_headers_and_profile_to_gateway():
    row, settings = model_fixture(
        profile={"version": 1, "api_format": "openai", "protocol": "openai-response"}
    )
    observed = []

    class Probe:
        async def validate_capability(self, snapshot, credential, **kwargs):
            observed.append((snapshot, credential))
            await kwargs["on_request"]({})
            return AgentCapabilityEvidence("openai_responses.v1", True, True, requests=1)

    public = probe_service(row, settings, Probe()).verify(17, {"row_version": 3})
    frozen, credential = observed[0]
    assert frozen["runtime_profile"]["protocol"] == "openai-response"
    assert credential.api_key.get_secret_value() == "private-api-key"
    assert credential.headers[0].value.get_secret_value() == "private-header"
    assert "unused-signing-secret" not in repr(credential)
    assert public.verified and public.protocol == "openai_responses.v1"
    assert "private-header" not in public.model_dump_json()


def test_unsupported_saved_profile_is_rejected_before_model_verify_remote_io():
    row, settings = model_fixture(
        profile={"version": 1, "api_format": "openai", "protocol": "plugin/unknown"}
    )

    class Forbidden:
        async def validate_capability(self, *_, **__):
            raise AssertionError("Unsupported saved profile must not call the provider")

    with pytest.raises(WorkflowError) as error:
        probe_service(row, settings, Forbidden()).verify(17, {"row_version": 3})
    assert error.value.code == "unsupported_agent_protocol"
    assert error.value.status_code == 422
    assert row.capability_cache["agent_probe"]["requests"] == 0


def test_probe_current_model_check_includes_saved_header_account_and_profile():
    row, _ = model_fixture(
        profile={"version": 1, "api_format": "openai", "protocol": "openai-response"}
    )
    frozen = model_snapshot(row)
    assert AgentModelService._same_probe_model(row, frozen)
    row.runtime_credentials_cipher = "changed-account-envelope"
    assert not AgentModelService._same_probe_model(row, frozen)
    row.runtime_credentials_cipher = frozen["runtime_credentials_cipher"]
    row.runtime_profile["protocol"] = "chat-completion"
    assert not AgentModelService._same_probe_model(row, frozen)


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_agent_runtime_decrypts_frozen_header_credentials_and_preserves_legacy_key(replay, legacy):
    row, settings = model_fixture(headers=not legacy)
    frozen = model_snapshot(row)
    if legacy:
        frozen.pop("runtime_credentials_cipher", None)
        frozen.pop("runtime_profile", None)
    observed = []

    class Gateway:
        async def run_segment(self, snapshot, credential, **kwargs):
            observed.append((snapshot, credential))
            return AgentSegmentResult("Safe", {}, {}, "openai_chat.v1", 1)

        async def replay_segment(self, *args, **kwargs):
            assert replay
            return await self.run_segment(*args, **kwargs)

    runtime = object.__new__(AgentRuntime)
    runtime.settings = settings
    runtime.gateway = Gateway()
    runtime.store = SimpleNamespace(save_result=lambda *args: False)
    claim = Claim(
        1,
        1,
        "token",
        "model",
        1,
        frozen,
        {
            "codec": "agent.segment-input",
            "version": 1,
            "kwargs": {
                "instructions": "Answer.",
                "user_prompt": "Hello.",
                "stream": False,
            },
        },
        {},
        {} if replay else None,
    )
    asyncio.run(runtime._decision(claim))
    credential = observed[0][1]
    if legacy:
        assert credential == "private-api-key"
    else:
        assert credential.api_key.get_secret_value() == "private-api-key"
        assert credential.headers[0].value.get_secret_value() == "private-header"
        assert "unused-signing-secret" not in repr(credential)
    assert "private-header" not in repr(claim.snapshot)


def test_candidate_content_redacts_saved_header_values_and_keeps_private_intent_unchanged():
    row, settings = model_fixture()
    run = SimpleNamespace(config_snapshot=model_snapshot(row))
    private = {"name": "private-header", "content": "private-api-key and private-header"}
    before = deepcopy(private)
    public = public_content(private, candidate_secret(run, settings))
    assert public == {"name": "[redacted]", "content": "[redacted] and [redacted]"}
    assert private == before


def test_legacy_candidate_without_encrypted_credentials_requires_no_master_key():
    run = SimpleNamespace(config_snapshot={"credential_cipher": None})
    assert candidate_secret(run, SimpleNamespace()) == ""
    assert (
        public_content("Candidate text", candidate_secret(run, SimpleNamespace()))
        == "Candidate text"
    )
