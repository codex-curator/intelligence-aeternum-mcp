"""
Orders Routes
==============
Create and manage dataset purchase orders.

Supports two payment methods:
  - "stripe" -- Human buyers via Stripe Checkout (card, ACH, etc.)
  - "x402"   -- AI agent buyers via x402 USDC micropayments on Base L2

Orders are stored in Firestore collection ``data_portal_orders``.
Every commercial order includes an auto-generated AB 2013 compliance manifest.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import stripe
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from auth import generate_signed_url
from compliance import generate_ab2013_manifest
from pricing import PRICING_TIERS, calculate_price

logger = logging.getLogger("data-portal.orders")

router = APIRouter(prefix="/orders", tags=["orders"])

DATA_BUCKET = os.environ.get("DATA_BUCKET", "alexandria-download-1m")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateOrderRequest(BaseModel):
    """Create a new order for dataset access."""
    dataset_id: str = Field(description="ID of the dataset to purchase")
    quantity: int = Field(ge=1, description="Number of images")
    payment_method: str = Field(
        description="Payment method: 'stripe' or 'x402'",
        pattern="^(stripe|x402)$",
    )
    email: Optional[str] = Field(
        default=None,
        description="Email address (required for Stripe orders)",
    )
    pricing_tier: Optional[str] = Field(
        default=None,
        description="Explicit pricing tier. Auto-detected from quantity if omitted.",
    )
    use_case: Optional[str] = Field(
        default=None,
        description="Intended use: academic, commercial, personal, agent",
    )
    organization: Optional[str] = Field(default=None)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("")
async def create_order(body: CreateOrderRequest, request: Request):
    """Create a purchase order for dataset images.

    For Stripe orders, returns a Stripe Checkout URL.
    For x402 orders, returns payment instructions (amount, wallet, chain).
    Free-tier (academic/individual) orders are auto-fulfilled.
    """
    db = request.state.db

    # Validate dataset exists
    from routes.catalog import DATASETS

    if body.dataset_id not in DATASETS:
        raise HTTPException(
            status_code=404,
            detail=f"Dataset '{body.dataset_id}' not found. "
            f"Available: {list(DATASETS.keys())}",
        )

    # Determine pricing tier
    tier = body.pricing_tier
    if not tier:
        # Auto-detect based on payment method and quantity
        if body.payment_method == "x402":
            tier = "agent_batch" if body.quantity >= 100 else "agent_single"
        elif body.use_case == "academic":
            tier = "academic"
        elif body.quantity >= 10000:
            tier = "corporate_large"
        elif body.quantity >= 1000:
            tier = "corporate_small"
        else:
            tier = "agent_single"

    if tier not in PRICING_TIERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid pricing tier '{tier}'. Valid: {list(PRICING_TIERS.keys())}",
        )

    # Calculate price
    try:
        price_info = calculate_price(tier, body.quantity)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Stripe requires email
    if body.payment_method == "stripe" and not body.email and price_info["total"] > 0:
        raise HTTPException(
            status_code=400,
            detail="Email is required for Stripe orders",
        )

    # Build order document
    order_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    order_doc = {
        "order_id": order_id,
        "dataset_id": body.dataset_id,
        "quantity": body.quantity,
        "pricing_tier": tier,
        "total_price": price_info["total"],
        "currency": price_info["currency"],
        "payment_method": body.payment_method,
        "email": body.email,
        "organization": body.organization,
        "use_case": body.use_case,
        "status": "pending",
        "created_at": now,
    }

    # Generate AB 2013 compliance manifest for commercial orders
    if price_info["total"] > 0:
        manifest = generate_ab2013_manifest(order_doc, body.dataset_id)
        order_doc["compliance_manifest"] = manifest["json"]
    else:
        # Free tier -- still attach a lightweight manifest
        manifest = generate_ab2013_manifest(order_doc, body.dataset_id)
        order_doc["compliance_manifest"] = manifest["json"]

    # ---- Free tier: auto-fulfill ----
    if price_info["total"] == 0:
        order_doc["status"] = "fulfilled"
        order_doc["fulfilled_at"] = now
        await db.collection("data_portal_orders").document(order_id).set(order_doc)

        return {
            "order_id": order_id,
            "status": "fulfilled",
            "tier": tier,
            "quantity": body.quantity,
            "total_price": 0,
            "message": f"Free access ({tier}) granted. Download your images below.",
            "downloads_url": f"/orders/{order_id}/downloads",
            "compliance_manifest": manifest["json"],
            "license": "Intelligence Aeternum Data License v1.0 -- Attribution required.",
        }

    # ---- Stripe payment ----
    if body.payment_method == "stripe":
        stripe_key = request.state.stripe_secret_key
        if not stripe_key:
            # Store order as pending; provide manual payment info
            await db.collection("data_portal_orders").document(order_id).set(order_doc)
            return {
                "order_id": order_id,
                "status": "pending_payment",
                "tier": tier,
                "quantity": body.quantity,
                "total_price": price_info["total"],
                "currency": "USD",
                "message": "Stripe is not yet configured. Contact data@iaeternum.ai to complete your purchase.",
            }

        stripe.api_key = stripe_key

        try:
            price_cents = int(price_info["total"] * 100)
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": price_cents,
                        "product_data": {
                            "name": f"Alexandria Aeternum - {body.dataset_id}",
                            "description": f"{body.quantity:,} images, {tier} tier",
                        },
                    },
                    "quantity": 1,
                }],
                mode="payment",
                customer_email=body.email,
                metadata={"order_id": order_id},
                success_url=f"https://iaeternum.ai/orders/{order_id}?status=success",
                cancel_url=f"https://iaeternum.ai/orders/{order_id}?status=cancelled",
            )
            order_doc["stripe_session_id"] = session.id
            order_doc["checkout_url"] = session.url
        except stripe.StripeError as e:
            logger.error("Stripe session creation failed: %s", e)
            raise HTTPException(status_code=502, detail=f"Stripe error: {e}")

        await db.collection("data_portal_orders").document(order_id).set(order_doc)

        return {
            "order_id": order_id,
            "status": "pending_payment",
            "tier": tier,
            "quantity": body.quantity,
            "total_price": price_info["total"],
            "currency": "USD",
            "checkout_url": session.url,
            "message": "Complete payment via Stripe to access your dataset.",
        }

    # ---- x402 payment ----
    if body.payment_method == "x402":
        wallet = request.state.base_wallet_address
        order_doc["status"] = "awaiting_x402"
        await db.collection("data_portal_orders").document(order_id).set(order_doc)

        return {
            "order_id": order_id,
            "status": "awaiting_x402",
            "tier": tier,
            "quantity": body.quantity,
            "total_price": price_info["total"],
            "currency": "USDC",
            "x402": {
                "version": "1.0",
                "amount": str(price_info["total"]),
                "currency": "USDC",
                "network": "base",
                "recipient": wallet,
                "facilitator": "https://x402.org/facilitator",
                "description": f"{body.quantity:,} images from {body.dataset_id}",
            },
            "message": (
                f"Send {price_info['total']:.2f} USDC on Base L2 via x402. "
                "Include the order_id in the payment memo."
            ),
        }

    raise HTTPException(status_code=400, detail="Unsupported payment method")


@router.get("/{order_id}")
async def get_order(order_id: str, request: Request):
    """Get order status and details.

    When status is 'fulfilled', includes download links.
    """
    db = request.state.db
    doc_ref = db.collection("data_portal_orders").document(order_id)
    doc = await doc_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=404, detail="Order not found")

    order = doc.to_dict()

    response = {
        "order_id": order.get("order_id"),
        "dataset_id": order.get("dataset_id"),
        "quantity": order.get("quantity"),
        "pricing_tier": order.get("pricing_tier"),
        "total_price": order.get("total_price"),
        "currency": order.get("currency"),
        "payment_method": order.get("payment_method"),
        "status": order.get("status"),
        "created_at": order.get("created_at"),
        "fulfilled_at": order.get("fulfilled_at"),
    }

    if order.get("status") in ("fulfilled", "completed"):
        response["downloads_url"] = f"/orders/{order_id}/downloads"

    if order.get("compliance_manifest"):
        response["compliance_manifest"] = order["compliance_manifest"]

    return response


@router.get("/{order_id}/downloads")
async def get_downloads(
    order_id: str,
    request: Request,
    offset: int = 0,
    limit: int = 100,
):
    """Get signed download URLs for a fulfilled order.

    URLs are valid for 24 hours.  Use offset/limit for pagination on large orders.
    """
    db = request.state.db
    doc_ref = db.collection("data_portal_orders").document(order_id)
    doc = await doc_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=404, detail="Order not found")

    order = doc.to_dict()

    if order.get("status") not in ("fulfilled", "completed"):
        raise HTTPException(
            status_code=403,
            detail=f"Order not fulfilled. Current status: {order.get('status')}. "
            "Complete payment first.",
        )

    dataset_id = order.get("dataset_id", "met-museum")
    quantity = order.get("quantity", 100)
    bucket = request.state.data_bucket

    # Determine GCS prefix from dataset
    from routes.catalog import DATASETS

    ds = DATASETS.get(dataset_id)
    prefix = ds.gcs_prefix if ds else f"{dataset_id}/"

    # Generate signed download URLs (24hr expiry for purchased content)
    downloads = []
    end = min(offset + limit, quantity)
    for i in range(offset, end):
        blob_path = f"{prefix}{i:06d}.jpg"
        meta_path = f"{prefix}{i:06d}_meta.json"
        try:
            image_url = generate_signed_url(bucket, blob_path, expiration_hours=24)
            meta_url = generate_signed_url(bucket, meta_path, expiration_hours=24)
        except Exception:
            image_url = f"https://storage.googleapis.com/{bucket}/{blob_path}"
            meta_url = f"https://storage.googleapis.com/{bucket}/{meta_path}"

        downloads.append({
            "index": i,
            "image_url": image_url,
            "metadata_url": meta_url,
        })

    return {
        "order_id": order_id,
        "dataset_id": dataset_id,
        "total_images": quantity,
        "offset": offset,
        "limit": limit,
        "count": len(downloads),
        "expires_in": "24 hours",
        "downloads": downloads,
        "next_offset": offset + limit if offset + limit < quantity else None,
    }
