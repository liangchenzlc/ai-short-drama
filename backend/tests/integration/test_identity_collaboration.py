import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import aliased, sessionmaker

from short_drama.core.config import Settings
from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import WorkflowError
from short_drama.dao.task_runtime_dao import TaskRuntimeDAO
from short_drama.domain import AIGenerationRecord, Asset, AsyncTask, MediaFile, Project
from short_drama.domain.collaboration import (
    EmailChallenge,
    EmailOutbox,
    ProjectInvitation,
    ProjectMember,
    ResourceImport,
    UserSession,
)
from short_drama.main import create_app
from short_drama.service.base import utcnow
from short_drama.service.resource_import_service import cleanup_imports, process_import
from short_drama.storage.models import StoredObject
from short_drama.tasks.email import deliver_one, prune_limits
from short_drama.utils.snowflake import next_id

pytestmark = pytest.mark.integration
PASSWORD = "a-strong-test-password-42"


@pytest.fixture
def identity_app(db_session):
    settings = Settings(
        auth_enabled=True,
        auth_cookie_secure=False,
        public_origin="http://testserver",
        encryption_key=base64.b64encode(b"x" * 32).decode(),
    )
    factory = sessionmaker(db_session.get_bind(), expire_on_commit=False, autoflush=False)
    app = create_app(settings)
    app.state.session_factory, app.state.storage = factory, None
    return app, factory, settings


def code_for(factory, settings, challenge_id):
    with factory() as session:
        row = session.scalar(
            select(EmailOutbox).where(EmailOutbox.challenge_id == int(challenge_id))
        )
        return json.loads(
            KeyCipher(settings.encryption_key.get_secret_value()).decrypt(row.payload_cipher)
        )["code"]


def account(identity_app, username):
    app, factory, settings = identity_app
    client = TestClient(app)
    client.headers["Origin"] = settings.public_origin
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "display_name": "Same display name",
            "email": username + "@example.test",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    identifier = response.json()["challenge_id"]
    response = client.post(
        "/api/v1/auth/verification/confirm",
        json={"challenge_id": identifier, "code": code_for(factory, settings, identifier)},
    )
    assert response.status_code == 200, response.text
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = client.cookies["sd_csrf"]
    return client, response.json()["user"]


def project(client):
    response = client.post("/api/v1/projects", json={"name": "Shared production", "aspect": "16:9"})
    assert response.status_code == 201, response.text
    return response.json()


def join(identity_app, owner, recipient, project_id, recipient_id):
    response = owner.post(
        f"/api/v1/projects/{project_id}/invitations", json={"target_user_id": recipient_id}
    )
    assert response.status_code == 201, response.text
    token = response.json()["url"].rsplit("/", 1)[1]
    response = recipient.post(f"/api/v1/invitations/{token}/verification")
    assert response.status_code == 200, response.text
    proof = {
        "challenge_id": response.json()["challenge_id"],
        "code": code_for(identity_app[1], identity_app[2], response.json()["challenge_id"]),
    }
    response = recipient.post(f"/api/v1/invitations/{token}/accept", json=proof)
    assert response.status_code == 200, response.text
    return token, proof


def test_accounts_real_proofs_csrf_revocation_and_outbox(identity_app):
    app, factory, settings = identity_app
    anonymous = TestClient(app)
    assert anonymous.get("/api/v1/projects").status_code == 401
    assert anonymous.post("/api/v1/auth/register", json={}).status_code == 403
    alice, user = account(identity_app, "alice")
    assert user["email_verified"]
    assert alice.get("/api/v1/test/db").status_code == 200
    assert alice.get("/api/v1/auth/me").json()["user"]["id"] == user["id"]
    assert (
        alice.post(
            "/api/v1/projects",
            headers={"X-CSRF-Token": "wrong"},
            json={"name": "Bad", "aspect": "16:9"},
        ).status_code
        == 403
    )
    assert alice.post("/api/v1/auth/logout").status_code == 200
    assert alice.get("/api/v1/projects").status_code == 401
    sent = []
    while deliver_one(factory, settings, send=sent.append):
        pass
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(EmailOutbox)
                .where(EmailOutbox.payload_cipher.is_not(None))
            )
            == 0
        )


