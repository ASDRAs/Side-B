import asyncio
import math
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Header, HTTPException, Request

from app.services.access import (
    AccessConflictError,
    AccessRequestQuotaExceededError,
    AccessStoreUnavailableError,
    AccessTargetProtectedError,
    AccessUserNotFoundError,
    InvalidAccessInputError,
)

AuthMode = Literal["legacy", "dual", "firebase"]
AuthFeature = Literal[
    "recommend",
    "genre",
    "youtube_export",
    "preview",
    # Approval store reads/writes have their own budgets so that status checks
    # and administration cannot drain feature limits, and vice versa.
    "access_status",
    "access_lookup",
    "access_request",
    "admin_read",
    "admin_write",
]
RATE_LIMITED_FEATURES: tuple[AuthFeature, ...] = (
    "recommend",
    "genre",
    "youtube_export",
    "preview",
    "access_status",
    "access_lookup",
    "access_request",
    "admin_read",
    "admin_write",
)


class AuthenticationError(Exception):
    pass


class AuthenticationConfigurationError(Exception):
    pass


class AuthenticationUnavailableError(Exception):
    pass


class AuthenticationDeniedError(Exception):
    """The environment allowlist (legacy approval mode) rejected the account."""


class IdentityRejectedError(AuthenticationDeniedError):
    """The token is valid but is not a verified Google account."""


class AccessNotApprovedError(Exception):
    def __init__(self, status: str) -> None:
        super().__init__("This account is not approved for Side-B")
        self.status = status


class AdminRequiredError(Exception):
    pass


class AccessManagementDisabledError(Exception):
    pass


class FeatureRateLimitError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Backend request rate exceeded")
        self.retry_after = max(1, retry_after)


@dataclass(frozen=True)
class AuthenticatedUser:
    uid: str
    email: str | None = None
    display_name: str | None = None
    provider: str = "legacy"


class FirebaseTokenVerifier:
    """Verify Firebase ID tokens with an explicitly selected Firebase project."""

    def __init__(
        self,
        project_id: str,
        *,
        verify_transport: Callable[[str], Mapping[str, Any]] | None = None,
        verify_timeout_seconds: float = 8.0,
        http_timeout_seconds: float = 5.0,
        max_concurrency: int = 8,
    ) -> None:
        self.project_id = str(project_id or "").strip()
        self._verify_transport = verify_transport
        self._verify_timeout_seconds = max(1.0, verify_timeout_seconds)
        self._http_timeout_seconds = max(1.0, http_timeout_seconds)
        self._verify_slots = asyncio.Semaphore(max(1, max_concurrency))
        self._app = None
        self._app_lock = threading.Lock()

    def _firebase_app(self):
        if not self.project_id:
            raise AuthenticationConfigurationError(
                "FIREBASE_PROJECT_ID is not configured"
            )
        if self._app is not None:
            return self._app
        with self._app_lock:
            if self._app is None:
                import firebase_admin

                name = f"side-b-auth-{self.project_id}"
                try:
                    self._app = firebase_admin.get_app(name)
                except ValueError:
                    self._app = firebase_admin.initialize_app(
                        options={
                            "projectId": self.project_id,
                            "httpTimeout": self._http_timeout_seconds,
                        },
                        name=name,
                    )
        return self._app

    def _verify(self, token: str) -> Mapping[str, Any]:
        if self._verify_transport is not None:
            return self._verify_transport(token)

        from firebase_admin import auth
        from google.auth import exceptions as google_auth_exceptions

        try:
            return auth.verify_id_token(
                token,
                app=self._firebase_app(),
                check_revoked=True,
            )
        except (
            auth.ExpiredIdTokenError,
            auth.InvalidIdTokenError,
            auth.RevokedIdTokenError,
            auth.UserDisabledError,
            auth.UserNotFoundError,
        ) as exc:
            raise AuthenticationError("Invalid Firebase ID token") from exc
        except AuthenticationConfigurationError:
            raise
        except (
            auth.CertificateFetchError,
            auth.ConfigurationNotFoundError,
            auth.InsufficientPermissionError,
            auth.UnexpectedResponseError,
            google_auth_exceptions.GoogleAuthError,
            OSError,
        ) as exc:
            raise AuthenticationUnavailableError(
                "Firebase token verification is unavailable"
            ) from exc
        except Exception as exc:
            raise AuthenticationUnavailableError(
                "Firebase token verification is unavailable"
            ) from exc

    async def verify(self, token: str) -> Mapping[str, Any]:
        if not self.project_id:
            raise AuthenticationConfigurationError(
                "FIREBASE_PROJECT_ID is not configured"
            )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._verify_timeout_seconds
        try:
            await asyncio.wait_for(
                self._verify_slots.acquire(),
                timeout=self._verify_timeout_seconds,
            )
        except TimeoutError as exc:
            raise AuthenticationUnavailableError(
                "Firebase verification capacity is unavailable"
            ) from exc

        future = loop.run_in_executor(None, self._verify, token)
        # A timed-out caller must not make the still-running worker look free.
        future.add_done_callback(lambda _future: self._verify_slots.release())
        remaining = max(0.001, deadline - loop.time())
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=remaining)
        except TimeoutError as exc:
            raise AuthenticationUnavailableError(
                "Firebase token verification timed out"
            ) from exc


