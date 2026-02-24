"""Coinbase CDP JWT generation for x402 facilitator authentication.

Generates ES256 JWTs compatible with the Coinbase Developer Platform API.
Used by auth.py to authenticate with the CDP x402 facilitator at
https://api.cdp.coinbase.com/platform/v2/x402/settle
"""

import json
import time
import uuid
from typing import Optional

import cryptography.hazmat.primitives.asymmetric.ec as ec
from cryptography.hazmat.primitives import serialization
import jwt  # PyJWT


def _build_cdp_jwt(
    api_key_id: str,
    api_key_secret: str,
    request_host: str,
    request_path: str,
    request_method: str = "POST",
) -> str:
    """Build a CDP-compatible ES256 JWT for API authentication.

    Args:
        api_key_id: CDP API key ID (from Coinbase Developer Portal).
        api_key_secret: CDP API key secret (EC private key in PEM format).
        request_host: API host (e.g., "api.cdp.coinbase.com").
        request_path: API path (e.g., "/platform/v2/x402/settle").
        request_method: HTTP method (default "POST").

    Returns:
        Signed JWT string.
    """
    now = int(time.time())
    uri = f"{request_method} {request_host}{request_path}"

    payload = {
        "sub": api_key_id,
        "iss": "cdp",
        "aud": ["cdp_service"],
        "nbf": now,
        "exp": now + 120,  # 2 minute expiry
        "uris": [uri],
    }

    headers = {
        "kid": api_key_id,
        "nonce": uuid.uuid4().hex,
        "typ": "JWT",
    }

    # The CDP API key secret is an EC private key in PEM format
    # Handle both raw PEM and escaped newline formats
    secret = api_key_secret.replace("\\n", "\n")
    if not secret.startswith("-----"):
        secret = f"-----BEGIN EC PRIVATE KEY-----\n{secret}\n-----END EC PRIVATE KEY-----"

    return jwt.encode(payload, secret, algorithm="ES256", headers=headers)
