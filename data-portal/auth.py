"""
Authentication, rate limiting, and payment verification for the Data Portal.

Provides:
- API key validation against Firestore
- Sliding-window rate limiter (in-memory)
- x402 USDC payment verification (Base L2)
- GCS signed URL generation
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections import defaultdict
from datetime import timedelta
from typing import Optional

import httpx
from fastapi import Depends, Header, HTTPException, Request
from google.cloud import firestore, storage

logger = logging.getLogger("data-portal.auth")

X402_FACILITATOR_URL = os.environ.get(
    "X402_FACILITATOR_URL",
    "https://api.cdp.coinbase.com/platform/v2/x402",
)
BASE_WALLET_ADDRESS = os.environ.get("BASE_WALLET_ADDRESS", "0xFE141943a93c184606F3060103D975662327063B")
X402_TEST_MODE = os.environ.get("X402_TEST_MODE", "false").lower() == "true"

# Coinbase CDP API credentials for facilitator authentication
CDP_API_KEY_ID = os.environ.get("CDP_API_KEY_ID", "")
CDP_API_KEY_SECRET = os.environ.get("CDP_API_KEY_SECRET", "")

# ---------------------------------------------------------------------------
# GCS Signed URLs
# ---------------------------------------------------------------------------

_storage_client: storage.Client | None = None


def _get_storage_client() -> storage.Client:
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client()
    return _storage_client


def generate_signed_url(
    bucket_name: str,
    blob_path: str,
    expiration_hours: int = 1,
) -> str:
    """Generate a V4 signed URL for a GCS object.

    On Cloud Run, uses IAM-based signing (no private key required).
    Requires the service account to have iam.serviceAccountTokenCreator on itself.

    Args:
        bucket_name: GCS bucket name.
        blob_path: Object path within the bucket.
        expiration_hours: URL validity in hours (1 for preview, 24 for purchases).

    Returns:
        Signed URL string.
    """
    import google.auth

    client = _get_storage_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)

    # On Cloud Run, use IAM-based signing (no private key needed).
    # Requires roles/iam.serviceAccountTokenCreator on the compute SA.
    credentials, project = google.auth.default()
    sa_email = getattr(credentials, "service_account_email", None)

    if sa_email and not credentials.token:
        from google.auth.transport.requests import Request
        credentials.refresh(Request())

    url = blob.generate_signed_url(
        version="v4",
        expiration=timedelta(hours=expiration_hours),
        method="GET",
        service_account_email=sa_email,
        access_token=credentials.token,
    )
    return url


# ---------------------------------------------------------------------------
# API Key validation
# ---------------------------------------------------------------------------


async def validate_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> dict:
    """Validate an API key against Firestore ``data_portal_api_keys`` collection.

    Returns the key document data on success.
    Raises 401 if missing/invalid, 403 if revoked.
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    db: firestore.AsyncClient = request.state.db
    doc_ref = db.collection("data_portal_api_keys").document(key_hash)
    doc = await doc_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=401, detail="Invalid API key")

    key_data = doc.to_dict()
    if key_data.get("revoked", False):
        raise HTTPException(status_code=403, detail="API key has been revoked")

    # Update last-used timestamp (fire-and-forget)
    try:
        await doc_ref.update({"last_used": firestore.SERVER_TIMESTAMP})
    except Exception:
        pass  # non-critical

    return key_data