class AuthenticationService:
    """Identity verification, plus the legacy environment allowlist.

    With ``enforce_allowlist=False`` the service answers only "who is this?";
    approval then comes from the Firestore access store. That combination is
    restricted to Firebase-only mode so neither the shared legacy token nor an
    environment allowlist can stand in for a stored approval.
    """

    def __init__(
        self,
        *,
        mode: AuthMode,
        legacy_token: str | None,
        firebase_verifier: FirebaseTokenVerifier,
        allowed_uids: frozenset[str] = frozenset(),
        allowed_emails: frozenset[str] = frozenset(),
        unauthenticated_legacy_features: frozenset[str] = frozenset(),
        enforce_allowlist: bool = True,
    ) -> None:
        if not enforce_allowlist and (
            mode != "firebase"
            or allowed_uids
            or allowed_emails
            or unauthenticated_legacy_features
        ):
            raise ValueError(
                "External approval requires Firebase-only mode without allowlists"
            )
        self.mode = mode
        self.firebase_project_id = firebase_verifier.project_id
        self._legacy_token = str(legacy_token or "").strip()
        self._firebase_verifier = firebase_verifier
        self._allowed_uids = allowed_uids
        self._allowed_emails = frozenset(email.casefold() for email in allowed_emails)
        self._unauthenticated_legacy_features = unauthenticated_legacy_features
        self.enforce_allowlist = enforce_allowlist

    @property
    def config(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "firebase_project_id": (
                self.firebase_project_id
                if self.mode in {"dual", "firebase"} and self.firebase_project_id
                else None
            ),
        }

    @staticmethod
    def _bearer_token(authorization: str | None) -> str | None:
        if authorization is None:
            return None
        parts = authorization.strip().split()
        if len(parts) != 2 or parts[0].casefold() != "bearer" or not parts[1]:
            raise AuthenticationError("Malformed bearer credential")
        return parts[1]

    async def _authenticate_firebase(self, token: str) -> AuthenticatedUser:
        claims = await self._firebase_verifier.verify(token)
        project_id = self.firebase_project_id
        if (
            claims.get("aud") != project_id
            or claims.get("iss") != f"https://securetoken.google.com/{project_id}"
        ):
            raise AuthenticationError("Firebase token project does not match")

        uid = str(claims.get("uid") or claims.get("sub") or "").strip()
        firebase = claims.get("firebase")
        provider = (
            firebase.get("sign_in_provider") if isinstance(firebase, dict) else None
        )
        email = str(claims.get("email") or "").strip()
        if (
            not uid
            or provider != "google.com"
            or claims.get("email_verified") is not True
        ):
            raise IdentityRejectedError("A verified Google account is required")
        if self.enforce_allowlist and not (
            uid in self._allowed_uids
            or (email and email.casefold() in self._allowed_emails)
        ):
            raise AuthenticationDeniedError("This account is not approved")

        display_name = str(claims.get("name") or "").strip() or None
        return AuthenticatedUser(
            uid=uid,
            email=email or None,
            display_name=display_name,
            provider=provider,
        )

    async def authenticate(
        self,
        *,
        feature: AuthFeature,
        authorization: str | None,
        legacy_token: str | None,
    ) -> AuthenticatedUser:
        bearer = self._bearer_token(authorization)
        legacy = str(legacy_token or "").strip()

        if bearer is not None:
            if legacy:
                raise AuthenticationError("Mixed authentication credentials")
            if self.mode == "legacy":
                raise AuthenticationError("Bearer authentication is not enabled")
            return await self._authenticate_firebase(bearer)

        if self.mode == "firebase":
            raise AuthenticationError("A Firebase bearer credential is required")
        if feature in self._unauthenticated_legacy_features and not self._legacy_token:
            return AuthenticatedUser(uid="legacy-anonymous", provider="anonymous")
        if not self._legacy_token:
            raise AuthenticationConfigurationError(
                "SIDE_B_ACCESS_TOKEN is not configured"
            )
        if not legacy or not secrets.compare_digest(legacy, self._legacy_token):
            raise AuthenticationError("Invalid legacy backend access token")
        return AuthenticatedUser(uid="legacy-shared", provider="legacy")