def test_expired_logout_clears_cookies_but_still_requires_trusted_origin(identity_app):
    client, user = account(identity_app, "expired_logout")
    assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.get("/api/v1/auth/me").status_code == 200
    with identity_app[1].begin() as session:
        session.execute(
            update(UserSession)
            .where(UserSession.user_id == int(user["id"]))
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
    assert (
        client.post("/api/v1/auth/logout", headers={"Origin": "https://attacker.test"}).status_code
        == 403
    )
    assert "sd_session" in client.cookies
    del client.headers["X-CSRF-Token"]
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert "sd_session" not in client.cookies
    assert "sd_csrf" not in client.cookies
    assert client.get("/api/v1/auth/me").status_code == 401


def test_email_cleanup_reclaims_abandoned_sender_without_smtp_and_respects_active_lease(
    identity_app,
):
    account(identity_app, "abandoned_sender")
    account(identity_app, "active_sender")
    factory, settings = identity_app[1:]
    assert not settings.smtp_host
    with factory.begin() as session:
        rows = list(session.scalars(select(EmailOutbox).order_by(EmailOutbox.id)))
        assert len(rows) == 2
        for index, row in enumerate(rows):
            row.status, row.lease_token = "sending", uuid4().hex
            row.lease_until = utcnow() + timedelta(minutes=-1 if index == 0 else 1)
        abandoned_id, active_id = [row.id for row in rows]
    assert deliver_one(factory, settings) is False
    prune_limits(factory)
    with factory.begin() as session:
        abandoned, active = (
            session.get(EmailOutbox, abandoned_id),
            session.get(EmailOutbox, active_id),
        )
        assert abandoned.status == "expired" and abandoned.payload_cipher is None
        assert abandoned.lease_token is None and abandoned.lease_until is None
        assert active.status == "sending" and active.payload_cipher is not None
        active.lease_until = utcnow() - timedelta(seconds=1)
    prune_limits(factory)
    with factory() as session:
        active = session.get(EmailOutbox, active_id)
        assert active.status == "expired" and active.payload_cipher is None


def test_designated_identity_invitation_and_membership_loss(identity_app):
    owner, a = account(identity_app, "owner")
    bob, b = account(identity_app, "bob")
    eve, c = account(identity_app, "eve")
    p = project(owner)
    assert bob.get(f"/api/v1/projects/{p['id']}").status_code == 404
    invite = owner.post(
        f"/api/v1/projects/{p['id']}/invitations", json={"target_user_id": b["id"]}
    ).json()
    token = invite["url"].rsplit("/", 1)[1]
    assert eve.get(f"/api/v1/invitations/{token}").status_code == 403
    assert eve.post(f"/api/v1/invitations/{token}/verification").status_code == 403
    challenge = bob.post(f"/api/v1/invitations/{token}/verification").json()["challenge_id"]
    proof = {
        "challenge_id": challenge,
        "code": code_for(identity_app[1], identity_app[2], challenge),
    }
    assert bob.post(f"/api/v1/invitations/{token}/accept", json=proof).status_code == 200
    assert bob.post(f"/api/v1/invitations/{token}/accept", json=proof).status_code == 200
    assert bob.get(f"/api/v1/projects/{p['id']}").status_code == 200
    assert (
        bob.post(
            f"/api/v1/projects/{p['id']}/invitations", json={"target_user_id": c["id"]}
        ).status_code
        == 403
    )
    assert bob.delete(f"/api/v1/projects/{p['id']}").status_code == 403
    assert owner.delete(f"/api/v1/projects/{p['id']}/members/{b['id']}").status_code == 200
    assert bob.get(f"/api/v1/projects/{p['id']}").status_code == 404
    assert bob.get(f"/api/v1/invitations/{token}").status_code == 410
    assert bob.post(f"/api/v1/invitations/{token}/accept", json=proof).status_code == 410


def test_scope_filters_counts_children_versions_and_model_privacy(identity_app):
    owner, a = account(identity_app, "author")
    bob, b = account(identity_app, "editor")
    eve, _c = account(identity_app, "outsider")
    p = project(owner)
    join(identity_app, owner, bob, p["id"], b["id"])
    episode = owner.post(f"/api/v1/projects/{p['id']}/episodes", json={"title": "Episode one"})
    assert episode.status_code == 201, episode.text
    e = episode.json()
    assert eve.get("/api/v1/projects").json()["total"] == 0
    assert bob.get("/api/v1/projects").json()["total"] == 1
    assert eve.get(f"/api/v1/projects/{p['id']}/episodes/{e['id']}").status_code == 404
    response = bob.patch(
        f"/api/v1/projects/{p['id']}", json={"name": "Edited", "row_version": p["row_version"]}
    )
    assert response.status_code == 200, response.text
    assert (
        owner.patch(
            f"/api/v1/projects/{p['id']}", json={"name": "Stale", "row_version": p["row_version"]}
        ).status_code
        == 409
    )
    config = owner.post(
        "/api/v1/ai-model-configs",
        json={
            "service_type": "image",
            "name": "Private",
            "provider": "test",
            "model_key": "gpt-image-1",
            "base_url": "https://example.com",
            "apikey": "private-key",
        },
    )
    assert config.status_code == 201, config.text
    assert bob.get("/api/v1/ai-model-configs").json()["total"] == 0
    assert bob.get(f"/api/v1/ai-model-configs/{config.json()['id']}").status_code == 404
    asset = owner.post(
        f"/api/v1/projects/{p['id']}/assets",
        json={"kind": "character", "name": "Character"},
        headers={"Idempotency-Key": "asset-one"},
    )
    assert asset.status_code == 201, asset.text
    assert bob.get(f"/api/v1/assets/{asset.json()['id']}").status_code == 200
    assert eve.get(f"/api/v1/assets/{asset.json()['id']}").status_code == 404


def test_failed_email_proofs_commit_attempts_and_cannot_reuse_registration(identity_app):
    app, factory, settings = identity_app
    client = TestClient(app, headers={"Origin": settings.public_origin})
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "pending",
            "display_name": "Pending",
            "email": "pending@example.test",
            "password": PASSWORD,
        },
    )
    identifier = response.json()["challenge_id"]
    correct = code_for(factory, settings, identifier)
    wrong = "000000" if correct != "000000" else "111111"
    for _ in range(5):
        assert (
            client.post(
                "/api/v1/auth/verification/confirm",
                json={"challenge_id": identifier, "code": wrong},
            ).status_code
            == 422
        )
    assert (
        client.post(
            "/api/v1/auth/verification/confirm", json={"challenge_id": identifier, "code": correct}
        ).status_code
        == 422
    )
    with factory() as session:
        assert session.get(EmailChallenge, int(identifier)).attempts == 5


