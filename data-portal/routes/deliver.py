"""
Delivery Routes — On-demand artifact fulfillment.
===================================================
Customers pick artifacts from the manifest, pay, and receive
infused .png + golden_codex.json on demand. Images are fetched
from museum APIs only when purchased (no bulk pre-downloading).

POST /deliver/order           — Create order + get payment instructions
POST /deliver/order/{id}/fulfill — Verify payment + trigger fulfillment
GET  /deliver/order/{id}      — Poll order status + download links
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from auth import (
    BASE_WALLET_ADDRESS,
    generate_signed_url,
    get_client_fingerprint,
    rate_limiter,
    verify_x402_payment,
)
from image_fetcher import ImageFetcher, ImageFetchError
from pricing import (
    GENESIS_DISCOUNT,
    calculate_price,
    genesis_days_remaining,
    is_genesis_epoch,
)

logger = logging.getLogger("data-portal.deliver")

router = APIRouter(prefix="/deliver", tags=["deliver"])

DATA_BUCKET = os.environ.get("DATA_BUCKET", "alexandria-download-1m")
NOVA_AGENT_URL = os.environ.get("NOVA_AGENT_URL", "https://nova-agent-172867820131.us-west1.run.app")
ATLAS_AGENT_URL = os.environ.get("ATLAS_AGENT_URL", "https://atlas-agent-172867820131.us-west1.run.app")

CACHE_PREFIX = "cache"  # GCS path: cache/{museum}/{object_id}/

# Tier pricing for delivery orders
DELIVERY_TIER_PRICES = {
    "human_standard": 0.05,     # Metadata + optimized image (no enrichment)
    "hybrid_premium": 0.20,     # Metadata + Nova enrichment + Atlas infusion
}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class DeliveryOrderRequest(BaseModel):
    """Create a delivery order for specific artifacts."""
    artifact_ids: list[str] = Field(
        min_length=1,
        max_length=100,
        description="Manifest artifact IDs (e.g. ['met_436965', 'chicago_27992'])",
    )
    tier: str = Field(
        default="hybrid_premium",
        description="Delivery tier: human_standard ($0.05) or hybrid_premium ($0.20)",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _genesis_info() -> dict:
    genesis = is_genesis_epoch()
    return {
        "genesis_epoch": genesis,
        "genesis_days_remaining": genesis_days_remaining() if genesis else 0,
    }


def _delivery_price(tier: str) -> float:
    base = DELIVERY_TIER_PRICES.get(tier, 0.20)
    if is_genesis_epoch():
        return round(base * GENESIS_DISCOUNT, 2)
    return base


def _cache_gcs_prefix(museum: str, object_id: str) -> str:
    return f"{CACHE_PREFIX}/{museum}/{object_id}/"


async def _manifest_lookup(db, artifact_id: str) -> Optional[dict]:
    """Look up a single artifact in the alexandria_manifest collection."""
    doc = await db.collection("alexandria_manifest").document(artifact_id).get()
    if doc.exists:
        return doc.to_dict()
    return None


async def _check_cache(bucket_obj, museum: str, object_id: str) -> Optional[dict]:
    """Check if an artifact has already been fetched/enriched in GCS cache."""
    prefix = _cache_gcs_prefix(museum, object_id)
    infused_blob = bucket_obj.blob(f"{prefix}infused.png")
    json_blob = bucket_obj.blob(f"{prefix}golden_codex.json")

    if infused_blob.exists() and json_blob.exists():
        return {
            "infused_path": f"{prefix}infused.png",
            "json_path": f"{prefix}golden_codex.json",
        }

    # Check for optimized-only (human_standard without enrichment)
    optimized_blob = bucket_obj.blob(f"{prefix}optimized.jpg")
    if optimized_blob.exists():
        return {
            "optimized_path": f"{prefix}optimized.jpg",
        }

    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/order")
async def create_delivery_order(
    body: DeliveryOrderRequest,
    request: Request,
):
    """Create an order for specific artifacts from the manifest.

    Validates all artifacts exist, calculates price, returns payment instructions.
    """
    db = request.state.db

    if body.tier not in DELIVERY_TIER_PRICES:
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"Invalid tier: {body.tier}",
                "valid_tiers": list(DELIVERY_TIER_PRICES.keys()),
            },
        )

    # Validate all artifacts exist in manifest
    found = {}
    missing = []
    for aid in body.artifact_ids:
        doc = await _manifest_lookup(db, aid)
        if doc:
            found[aid] = doc
        else:
            missing.append(aid)

    if missing:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Some artifacts not found in manifest",
                "missing": missing,
                "found": len(found),
            },
        )

    # Calculate price
    unit_price = _delivery_price(body.tier)
    count = len(body.artifact_ids)
    total = round(unit_price * count, 2)

    # Volume discount (10% off for 50+, 20% off for 100+)
    discount = 0.0
    if count >= 100:
        discount = 0.20
    elif count >= 50:
        discount = 0.10
    if discount:
        total = round(total * (1 - discount), 2)

    order_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    order_doc = {
        "order_id": order_id,
        "artifact_ids": body.artifact_ids,
        "tier": body.tier,
        "count": count,
        "unit_price": unit_price,
        "discount": discount,
        "total_price": total,
        "currency": "USDC",
        "status": "awaiting_payment",
        "created_at": now,
        "artifacts": {
            aid: {"title": doc.get("title", ""), "museum": doc.get("museum", "")}
            for aid, doc in found.items()
        },
    }

    await db.collection("delivery_orders").document(order_id).set(order_doc)

    return {
        "order_id": order_id,
        "count": count,
        "tier": body.tier,
        "unit_price": unit_price,
        "discount": f"{int(discount * 100)}%" if discount else None,
        "total_price": total,
        "currency": "USDC",
        "status": "awaiting_payment",
        "payment": {
            "x402": {
                "amount": str(total),
                "currency": "USDC",
                "network": "base",
                "recipient": BASE_WALLET_ADDRESS,
                "facilitator": "https://x402.org/facilitator",
            },
        },
        "fulfill_url": f"/deliver/order/{order_id}/fulfill",
        "poll_url": f"/deliver/order/{order_id}",
        "artifacts": [
            {"id": aid, "title": doc.get("title", ""), "museum": doc.get("museum", "")}
            for aid, doc in found.items()
        ],
        **_genesis_info(),
    }


@router.post("/order/{order_id}/fulfill")
async def fulfill_delivery_order(
    order_id: str,
    request: Request,
    x_payment: Optional[str] = Header(None, alias="X-PAYMENT"),
    payment_sig: Optional[str] = Header(None, alias="PAYMENT-SIGNATURE"),
):
    """Verify payment and trigger on-demand fulfillment.

    For each artifact:
    1. Check GCS cache (serve if exists)
    2. Fetch image from museum API
    3. Optimize (2048px, JPEG Q90)
    4. If hybrid_premium: Nova enrichment + Atlas infusion
    5. Upload to GCS cache
    6. Return signed download URLs (24h)
    """
    x_payment = payment_sig or x_payment
    db = request.state.db

    # Load order
    doc = await db.collection("delivery_orders").document(order_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Order not found")

    order = doc.to_dict()

    if order.get("status") == "fulfilled":
        return await _build_download_response(order_id, order, request)

    if order.get("status") not in ("awaiting_payment",):
        raise HTTPException(status_code=400, detail=f"Order status is '{order.get('status')}', cannot fulfill")

    # Verify payment
    total = order.get("total_price", 0)
    if not x_payment:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "Payment required",
                "amount": total,
                "currency": "USDC",
                "network": "base",
                "recipient": BASE_WALLET_ADDRESS,
            },
        )

    payment_result = await verify_x402_payment(x_payment, total)
    if not payment_result.valid:
        raise HTTPException(
            status_code=402,
            detail={"error": "Payment verification failed", "amount": total},
        )

    # Mark as processing
    await db.collection("delivery_orders").document(order_id).update({
        "status": "processing",
        "payment_tx": payment_result.tx_hash,
        "fulfillment_started_at": datetime.now(timezone.utc).isoformat(),
    })

    # Fire background fulfillment
    asyncio.create_task(_fulfill_order_background(db, order_id, order, request.state.data_bucket))

    return {
        "order_id": order_id,
        "status": "processing",
        "message": "Payment verified. Fulfillment in progress.",
        "estimated_time": f"{len(order.get('artifact_ids', []))} artifacts × ~30s each",
        "poll_url": f"/deliver/order/{order_id}",
    }


@router.get("/order/{order_id}")
async def get_delivery_order(
    order_id: str,
    request: Request,
):
    """Poll order status. When fulfilled, includes signed download URLs."""
    db = request.state.db

    doc = await db.collection("delivery_orders").document(order_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Order not found")

    order = doc.to_dict()

    response = {
        "order_id": order_id,
        "status": order.get("status"),
        "tier": order.get("tier"),
        "count": order.get("count"),
        "total_price": order.get("total_price"),
        "created_at": order.get("created_at"),
    }

    if order.get("status") == "fulfilled":
        return await _build_download_response(order_id, order, request)

    if order.get("status") == "processing":
        completed = order.get("fulfilled_artifacts", {})
        total = order.get("count", 0)
        response["progress"] = {
            "completed": len(completed),
            "total": total,
        }

    if order.get("status") == "failed":
        response["error"] = order.get("error", "Unknown error")

    return response


# ---------------------------------------------------------------------------
# Background fulfillment
# ---------------------------------------------------------------------------


async def _fulfill_order_background(db, order_id: str, order: dict, bucket_name: str):
    """Process each artifact in an order: fetch, optimize, enrich, cache."""
    from google.cloud import storage as gcs

    client = gcs.Client()
    bucket_obj = client.bucket(bucket_name)

    artifact_ids = order.get("artifact_ids", [])
    tier = order.get("tier", "hybrid_premium")
    fulfilled = {}
    errors = {}

    fetcher = ImageFetcher()

    try:
        for aid in artifact_ids:
            try:
                result = await _process_single_artifact(
                    db, fetcher, bucket_obj, bucket_name, aid, tier,
                )
                fulfilled[aid] = result
            except Exception as e:
                logger.error("Failed to process artifact %s: %s", aid, e)
                errors[aid] = str(e)

            # Update progress incrementally
            await db.collection("delivery_orders").document(order_id).update({
                "fulfilled_artifacts": fulfilled,
            })

        # Mark order complete
        status = "fulfilled" if fulfilled else "failed"
        await db.collection("delivery_orders").document(order_id).update({
            "status": status,
            "fulfilled_at": datetime.now(timezone.utc).isoformat(),
            "fulfilled_artifacts": fulfilled,
            "errors": errors if errors else None,
        })

        logger.info(
            "Order %s: %d fulfilled, %d errors",
            order_id, len(fulfilled), len(errors),
        )

    except Exception as exc:
        logger.error("Order fulfillment failed: %s", exc)
        await db.collection("delivery_orders").document(order_id).update({
            "status": "failed",
            "error": str(exc),
        })
    finally:
        await fetcher.close()


async def _process_single_artifact(
    db,
    fetcher: ImageFetcher,
    bucket_obj,
    bucket_name: str,
    artifact_id: str,
    tier: str,
) -> dict:
    """Process a single artifact: fetch, optimize, enrich (if premium), cache."""

    # 1. Load manifest doc
    manifest = await _manifest_lookup(db, artifact_id)
    if not manifest:
        raise ValueError(f"Artifact {artifact_id} not found in manifest")

    museum = manifest.get("museum", "")
    object_id = manifest.get("object_id", "")
    prefix = _cache_gcs_prefix(museum, object_id)

    # 2. Check cache
    cached = await asyncio.get_event_loop().run_in_executor(
        None, lambda: _check_cache(bucket_obj, museum, object_id)
    )

    if cached and "infused_path" in cached and tier == "hybrid_premium":
        return {"source": "cache", "paths": cached}
    if cached and "optimized_path" in cached and tier == "human_standard":
        return {"source": "cache", "paths": cached}

    # 3. Fetch image from museum
    image_bytes = await fetcher.fetch_image(manifest)

    # 4. Optimize
    optimized = fetcher.optimize_image(image_bytes, max_dim=2048, quality=90)

    # Upload optimized image to cache
    opt_path = f"{prefix}optimized.jpg"
    opt_blob = bucket_obj.blob(opt_path)
    opt_blob.upload_from_string(optimized, content_type="image/jpeg")

    result = {"optimized_path": opt_path}

    # 5. For hybrid_premium: Nova enrichment + Atlas infusion
    if tier == "hybrid_premium":
        # Generate a temporary signed URL for Nova/Atlas to access
        try:
            temp_url = generate_signed_url(bucket_name, opt_path, expiration_hours=1)
        except Exception:
            temp_url = f"gs://{bucket_name}/{opt_path}"

        # Nova enrichment
        golden_codex = {}
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                nova_resp = await client.post(f"{NOVA_AGENT_URL}/enrich", json={
                    "image_url": temp_url,
                    "user_id": "delivery_pipeline",
                    "image_id": artifact_id,
                    "parameters": {"analysis_depth": "full"},
                    "custom_metadata": {
                        "title": manifest.get("title", ""),
                        "artist": manifest.get("artist", ""),
                        "date": manifest.get("date", ""),
                        "medium": manifest.get("medium", ""),
                        "museum": manifest.get("museum", ""),
                        "museum_url": manifest.get("museum_url", ""),
                    },
                })
                if nova_resp.status_code == 200:
                    golden_codex = nova_resp.json().get("golden_codex", {})
        except Exception as e:
            logger.warning("Nova enrichment failed for %s: %s", artifact_id, e)
            # Continue without enrichment — still deliver the optimized image
            golden_codex = {
                "title": manifest.get("title", ""),
                "artist": manifest.get("artist", ""),
                "_enrichment_status": "nova_unavailable",
            }

        # Save Golden Codex JSON to cache
        json_path = f"{prefix}golden_codex.json"
        json_blob = bucket_obj.blob(json_path)
        json_blob.upload_from_string(
            json.dumps(golden_codex, indent=2, ensure_ascii=False),
            content_type="application/json",
        )
        result["json_path"] = json_path

        # Atlas infusion (XMP embed)
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                atlas_resp = await client.post(f"{ATLAS_AGENT_URL}/infuse", json={
                    "image_url": temp_url,
                    "user_id": "delivery_pipeline",
                    "golden_codex": golden_codex,
                    "metadata_mode": "full_gcx",
                })
                if atlas_resp.status_code == 200:
                    atlas_data = atlas_resp.json()
                    final_url = atlas_data.get("final_url", "")
                    if final_url:
                        result["infused_path"] = f"{prefix}infused.png"
                        # Atlas stores the infused file; we record its path
                        result["soulmark"] = atlas_data.get("soulmark", "")
                        result["phash"] = atlas_data.get("perceptual_hash", "")
                    else:
                        # Atlas didn't return a final URL; copy optimized as fallback
                        result["infused_path"] = opt_path
                else:
                    result["infused_path"] = opt_path
        except Exception as e:
            logger.warning("Atlas infusion failed for %s: %s", artifact_id, e)
            result["infused_path"] = opt_path

    # 6. Update manifest with cache info
    await db.collection("alexandria_manifest").document(artifact_id).update({
        "cached": True,
        "cache_path": prefix,
        "last_fulfilled": datetime.now(timezone.utc).isoformat(),
    })

    result["source"] = "fresh"
    return result


async def _build_download_response(order_id: str, order: dict, request: Request) -> dict:
    """Build response with signed download URLs for a fulfilled order."""
    bucket = request.state.data_bucket
    fulfilled = order.get("fulfilled_artifacts", {})
    downloads = []

    for aid, info in fulfilled.items():
        paths = info if isinstance(info, dict) else {}
        dl = {"artifact_id": aid}

        # Generate signed URLs for available files
        for key in ("infused_path", "optimized_path", "json_path"):
            path = paths.get(key, "")
            if path:
                try:
                    url = generate_signed_url(bucket, path, expiration_hours=24)
                except Exception:
                    url = f"gs://{bucket}/{path}"
                dl[key.replace("_path", "_url")] = url

        if paths.get("soulmark"):
            dl["soulmark"] = paths["soulmark"]

        downloads.append(dl)

    return {
        "order_id": order_id,
        "status": "fulfilled",
        "tier": order.get("tier"),
        "count": len(downloads),
        "total_price": order.get("total_price"),
        "expires_in": "24 hours",
        "downloads": downloads,
        "errors": order.get("errors"),
        "fulfilled_at": order.get("fulfilled_at"),
        **_genesis_info(),
    }