@dataclass
class _Bucket:
    events: deque[float]
    last_seen: float


class FeatureRateLimiter:
    """Instance-local user/feature limits with separate aggregate backstops."""

    def __init__(
        self,
        *,
        user_limits: Mapping[AuthFeature, int],
        aggregate_limits: Mapping[AuthFeature, int],
        window_seconds: float = 60.0,
        inactive_ttl_seconds: float = 600.0,
        max_user_buckets: int = 1_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._user_limits = dict(user_limits)
        self._aggregate_limits = dict(aggregate_limits)
        self._window_seconds = max(1.0, window_seconds)
        self._inactive_ttl_seconds = max(self._window_seconds, inactive_ttl_seconds)
        self._max_user_buckets = max(1, max_user_buckets)
        self._clock = clock
        self._user_buckets: dict[tuple[str, AuthFeature], _Bucket] = {}
        self._aggregate: dict[AuthFeature, deque[float]] = {
            feature: deque() for feature in self._aggregate_limits
        }
        self._lock = asyncio.Lock()

    @staticmethod
    def _purge(events: deque[float], cutoff: float) -> None:
        while events and events[0] <= cutoff:
            events.popleft()

    def _retry_after(self, events: deque[float], now: float) -> int:
        return max(1, math.ceil(self._window_seconds - (now - events[0])))

    def _cleanup_inactive(self, now: float, cutoff: float) -> None:
        stale_before = now - self._inactive_ttl_seconds
        for key, bucket in list(self._user_buckets.items()):
            self._purge(bucket.events, cutoff)
            if not bucket.events and bucket.last_seen <= stale_before:
                del self._user_buckets[key]

    async def consume(self, uid: str, feature: AuthFeature) -> None:
        async with self._lock:
            now = self._clock()
            cutoff = now - self._window_seconds
            self._cleanup_inactive(now, cutoff)

            aggregate = self._aggregate.setdefault(feature, deque())
            self._purge(aggregate, cutoff)
            aggregate_limit = max(1, self._aggregate_limits[feature])
            if len(aggregate) >= aggregate_limit:
                raise FeatureRateLimitError(self._retry_after(aggregate, now))

            key = (uid, feature)
            bucket = self._user_buckets.get(key)
            if bucket is None:
                if len(self._user_buckets) >= self._max_user_buckets:
                    raise FeatureRateLimitError(1)
                bucket = _Bucket(events=deque(), last_seen=now)
                self._user_buckets[key] = bucket
            self._purge(bucket.events, cutoff)
            user_limit = max(1, self._user_limits[feature])
            if len(bucket.events) >= user_limit:
                bucket.last_seen = now
                raise FeatureRateLimitError(self._retry_after(bucket.events, now))

            bucket.events.append(now)
            bucket.last_seen = now
            aggregate.append(now)

    @property
    def user_bucket_count(self) -> int:
        return len(self._user_buckets)


AUTHORIZATION_ERRORS = (
    AuthenticationConfigurationError,
    AuthenticationUnavailableError,
    AuthenticationDeniedError,
    AuthenticationError,
    FeatureRateLimitError,
    AccessNotApprovedError,
    AccessStoreUnavailableError,
    AdminRequiredError,
    AccessManagementDisabledError,
    AccessTargetProtectedError,
    AccessUserNotFoundError,
    AccessConflictError,
    AccessRequestQuotaExceededError,
    InvalidAccessInputError,
)


def _error(
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, **extra},
        headers=headers,
    )


