"""
Verilian Reader API — Decode Golden Codex metadata from infused images
======================================================================

FREE (5/day) then $0.05 USDC per read via x402.
Unlimited reader access unlocked with any data purchase.

Endpoints:
  GET /agent/reader              — Info + GitHub reader link
  GET /agent/reader/{artifact_id} — Decode an artifact's embedded metadata
"""

from __future__ import annotations

import base64
import gzip
import json
import logging
import os
import subprocess
import tempfile
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from google.cloud import storage as gcs

from auth import get_client_fingerprint, rate_limiter, verify_x402_payment, BASE_WALLET_ADDRESS

logger = logging.getLogger("data-portal.reader")

router = APIRouter(prefix="/agent", tags=["reader"])

# Bucket containing the _final.png infused images
ASSETS_BUCKET = os.environ.get("ASSETS_BUCKET", "codex-aeternum-assets")
IMAGES_PREFIX = "alexandria-aeternum/images"

# Rate limit: 5 free reads/day, then $0.05 per read
FREE_READS_PER_DAY = 5
READER_PRICE = 0.05  # $0.05 USDC per read after free tier

# Fields to check for Golden Codex payload (priority order)
CODEX_PAYLOAD_FIELDS = [
    "XMP-gc:CodexPayload",
    "XMP-gcodex:CodexPayload",
    "XMP-artiswa:GoldenCodex",
    "CodexPayload",
    "GoldenCodex",
]


# ---------------------------------------------------------------------------
# Core decode functions (from Verilian agent)
# ---------------------------------------------------------------------------


def _decode_codex_payload(encoded: str) -> dict:
    """Decode Golden Codex payload: base64 → gzip → JSON."""
    try:
        decoded_bytes = base64.b64decode(encoded)
        json_bytes = gzip.decompress(decoded_bytes)
        return json.loads(json_bytes.decode("utf-8"))
    except Exception:
        return json.loads(encoded)


