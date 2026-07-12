import secrets
from hashlib import sha256
import jwt
from fastapi import Depends, Header, HTTPException, status

from .config import (
    API_AUTH_TOKEN,
    REQUIRE_API_AUTH,
    RATE_LIMIT_PER_MINUTE,
    DAILY_REQUEST_QUOTA,
    AUTH_JWKS_URL,
    AUTH_ISSUER,
    AUTH_AUDIENCE,
)
from .usage_limits import InMemoryUsageLimiter

usage_limiter = InMemoryUsageLimiter(
    per_minute_limit=RATE_LIMIT_PER_MINUTE, daily_quota=DAILY_REQUEST_QUOTA
)


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def _verify_jwt_token(token: str) -> str:
    if not (AUTH_JWKS_URL and AUTH_ISSUER and AUTH_AUDIENCE):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT auth not configured on server",
        )
    try:
        jwk_client = jwt.PyJWKClient(AUTH_JWKS_URL)
        signing_key = jwk_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=AUTH_AUDIENCE,
            issuer=AUTH_ISSUER,
        )
        user_sub = str(payload.get("sub") or "").strip()
        if not user_sub:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="JWT missing subject",
            )
        return f"user:{user_sub}"
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid JWT token",
        )


def require_api_auth(authorization: str | None = Header(default=None)) -> str:
    """Auth dependency with JWT-first verification and token fallback."""
    if not REQUIRE_API_AUTH:
        return "anonymous-local-dev"

    token = _extract_bearer_token(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization token",
        )

    # Production path: validate user JWT (Cognito/OIDC compatible).
    if AUTH_JWKS_URL and AUTH_ISSUER and AUTH_AUDIENCE:
        return _verify_jwt_token(token)

    # Fallback path: static token for local/dev bootstrap.
    if not API_AUTH_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Server auth misconfigured",
        )
    if not secrets.compare_digest(token, API_AUTH_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )
    return f"token:{sha256(token.encode('utf-8')).hexdigest()[:16]}"


def enforce_usage_limits(user_key: str = Depends(require_api_auth)) -> str:
    result = usage_limiter.check_and_record(user_key)
    if not result.ok:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=result.reason or "Usage limit exceeded",
        )
    return user_key
