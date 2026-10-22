import base64
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import aliased

from short_drama.core.config import Settings
from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import WorkflowError
from short_drama.db.session import session_factory
from short_drama.domain import MediaFile, Project
from short_drama.domain.collaboration import EmailChallenge, EmailOutbox, UserSession
from short_drama.main import create_app
from short_drama.service.base import utcnow
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
    factory = session_factory(db_session.get_bind())
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
    response = client.post("/api/v1/projects", json={"name": "Single work"})
    assert response.status_code == 201, response.text
    return response.json()


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
            json={"name": "Bad"},
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

    def presigned_get(self, bucket, object_name, expires_seconds):
        self.objects[bucket, object_name]
        return f"https://storage.test/{bucket}/{object_name}?expires={expires_seconds}"


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


def test_works_assets_models_and_renames_are_owner_only(identity_app):
    owner, _ = account(identity_app, "work_owner")
    other, user = account(identity_app, "other_owner")
    work = project(owner)
    root = f"/api/v1/projects/{work['id']}"
    assert owner.get(root).status_code == 200
    assert other.get("/api/v1/projects").json()["total"] == 0
    assert other.get(root).status_code == 404
    assert other.patch(root, json={"name": "illegal", "row_version": "1"}).status_code == 404
    changed = owner.patch(root, json={"name": "renamed", "row_version": "1"})
    assert changed.status_code == 200, changed.text
    assert owner.patch(root, json={"name": "stale", "row_version": "1"}).status_code == 409
    for path in ("/members", "/invitations", "/episodes"):
        assert owner.post(root + path, json={"target_user_id": user["id"]}).status_code == 404
    assert owner.delete(root).status_code == 422
    assert (
        other.delete(root, params={"row_version": changed.json()["row_version"]}).status_code == 404
    )
    assert owner.get(root).status_code == 200
    assert owner.get("/api/v1/libraries/global/assets").status_code == 404
    assert owner.get("/api/v1/users/search", params={"q": "other"}).status_code == 404
    assert (
        owner.post(
            root + "/imports",
            json={"source_type": "asset", "source_id": "1"},
            headers={"Idempotency-Key": "retired-asset-import"},
        ).status_code
        == 422
    )
    asset = owner.post(
        root + "/assets",
        json={"kind": "character", "name": "actor"},
        headers={"Idempotency-Key": "owned-asset"},
    )
    assert asset.status_code == 201, asset.text
    asset_path = f"/api/v1/assets/{asset.json()['id']}"
    assert owner.get(asset_path).status_code == 200
    assert other.get(asset_path).status_code == 404
    model = image_config(owner)
    assert other.get("/api/v1/ai-model-configs").json()["total"] == 0
    assert other.get(f"/api/v1/ai-model-configs/{model}").status_code == 404


def test_owner_filters_cover_aliases_counts_and_bulk_writes(identity_app):
    owner, _ = account(identity_app, "query_owner")
    _, outsider = account(identity_app, "query_outsider")
    work = project(owner)
    from short_drama.core.identity import ActorContext

    with identity_app[1].begin() as session:
        session.info["actor"] = ActorContext(
            int(outsider["id"]), "outsider", outsider["email"], True, 1, "", "test"
        )
        alias = aliased(Project)
        assert session.scalar(select(func.count()).select_from(alias)) == 0
        assert (
            session.execute(
                update(Project).where(Project.id == int(work["id"])).values(name="illegal")
            ).rowcount
            == 0
        )
        with pytest.raises(WorkflowError):
            session.execute(update(Project).values(owner_user_id=int(outsider["id"])))
        with pytest.raises(WorkflowError):
            session.execute(Project.__table__.select())
    assert owner.get(f"/api/v1/projects/{work['id']}").json()["name"] == "Single work"


def test_password_reset_revokes_the_current_session(identity_app):
    client, user = account(identity_app, "reset_owner")
    requested = client.post("/api/v1/auth/password/request", json={"email": user["email"]})
    assert requested.status_code == 200, requested.text
    challenge = requested.json()["challenge_id"]
    reset = client.post(
        "/api/v1/auth/password/reset",
        json={
            "challenge_id": challenge,
            "code": code_for(identity_app[1], identity_app[2], challenge),
            "password": PASSWORD + "new",
        },
    )
    assert reset.status_code == 200, reset.text
    assert client.get("/api/v1/auth/me").status_code == 401
