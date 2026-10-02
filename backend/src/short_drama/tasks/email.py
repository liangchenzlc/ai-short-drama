"""Leased, encrypted email outbox. Delivery is at-least-once; proofs are one-use."""

import json
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage
from uuid import uuid4

from sqlalchemy import or_, select, update

from short_drama.core.crypto import KeyCipher
from short_drama.domain.collaboration import AuthRateLimit, EmailChallenge, EmailOutbox
from short_drama.service.base import utcnow


def smtp_send(settings, payload):
    message = EmailMessage()
    message["From"], message["To"] = settings.smtp_from, payload["email"]
    message["Subject"] = "短剧工作台 · 邮箱验证"
    purposes = {
        "registration": "完成账号注册",
        "invitation": "接受项目邀请",
        "password_reset": "重置账号密码",
    }
    message.set_content(
        f"你的验证码是：{payload['code']}\n\n用于{purposes[payload['purpose']]}，十分钟内有效。若不是你本人操作，请忽略此邮件。"
    )
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        smtp.ehlo()
        if settings.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        smtp.send_message(message)


def deliver_one(factory, settings, send=None):
    now = utcnow()
    if send is None and (not settings.smtp_host or not settings.smtp_from):
        return False
    with factory.begin() as session:
        row = session.scalar(
            select(EmailOutbox)
            .where(
                EmailOutbox.next_run_at <= now,
                or_(
                    EmailOutbox.status == "pending",
                    (EmailOutbox.status == "sending") & (EmailOutbox.lease_until <= now),
                ),
            )
            .order_by(EmailOutbox.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if not row:
            return False
        challenge = session.get(EmailChallenge, row.challenge_id)
        if (
            not challenge
            or challenge.consumed_at
            or challenge.expires_at <= now
            or row.attempts >= 5
        ):
            row.status, row.payload_cipher = "expired", None
            row.lease_until = row.lease_token = None
            return True
        token = uuid4().hex
        row.status, row.lease_token, row.lease_until = "sending", token, now + timedelta(seconds=60)
        row.attempts += 1
        identifier, envelope = row.id, row.payload_cipher
    success = False
    try:
        payload = json.loads(
            KeyCipher(settings.encryption_key.get_secret_value()).decrypt(envelope)
        )
        (send or (lambda value: smtp_send(settings, value)))(payload)
        success = True
    except Exception:
        # Never log SMTP exceptions: they may contain recipients or message contents.
        pass
    with factory.begin() as session:
        row = session.scalar(
            select(EmailOutbox)
            .where(EmailOutbox.id == identifier, EmailOutbox.lease_token == token)
            .with_for_update()
        )
        if row:
            row.status = "sent" if success else "pending"
            row.lease_until = row.lease_token = None
            row.next_run_at = utcnow() + timedelta(seconds=2**row.attempts * 5)
            if success:
                row.payload_cipher = None
    return True


def prune_limits(factory):
    with factory.begin() as session:
        expired = select(EmailChallenge.id).where(
            or_(
                EmailChallenge.expires_at <= utcnow(),
                EmailChallenge.consumed_at.is_not(None),
            )
        )
        session.execute(
            update(EmailOutbox)
            .where(
                EmailOutbox.challenge_id.in_(expired),
                EmailOutbox.payload_cipher.is_not(None),
                or_(
                    EmailOutbox.status != "sending",
                    EmailOutbox.lease_until <= utcnow(),
                ),
            )
            .values(payload_cipher=None, status="expired", lease_token=None, lease_until=None)
        )
        session.execute(
            AuthRateLimit.__table__.delete().where(
                AuthRateLimit.window_start < utcnow() - timedelta(days=1)
            )
        )