class CopyStorage:
    """Object-level supplier substitute; tests assert bytes and independent locators."""

    def __init__(self):
        self.objects = {("image", "personal.png"): b"independent-content"}
        self.on_copy = None

    def copy(self, bucket, source, target):
        self.objects[bucket, target] = self.objects[bucket, source]
        if self.on_copy:
            action, self.on_copy = self.on_copy, None
            action()
        return StoredObject(
            bucket=bucket,
            object_name=target,
            storage_locator=f"minio://{bucket}/{target}",
            size=len(self.objects[bucket, target]),
            content_type="image/png",
        )

    def remove(self, bucket, object_name, **kwargs):
        self.objects.pop((bucket, object_name), None)


def private_media(identity_app, user_id, storage=None):
    storage = storage or CopyStorage()
    identifier = next_id()
    with identity_app[1].begin() as session:
        session.add(
            MediaFile(
                id=identifier,
                format_code="image/png",
                storage_locator="minio://image/personal.png",
                original_name="Private",
                byte_size=len(storage.objects["image", "personal.png"]),
                scope_user_id=int(user_id),
                project_id=None,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
    return identifier, storage


def image_config(client, secret="per-account-key"):
    response = client.post(
        "/api/v1/ai-model-configs",
        json={
            "service_type": "image",
            "name": "Personal model",
            "provider": "openai",
            "model_key": "gpt-image-1",
            "base_url": "https://example.com",
            "apikey": secret,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def generation(client, config_id, project_id=None, key="same-browser-key"):
    response = client.post(
        "/api/v1/ai/generations/image",
        json={
            "config_id": config_id,
            "project_id": project_id,
            "input": {"prompt": "Shared scene"},
        },
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_model_credentials_task_controls_scoped_history_and_revoked_workers(identity_app):
    owner, a = account(identity_app, "task_owner")
    bob, b = account(identity_app, "task_editor")
    p = project(owner)
    join(identity_app, owner, bob, p["id"], b["id"])
    ca, cb = image_config(owner, "owner-secret"), image_config(bob, "editor-secret")
    assert (
        bob.post(
            "/api/v1/ai/generations/image",
            json={"config_id": ca, "project_id": p["id"], "input": {"prompt": "Test"}},
            headers={"Idempotency-Key": "wrong-key"},
        ).status_code
        == 404
    )
    ta, tb = generation(owner, ca, p["id"]), generation(bob, cb, p["id"])
    assert ta["generation_id"] != tb["generation_id"]
    assert bob.get(f"/api/v1/ai/generations/{ta['generation_id']}").status_code == 404
    assert owner.get(f"/api/v1/ai/generations/{tb['generation_id']}").status_code == 404
    assert bob.post(f"/api/v1/ai/generations/{ta['generation_id']}/cancel").status_code == 404
    for endpoint in ("", "/records"):
        assert bob.get(f"/api/v1/ai/generations/{ta['generation_id']}{endpoint}").status_code == 404
    assert owner.get("/api/v1/ai/generations", params={"project_id": p["id"]}).json()["total"] == 1
    assert (
        owner.get("/api/v1/ai/generations", params={"resource_scope": "personal"}).json()["total"]
        == 0
    )
    with identity_app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(tb["generation_id"]))
        )
        assert (
            KeyCipher(identity_app[2].encryption_key.get_secret_value()).decrypt(
                record.credential_cipher
            )
            == "editor-secret"
        )
    assert owner.delete(f"/api/v1/projects/{p['id']}/members/{b['id']}").status_code == 200
    with identity_app[1]() as session:
        task = session.get(AsyncTask, int(tb["generation_id"]))
        assert task.status == "cancelled"
        assert (
            TaskRuntimeDAO(identity_app[1], identity_app[2]).claim_execution(
                task.id, task.message_version
            )
            is None
        )
    assert bob.get(f"/api/v1/ai/generations/{tb['generation_id']}").status_code == 404
    assert owner.get(f"/api/v1/ai/generations/{tb['generation_id']}").status_code == 404


def test_import_independent_physical_copy_idempotency_and_adoption_scope(identity_app):
    owner, a = account(identity_app, "copy_owner")
    p = project(owner)
    mid, storage = private_media(identity_app, a["id"])
    asset = owner.post(
        "/api/v1/libraries/global/assets",
        json={"kind": "character", "name": "Private actor"},
        headers={"Idempotency-Key": "private-actor"},
    )
    assert asset.status_code == 201, asset.text
    with identity_app[1].begin() as session:
        session.get(Asset, int(asset.json()["id"])).media_id = mid
    payload = {"source_type": "asset", "source_id": asset.json()["id"]}
    path = f"/api/v1/projects/{p['id']}/imports"
    first = owner.post(path, json=payload, headers={"Idempotency-Key": "copy-receipt"})
    assert first.status_code == 202, first.text
    assert (
        owner.post(path, json=payload, headers={"Idempotency-Key": "copy-receipt"}).json()["id"]
        == first.json()["id"]
    )
    assert process_import(identity_app[1], storage, identity_app[2]) is True
    status = owner.get(f"{path}/{first.json()['id']}").json()
    assert status["status"] == "succeeded"
    with identity_app[1].begin() as session:
        copied = session.get(Asset, int(status["result_id"]))
        copied_media = session.get(MediaFile, copied.media_id)
        assert copied.id != int(asset.json()["id"]) and copied.media_id != mid
        assert copied.project_id == int(p["id"]) and copied.scope_user_id is None
        assert copied_media.storage_locator != session.get(MediaFile, mid).storage_locator
        session.get(Asset, int(asset.json()["id"])).name = "Changed personal actor"
    assert owner.get(f"/api/v1/assets/{status['result_id']}").json()["name"] == "Private actor"
    # Direct writable references cannot cross from personal into a project.
    target = owner.post(
        f"/api/v1/projects/{p['id']}/assets",
        json={"kind": "character", "name": "Wrong reference"},
        headers={"Idempotency-Key": "illegal-reference"},
    ).json()
    denied = owner.post(f"/api/v1/assets/{target['id']}/candidates", json={"media_id": str(mid)})
    assert denied.status_code == 404, denied.text
    assert len(storage.objects) == 2


def test_import_revoked_during_external_copy_commits_cancellation_and_cleans_objects(identity_app):
    owner, a = account(identity_app, "revocation_owner")
    bob, b = account(identity_app, "revocation_editor")
    p = project(owner)
    join(identity_app, owner, bob, p["id"], b["id"])
    mid, storage = private_media(identity_app, b["id"])
    receipt = bob.post(
        f"/api/v1/projects/{p['id']}/imports",
        json={"source_type": "media", "source_id": str(mid)},
        headers={"Idempotency-Key": "copy-then-revoke"},
    )
    assert receipt.status_code == 202, receipt.text
    storage.on_copy = lambda: owner.delete(
        f"/api/v1/projects/{p['id']}/members/{b['id']}"
    ).raise_for_status()
    assert process_import(identity_app[1], storage, identity_app[2])
    with identity_app[1]() as session:
        assert session.get(ResourceImport, int(receipt.json()["id"])).status == "cancelled"
        assert (
            session.scalar(
                select(func.count())
                .select_from(MediaFile)
                .where(MediaFile.project_id == int(p["id"]))
            )
            == 0
        )
    assert len(storage.objects) == 1
    assert process_import(identity_app[1], storage, identity_app[2]) is False


@pytest.mark.parametrize("status", ["cancelled", "failed"])
def test_import_janitor_waits_for_lease_and_retries_storage_failure(identity_app, status):
    owner, user = account(identity_app, "cleanup_owner")
    p = project(owner)
    mid, storage = private_media(identity_app, user["id"])
    receipt = owner.post(
        f"/api/v1/projects/{p['id']}/imports",
        json={"source_type": "media", "source_id": str(mid)},
        headers={"Idempotency-Key": "orphan-receipt"},
    )
    assert receipt.status_code == 202, receipt.text
    identifier = int(receipt.json()["id"])
    with identity_app[1].begin() as session:
        job = session.get(ResourceImport, identifier)
        target = f"imports/{job.id}/{job.snapshot['media'][0]['copy_id']}"
        job.status, job.lease_token = status, "crashed-worker"
        job.lease_until = utcnow() + timedelta(minutes=10)
    storage.objects["image", target] = b"partial-copy"
    cleanup_imports(identity_app[1], storage, identity_app[2])
    assert ("image", target) in storage.objects
    with identity_app[1].begin() as session:
        session.get(ResourceImport, identifier).lease_until = utcnow() - timedelta(minutes=6)

    original_remove = storage.remove

    def unavailable(*args, **kwargs):
        raise OSError("Storage temporarily unavailable")

    storage.remove = unavailable
    cleanup_imports(identity_app[1], storage, identity_app[2])
    with identity_app[1]() as session:
        assert session.get(ResourceImport, identifier).cleanup_at is None
    assert ("image", target) in storage.objects
    storage.remove = original_remove
    cleanup_imports(identity_app[1], storage, identity_app[2])
    with identity_app[1]() as session:
        job = session.get(ResourceImport, identifier)
        assert job.cleanup_at is not None
        assert job.lease_token is None and job.lease_until is None
    assert set(storage.objects) == {("image", "personal.png")}
    cleanup_imports(identity_app[1], storage, identity_app[2])
    assert set(storage.objects) == {("image", "personal.png")}


def test_import_janitor_preserves_committed_media_and_retry_uses_stable_destination(identity_app):
    owner, user = account(identity_app, "retry_copy_owner")
    p = project(owner)
    mid, storage = private_media(identity_app, user["id"])
    receipt = owner.post(
        f"/api/v1/projects/{p['id']}/imports",
        json={"source_type": "media", "source_id": str(mid)},
        headers={"Idempotency-Key": "retry-copy-receipt"},
    )
    assert receipt.status_code == 202, receipt.text
    identifier = int(receipt.json()["id"])

    def lost_response():
        raise OSError("Copy response lost after object was written")

    storage.on_copy = lost_response
    assert process_import(identity_app[1], storage, identity_app[2])
    with identity_app[1]() as session:
        job = session.get(ResourceImport, identifier)
        target = f"imports/{job.id}/{job.snapshot['media'][0]['copy_id']}"
        assert job.status == "pending" and job.attempts == 1
    assert ("image", target) in storage.objects
    assert process_import(identity_app[1], storage, identity_app[2])
    with identity_app[1].begin() as session:
        job = session.get(ResourceImport, identifier)
        assert job.status == "succeeded" and job.attempts == 2
        assert session.get(MediaFile, job.result_id).storage_locator == f"minio://image/{target}"
        # Model an uncertain completion receipt: persisted references take precedence.
        job.status = "failed"
    cleanup_imports(identity_app[1], storage, identity_app[2])
    assert storage.objects["image", target] == storage.objects["image", "personal.png"]


def test_stale_copy_worker_reopens_cleanup_after_terminal_receipt_was_cleaned(identity_app):
    owner, user = account(identity_app, "delayed_copy_owner")
    p = project(owner)
    mid, storage = private_media(identity_app, user["id"])
    receipt = owner.post(
        f"/api/v1/projects/{p['id']}/imports",
        json={"source_type": "media", "source_id": str(mid)},
        headers={"Idempotency-Key": "delayed-copy-receipt"},
    )
    assert receipt.status_code == 202, receipt.text
    identifier = int(receipt.json()["id"])

    def clean_before_delayed_copy_returns():
        with identity_app[1].begin() as session:
            job = session.get(ResourceImport, identifier)
            target = f"imports/{job.id}/{job.snapshot['media'][0]['copy_id']}"
            job.status, job.lease_until = "failed", utcnow() - timedelta(minutes=6)
        cleanup_imports(identity_app[1], storage, identity_app[2])
        assert ("image", target) not in storage.objects
        storage.objects["image", target] = storage.objects["image", "personal.png"]

    storage.on_copy = clean_before_delayed_copy_returns
    assert process_import(identity_app[1], storage, identity_app[2])
    with identity_app[1]() as session:
        job = session.get(ResourceImport, identifier)
        assert job.status == "failed" and job.result_id is None
        assert job.cleanup_at is None
    cleanup_imports(identity_app[1], storage, identity_app[2])
    assert set(storage.objects) == {("image", "personal.png")}


def test_parallel_accept_one_membership_and_fresh_proofs_are_invitation_bound(identity_app):
    owner, a = account(identity_app, "proof_owner")
    bob, b = account(identity_app, "proof_editor")
    p = project(owner)
    tokens = [
        owner.post(f"/api/v1/projects/{p['id']}/invitations", json={"target_user_id": b["id"]})
        .json()["url"]
        .rsplit("/", 1)[1]
        for _ in range(2)
    ]
    challenge = bob.post(f"/api/v1/invitations/{tokens[0]}/verification").json()["challenge_id"]
    proof = {
        "challenge_id": challenge,
        "code": code_for(identity_app[1], identity_app[2], challenge),
    }
    assert bob.post(f"/api/v1/invitations/{tokens[1]}/accept", json=proof).status_code == 422

    # Explicit clients without context managers avoid starting business-database lifespan.
    def accept_without_lifespan(_):
        parallel = TestClient(identity_app[0], headers=dict(bob.headers), cookies=dict(bob.cookies))
        response = parallel.post(f"/api/v1/invitations/{tokens[0]}/accept", json=proof)
        parallel.close()
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(accept_without_lifespan, range(2))) == [200, 200]
    with identity_app[1]() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ProjectMember)
                .where(
                    ProjectMember.project_id == int(p["id"]), ProjectMember.user_id == int(b["id"])
                )
            )
            == 1
        )


