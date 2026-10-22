from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from short_drama.api.identity_middleware import install_identity
from short_drama.core.exceptions import WorkflowError
from short_drama.service.auth_service import AuthService
from short_drama.tasks.email import smtp_send


def test_retired_invitation_does_not_bypass_authentication(monkeypatch):
    app = FastAPI()
    app.state.session_factory = None
    settings = SimpleNamespace(auth_enabled=True, public_origin="http://testserver")

    def require_session(*_):
        raise WorkflowError("unauthenticated", "Login required", 401)

    monkeypatch.setattr(AuthService, "authenticate", require_session)
    install_identity(app, settings)

    @app.get("/api/v1/auth/capabilities")
    def capabilities():
        return {"auth_enabled": True}

    with TestClient(app) as client:
        assert client.get("/api/v1/auth/capabilities").status_code == 200
        response = client.get("/api/v1/invitations/" + "a" * 43)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"


@pytest.mark.parametrize(
    ("purpose", "expected"),
    [("registration", "完成账号注册"), ("password_reset", "重置账号密码")],
)
def test_current_account_proofs_keep_their_email_purpose(monkeypatch, purpose, expected):
    messages = []

    class SMTP:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def ehlo(self):
            pass

        def send_message(self, message):
            messages.append(message)

    monkeypatch.setattr("short_drama.tasks.email.smtplib.SMTP", SMTP)
    settings = SimpleNamespace(
        smtp_from="sender@example.test",
        smtp_host="mail.example.test",
        smtp_port=25,
        smtp_starttls=False,
        smtp_username="",
    )
    smtp_send(settings, {"email": "recipient@example.test", "code": "123456", "purpose": purpose})
    assert len(messages) == 1
    assert expected in messages[0].get_content()
    assert "123456" in messages[0].get_content()


def test_retired_invitation_mail_is_rejected_before_smtp(monkeypatch):
    def unexpected_smtp(*_args, **_kwargs):
        pytest.fail("Retired invitations must not reach SMTP")

    monkeypatch.setattr("short_drama.tasks.email.smtplib.SMTP", unexpected_smtp)
    settings = SimpleNamespace(
        smtp_from="sender@example.test", smtp_host="mail.example.test", smtp_port=25
    )
    with pytest.raises(KeyError, match="invitation"):
        smtp_send(
            settings,
            {"email": "recipient@example.test", "code": "123456", "purpose": "invitation"},
        )