def _extract_metadata_exiftool(image_path: str) -> dict:
    """Extract all metadata from image using ExifTool."""
    cmd = ["exiftool", "-json", "-a", "-G1", image_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        logger.warning("ExifTool stderr: %s", result.stderr[:200])
    try:
        metadata_list = json.loads(result.stdout)
        return metadata_list[0] if metadata_list else {}
    except json.JSONDecodeError as e:
        logger.error("Failed to parse ExifTool output: %s", e)
        return {}


def _find_codex_payload(metadata: dict) -> Optional[str]:
    """Find Golden Codex payload in metadata, checking multiple field variants."""
    for field in CODEX_PAYLOAD_FIELDS:
        if field in metadata and metadata[field]:
            logger.info("Found Golden Codex in field: %s", field)
            return metadata[field]
    # Fallback: scan all keys
    for key, value in metadata.items():
        key_lower = key.lower()
        if ("codexpayload" in key_lower or "goldencodex" in key_lower):
            if value and isinstance(value, str) and len(value) > 50:
                logger.info("Found Golden Codex in field: %s", key)
                return value
    return None


def _calculate_verification(raw_metadata: dict, golden_codex: dict | None) -> dict:
    """Calculate verification score and stats."""
    total_exif_fields = sum(1 for v in raw_metadata.values() if v)
    has_codex = golden_codex is not None
    codex_fields = 0
    codex_sections = []

    if has_codex:
        for key, value in golden_codex.items():
            if key.startswith("_"):
                continue
            codex_sections.append(key)
            if isinstance(value, dict):
                codex_fields += len(value)
            elif isinstance(value, list):
                codex_fields += len(value)
            elif value:
                codex_fields += 1

    return {
        "golden_codex_detected": has_codex,
        "richness": "golden" if has_codex else ("rich" if total_exif_fields > 20 else "minimal"),
        "exif_fields_total": total_exif_fields,
        "codex_top_level_sections": len(codex_sections),
        "codex_total_fields": codex_fields,
        "sections_found": codex_sections,
        "decode_method": "gzip+base64 → JSON" if has_codex else None,
        "xmp_namespace": "http://ns.goldencodex.io/schema/1.0/",
    }


def _reader_x402_headers(amount: float) -> dict:
    """Build x402 payment headers for reader 402 response."""
    import base64 as b64
    x402_network = os.environ.get("X402_NETWORK", "eip155:8453")
    usdc_addresses = {
        "eip155:8453": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "eip155:84532": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    }
    eip712_domains = {
        "eip155:8453": {"name": "USD Coin", "version": "2"},
        "eip155:84532": {"name": "USDC", "version": "2"},
    }
    amount_smallest = str(int(round(amount * 1_000_000)))
    payload = {
        "x402Version": 2,
        "accepts": [{
            "scheme": "exact",
            "network": x402_network,
            "asset": usdc_addresses.get(x402_network, usdc_addresses["eip155:8453"]),
            "amount": amount_smallest,
            "payTo": BASE_WALLET_ADDRESS,
            "maxTimeoutSeconds": 300,
            "extra": eip712_domains.get(x402_network, eip712_domains["eip155:8453"]),
        }],
    }
    encoded = b64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return {
        "PAYMENT-REQUIRED": encoded,
        "X-PAYMENT-REQUIRED": str(amount),
        "X-PAYMENT-CURRENCY": "USDC",
        "X-PAYMENT-CHAIN": "base",
        "X-PAYMENT-RECIPIENT": BASE_WALLET_ADDRESS,
    }


async def _check_buyer_history(db, fingerprint: str) -> bool:
    """Check if this client has any x402 purchase history → unlimited reads."""
    try:
        query = db.collection("data_portal_transactions").where(
            "buyer_ip", "==", fingerprint.split("|")[0]
        ).limit(1)
        docs = [doc async for doc in query.stream()]
        return len(docs) > 0
    except Exception as e:
        logger.warning("Buyer history check failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# GET /agent/reader — Info page
# ---------------------------------------------------------------------------


@router.get("/reader", tags=["reader"], summary="Verilian Reader — decode Golden Codex metadata from images")
async def reader_info():
    """Discover the Verilian Reader API.

    The Golden Codex embeds rich AI training metadata directly inside images
    using XMP (gzip + base64 compressed). Verilian extracts and decodes it.

    5 free reads per day. Unlimited with any x402 purchase.
    """
    return {
        "service": "Verilian Reader",
        "version": "1.0.0",
        "description": (
            "Decode Golden Codex metadata embedded inside images. "
            "Each infused image carries 22+ sections of deep analysis — "
            "visual, emotional, symbolic, cultural, provenance — "
            "compressed into the XMP layer. Verilian reveals it."
        ),
        "how_it_works": {
            "1_embed": "Atlas agent compresses Golden Codex JSON → gzip → base64 → XMP-gc:CodexPayload",
            "2_infuse": "Metadata is written into the image file via ExifTool (XMP namespace)",
            "3_decode": "Verilian extracts XMP, decompresses, and returns the full Golden Codex",
        },
        "usage": {
            "read_artifact": "GET /agent/reader/{artifact_id}",
            "example": "GET /agent/reader/GCX-AA-00042",
            "rate_limit": f"{FREE_READS_PER_DAY}/day free, unlimited with any x402 purchase",
        },
        "artifact_id_format": "GCX-AA-{00001-10090} — Alexandria Aeternum collection",
        "total_infused_images": 10090,
        "xmp_namespace": "http://ns.goldencodex.io/schema/1.0/",
        "open_source_reader": {
            "github": "https://github.com/codex-curator/golden-codex-reader",
            "description": "Download the reader and decode any Golden Codex image locally, forever, for free.",
        },
        "provenance_badges": {
            "sovereign": "Both embedded XMP + registry hash confirmed",
            "registry": "Hash match found (metadata may have been stripped)",
            "embedded": "XMP intact but not yet registered",
            "unregistered": "Unknown image",
        },
        "buy_data": {
            "search": "GET /agent/search?q={query}",
            "oracle": "GET /agent/artifact/{id}/oracle ($0.16-$1.25 USDC)",
            "genesis_ten": "GET /agent/genesis-ten ($10.00 USDC — 10 iconic artworks)",
        },
    }


# ---------------------------------------------------------------------------
# GET /agent/reader/{artifact_id} — Decode embedded metadata
# ---------------------------------------------------------------------------


@router.get("/reader/{artifact_id}", tags=["reader"], summary="Decode Golden Codex from an infused image (5/day free)")
async def read_artifact(artifact_id: str, request: Request):
    """Decode the Golden Codex embedded in an infused image.

    5 free reads per day. After that, $0.05 USDC per read via x402.
    Unlimited reads unlocked with any data purchase.

    Downloads the infused _final.png from the Alexandria Aeternum archive,
    runs ExifTool to extract XMP-gc:CodexPayload, decompresses (gzip + base64),
    and returns the full decoded Golden Codex JSON.

    This proves the metadata lives INSIDE the image — not in a database.
    """
    db = request.state.db
    fingerprint = get_client_fingerprint(request)
    paid_this_read = False

    # Check purchase history → unlimited reads
    is_buyer = await _check_buyer_history(db, fingerprint)

    if not is_buyer:
        allowed = await rate_limiter.check_async(
            f"reader:{fingerprint}", FREE_READS_PER_DAY, 86400
        )
        if not allowed:
            # Free tier exhausted — accept $0.05 x402 payment to continue
            x_payment = (
                request.headers.get("PAYMENT-SIGNATURE", "")
                or request.headers.get("X-PAYMENT", "")
            )
            if not x_payment:
                raise HTTPException(
                    status_code=402,
                    detail={
                        "error": "Payment required",
                        "x402": {
                            "version": "1.0",
                            "amount": str(READER_PRICE),
                            "currency": "USDC",
                            "network": "base",
                            "description": (
                                f"Verilian Reader: {FREE_READS_PER_DAY} free reads used today. "
                                f"${READER_PRICE:.2f} USDC per additional read. "
                                "Or download the free open-source reader: "
                                "https://github.com/codex-curator/golden-codex-reader"
                            ),
                            "facilitator": "https://x402.org/facilitator",
                            "recipient": BASE_WALLET_ADDRESS,
                        },
                        "message": f"Free reads exhausted. ${READER_PRICE:.2f} USDC per read via x402.",
                        "open_source": "https://github.com/codex-curator/golden-codex-reader",
                    },
                    headers=_reader_x402_headers(READER_PRICE),
                )
            # Verify payment
            payment_result = await verify_x402_payment(x_payment, READER_PRICE)
            if not payment_result.valid:
                raise HTTPException(
                    status_code=402,
                    detail={
                        "error": "Payment required",
                        "message": f"Payment verification failed: {payment_result.error}",
                        "amount": str(READER_PRICE),
                    },
                    headers=_reader_x402_headers(READER_PRICE),
                )
            paid_this_read = True

    # Validate artifact ID format
    if not artifact_id.startswith("GCX-AA-"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Invalid artifact ID format",
                "expected": "GCX-AA-{00001-10090}",
                "example": "GCX-AA-00042",
                "hint": "Use GET /agent/reader for documentation",
            },
        )

    # Download the infused image from GCS
    blob_path = f"{IMAGES_PREFIX}/{artifact_id}_final.png"
    logger.info("Reader: downloading %s/%s", ASSETS_BUCKET, blob_path)

    try:
        client = gcs.Client()
        bucket = client.bucket(ASSETS_BUCKET)
        blob = bucket.blob(blob_path)

        if not blob.exists():
            raise HTTPException(
                status_code=404,
                detail={
                    "error": f"Artifact not found: {artifact_id}",
                    "hint": "Valid range: GCX-AA-00001 through GCX-AA-10090",
                },
            )

        # Download to temp file
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            blob.download_to_filename(tmp.name)
            tmp_path = tmp.name

    except HTTPException:
        raise
    except Exception as e:
        logger.error("GCS download failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to download image: {e}")

    try:
        # Extract metadata with ExifTool
        raw_metadata = _extract_metadata_exiftool(tmp_path)
        logger.info("Reader: extracted %d metadata fields from %s", len(raw_metadata), artifact_id)

        # Find and decode Golden Codex payload
        golden_codex = None
        payload = _find_codex_payload(raw_metadata)

        if payload:
            try:
                golden_codex = _decode_codex_payload(payload)
                logger.info("Reader: decoded Golden Codex from %s", artifact_id)
            except Exception as e:
                logger.warning("Reader: failed to decode payload from %s: %s", artifact_id, e)

        # Build verification report
        verification = _calculate_verification(raw_metadata, golden_codex)

        # Extract basic image info
        image_info = {}
        for field in ["ImageWidth", "ImageHeight", "FileSize", "FileType", "Megapixels"]:
            for key, value in raw_metadata.items():
                if field.lower() in key.lower():
                    image_info[field] = value
                    break

        response = {
            "artifact_id": artifact_id,
            "status": "decoded" if golden_codex else "no_codex_found",
            "verification": verification,
            "image_info": image_info,
        }

        if golden_codex:
            title = golden_codex.get("title", "")
            if not title:
                # Try nested identifiers
                ids = golden_codex.get("_identifiers", {})
                title = ids.get("title", golden_codex.get("coreIdentity", {}).get("title", ""))

            response["title"] = title
            response["golden_codex"] = golden_codex
            response["decoded_from"] = "XMP-gc:CodexPayload embedded in image file"
            response["proof"] = (
                "This metadata was extracted directly from the image's XMP layer — "
                "not from a database. The Golden Codex travels with the image, forever."
            )
        else:
            response["message"] = (
                "No Golden Codex payload found in this image. "
                "The image may not have been infused yet, or the XMP was stripped."
            )

        if is_buyer:
            access_tier = "unlimited (purchase history detected)"
        elif paid_this_read:
            access_tier = f"paid (${READER_PRICE:.2f} USDC)"
        else:
            access_tier = f"free ({FREE_READS_PER_DAY}/day)"
        response["reader_access"] = access_tier
        response["open_source_reader"] = "https://github.com/codex-curator/golden-codex-reader"
        response["buy_full_collection"] = "GET /agent/search or GET /agent/genesis-ten"

        return response

    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