def test_archive_preserves_children_and_scoped_alias_counts_and_bulk_writes(identity_app):
    owner, a = account(identity_app, "archive_owner")
    eve, e = account(identity_app, "archive_outsider")
    p = project(owner)
    child = owner.post(
        f"/api/v1/projects/{p['id']}/episodes", json={"title": "Retained episode"}
    ).json()
    actor = SimpleNamespace(user_id=int(e["id"]))
    with identity_app[1].begin() as session:
        session.info["actor"] = actor
        project_alias = aliased(Project)
        assert session.scalar(select(func.count()).select_from(project_alias)) == 0
        assert (
            session.execute(
                update(Project).where(Project.id == int(p["id"])).values(name="Illegal edit")
            ).rowcount
            == 0
        )
        with pytest.raises(WorkflowError):
            session.execute(update(Project).values(owner_user_id=int(e["id"])))
        with pytest.raises(WorkflowError):
            session.execute(Project.__table__.select())
    assert owner.delete(f"/api/v1/projects/{p['id']}").status_code == 204
    assert owner.get(f"/api/v1/projects/{p['id']}").status_code == 404
    with identity_app[1]() as session:
        assert session.get(Project, int(p["id"])).archived_at is not None
        from short_drama.domain import Episode

        assert session.get(Episode, int(child["id"])) is not None