def http_error(exc: Exception) -> HTTPException:
    """Map auth and approval outcomes to the documented status/code contract."""
    if isinstance(exc, AuthenticationConfigurationError):
        return _error(503, "auth_configuration_error", str(exc))
    if isinstance(exc, AuthenticationUnavailableError):
        return _error(
            503,
            "auth_verification_unavailable",
            "로그인 확인 서비스를 일시적으로 사용할 수 없습니다.",
        )
    if isinstance(exc, IdentityRejectedError):
        return _error(403, "auth_identity_unverified", str(exc))
    if isinstance(exc, AuthenticationDeniedError):
        return _error(403, "auth_account_denied", str(exc))
    if isinstance(exc, AuthenticationError):
        return _error(401, "auth_unauthorized", str(exc))
    if isinstance(exc, FeatureRateLimitError):
        return _error(
            429,
            "auth_rate_limited",
            "요청이 너무 많습니다.",
            headers={"Retry-After": str(exc.retry_after)},
        )
    if isinstance(exc, AccessNotApprovedError):
        return _error(
            403,
            "access_not_approved",
            "관리자가 승인한 계정만 사용할 수 있습니다.",
            access_status=exc.status,
        )
    if isinstance(exc, AccessStoreUnavailableError):
        # Never downgrade an outage to "not approved" or to an env allowlist.
        return _error(
            503,
            "access_store_unavailable",
            "계정 승인 상태를 일시적으로 확인할 수 없습니다.",
        )
    if isinstance(exc, AdminRequiredError):
        return _error(403, "admin_required", "관리자 권한이 필요합니다.")
    if isinstance(exc, AccessManagementDisabledError):
        return _error(
            404,
            "access_management_disabled",
            "이 서버는 계정 승인 관리를 사용하지 않습니다.",
        )
    if isinstance(exc, AccessTargetProtectedError):
        return _error(
            403,
            "access_admin_target_protected",
            "관리자 계정의 상태는 서버 설정으로만 변경할 수 있습니다.",
        )
    if isinstance(exc, AccessUserNotFoundError):
        return _error(404, "access_user_not_found", "신청 기록이 없는 계정입니다.")
    if isinstance(exc, AccessConflictError):
        return _error(
            409,
            exc.code,
            exc.message,
            current_status=exc.current_status,
            current_revision=exc.current_revision,
        )
    if isinstance(exc, AccessRequestQuotaExceededError):
        return _error(
            429,
            "access_request_quota_exceeded",
            "오늘 받을 수 있는 사용 신청 수를 초과했습니다. 내일 다시 신청하세요.",
            headers={"Retry-After": "3600"},
        )
    if isinstance(exc, InvalidAccessInputError):
        return _error(422, "access_invalid_input", str(exc))
    raise TypeError(f"Unhandled authorization error: {type(exc).__name__}")


def access_store(request: Request):
    """Return the Firestore approval store, or None for env-allowlist mode.

    A store paired with anything but allowlist-free Firebase-only identity is a
    configuration error and fails closed instead of mixing approval sources.
    """
    store = getattr(request.app.state, "access_store", None)
    if store is None:
        return None
    service = request.app.state.auth_service
    if service.mode != "firebase" or service.enforce_allowlist:
        raise AuthenticationConfigurationError(
            "Firestore approval requires Firebase-only authentication"
        )
    return store


