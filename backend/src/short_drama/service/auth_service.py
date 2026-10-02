"""Identity operations own short independent transactions, including failed proof attempts."""

import hashlib
import hmac
import json
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select, update
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.exc import IntegrityError

from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import ConfigurationError, WorkflowError
from short_drama.core.identity import ActorContext, token_hash
from short_drama.domain.collaboration import (
    AuthRateLimit,
    EmailChallenge,
    EmailOutbox,
    User,
    UserSession,
)
from short_drama.schemas.identity import normalize_email
from short_drama.utils.snowflake import next_id

from .base import utcnow

PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
DUMMY_PASSWORD = PASSWORDS.hash(secrets.token_urlsafe(32))


def user_dto(user):
    return {
        "id": str(user.id),
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "email_verified": user.email_verified_at is not None,
    }


class AuthService:
    def __init__(self, factory, settings):
        self.factory, self.settings = factory, settings

    def limit(self, action, identity, maximum=10, seconds=600):
        """Atomic cross-process counter. Commit independently from rejected operations."""
        now = utcnow()
        key = token_hash(f"{action}:{identity}")
        with self.factory.begin() as session:
            session.execute(
                insert(AuthRateLimit)
                .values(key_hash=key, window_start=now, count=0)
                .on_duplicate_key_update(key_hash=key)
            )
            counter = session.scalar(
                select(AuthRateLimit).where(AuthRateLimit.key_hash == key).with_for_update()
            )
            if counter.window_start <= now - timedelta(seconds=seconds):
                counter.window_start, counter.count = now, 0
            counter.count += 1
            allowed = counter.count <= maximum
        if not allowed:
            raise WorkflowError("rate_limited", "Too many attempts; try again later", 429)

    def cipher(self):
        key = self.settings.encryption_key
        if key is None:
            raise ConfigurationError("Email encryption is not configured")
        return KeyCipher(key.get_secret_value())

    def proof_hash(self, identifier, code):
        key = self.settings.email_proof_key or self.settings.encryption_key
        if key is None:
            raise ConfigurationError("Email verification is not configured")
        return hmac.new(
            key.get_secret_value().encode(), f"{identifier}:{code}".encode(), hashlib.sha256
        ).hexdigest()

    def issue(self, session, user, purpose, invitation_id=None):
        """Caller locks user (and invitation, when applicable) before issuance."""
        now = utcnow()
        latest = session.scalar(
            select(EmailChallenge)
            .where(
                EmailChallenge.user_id == user.id,
                EmailChallenge.purpose == purpose,
                EmailChallenge.invitation_id == invitation_id,
            )
            .order_by(EmailChallenge.created_at.desc())
            .limit(1)
        )
        if latest and latest.created_at > now - timedelta(seconds=60):
            raise WorkflowError("rate_limited", "Please wait before requesting another code", 429)
        session.execute(
            update(EmailChallenge)
            .where(
                EmailChallenge.user_id == user.id,
                EmailChallenge.purpose == purpose,
                EmailChallenge.invitation_id == invitation_id,
                EmailChallenge.consumed_at.is_(None),
            )
            .values(consumed_at=now)
        )
        identifier, code = next_id(), f"{secrets.randbelow(1000000):06}"
        challenge = EmailChallenge(
            id=identifier,
            user_id=user.id,
            purpose=purpose,
            invitation_id=invitation_id,
            email=user.email,
            proof_hash=self.proof_hash(identifier, code),
            attempts=0,
            expires_at=now + timedelta(minutes=10),
            created_at=now,
        )
        session.add(challenge)
        session.flush()
        session.add(
            EmailOutbox(
                id=next_id(),
                challenge_id=identifier,
                payload_cipher=self.cipher().encrypt(
                    json.dumps({"email": user.email, "code": code, "purpose": purpose})
                ),
                status="pending",
                attempts=0,
                next_run_at=now,
                created_at=now,
            )
        )
        return {"challenge_id": str(identifier), "expires_in": 600}

    def check_proof(self, session, proof, purpose, user_id=None, invitation_id=None):
        query = select(EmailChallenge).where(EmailChallenge.id == proof.challenge_id)
        challenge = session.scalar(query)
        if (
            not challenge
            or challenge.purpose != purpose
            or (user_id is not None and challenge.user_id != user_id)
            or challenge.invitation_id != invitation_id
        ):
            return None
        # Issuance/reset/login and invitation acceptance all lock user first.
        # Re-read the proof under its lock so parallel consumes cannot both pass.
        user = session.scalar(select(User).where(User.id == challenge.user_id).with_for_update())
        challenge = session.scalar(
            query.with_for_update().execution_options(populate_existing=True)
        )
        if challenge.consumed_at or challenge.expires_at <= utcnow() or challenge.attempts >= 5:
            return None
        challenge.attempts += 1
        if not hmac.compare_digest(challenge.proof_hash, self.proof_hash(challenge.id, proof.code)):
            return None
        if not user or user.status != "active" or user.email != challenge.email:
            return None
        challenge.consumed_at = utcnow()
        return user

    def register(self, payload):
        digest = PASSWORDS.hash(payload.password)
        try:
            with self.factory.begin() as session:
                user = User(
                    id=next_id(),
                    username=payload.username,
                    display_name=payload.display_name,
                    email=payload.email,
                    password_hash=digest,
                    status="active",
                    created_at=utcnow(),
                )
                session.add(user)
                session.flush()
                return self.issue(session, user, "registration")
        except IntegrityError:
            raise WorkflowError(
                "account_unavailable", "Account name or email is unavailable", 409
            ) from None

    def request_email(self, email, purpose):
        email = normalize_email(email)
        with self.factory.begin() as session:
            user = session.scalar(
                select(User).where(User.email == email, User.status == "active").with_for_update()
            )
            if user and (purpose != "registration" or not user.email_verified_at):
                return self.issue(session, user, purpose)
        # Opaque response with the same shape; no directory lookup disclosure.
        return {"challenge_id": str(next_id()), "expires_in": 600}

    def verify(self, proof, purpose="registration"):
        with self.factory.begin() as session:
            user = self.check_proof(session, proof, purpose)
            if user:
                if purpose == "registration":
                    user.email_verified_at = utcnow()
                else:
                    user.password_hash = PASSWORDS.hash(proof.password)
                    session.execute(
                        update(UserSession)
                        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
                        .values(revoked_at=utcnow())
                    )
        if not user:
            raise WorkflowError("email_proof_invalid", "Code is invalid or expired", 422)
        return {"verified": True}

    def login(self, payload):
        with self.factory.begin() as session:
            user = session.scalar(
                select(User)
                .where(User.username == payload.username.strip().lower())
                .with_for_update()
            )
            try:
                valid = PASSWORDS.verify(
                    user.password_hash if user else DUMMY_PASSWORD, payload.password
                )
            except (VerificationError, InvalidHashError):
                valid = False
            if not valid or not user or user.status != "active":
                raise WorkflowError("login_failed", "Account or password is incorrect", 401)
            if not user.email_verified_at:
                raise WorkflowError(
                    "email_verification_required", "Verify your email before signing in", 403
                )
            if PASSWORDS.check_needs_rehash(user.password_hash):
                user.password_hash = PASSWORDS.hash(payload.password)
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            session.add(
                UserSession(
                    id=next_id(),
                    user_id=user.id,
                    token_hash=token_hash(token),
                    csrf_hash=token_hash(csrf),
                    expires_at=utcnow() + timedelta(days=self.settings.auth_session_days),
                    created_at=utcnow(),
                )
            )
            return user_dto(user), token, csrf

    def authenticate(self, token, request_id):
        if not token or len(token) > 100:
            raise WorkflowError("authentication_required", "Please sign in", 401)
        with self.factory() as session:
            row = session.execute(
                select(UserSession, User)
                .join(User, User.id == UserSession.user_id)
                .where(
                    UserSession.token_hash == token_hash(token),
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > utcnow(),
                    User.status == "active",
                )
            ).first()
            if not row:
                raise WorkflowError("authentication_required", "Please sign in", 401)
            login, user = row
            return ActorContext(
                user.id,
                user.username,
                user.email,
                user.email_verified_at is not None,
                login.id,
                login.csrf_hash,
                request_id,
            )