def test_designated_email_invitation_expiry_revoke_and_password_reset_session_loss(identity_app):
    owner, a = account(identity_app, "mail_owner")
    p = project(owner)
    invite = owner.post(
        f"/api/v1/projects/{p['id']}/invitations", json={"target_email": "new_editor@example.test"}
    ).json()
    token = invite["url"].rsplit("/", 1)[1]
    anonymous = TestClient(identity_app[0])
    assert "project_name" not in anonymous.get(f"/api/v1/invitations/{token}").json()
    bob, b = account(identity_app, "new_editor")
    challenge = bob.post(f"/api/v1/invitations/{token}/verification").json()["challenge_id"]
    proof = {
        "challenge_id": challenge,
        "code": code_for(identity_app[1], identity_app[2], challenge),
    }
    assert owner.delete(f"/api/v1/projects/{p['id']}/invitations/{invite['id']}").status_code == 200
    assert bob.post(f"/api/v1/invitations/{token}/accept", json=proof).status_code == 410
    second = owner.post(
        f"/api/v1/projects/{p['id']}/invitations", json={"target_user_id": b["id"]}
    ).json()
    with identity_app[1].begin() as session:
        session.get(ProjectInvitation, int(second["id"])).expires_at = utcnow() - timedelta(
            seconds=1
        )
    assert bob.get("/api/v1/invitations/" + second["url"].rsplit("/", 1)[1]).status_code == 410
    reset = bob.post("/api/v1/auth/password/request", json={"email": b["email"]}).json()[
        "challenge_id"
    ]
    reset_proof = {
        "challenge_id": reset,
        "code": code_for(identity_app[1], identity_app[2], reset),
        "password": PASSWORD + "new",
    }
    assert bob.post("/api/v1/auth/password/reset", json=reset_proof).status_code == 200
    assert bob.get("/api/v1/auth/me").status_code == 401