# ---------------------------------------------------------------------------
# In-memory sliding window rate limiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Firestore-backed rate limiter for multi-instance Cloud Run deployments.

    Uses Firestore documents with TTL for automatic cleanup.
    Falls back to in-memory if Firestore is unavailable.
    """

    def __init__(self):
        self._memory: dict[str, list[float]] = defaultdict(list)
        self._db: firestore.AsyncClient | None = None

    def set_db(self, db: firestore.AsyncClient):
        """Set the Firestore client for persistent rate limiting."""
        self._db = db

    async def check_async(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """Return True if the request is allowed, False if rate-limited.

        Uses Firestore for persistence across Cloud Run instances.
        """
        if self._db:
            try:
                return await self._check_firestore(key, max_requests, window_seconds)
            except Exception as e:
                logger.warning("Firestore rate limit check failed, using in-memory: %s", e)

        return self._check_memory(key, max_requests, window_seconds)

    async def _check_firestore(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """Check rate limit against Firestore counter document."""
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc)
        # Use day-based window key for daily limits
        if window_seconds >= 86400:
            window_key = now.strftime("%Y-%m-%d")
        else:
            window_key = str(int(now.timestamp()) // window_seconds)

        doc_id = f"{key}:{window_key}"
        doc_ref = self._db.collection("rate_limits").document(doc_id)

        @firestore.async_transactional
        async def update_in_txn(txn, ref):
            doc = await ref.get(transaction=txn)
            if doc.exists:
                data = doc.to_dict()
                count = data.get("count", 0)
                if count >= max_requests:
                    return False
                txn.update(ref, {"count": count + 1})
                return True
            else:
                # Set TTL for automatic cleanup (window + 1 hour buffer)
                expire_at = now + datetime.timedelta(seconds=window_seconds + 3600)
                txn.set(ref, {
                    "key": key,
                    "count": 1,
                    "window": window_key,
                    "created_at": now,
                    "expire_at": expire_at,
                })
                return True

        txn = self._db.transaction()
        return await update_in_txn(txn, doc_ref)

    def _check_memory(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """Fallback in-memory check."""
        now = time.monotonic()
        cutoff = now - window_seconds
        self._memory[key] = [t for t in self._memory[key] if t > cutoff]
        if len(self._memory[key]) >= max_requests:
            return False
        self._memory[key].append(now)
        return True

    def check(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """Synchronous fallback — in-memory only."""
        return self._check_memory(key, max_requests, window_seconds)

    def remaining(self, key: str, max_requests: int, window_seconds: int) -> int:
        now = time.monotonic()
        cutoff = now - window_seconds
        active = [t for t in self._memory[key] if t > cutoff]
        return max(0, max_requests - len(active))


# Singleton limiter
rate_limiter = RateLimiter()


def get_client_fingerprint(request: Request) -> str:
    """Extract a reliable client identifier from the request.

    Uses X-Forwarded-For (real client IP behind Cloud Run LB),
    falls back to direct connection IP. Also includes wallet
    address if present for double-keying.
    """
    # Cloud Run sets X-Forwarded-For with the real client IP
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        # First IP in chain is the original client
        client_ip = forwarded.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else "unknown"

    # Also key on wallet address if present (prevents wallet-hopping)
    wallet = request.headers.get("X-Wallet-Address", "")

    if wallet:
        return f"{client_ip}|{wallet}"
    return client_ip


def require_rate_limit(endpoint: str, max_requests: int, window_seconds: int):
    """FastAPI dependency factory for rate limiting.

    Usage::

        @router.get("/preview", dependencies=[Depends(require_rate_limit("preview", 10, 86400))])
    """

    async def _check(request: Request):
        # Key on API key if present, else client IP
        api_key = request.headers.get("X-API-Key", "")
        client_id = api_key or request.client.host if request.client else "unknown"
        limiter_key = f"{endpoint}:{client_id}"
        if not rate_limiter.check(limiter_key, max_requests, window_seconds):
            remaining = rate_limiter.remaining(limiter_key, max_requests, window_seconds)
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for {endpoint}. {remaining} requests remaining.",
                headers={"Retry-After": str(window_seconds)},
            )

    return _check


# ---------------------------------------------------------------------------
# x402 Payment verification
# ---------------------------------------------------------------------------


class X402PaymentResult:
    """Result of an x402 payment verification."""

    def __init__(self, valid: bool, amount_usd: float = 0.0, tx_hash: str = "", error: str = ""):
        self.valid = valid
        self.amount_usd = amount_usd
        self.tx_hash = tx_hash
        self.error = error


async def verify_x402_payment(
    payment_header: str,
    required_amount_usd: float,
) -> X402PaymentResult:
    """Verify an x402 payment from payment headers.

    Supports both V1 (X-PAYMENT) and V2 (PAYMENT-SIGNATURE) formats.

    In test mode (X402_TEST_MODE=true), accepts any non-empty header as valid
    with the required amount. In production, contacts the x402 facilitator
    to settle the Base L2 USDC payment.

    Args:
        payment_header: Contents of the X-PAYMENT or PAYMENT-SIGNATURE header.
        required_amount_usd: Minimum payment amount in USD.

    Returns:
        X402PaymentResult with verification outcome.
    """
    if not payment_header:
        return X402PaymentResult(valid=False, error="Missing payment header")

    # Test mode -- accept anything for development
    if X402_TEST_MODE:
        logger.warning("x402 test mode: accepting payment without verification")
        return X402PaymentResult(
            valid=True,
            amount_usd=required_amount_usd,
            tx_hash="test-mode-no-tx",
        )

    # Network-aware USDC address
    x402_network = os.environ.get("X402_NETWORK", "eip155:8453")
    USDC_ADDRESSES = {
        "eip155:8453": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",   # Base mainnet
        "eip155:84532": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # Base Sepolia
    }
    USDC_ASSET = USDC_ADDRESSES.get(x402_network, USDC_ADDRESSES["eip155:8453"])

    # EIP-712 domain info for USDC (required by facilitator for signature verification)
    USDC_EIP712_DOMAINS = {
        "eip155:8453": {"name": "USD Coin", "version": "2"},
        "eip155:84532": {"name": "USDC", "version": "2"},
    }
    usdc_domain = USDC_EIP712_DOMAINS.get(x402_network, USDC_EIP712_DOMAINS["eip155:8453"])

    # Production: settle via x402 facilitator (handles both V1 and V2)
    try:
        import base64
        import json as _json

        # Decode the payment signature (base64 JSON)
        try:
            decoded_bytes = base64.b64decode(payment_header)
            payment_payload = _json.loads(decoded_bytes)
        except Exception:
            # Might be raw JSON or V1 format
            try:
                payment_payload = _json.loads(payment_header)
            except Exception:
                return X402PaymentResult(valid=False, error="Cannot decode payment header")

        version = payment_payload.get("x402Version", 1)

        # Build the requirements that match what we originally sent
        amount_smallest = str(int(round(required_amount_usd * 1_000_000)))
        requirements = {
            "scheme": "exact",
            "network": x402_network,
            "asset": USDC_ASSET,
            "amount": amount_smallest,
            "payTo": BASE_WALLET_ADDRESS,
            "maxTimeoutSeconds": 300,
            "extra": usdc_domain,
        }

        # Call facilitator settle endpoint
        settle_body = {
            "x402Version": version,
            "paymentPayload": payment_payload,
            "paymentRequirements": requirements,
        }

        headers = {"Content-Type": "application/json"}

        # Add CDP JWT auth if using Coinbase facilitator
        if "cdp.coinbase.com" in X402_FACILITATOR_URL and CDP_API_KEY_ID and CDP_API_KEY_SECRET:
            try:
                from coinbase_jwt import _build_cdp_jwt
                jwt_token = _build_cdp_jwt(
                    CDP_API_KEY_ID, CDP_API_KEY_SECRET,
                    "api.cdp.coinbase.com",
                    "/platform/v2/x402/settle",
                )
                headers["Authorization"] = f"Bearer {jwt_token}"
            except ImportError:
                logger.warning("coinbase_jwt module not available, trying without auth")

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.post(
                f"{X402_FACILITATOR_URL}/settle",
                json=settle_body,
                headers=headers,
            )

        if resp.status_code != 200:
            logger.warning("x402 facilitator settle failed: %s %s", resp.status_code, resp.text[:500])
            return X402PaymentResult(
                valid=False,
                error=f"Facilitator settle failed ({resp.status_code}): {resp.text[:200]}",
            )

        raw = resp.json()
        logger.info("x402 facilitator response: %s", _json.dumps(raw)[:500] if isinstance(raw, dict) else str(raw)[:500])

        # Facilitator may return a dict or a plain string (tx hash).
        if isinstance(raw, str):
            # Treat a bare string as a successful tx hash
            return X402PaymentResult(valid=True, amount_usd=required_amount_usd, tx_hash=raw)

        if not isinstance(raw, dict):
            return X402PaymentResult(valid=False, error=f"Unexpected facilitator response type: {type(raw).__name__}")

        success = raw.get("success", False)
        # transaction can be a string (tx hash) or dict with txHash
        tx_field = raw.get("transaction", raw.get("txHash", ""))
        tx_hash = tx_field.get("txHash", "") if isinstance(tx_field, dict) else str(tx_field)

        if not success:
            return X402PaymentResult(valid=False, error=raw.get("error", "Settlement failed"))

        logger.info("x402 payment settled: tx=%s amount=$%.2f", tx_hash, required_amount_usd)
        return X402PaymentResult(
            valid=True,
            amount_usd=required_amount_usd,
            tx_hash=tx_hash,
        )

    except httpx.TimeoutException:
        return X402PaymentResult(valid=False, error="x402 facilitator timeout")
    except Exception as e:
        logger.exception("x402 verification failed")
        return X402PaymentResult(valid=False, error=f"Verification error: {e}")


async def require_x402_payment(
    request: Request,
    x_payment: Optional[str] = Header(None, alias="X-PAYMENT"),
    payment_sig: Optional[str] = Header(None, alias="PAYMENT-SIGNATURE"),
) -> X402PaymentResult:
    """FastAPI dependency that requires a valid x402 payment header.

    Supports both V1 (X-PAYMENT) and V2 (PAYMENT-SIGNATURE) headers.
    The required amount must be set on request.state.x402_required_amount
    before this dependency runs, or it defaults to 0.01 USD.
    """
    payment_header = payment_sig or x_payment or ""
    required = getattr(request.state, "x402_required_amount", 0.01)
    result = await verify_x402_payment(payment_header, required)
    if not result.valid:
        raise HTTPException(
            status_code=402,
            detail=f"Payment required: {result.error}",
            headers={
                "X-PAYMENT-REQUIRED": str(required),
                "X-PAYMENT-CURRENCY": "USDC",
                "X-PAYMENT-CHAIN": "base",
                "X-PAYMENT-RECIPIENT": BASE_WALLET_ADDRESS,
            },
        )
    return result