async def authenticate_identity(
    request: Request,
    *,
    feature: AuthFeature,
    authorization: str | None,
    legacy_token: str | None = None,
    auth_feature: AuthFeature | None = None,
) -> AuthenticatedUser:
    """Verify the caller and charge one request to ``feature``'s budget.

    ``auth_feature`` selects the legacy anonymous-development semantics when it
    differs from the budget, as for ``/auth/me``.
    """
    user = await request.app.state.auth_service.authenticate(
        feature=auth_feature or feature,
        authorization=authorization,
        legacy_token=legacy_token,
    )
    await request.app.state.feature_rate_limiter.consume(user.uid, feature)
    return user


async def _authorize_feature(
    request: Request,
    feature: AuthFeature,
    authorization: str | None,
    legacy_token: str | None,
) -> AuthenticatedUser:
    try:
        store = access_store(request)
        if store is None:
            return await authenticate_identity(
                request,
                feature=feature,
                authorization=authorization,
                legacy_token=legacy_token,
            )
        # The per-user budget before the store read bounds database reads. The
        # feature budget is charged only to approved users, so unapproved
        # accounts cannot drain it.
        user = await authenticate_identity(
            request,
            feature="access_lookup",
            authorization=authorization,
            legacy_token=legacy_token,
        )
        record = await store.get(user.uid)
        status = record.status if record is not None else "unregistered"
        if status != "approved":
            raise AccessNotApprovedError(status)
        await request.app.state.feature_rate_limiter.consume(user.uid, feature)
        return user
    except AUTHORIZATION_ERRORS as exc:
        raise http_error(exc) from exc


async def authorize_recommend(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    legacy_token: str | None = Header(default=None, alias="X-Side-B-Access-Token"),
) -> AuthenticatedUser:
    return await _authorize_feature(request, "recommend", authorization, legacy_token)


async def authorize_genre(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    legacy_token: str | None = Header(default=None, alias="X-Side-B-Access-Token"),
) -> AuthenticatedUser:
    return await _authorize_feature(request, "genre", authorization, legacy_token)


async def authorize_youtube_export(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    legacy_token: str | None = Header(default=None, alias="X-Side-B-Export-Token"),
) -> AuthenticatedUser:
    return await _authorize_feature(
        request, "youtube_export", authorization, legacy_token
    )


async def authorize_preview(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    legacy_token: str | None = Header(default=None, alias="X-Side-B-Access-Token"),
) -> AuthenticatedUser | None:
    """Approved users only when approvals live in Firestore.

    Environment-allowlist deployments keep the historical public preview, so
    existing legacy clients and local development are unchanged.
    """
    try:
        if access_store(request) is None:
            return None
    except AUTHORIZATION_ERRORS as exc:
        raise http_error(exc) from exc
    return await _authorize_feature(request, "preview", authorization, legacy_token)


async def _authorize_admin(
    request: Request, feature: AuthFeature, authorization: str | None
) -> AuthenticatedUser:
    try:
        if access_store(request) is None:
            raise AccessManagementDisabledError()
        user = await request.app.state.auth_service.authenticate(
            feature=feature, authorization=authorization, legacy_token=None
        )
        # Re-checked on every call, including idempotent replays, against the
        # server setting and never against anything the client sends.
        if user.uid not in request.app.state.admin_uids:
            raise AdminRequiredError()
        await request.app.state.feature_rate_limiter.consume(user.uid, feature)
        return user
    except AUTHORIZATION_ERRORS as exc:
        raise http_error(exc) from exc


async def authorize_admin_read(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> AuthenticatedUser:
    return await _authorize_admin(request, "admin_read", authorization)


async def authorize_admin_write(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> AuthenticatedUser:
    return await _authorize_admin(request, "admin_write", authorization)


async def authorize_access_request(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> AuthenticatedUser:
    """Any verified Google identity may ask for access, but only for itself."""
    try:
        if access_store(request) is None:
            raise AccessManagementDisabledError()
        return await authenticate_identity(
            request, feature="access_request", authorization=authorization
        )
    except AUTHORIZATION_ERRORS as exc:
        raise http_error(exc) from exc