@pytest.mark.storage_integration
@pytest.mark.skipif(
    os.environ.get("RUN_STORAGE_INTEGRATION") != "1",
    reason="Opt in to private MinIO temporary-object roundtrip",
)
def test_actual_minio_import_bytes_are_independent_and_temporary_objects_are_cleaned(identity_app):
    from short_drama.storage.minio import MinioStorage
    from short_drama.storage.models import ObjectLocation

    owner, a = account(identity_app, "minio_owner")
    p = project(owner)
    storage = MinioStorage(identity_app[2])
    bucket = identity_app[2].minio_image_bucket
    source = f"collaboration-tests/{uuid4().hex}/source.png"
    cleanup = [(bucket, source)]
    data = b"private-independent-copy-test"
    try:
        stored = storage.put(bucket, source, BytesIO(data), len(data), "image/png")
        mid = next_id()
        with identity_app[1].begin() as session:
            session.add(
                MediaFile(
                    id=mid,
                    format_code="image/png",
                    storage_locator=stored.storage_locator,
                    original_name="Copy test",
                    byte_size=len(data),
                    scope_user_id=int(a["id"]),
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
        path = f"/api/v1/projects/{p['id']}/imports"
        receipt = owner.post(
            path,
            json={"source_type": "media", "source_id": str(mid)},
            headers={"Idempotency-Key": "actual-storage-copy"},
        )
        assert receipt.status_code == 202, receipt.text
        with identity_app[1]() as session:
            job = session.get(ResourceImport, int(receipt.json()["id"]))
            cleanup.extend(
                (bucket, f"imports/{job.id}/{entry['copy_id']}") for entry in job.snapshot["media"]
            )
        process_import(identity_app[1], storage, identity_app[2])
        status = owner.get(f"{path}/{receipt.json()['id']}").json()
        assert status["status"] == "succeeded"
        with identity_app[1]() as session:
            copied = session.get(MediaFile, int(status["result_id"]))
            destination = ObjectLocation.parse(copied.storage_locator, {bucket})
        with storage.open(destination.bucket, destination.object_name) as response:
            assert response.read() == data
        storage.remove(bucket, source)
        with storage.open(destination.bucket, destination.object_name) as response:
            assert response.read() == data
    finally:
        for target_bucket, key in cleanup:
            storage.remove(target_bucket, key)
        storage.close()
