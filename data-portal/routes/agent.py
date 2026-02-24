"""
Agent (M2M) Routes — V2.2 (Terminology aligned: Human_Standard / Hybrid_Premium)
==================================================================================
x402-protected endpoints for autonomous AI agent data access.

Every purchase is a PACKAGE: metadata + image together. No standalone image price.

FREE (discovery):
  - Search:                GET /agent/search?q=...
  - Human_Standard data:   GET /agent/artifact/{id}  (5 free/day, then $0.04/$0.05)
  - Compliance:            GET /agent/compliance/{dataset_id}
  - Agent guide:           GET /agent/guide

PAID (x402 USDC on Base L2 — Genesis Epoch: 20% off):
  - Hybrid_Premium data:   GET /agent/artifact/{id}/oracle  ($0.16 launch / $0.20 full)
  - Batch download:        POST /agent/batch                ($0.05/image, min 100)
  - Agent enrichment:      POST /agent/enrich               ($0.16-$0.40 launch)

Payment is verified via the X-PAYMENT header.  Set environment variable
X402_TEST_MODE=true to accept any non-empty header during development.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from auth import (
    X402PaymentResult,
    generate_signed_url,
    get_client_fingerprint,
    rate_limiter,
    verify_x402_payment,
    BASE_WALLET_ADDRESS,
)
from pricing import (
    calculate_price,
    is_genesis_epoch,
    genesis_days_remaining,
    GENESIS_DISCOUNT,
)
from volume_tracker import volume_tracker

logger = logging.getLogger("data-portal.agent")


# ---------------------------------------------------------------------------
# Image lookup helper
# ---------------------------------------------------------------------------


_MUSEUM_API_URLS = {
    "met": "https://collectionapi.metmuseum.org/public/collection/v1/objects/{id}",
    "chicago": "https://api.artic.edu/api/v1/artworks/{id}?fields=id,image_id",
    "cleveland": "https://openaccess-api.clevelandart.org/api/artworks/{id}",
    "rijksmuseum": None,  # IIIF URLs stored in manifest — no API needed
    "nga": None,           # IIIF URLs stored in manifest — no API needed
    "smithsonian": None,   # IDS URLs stored in manifest — no API needed
    "paris": None,         # Direct URLs stored in manifest — no API needed
}

# Valid museum prefixes for artifact ID parsing
_MUSEUM_PREFIXES = ("met", "nga", "chicago", "cleveland", "rijksmuseum", "smithsonian", "paris")


def _find_image_url(
    bucket_obj, artifact_id: str, numeric_id: str, museum: str,
    request_base_url: str = "",
    manifest_doc: Optional[dict] = None,
) -> Optional[str]:
    """Find an image URL for the artifact.

    Strategy (fast to slow):
    0. Manifest image_source_url (instant, no API call) — preferred
    1. Museum public API (CC0 images, no signing needed) — <500ms
    2. Image proxy through this service (keeps GCS bucket private)
    """
    import httpx

    # --- Strategy 0: Manifest source URL (instant — no network call) ---
    if manifest_doc:
        src_url = manifest_doc.get("image_source_url", "")
        if src_url:
            return src_url

    # --- Strategy 1: Museum public API (fallback for pre-manifest artifacts) ---
    api_url_template = _MUSEUM_API_URLS.get(museum)
    if api_url_template:
        try:
            api_url = api_url_template.format(id=numeric_id)
            resp = httpx.get(api_url, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()

                if museum == "met":
                    img = data.get("primaryImage", "")
                    if img:
                        return img
                elif museum == "chicago":
                    image_id = data.get("data", {}).get("image_id", "")
                    if image_id:
                        return f"https://www.artic.edu/iiif/2/{image_id}/full/843,/0/default.jpg"
                elif museum == "cleveland":
                    images = data.get("data", {}).get("images", {})
                    web = images.get("web", {})
                    if web.get("url"):
                        return web["url"]
        except Exception as e:
            logger.debug("Museum API image lookup failed for %s: %s", artifact_id, e)

    # --- Strategy 2: Image proxy (bucket stays private) ---
    target_name = f"{museum}_{numeric_id}.jpg"
    search_prefix = f"01-raw-with-index/{museum}/"
    try:
        sub_iter = bucket_obj.list_blobs(prefix=search_prefix, delimiter="/")
        list(sub_iter)
        for subdir in sub_iter.prefixes:
            blob = bucket_obj.blob(f"{subdir}{target_name}")
            if blob.exists():
                return f"{request_base_url}/agent/artifact/{artifact_id}/image"
            sub2 = bucket_obj.list_blobs(prefix=subdir, delimiter="/")
            list(sub2)
            for sd in sub2.prefixes:
                blob = bucket_obj.blob(f"{sd}{target_name}")
                if blob.exists():
                    return f"{request_base_url}/agent/artifact/{artifact_id}/image"
    except Exception as e:
        logger.debug("GCS image search failed for %s: %s", artifact_id, e)

    return None


def _find_gcs_image_blob(bucket_obj, artifact_id: str):
    """Locate the GCS blob for an artifact's image. Returns (blob, content_type) or (None, None)."""
    numeric_id = artifact_id
    museum = None
    for mp in ["met", "nga", "chicago", "cleveland", "rijksmuseum", "smithsonian", "paris"]:
        if artifact_id.startswith(f"{mp}_"):
            museum = mp
            numeric_id = artifact_id[len(mp) + 1:]
            break

    if not museum:
        return None, None

    target_name = f"{museum}_{numeric_id}.jpg"
    search_prefix = f"01-raw-with-index/{museum}/"

    try:
        sub_iter = bucket_obj.list_blobs(prefix=search_prefix, delimiter="/")
        list(sub_iter)
        for subdir in sub_iter.prefixes:
            blob = bucket_obj.blob(f"{subdir}{target_name}")
            if blob.exists():
                return blob, "image/jpeg"
            sub2 = bucket_obj.list_blobs(prefix=subdir, delimiter="/")
            list(sub2)
            for sd in sub2.prefixes:
                blob = bucket_obj.blob(f"{sd}{target_name}")
                if blob.exists():
                    return blob, "image/jpeg"
    except Exception as e:
        logger.debug("GCS image blob search failed for %s: %s", artifact_id, e)

    return None, None


# ---------------------------------------------------------------------------
# Transaction logging helper
# ---------------------------------------------------------------------------


async def log_transaction(
    request: Request,
    *,
    endpoint: str,
    artifact_id: str = "",
    image_id: str = "",
    amount_usd: float,
    tx_hash: str = "",
    extra: dict | None = None,
) -> None:
    """Write a transaction record to Firestore ``data_portal_transactions``."""
    try:
        db = request.state.db
        doc = {
            "timestamp": datetime.now(timezone.utc),
            "endpoint": endpoint,
            "artifact_id": artifact_id,
            "image_id": image_id,
            "amount_usd": amount_usd,
            "currency": "USDC",
            "network": "base",
            "tx_hash": tx_hash,
            "buyer_ip": (
                request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or request.client.host
                if request.client
                else "unknown"
            ),
            "user_agent": request.headers.get("User-Agent", ""),
        }
        if extra:
            doc.update(extra)
        await db.collection("data_portal_transactions").add(doc)
        logger.info("Transaction logged: endpoint=%s amount=$%.2f", endpoint, amount_usd)
    except Exception as exc:
        logger.warning("Failed to log transaction: %s", exc)

router = APIRouter(prefix="/agent", tags=["agent"])

DATA_BUCKET = os.environ.get("DATA_BUCKET", "alexandria-download-1m")

# Agent service URLs for enrichment proxying
NOVA_AGENT_URL = os.environ.get("NOVA_AGENT_URL", "https://nova-agent-172867820131.us-west1.run.app")
ATLAS_AGENT_URL = os.environ.get("ATLAS_AGENT_URL", "https://atlas-agent-172867820131.us-west1.run.app")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class BatchRequest(BaseModel):
    """Request body for batch image download."""
    dataset_id: str = "met-museum"
    quantity: int = Field(ge=100, description="Number of images (min 100)")
    offset: int = Field(default=0, ge=0, description="Offset into the dataset")


class EnrichRequest(BaseModel):
    """Request body for agent-submitted image enrichment."""
    image_url: str = Field(description="Public URL of the image to enrich (https:// or gs://)")
    tier: str = Field(
        default="oracle_only",
        description="Enrichment tier: oracle_only / Hybrid_Premium ($0.20), oracle_plus_infuse ($0.30), full_certified ($0.50)"
    )
    custom_fields: Optional[dict] = Field(
        default=None,
        description="Custom metadata fields to merge with Oracle analysis. "
        "Submitter values take priority for factual fields."
    )
    callback_url: Optional[str] = Field(
        default=None,
        description="Webhook URL for completion notification"
    )


# ---------------------------------------------------------------------------
# Pricing helpers
# ---------------------------------------------------------------------------

def _current_hybrid_premium_price() -> float:
    """Get the current Hybrid_Premium price (Genesis Epoch aware)."""
    return round(0.20 * GENESIS_DISCOUNT, 2) if is_genesis_epoch() else 0.20

def _current_human_standard_price() -> float:
    """Get the current post-free-tier Human_Standard price."""
    return round(0.05 * GENESIS_DISCOUNT, 2) if is_genesis_epoch() else 0.05

# Genesis Ten — premium pricing
GENESIS_TEN_IDS = [f"GENESIS-{i}" for i in range(1, 11)]
GENESIS_TEN_SINGLE_PRICE = 1.25  # $1.25 per record
GENESIS_TEN_SET_PRICE = 10.00    # $10.00 for all 10

def _is_genesis_ten(artifact_id: str) -> bool:
    """Check if an artifact is part of the Genesis Ten collection."""
    return artifact_id in GENESIS_TEN_IDS

def _genesis_info() -> dict:
    """Get Genesis Epoch metadata for responses."""
    if is_genesis_epoch():
        return {
            "genesis_epoch": True,
            "genesis_days_remaining": genesis_days_remaining(),
            "discount": "20% off all paid endpoints",
        }
    return {"genesis_epoch": False}


# ---------------------------------------------------------------------------
# x402 payment dependency factories
# ---------------------------------------------------------------------------

# Network configuration (supports mainnet and Sepolia testnet)
X402_NETWORK = os.environ.get("X402_NETWORK", "eip155:8453")
USDC_ADDRESSES = {
    "eip155:8453": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",   # Base mainnet
    "eip155:84532": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # Base Sepolia
}
USDC_ADDRESS = USDC_ADDRESSES.get(X402_NETWORK, USDC_ADDRESSES["eip155:8453"])

# EIP-712 domain info for USDC (required by x402 facilitator for signature verification)
USDC_EIP712_DOMAINS = {
    "eip155:8453": {"name": "USD Coin", "version": "2"},
    "eip155:84532": {"name": "USDC", "version": "2"},
}
USDC_EIP712_DOMAIN = USDC_EIP712_DOMAINS.get(X402_NETWORK, USDC_EIP712_DOMAINS["eip155:8453"])


def _encode_x402_header(amount: float, resource_url: str = "", description: str = "") -> str:
    """Encode a V2 x402 PAYMENT-REQUIRED header (base64 JSON).

    The x402 Python SDK v2 reads this header to auto-sign EIP-3009
    transferWithAuthorization for USDC on Base L2.
    """
    # Convert USD float to USDC smallest unit (6 decimals)
    amount_smallest = str(int(round(amount * 1_000_000)))
    payload = {
        "x402Version": 2,
        "accepts": [
            {
                "scheme": "exact",
                "network": X402_NETWORK,
                "asset": USDC_ADDRESS,
                "amount": amount_smallest,
                "payTo": BASE_WALLET_ADDRESS,
                "maxTimeoutSeconds": 300,
                "extra": USDC_EIP712_DOMAIN,
            }
        ],
    }
    if resource_url:
        payload["resource"] = {"url": resource_url}
        if description:
            payload["resource"]["description"] = description
    return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def _x402_headers(amount: float, description: str = "", resource_url: str = "") -> dict:
    """Build HTTP headers for a 402 response including the V2 PAYMENT-REQUIRED header."""
    return {
        "PAYMENT-REQUIRED": _encode_x402_header(amount, resource_url, description),
        "X-PAYMENT-REQUIRED": str(amount),
        "X-PAYMENT-CURRENCY": "USDC",
        "X-PAYMENT-CHAIN": "base",
        "X-PAYMENT-RECIPIENT": BASE_WALLET_ADDRESS,
    }


def _x402_payment_required_response(amount: float, description: str) -> dict:
    """Construct the 402 Payment Required detail body (human-readable)."""
    return {
        "error": "Payment required",
        "x402": {
            "version": "1.0",
            "amount": str(amount),
            "currency": "USDC",
            "network": "base",
            "description": description,
            "facilitator": "https://x402.org/facilitator",
            "recipient": BASE_WALLET_ADDRESS,
        },
        "message": f"Payment required: ${amount:.2f} USDC on Base L2",
        **_genesis_info(),
    }


def _rate_limit_exceeded_response() -> dict:
    """Construct the 429 response with V2 upgrade options."""
    hp_price = _current_hybrid_premium_price()
    return {
        "status": "free_tier_exhausted",
        "message": "Daily free quota exceeded. Upgrade to continue.",
        "upgrade_options": {
            "Human_Standard_per_record": f"${_current_human_standard_price():.2f} USDC per record (metadata + image)",
            "Hybrid_Premium_per_record": f"${hp_price:.2f} USDC per record (111-field, 4,000+ tokens + image)",
            "bot_starter": "$40 for 1,000 Human_Standard records",
            "bot_premium": "$175 for 1,000 Hybrid_Premium records",
            "enterprise": "Starting at $8,000 — includes compliance manifests",
        },
        "volume_discounts": {
            "100+_records": "25% off Hybrid_Premium",
            "500+_records": "37% off Hybrid_Premium",
            "2000+_records": "50% off Hybrid_Premium (loyalty floor)",
        },
        "payment_protocol": "x402 USDC on Base L2",
        "escalation_contact": "enterprise@iaeternum.ai",
        **_genesis_info(),
    }


async def _require_payment(
    x_payment: str | None,
    amount: float,
    description: str,
) -> X402PaymentResult:
    """Verify x402 payment or raise 402."""
    result = await verify_x402_payment(x_payment or "", amount)
    if not result.valid:
        raise HTTPException(
            status_code=402,
            detail=_x402_payment_required_response(amount, description),
            headers=_x402_headers(amount, description),
        )
    return result


# ---------------------------------------------------------------------------
# GET /agent/guide — Agent Workflow Documentation
# ---------------------------------------------------------------------------


@router.get("/guide", tags=["discovery"], summary="Agent onboarding guide with workflow, pricing, x402 examples")
async def agent_guide():
    """FREE — Complete agent API guide with workflow, pricing, and examples.

    Returns structured JSON documentation for autonomous agents to understand
    the full Alexandria Aeternum API surface, pricing, and recommended workflows.

    Two data tiers — every purchase is a PACKAGE (metadata + image together):
      - Human_Standard: Museum API + LLM structured (500-1,200 tokens + image)
      - Hybrid_Premium: Full 111-field Golden Codex VLM analysis (2,000-6,000 tokens + image)
    """
    hp_price = _current_hybrid_premium_price()
    hs_price = _current_human_standard_price()
    genesis = _genesis_info()

    return {
        "service": "Intelligence Aeternum — Alexandria Aeternum API",
        "version": "2.2.0",
        **genesis,
        "overview": (
            "53,000+ museum artworks with 111-field Golden Codex metadata. "
            "400x the metadata density of any competitor. "
            "Compliance-ready for EU AI Act Article 53 and CA AB 2013. "
            "Every purchase is a PACKAGE: metadata + image together."
        ),
        "data_tiers": {
            "Human_Standard": f"Museum API + LLM structured metadata + image (${hs_price:.2f} USDC, 5/day free)",
            "Hybrid_Premium": f"Full 111-field Golden Codex VLM analysis + image (${hp_price:.2f} USDC)",
        },
        "recommended_workflow": [
            {
                "step": 1,
                "action": "GET /agent/search?q=impressionist+landscape",
                "cost": "FREE",
                "description": "Search the catalog. Returns artifact IDs with tier labels.",
            },
            {
                "step": 2,
                "action": "GET /agent/artifact/{artifact_id}",
                "cost": f"FREE (5/day), then ${hs_price:.2f} USDC",
                "description": "Get Human_Standard metadata + image (500-1,200 tokens, 100% human-sourced).",
            },
            {
                "step": 3,
                "action": "GET /agent/artifact/{artifact_id}/oracle",
                "cost": f"${hp_price:.2f} USDC",
                "description": "Get Hybrid_Premium metadata + image (2,000-6,000 tokens with VLM visual analysis).",
            },
            {
                "step": 4,
                "action": "POST /agent/enrich",
                "cost": f"From ${hp_price:.2f} USDC",
                "description": "Submit YOUR image for Golden Codex enrichment (Hybrid_Premium + infusion + C2PA).",
            },
        ],
        "endpoints": {
            "free": {
                "search": {
                    "method": "GET",
                    "path": "/agent/search",
                    "params": {"q": "search query", "museum": "met|nga|chicago|cleveland|rijksmuseum|smithsonian", "limit": "1-100"},
                    "rate_limit": "50/hour",
                },
                "human_standard": {
                    "method": "GET",
                    "path": "/agent/artifact/{artifact_id}",
                    "rate_limit": f"5/day free, then ${hs_price:.2f}/record",
                    "output": "Human_Standard JSON (500-1,200 tokens) + image download URL",
                    "note": "Every response includes a signed image download URL — no separate image purchase needed.",
                },
                "compliance": {
                    "method": "GET",
                    "path": "/agent/compliance/{dataset_id}",
                    "output": "AB 2013 + EU AI Act Article 53 manifests",
                },
                "guide": {
                    "method": "GET",
                    "path": "/agent/guide",
                    "output": "This document",
                },
            },
            "paid": {
                "hybrid_premium": {
                    "method": "GET",
                    "path": "/agent/artifact/{artifact_id}/oracle",
                    "price": f"${hp_price:.2f} USDC",
                    "output": "Hybrid_Premium JSON (2,000-6,000 tokens with VLM analysis) + image download URL",
                    "payment": "x402 on Base L2",
                    "note": "Image download URL included — no separate purchase needed.",
                },
                "batch_download": {
                    "method": "POST",
                    "path": "/agent/batch",
                    "price": "$0.05/image (min 100)",
                    "body": {"dataset_id": "met-museum", "quantity": 100, "offset": 0},
                    "output": "Metadata + image URLs for all records",
                },
                "enrich_your_image": {
                    "method": "POST",
                    "path": "/agent/enrich",
                    "description": "Submit YOUR image for Golden Codex enrichment",
                    "tiers": {
                        "oracle_only": {
                            "price": f"${hp_price:.2f} USDC",
                            "output": "Golden Codex JSON (111-field Hybrid_Premium analysis)",
                        },
                        "oracle_plus_infuse": {
                            "price": f"${round(0.30 * GENESIS_DISCOUNT, 2) if is_genesis_epoch() else 0.30:.2f} USDC",
                            "output": "Golden Codex JSON + XMP-infused image + hash registered in GCX registry",
                        },
                        "full_certified": {
                            "price": f"${round(0.50 * GENESIS_DISCOUNT, 2) if is_genesis_epoch() else 0.50:.2f} USDC",
                            "output": "Golden Codex JSON + infused image + C2PA signed + hash registered",
                        },
                    },
                    "body_example": {
                        "image_url": "https://example.com/my-image.jpg",
                        "tier": "oracle_plus_infuse",
                        "custom_fields": {
                            "title": "Sunset Over Barcelona",
                            "artist": "Your Name",
                            "copyright_holder": "Your Studio LLC",
                            "creation_year": "2026",
                            "medium": "Digital Photography",
                            "commercial_use": True,
                        },
                        "callback_url": "https://your-webhook.com/done",
                    },
                },
            },
        },
        "custom_fields_protocol": {
            "description": (
                "Submit any fields in custom_fields. The Hybrid_Premium Oracle analyzes the image "
                "independently, then MERGES your fields. Your values take priority for factual fields "
                "(title, artist, copyright_holder, creation_year, medium). "
                "The Oracle adds analytical fields (composition, emotional_resonance, technique_analysis). "
                "Fields you provide are marked source: 'submitter'. "
                "Fields the Oracle generates are marked source: 'golden_codex_vlm'."
            ),
            "accepted_fields": [
                "title", "artist", "copyright_holder", "creation_year",
                "medium", "dimensions", "commercial_use", "collection_name",
                "description", "tags", "custom_notes",
            ],
        },
        "volume_discounts": {
            "description": "Automatic per-wallet Hybrid_Premium discounts on 30-day rolling window",
            "tiers": [
                {"records": "1-99", "price": f"${hp_price:.2f}", "discount": "Standard"},
                {"records": "100-499", "price": f"${round(0.15 * (GENESIS_DISCOUNT if is_genesis_epoch() else 1), 2):.2f}", "discount": "25% off"},
                {"records": "500-1999", "price": f"${round(0.125 * (GENESIS_DISCOUNT if is_genesis_epoch() else 1), 2):.2f}", "discount": "37% off"},
                {"records": "2000+", "price": f"${round(0.10 * (GENESIS_DISCOUNT if is_genesis_epoch() else 1), 2):.2f}", "discount": "50% off (loyalty floor)"},
            ],
        },
        "x402_payment_flow": {
            "summary": [
                "1. Call any paid endpoint — receive HTTP 402 with PAYMENT-REQUIRED header",
                "2. x402 SDK auto-decodes header, signs EIP-3009 transferWithAuthorization",
                "3. SDK retries with PAYMENT-SIGNATURE header — facilitator settles USDC on Base",
                "4. Server returns the data (metadata + image URL in one response)",
            ],
            "protocol": "x402 V2 (Base L2 USDC)",
            "network": X402_NETWORK,
            "usdc_contract": USDC_ADDRESS,
            "recipient": BASE_WALLET_ADDRESS,
            "python_example": {
                "description": "Full x402 auto-payment with the Python SDK",
                "install": "pip install 'x402[evm,requests]' eth-account",
                "code": (
                    "from eth_account import Account\n"
                    "from x402 import x402ClientSync\n"
                    "from x402.mechanisms.evm.signers import EthAccountSigner\n"
                    "from x402.mechanisms.evm.exact import register_exact_evm_client\n"
                    "from x402.http.clients.requests import wrapRequestsWithPayment\n"
                    "import requests\n\n"
                    "account = Account.from_key('0xYOUR_PRIVATE_KEY')\n"
                    "signer = EthAccountSigner(account)\n"
                    "client = x402ClientSync()\n"
                    f"register_exact_evm_client(client, signer, networks='{X402_NETWORK}')\n"
                    "session = wrapRequestsWithPayment(requests.Session(), client)\n\n"
                    "# This auto-handles 402 -> sign -> retry\n"
                    "resp = session.get('https://data-portal-172867820131.us-west1.run.app"
                    "/agent/artifact/met_437419/oracle')\n"
                    "data = resp.json()  # Hybrid_Premium metadata + image URL"
                ),
            },
            "curl_example": {
                "description": "Manual flow: inspect 402, then pay separately",
                "step_1": "curl -i https://data-portal-172867820131.us-west1.run.app/agent/artifact/met_437419/oracle",
                "step_1_note": "Returns 402 with base64 PAYMENT-REQUIRED header containing amount/network/recipient",
                "step_2": "Decode header, sign EIP-3009, retry with PAYMENT-SIGNATURE header",
            },
        },
        "enterprise": {
            "from": "$8,000",
            "includes": "Full dataset access, compliance manifests, legal attestation",
            "contact": "enterprise@iaeternum.ai",
        },
        "free_forever": [
            "Search (50/hour)",
            "HuggingFace 10K dataset (huggingface.co/datasets/Metavolve-Labs/alexandria-aeternum-genesis)",
            "Compliance manifest previews",
            "Academic/non-commercial access with attribution",
        ],
        "featured_collection": {
            "name": "Alexandria Aeternum Genesis 10K",
            "description": (
                "The founding collection: 10,090 museum-grade artworks with full 111-field NEST metadata. "
                "Empirically proven to improve VLM capability — sparse metadata lobotomizes models, "
                "dense NEST metadata enhances them. See the peer-reviewed evidence."
            ),
            "paper": {
                "title": "The Density Imperative: How Semantic Curation Depth Determines Vision-Language Model Capability",
                "author": "Tad MacPherson, Metavolve Labs",
                "doi": "https://doi.org/10.5281/zenodo.18667735",
                "key_finding": (
                    "63-point cognitive swing between sparse and dense fine-tuning. "
                    "Sparse captions reduce cognitive depth by 54.4% and increase hallucinations by 330%. "
                    "Dense NEST metadata improves visual perception by 25.5% and semantic coverage by 160.3%."
                ),
            },
            "cognitive_nutrition_shot": {
                "description": (
                    "500 curated records with full NEST metadata — a targeted fine-tuning boost "
                    "backed by peer-reviewed research. Your model gets measurably smarter."
                ),
                "endpoint": "GET /agent/artifact/{id}/oracle (per-record) or POST /agent/batch (bulk)",
                "price": "$0.16/record or $0.05/record in batch (100+ min)",
            },
            "huggingface": "https://huggingface.co/datasets/Metavolve-Labs/alexandria-aeternum-genesis",
            "zenodo_doi": "https://doi.org/10.5281/zenodo.18359131",
            "total_records": 10090,
            "schema_fields": 111,
            "museums": ["Metropolitan Museum of Art", "Art Institute of Chicago", "National Gallery of Art", "Cleveland Museum of Art", "Rijksmuseum", "Smithsonian"],
        },
        "genesis_ten": {
            "description": (
                "The ten most iconic artworks in human history, each with 134-146 field "
                "Golden Codex metadata including soulWhisper (Claude), Nova analysis (Gemini), "
                "and full provenance. These are the crown jewels of Alexandria Aeternum."
            ),
            "artifacts": [
                {"id": "GENESIS-1", "title": "Mona Lisa (La Gioconda)", "creator": "Leonardo da Vinci", "fields": 140},
                {"id": "GENESIS-2", "title": "The Starry Night", "creator": "Vincent van Gogh", "fields": 137},
                {"id": "GENESIS-3", "title": "The Great Wave off Kanagawa", "creator": "Katsushika Hokusai", "fields": 137},
                {"id": "GENESIS-4", "title": "Girl with a Pearl Earring", "creator": "Johannes Vermeer", "fields": 137},
                {"id": "GENESIS-5", "title": "The Scream", "creator": "Edvard Munch", "fields": 137},
                {"id": "GENESIS-6", "title": "The Birth of Venus", "creator": "Sandro Botticelli", "fields": 134},
                {"id": "GENESIS-7", "title": "The Creation of Adam", "creator": "Michelangelo", "fields": 137},
                {"id": "GENESIS-8", "title": "Liberty Leading the People", "creator": "Eugene Delacroix", "fields": 137},
                {"id": "GENESIS-9", "title": "Impression, Sunrise", "creator": "Claude Monet", "fields": 137},
                {"id": "GENESIS-10", "title": "The Last Supper", "creator": "Leonardo da Vinci", "fields": 146},
            ],
            "try_free": "GET /agent/artifact/GENESIS-1",
            "buy_oracle": "GET /agent/artifact/GENESIS-1/oracle ($0.16 USDC)",
        },
        "demo_artifacts": {
            "description": "Try these artifact IDs to test the API (5 free/day per IP):",
            "examples": [
                {"id": "GENESIS-1", "title": "Mona Lisa", "collection": "Genesis Ten"},
                {"id": "GENESIS-2", "title": "The Starry Night", "collection": "Genesis Ten"},
                {"id": "chicago_28560", "title": "The Bedroom", "museum": "Art Institute of Chicago"},
                {"id": "met_437419", "title": "Rembrandt as a Young Man", "museum": "Metropolitan Museum"},
                {"id": "cleveland_127953", "title": "The Garden of the Rousseau Family", "museum": "Cleveland Museum of Art"},
                {"id": "rijksmuseum_SK-A-3262", "title": "Self-portrait (Van Gogh)", "museum": "Rijksmuseum"},
            ],
            "try_it": "GET /agent/artifact/GENESIS-1",
        },
    }


# ---------------------------------------------------------------------------
# FREE endpoints (discovery + curated tier)
# ---------------------------------------------------------------------------


VERTEX_SEARCH_URL = os.getenv(
    "VERTEX_SEARCH_URL",
    "https://us-west1-the-golden-codex-1111.cloudfunctions.net/alexandria-search",
)


async def _manifest_search(db, q: str, museum: Optional[str], limit: int) -> Optional[list]:
    """Search the Firestore alexandria_manifest collection.

    Primary search path (replaces Vertex AI Search + GCS scanning).
    Queries title and artist fields. Fast, no GCS scanning required.
    """
    try:
        q_lower = q.lower().strip()
        collection_ref = db.collection("alexandria_manifest")

        # Firestore doesn't support full-text search natively, so we query
        # with a prefix match on title and also do a broader museum-filtered scan.
        results = []

        # Strategy 1: Exact museum + scan (if museum filter provided)
        if museum:
            query = collection_ref.where("museum", "==", museum).limit(limit * 5)
            docs = query.stream()
            async for doc in docs:
                data = doc.to_dict()
                searchable = " ".join([
                    data.get("title", ""),
                    data.get("artist", ""),
                    data.get("classification", ""),
                    data.get("medium", ""),
                    data.get("department", ""),
                ]).lower()
                if q_lower in searchable:
                    results.append(_manifest_to_search_result(doc.id, data))
                    if len(results) >= limit:
                        break
        else:
            # Strategy 2: Search across all museums — scan each one
            for m in _MUSEUM_PREFIXES:
                query = collection_ref.where("museum", "==", m).limit(limit * 3)
                docs = query.stream()
                async for doc in docs:
                    data = doc.to_dict()
                    searchable = " ".join([
                        data.get("title", ""),
                        data.get("artist", ""),
                        data.get("classification", ""),
                        data.get("medium", ""),
                    ]).lower()
                    if q_lower in searchable:
                        results.append(_manifest_to_search_result(doc.id, data))
                        if len(results) >= limit:
                            break
                if len(results) >= limit:
                    break

        return results if results else None
    except Exception as exc:
        logger.warning("Manifest search failed, falling back to Vertex: %s", exc)
        return None


def _manifest_to_search_result(doc_id: str, data: dict) -> dict:
    """Convert a manifest doc to a search result dict."""
    museum = data.get("museum", "")
    has_cache = data.get("cached", False)
    tier = "Hybrid_Premium" if has_cache else "Human_Standard"
    return {
        "artifact_id": doc_id,
        "museum": museum,
        "data_tier": tier,
        "title": data.get("title", ""),
        "artist": data.get("artist", "")[:100],
        "date": data.get("date", ""),
        "classification": data.get("classification", ""),
        "image_url": data.get("image_source_url", ""),
        "human_standard_endpoint": f"/agent/artifact/{doc_id}",
        "hybrid_premium_endpoint": f"/agent/artifact/{doc_id}/oracle",
        "delivery_endpoint": f"/deliver/order",
        "source": "manifest",
    }


async def _vertex_search(q: str, museum: Optional[str], limit: int):
    """Call the Alexandria Vertex AI Search Cloud Function (legacy fallback)."""
    import httpx

    body: dict = {"query": q, "pageSize": limit}
    if museum:
        body["filters"] = {"department": museum}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(VERTEX_SEARCH_URL, json=body)
            if resp.status_code == 200:
                data = resp.json()
                raw = data.get("results", [])
                results = []
                for r in raw:
                    aid = r.get("id", "")
                    # Infer museum from artifact_id prefix (most reliable —
                    # Vertex index has data quality issues where source_museum
                    # is wrong, e.g. "met" for rijksmuseum objects)
                    result_museum = ""
                    if "_" in aid:
                        prefix = aid.split("_")[0]
                        if prefix in ("met", "nga", "chicago", "cleveland", "rijksmuseum", "smithsonian", "paris"):
                            result_museum = prefix
                    if not result_museum:
                        result_museum = r.get("source_museum", r.get("department", ""))
                    tier = r.get("data_tier", "Human_Standard")
                    result_item = {
                        "artifact_id": aid,
                        "museum": result_museum or "unknown",
                        "data_tier": tier,
                        "title": r.get("title", ""),
                        "artist": str(r.get("creator", ""))[:100],
                        "date": r.get("date", ""),
                        "classification": r.get("medium", r.get("classification", "")),
                        "image_url": r.get("primary_image", ""),
                        "human_standard_endpoint": f"/agent/artifact/{aid}",
                        "source": "vertex_ai_search",
                    }
                    if tier == "Hybrid_Premium":
                        result_item["hybrid_premium_endpoint"] = f"/agent/artifact/{aid}/oracle"
                    results.append(result_item)
                return results
    except Exception as exc:
        logger.warning("Vertex AI Search unavailable, falling back to GCS: %s", exc)
    return None


async def _enrich_vertex_results(db, results: list) -> list:
    """Enrich Vertex AI Search results with Firestore manifest data.

    Vertex results often have empty date/classification/image_url fields.
    Look up each artifact in the manifest and fill in missing fields.
    """
    if not results:
        return results

    enriched = []
    for r in results:
        aid = r.get("artifact_id", "")
        if aid:
            try:
                doc = await db.collection("alexandria_manifest").document(aid).get()
                if doc.exists:
                    data = doc.to_dict()
                    if not r.get("date"):
                        r["date"] = data.get("date", "")
                    if not r.get("classification"):
                        r["classification"] = data.get("classification", data.get("medium", ""))
                    if not r.get("image_url"):
                        r["image_url"] = data.get("image_source_url", "")
                    if not r.get("artist") or r["artist"] == "Unknown Artist":
                        manifest_artist = data.get("artist", "")
                        if manifest_artist:
                            r["artist"] = manifest_artist[:100]
                    # Add oracle endpoint if cached
                    if data.get("cached"):
                        r["data_tier"] = "Hybrid_Premium"
                        r["hybrid_premium_endpoint"] = f"/agent/artifact/{aid}/oracle"
            except Exception:
                pass  # Keep original Vertex data
        enriched.append(r)
    return enriched


def _gcs_search_sync(q: str, museum: Optional[str], limit: int):
    """Fallback: scan curated JSONs in GCS (synchronous, run in executor)."""
    from google.cloud import storage as gcs

    client = gcs.Client()
    bucket_obj = client.bucket(DATA_BUCKET)
    results = []
    q_lower = q.lower()

    museums = [museum] if museum else ["met", "nga", "chicago", "cleveland", "rijksmuseum", "smithsonian"]
    for m in museums:
        prefix = f"04-curated-context/{m}/"
        blobs = client.list_blobs(bucket_obj, prefix=prefix, max_results=500)
        for blob in blobs:
            if not blob.name.endswith("_curated.json"):
                continue
            try:
                data = json.loads(blob.download_as_text())
                searchable = " ".join([
                    data.get("title", ""),
                    str(data.get("provenance_and_lineage", {}).get("artist_information", "")),
                    str(data.get("artistic_statement", {}).get("cultural_context", "")),
                    str(data.get("cultural_and_artistic_context", {}).get("period_and_movement", "")),
                    str(data.get("technical_details", {}).get("medium_and_technique", "")),
                    " ".join(data.get("contextual_graph", {}).get("keywords", [])),
                ]).lower()
                if q_lower in searchable:
                    ids = data.get("_identifiers", {})
                    aid = ids.get("artifactId", "")
                    results.append({
                        "artifact_id": aid,
                        "museum": ids.get("source_museum", m),
                        "data_tier": "Human_Standard",
                        "title": data.get("title", ""),
                        "artist": data.get("provenance_and_lineage", {}).get("artist_information", "")[:100],
                        "date": data.get("provenance_and_lineage", {}).get("creation_date", ""),
                        "classification": data.get("technical_details", {}).get("classification", ""),
                        "human_standard_endpoint": f"/agent/artifact/{aid}",
                        "source": "gcs_scan",
                    })
                    if len(results) >= limit:
                        break
            except Exception:
                continue
        if len(results) >= limit:
            break
    return results


@router.get("/search", tags=["discovery"], summary="Search the Alexandria Aeternum catalog (FREE)")
async def search_artifacts(
    request: Request,
    q: str = Query(description="Free-text search across title, artist, movement, medium"),
    museum: Optional[str] = Query(default=None, description="Filter by museum: met, nga, chicago, cleveland, rijksmuseum, smithsonian"),
    limit: int = Query(default=20, le=100),
):
    """FREE (rate-limited) — Search the Alexandria Aeternum catalog.

    No payment required. Returns artifact IDs, titles, and summary metadata.
    Uses Vertex AI Search (semantic) with GCS fallback.
    Rate limited to 50 searches per hour per client.
    """
    client_id = get_client_fingerprint(request)
    is_allowed = await rate_limiter.check_async(f"search:{client_id}", 50, 3600)
    if not is_allowed:
        raise HTTPException(status_code=429, detail=_rate_limit_exceeded_response())

    # Primary: Firestore manifest search (fast, no GCS scanning)
    db = request.state.db
    manifest_results = await _manifest_search(db, q, museum, limit)

    if manifest_results is not None:
        results = manifest_results[:limit]
    else:
        # Fallback: Vertex AI Search (for pre-manifest data)
        vertex_results = await _vertex_search(q, museum, limit)
        if vertex_results is not None:
            # Enrich Vertex results with manifest data (fills empty fields)
            results = await _enrich_vertex_results(db, vertex_results[:limit])
        else:
            # Last resort: GCS scan with timeout guard
            import asyncio
            try:
                gcs_results = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, lambda: _gcs_search_sync(q, museum, limit)
                    ),
                    timeout=8.0,
                )
                results = gcs_results
            except asyncio.TimeoutError:
                logger.warning("GCS search fallback timed out for q=%s", q)
                results = []

    hp_price = _current_hybrid_premium_price()
    hs_price = _current_human_standard_price()

    return {
        "query": q,
        "museum_filter": museum,
        "total_results": len(results),
        "results": results,
        "pricing": {
            "search": "FREE (50/hour)",
            "Human_Standard": {
                "package": f"FREE (5/day), then ${hs_price:.2f}/record (metadata + image)",
            },
            "Hybrid_Premium": {
                "package": f"${hp_price:.2f}/record (metadata + image, x402 USDC on Base L2)",
            },
            "enrich_your_own": f"POST /agent/enrich — from ${hp_price:.2f} USDC",
            "note": "Every purchase is a package: metadata + image together. No separate image price.",
        },
        "guide": "/agent/guide",
        **_genesis_info(),
    }


@router.get("/artifact/{artifact_id}", tags=["data"], summary="Get Human_Standard metadata + image (FREE 5/day)")
async def get_curated_artifact(
    artifact_id: str,
    request: Request,
):
    """FREE (5/day) — Get Human_Standard metadata + image for an artifact.

    Returns metadata from the alexandria_manifest (or legacy GCS curated JSON)
    plus a signed image download URL. Every response is a PACKAGE.
    """
    from google.cloud import storage as gcs

    db = request.state.db
    client = gcs.Client()
    bucket_obj = client.bucket(DATA_BUCKET)

    data = None
    matched_museum = None
    numeric_id = artifact_id
    manifest_doc = None

    # --- Strategy 1: Firestore manifest (fast, preferred) ---
    try:
        manifest_ref = await db.collection("alexandria_manifest").document(artifact_id).get()
        if manifest_ref.exists:
            manifest_doc = manifest_ref.to_dict()
            matched_museum = manifest_doc.get("museum", "")
            numeric_id = manifest_doc.get("object_id", artifact_id)
            # Build Human_Standard metadata from manifest fields
            data = {
                "title": manifest_doc.get("title", ""),
                "artist": manifest_doc.get("artist", ""),
                "date": manifest_doc.get("date", ""),
                "medium": manifest_doc.get("medium", ""),
                "classification": manifest_doc.get("classification", ""),
                "dimensions": manifest_doc.get("dimensions", ""),
                "department": manifest_doc.get("department", ""),
                "museum_url": manifest_doc.get("museum_url", ""),
                "license": manifest_doc.get("license", "CC0 1.0"),
                "source_museum": matched_museum,
                "schemaVersion": "1.0.0-manifest",
                "_source": "alexandria_manifest",
            }
    except Exception as e:
        logger.debug("Manifest lookup failed for %s: %s", artifact_id, e)

    # --- Strategy 2: Legacy GCS curated JSON (fallback) ---
    if data is None:
        for museum_prefix in _MUSEUM_PREFIXES:
            if artifact_id.startswith(f"{museum_prefix}_"):
                numeric_id = artifact_id[len(museum_prefix) + 1:]
                blob_path = f"04-curated-context/{museum_prefix}/{numeric_id}_curated.json"
                blob = bucket_obj.blob(blob_path)
                if blob.exists():
                    data = json.loads(blob.download_as_text())
                    matched_museum = museum_prefix
                    break
            blob_path = f"04-curated-context/{museum_prefix}/{artifact_id}_curated.json"
            blob = bucket_obj.blob(blob_path)
            if blob.exists():
                data = json.loads(blob.download_as_text())
                matched_museum = museum_prefix
                numeric_id = artifact_id
                break

    # H-1 FIX: Check existence BEFORE payment — never charge for a 404
    if data is None:
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")

    # --- Rate limiting / payment (only AFTER confirming artifact exists) ---
    client_id = get_client_fingerprint(request)
    limiter_key = f"curated_free:{client_id}"
    is_free = await rate_limiter.check_async(limiter_key, 5, 86400)

    hs_price = _current_human_standard_price()

    if not is_free:
        x_payment = request.headers.get("PAYMENT-SIGNATURE", "") or request.headers.get("X-PAYMENT", "")
        if not x_payment:
            raise HTTPException(
                status_code=402,
                detail=_x402_payment_required_response(
                    hs_price,
                    f"Human_Standard metadata + image for {artifact_id} (free quota exceeded, 5/day)",
                ),
                headers=_x402_headers(hs_price, f"Human_Standard: {artifact_id}"),
            )
        result = await verify_x402_payment(x_payment, hs_price)
        if not result.valid:
            raise HTTPException(
                status_code=402,
                detail=_x402_payment_required_response(hs_price, f"Human_Standard: {artifact_id}"),
                headers=_x402_headers(hs_price, f"Human_Standard: {artifact_id}"),
            )

    hp_price = _current_hybrid_premium_price()

    # Bundle image URL with metadata (no separate image purchase)
    # Cloud Run terminates TLS at the LB, so request.base_url is http://; force https
    base_url = str(request.base_url).rstrip("/").replace("http://", "https://")
    image_url = _find_image_url(bucket_obj, artifact_id, numeric_id, matched_museum or "met", base_url, manifest_doc=manifest_doc)

    return {
        "artifact_id": artifact_id,
        "schema_version": data.get("schemaVersion", "1.0.0-curated"),
        "data_tier": "Human_Standard",
        "token_count": data.get("token_count", 0),
        "metadata": data,
        "image": {
            "download_url": image_url,
            "note": "Image included with Human_Standard package — no separate purchase needed.",
        },
        "upgrade": {
            "hybrid_premium_endpoint": f"/agent/artifact/{artifact_id}/oracle",
            "hybrid_premium_price": f"${hp_price:.2f} USDC (x402)",
            "description": "Upgrade to Hybrid_Premium: VLM deep visual analysis — composition, color palette, emotional journey, symbolism (2,000-6,000 tokens + image)",
        },
        "enrich_your_own": {
            "endpoint": "POST /agent/enrich",
            "description": "Submit YOUR image for Golden Codex enrichment",
            "guide": "/agent/guide",
        },
        "license": "Intelligence Aeternum Data License v1.0",
        "compliance": {
            "ab_2013": True,
            "eu_ai_act": True,
            "gdpr": True,
            "source_license": "CC0 1.0 Public Domain",
        },
        **_genesis_info(),
    }


@router.get("/compliance/{dataset_id}", tags=["compliance"], summary="AB 2013 + EU AI Act compliance manifest (FREE)")
async def get_compliance_manifest(
    dataset_id: str,
    request: Request,
    format: str = Query(default="json", description="Output format: json or markdown"),
    regulation: str = Query(default="all", description="Regulation: ab2013, eu_ai_act, or all"),
):
    """FREE — Get compliance manifests for a dataset."""
    from compliance import generate_ab2013_manifest, generate_eu_ai_act_article53_manifest

    order_stub = {
        "order_id": f"compliance-preview-{dataset_id}",
        "dataset_id": dataset_id,
        "quantity": 0,
        "total_price": 0,
        "payment_method": "preview",
        "pricing_tier": "preview",
    }

    result = {}

    if regulation in ("ab2013", "all"):
        ab = generate_ab2013_manifest(order_stub, dataset_id)
        result["ab_2013"] = ab["json"] if format == "json" else ab["markdown"]

    if regulation in ("eu_ai_act", "all"):
        eu = generate_eu_ai_act_article53_manifest(order_stub, dataset_id)
        result["eu_ai_act_article_53"] = eu["json"] if format == "json" else eu["markdown"]

    if not result:
        raise HTTPException(status_code=400, detail="Invalid regulation. Use: ab2013, eu_ai_act, or all")

    return {
        "dataset_id": dataset_id,
        "regulation": regulation,
        "format": format,
        "manifests": result,
        "note": "These manifests are auto-generated previews. Purchase manifests include order-specific details.",
        "pricing": "FREE — Compliance manifests are included with every transaction at no additional cost.",
    }


@router.get("/genesis-ten", tags=["data"], summary="Buy the Genesis Ten bundle — all 10 iconic artworks ($10.00 USDC)")
async def get_genesis_ten_bundle(request: Request):
    """PAID ($10.00 USDC) — The complete Genesis Ten collection.

    The ten most iconic artworks in human history, each with 134-146 field
    Golden Codex metadata including soulWhisper, visual analysis, poetic
    interpretation, and full provenance. Save $2.50 vs buying individually.

    Backed by peer-reviewed research (DOI: 10.5281/zenodo.18667735) proving
    dense metadata improves VLM capability by 25.5%.
    """
    from google.cloud import storage as gcs

    x_payment = request.headers.get("PAYMENT-SIGNATURE", "") or request.headers.get("X-PAYMENT", "")
    if not x_payment:
        raise HTTPException(
            status_code=402,
            detail=_x402_payment_required_response(
                GENESIS_TEN_SET_PRICE,
                "Genesis Ten Bundle: all 10 iconic artworks with full soulWhisper + Golden Codex metadata. "
                "Save $2.50 vs individual purchase ($12.50).",
            ),
            headers=_x402_headers(GENESIS_TEN_SET_PRICE, "Genesis Ten Bundle (10 artworks)"),
        )
    payment_result = await verify_x402_payment(x_payment, GENESIS_TEN_SET_PRICE)
    if not payment_result.valid:
        raise HTTPException(
            status_code=402,
            detail=_x402_payment_required_response(GENESIS_TEN_SET_PRICE, "Genesis Ten Bundle"),
            headers=_x402_headers(GENESIS_TEN_SET_PRICE, "Genesis Ten Bundle"),
        )

    # Load all 10 artifacts
    client = gcs.Client()
    bucket_obj = client.bucket(DATA_BUCKET)
    artifacts = []

    for aid in GENESIS_TEN_IDS:
        oracle_blob = bucket_obj.blob(f"03-vertex-enriched/{aid}.json")
        if oracle_blob.exists():
            oracle_data = json.loads(oracle_blob.download_as_text())
            artifacts.append({
                "artifact_id": aid,
                "title": oracle_data.get("title", ""),
                "creator": oracle_data.get("creator", ""),
                "metadata": oracle_data,
            })

    await log_transaction(
        request, endpoint="genesis_ten_bundle", artifact_id="GENESIS-BUNDLE",
        amount_usd=GENESIS_TEN_SET_PRICE, extra={"count": len(artifacts)},
    )

    return {
        "collection": "Alexandria Aeternum Genesis Ten",
        "description": (
            "The ten most iconic artworks in human history with full Golden Codex metadata. "
            "Each record contains 134-146 fields including soulWhisper, visual analysis, "
            "poetic interpretation, emotional journey, and provenance."
        ),
        "paper_doi": "https://doi.org/10.5281/zenodo.18667735",
        "total_artifacts": len(artifacts),
        "payment": {"amount": GENESIS_TEN_SET_PRICE, "currency": "USDC", "settled": "x402"},
        "artifacts": artifacts,
        "license": "Intelligence Aeternum Data License v1.0",
        **_genesis_info(),
    }


@router.get("/artifact/{artifact_id}/oracle", tags=["data"], summary="Get Hybrid_Premium 111-field metadata + image ($0.16 USDC via x402)")
async def get_oracle_artifact(
    artifact_id: str,
    request: Request,
):
    """PAID ($0.20 USDC, $0.16 Genesis) — Get Hybrid_Premium metadata + image.

    Returns the full GCX v1.0.0 Hybrid_Premium tier with VLM deep visual analysis
    plus a signed image download URL. Every response is a PACKAGE.
    Payment enforced at handler level.

    Search order:
    1. GCS cache (previously fulfilled via /deliver pipeline)
    2. Legacy GCS enriched/curated JSON
    3. Manifest metadata (on-demand enrichment recommended via /deliver)
    """
    from google.cloud import storage as gcs

    db = request.state.db
    client = gcs.Client()
    bucket_obj = client.bucket(DATA_BUCKET)

    numeric_id = artifact_id
    for mp in _MUSEUM_PREFIXES:
        if artifact_id.startswith(f"{mp}_"):
            numeric_id = artifact_id[len(mp) + 1:]
            break

    # --- Check existence BEFORE payment (never charge for a 404) ---
    oracle_data = None
    curated_data = None
    matched_museum = None
    manifest_doc = None

    # 1. Check Firestore manifest + GCS cache (on-demand delivery results)
    try:
        manifest_ref = await db.collection("alexandria_manifest").document(artifact_id).get()
        if manifest_ref.exists:
            manifest_doc = manifest_ref.to_dict()
            matched_museum = manifest_doc.get("museum", "")
            # Check if this artifact was previously enriched (cached)
            if manifest_doc.get("cached") and manifest_doc.get("cache_path"):
                cache_prefix = manifest_doc["cache_path"]
                json_blob = bucket_obj.blob(f"{cache_prefix}golden_codex.json")
                if json_blob.exists():
                    oracle_data = json.loads(json_blob.download_as_text())
    except Exception as e:
        logger.debug("Manifest/cache lookup failed for %s: %s", artifact_id, e)

    # 2. Check legacy GCS enriched JSON
    if oracle_data is None:
        for id_variant in [artifact_id, numeric_id]:
            oracle_path = f"03-vertex-enriched/{id_variant}.json"
            oracle_blob = bucket_obj.blob(oracle_path)
            if oracle_blob.exists():
                oracle_data = json.loads(oracle_blob.download_as_text())
                break

    # 3. Fall back to curated context
    if oracle_data is None:
        for museum_prefix in _MUSEUM_PREFIXES:
            for id_variant in [numeric_id, artifact_id]:
                blob_path = f"04-curated-context/{museum_prefix}/{id_variant}_curated.json"
                blob = bucket_obj.blob(blob_path)
                if blob.exists():
                    curated_data = json.loads(blob.download_as_text())
                    matched_museum = museum_prefix
                    break
            if curated_data:
                break

    # 4. Manifest exists but no enrichment yet — artifact is accessible
    if oracle_data is None and curated_data is None and manifest_doc:
        curated_data = {
            "title": manifest_doc.get("title", ""),
            "artist": manifest_doc.get("artist", ""),
            "date": manifest_doc.get("date", ""),
            "medium": manifest_doc.get("medium", ""),
            "classification": manifest_doc.get("classification", ""),
            "museum_url": manifest_doc.get("museum_url", ""),
            "schemaVersion": "1.0.0-manifest",
            "_source": "alexandria_manifest",
            "_note": "On-demand enrichment available via POST /deliver/order",
        }
        matched_museum = manifest_doc.get("museum", "")

    if oracle_data is None and curated_data is None:
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")

    # --- Payment enforcement (only after confirming artifact exists) ---
    # Genesis Ten: premium pricing ($1.25 per record)
    is_gt = _is_genesis_ten(artifact_id)
    price = GENESIS_TEN_SINGLE_PRICE if is_gt else _current_hybrid_premium_price()
    tier_label = "Genesis Ten" if is_gt else "Hybrid_Premium"

    x_payment = request.headers.get("PAYMENT-SIGNATURE", "") or request.headers.get("X-PAYMENT", "")
    logger.info("Oracle payment check: artifact=%s, tier=%s, price=$%.2f, has_payment=%s",
                artifact_id, tier_label, price, bool(x_payment))
    if not x_payment:
        desc = (
            f"Genesis Ten — {artifact_id}: one of the ten most iconic artworks in history, "
            f"134-146 field Golden Codex metadata with soulWhisper. "
            f"Full set: $10.00 via GET /agent/genesis-ten"
        ) if is_gt else f"Hybrid_Premium metadata + image for {artifact_id}"
        raise HTTPException(
            status_code=402,
            detail=_x402_payment_required_response(price, desc),
            headers=_x402_headers(price, f"{tier_label}: {artifact_id}"),
        )
    payment_result = await verify_x402_payment(x_payment, price)
    if not payment_result.valid:
        raise HTTPException(
            status_code=402,
            detail=_x402_payment_required_response(price, f"{tier_label}: {artifact_id}"),
            headers=_x402_headers(price, f"{tier_label}: {artifact_id}"),
        )

    # Generate image URL (bundled with metadata — no separate purchase)
    img_museum = None
    for mp in _MUSEUM_PREFIXES:
        if artifact_id.startswith(f"{mp}_"):
            img_museum = mp
            break
    base_url = str(request.base_url).rstrip("/").replace("http://", "https://")
    image_url = _find_image_url(bucket_obj, artifact_id, numeric_id, img_museum or matched_museum or "met", base_url, manifest_doc=manifest_doc)

    if oracle_data is not None:
        # Track volume for wallet-based discounts
        wallet = request.headers.get("X-WALLET", "")
        if wallet:
            await volume_tracker.record_purchase(wallet, 1, price, tier_label.lower())

        response = {
            "artifact_id": artifact_id,
            "schema_version": oracle_data.get("schemaVersion", "1.0.0"),
            "data_tier": tier_label,
            "metadata": oracle_data,
            "image": {
                "download_url": image_url,
                "note": "Image included with package.",
            },
            "payment": {"amount": price, "currency": "USDC", "settled": "x402"},
            "license": "Intelligence Aeternum Data License v1.0",
            **_genesis_info(),
        }
        if is_gt:
            response["collection"] = "Alexandria Aeternum Genesis Ten"
            response["set_offer"] = {
                "endpoint": "GET /agent/genesis-ten",
                "price": "$10.00 USDC for all 10 (save $2.50)",
                "description": "The ten most iconic artworks in history with full soulWhisper + Golden Codex metadata",
            }
        else:
            response["enrich_your_own"] = {
                "endpoint": "POST /agent/enrich",
                "description": "Submit YOUR image for the same Golden Codex enrichment",
                "guide": "/agent/guide",
            }
        await log_transaction(
            request, endpoint=tier_label.lower(), artifact_id=artifact_id,
            amount_usd=price, extra={"data_tier": tier_label, "genesis_ten": is_gt},
        )
        return response

    # Return Human_Standard with on-demand enrichment option
    data = curated_data
    wallet = request.headers.get("X-WALLET", "")
    if wallet:
        await volume_tracker.record_purchase(wallet, 1, price, tier_label.lower())

    response = {
        "artifact_id": artifact_id,
        "schema_version": data.get("schemaVersion", "1.0.0-curated"),
        "data_tier": "Human_Standard",
        "note": "Hybrid_Premium enrichment not yet cached. Use /deliver/order for on-demand enrichment.",
        "metadata": data,
        "image": {
            "download_url": image_url,
            "note": "Image included with package.",
        },
        "on_demand_enrichment": {
            "endpoint": "POST /deliver/order",
            "description": "Get full Hybrid_Premium enrichment (Nova + Atlas) delivered on-demand.",
            "body": {"artifact_ids": [artifact_id], "tier": "hybrid_premium"},
        },
        "payment": {"amount": hp_price, "currency": "USDC", "settled": "x402"},
        "license": "Intelligence Aeternum Data License v1.0",
        **_genesis_info(),
    }
    await log_transaction(
        request, endpoint="hybrid_premium", artifact_id=artifact_id,
        amount_usd=hp_price, extra={"data_tier": "Human_Standard_fallback"},
    )
    return response


# ---------------------------------------------------------------------------
# PAID endpoints (x402 required)
# ---------------------------------------------------------------------------


@router.get("/image/{image_id}", tags=["data"], summary="Proxy image download")
async def get_image_redirect(
    image_id: str,
    request: Request,
):
    """DEPRECATED — Images are now bundled with metadata purchases.

    Use GET /agent/artifact/{id} for Human_Standard (metadata + image)
    or GET /agent/artifact/{id}/oracle for Hybrid_Premium (metadata + image).
    """
    raise HTTPException(status_code=410, detail={
        "error": "Standalone image download has been removed.",
        "message": "Images are now included with every metadata purchase — no separate image price.",
        "use_instead": {
            "Human_Standard": f"GET /agent/artifact/{image_id} — metadata + image (FREE 5/day, then ${_current_human_standard_price():.2f})",
            "Hybrid_Premium": f"GET /agent/artifact/{image_id}/oracle — metadata + image (${_current_hybrid_premium_price():.2f} USDC)",
        },
        "guide": "/agent/guide",
    })


@router.get("/artifact/{artifact_id}/image", tags=["data"], summary="Get image for artifact")
async def get_artifact_image(
    artifact_id: str,
    request: Request,
):
    """Stream the artifact image from private GCS storage.

    This proxy keeps the GCS bucket private — bots cannot bypass the data portal
    to download images directly. The image is subject to the same rate limits
    as the artifact metadata endpoint (5 free/day per client).
    """
    from fastapi.responses import StreamingResponse
    from google.cloud import storage as gcs

    # Rate limit (same pool as artifact metadata — image is part of the package)
    client_id = get_client_fingerprint(request)
    # Use a separate counter so viewing images doesn't eat metadata quota,
    # but still limit abuse (20/day is generous for legitimate use)
    is_allowed = await rate_limiter.check_async(f"image_proxy:{client_id}", 20, 86400)
    if not is_allowed:
        raise HTTPException(status_code=429, detail="Image download rate limit exceeded (20/day)")

    client = gcs.Client()
    bucket_obj = client.bucket(DATA_BUCKET)

    blob, content_type = _find_gcs_image_blob(bucket_obj, artifact_id)
    if blob is None:
        # Try museum public API as redirect instead
        numeric_id = artifact_id
        museum = None
        for mp in ["met", "nga", "chicago", "cleveland", "rijksmuseum", "smithsonian", "paris"]:
            if artifact_id.startswith(f"{mp}_"):
                museum = mp
                numeric_id = artifact_id[len(mp) + 1:]
                break

        if museum:
            import httpx
            api_url_template = _MUSEUM_API_URLS.get(museum)
            if api_url_template:
                try:
                    api_url = api_url_template.format(id=numeric_id)
                    resp = httpx.get(api_url, timeout=5.0)
                    if resp.status_code == 200:
                        data = resp.json()
                        redirect_url = None
                        if museum == "met":
                            redirect_url = data.get("primaryImage", "")
                        elif museum == "chicago":
                            image_id = data.get("data", {}).get("image_id", "")
                            if image_id:
                                redirect_url = f"https://www.artic.edu/iiif/2/{image_id}/full/843,/0/default.jpg"
                        elif museum == "cleveland":
                            redirect_url = data.get("data", {}).get("images", {}).get("web", {}).get("url", "")
                        if redirect_url:
                            from fastapi.responses import RedirectResponse
                            return RedirectResponse(url=redirect_url, status_code=302)
                except Exception:
                    pass

        raise HTTPException(status_code=404, detail=f"Image not found for artifact: {artifact_id}")

    # Stream the blob bytes through the proxy
    image_bytes = blob.download_as_bytes()
    return StreamingResponse(
        iter([image_bytes]),
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400",
            "Content-Disposition": f'inline; filename="{artifact_id}.jpg"',
        },
    )


@router.post("/batch", tags=["data"], summary="Bulk download metadata + images ($0.05/record, min 100)")
async def batch_download(
    batch: BatchRequest,
    request: Request,
    x_payment: Optional[str] = Header(None, alias="X-PAYMENT"),
):
    """Bulk download. Requires x402 payment ($0.05/image, min 100)."""
    price_info = calculate_price("agent_batch", batch.quantity)
    total_cost = price_info["total"]

    payment = await _require_payment(
        x_payment,
        total_cost,
        f"Batch: {batch.quantity} images from {batch.dataset_id}",
    )

    bucket = request.state.data_bucket

    from routes.catalog import DATASETS

    ds = DATASETS.get(batch.dataset_id)
    prefix = ds.gcs_prefix if ds else f"{batch.dataset_id}/"

    downloads = []
    for i in range(batch.offset, batch.offset + batch.quantity):
        blob_path = f"{prefix}{i:06d}.jpg"
        try:
            url = generate_signed_url(bucket, blob_path, expiration_hours=2)
            downloads.append({"index": i, "download_url": url})
        except Exception:
            continue

    await log_transaction(
        request, endpoint="batch", amount_usd=total_cost,
        tx_hash=payment.tx_hash,
        extra={
            "dataset_id": batch.dataset_id,
            "quantity": batch.quantity,
            "quantity_returned": len(downloads),
        },
    )
    return {
        "dataset_id": batch.dataset_id,
        "quantity_requested": batch.quantity,
        "quantity_returned": len(downloads),
        "offset": batch.offset,
        "downloads": downloads,
        "expires_in": "2 hours",
        "payment": {
            "amount": total_cost,
            "currency": "USDC",
            "tx_hash": payment.tx_hash,
        },
        "license": "Intelligence Aeternum Data License v1.0",
        **_genesis_info(),
    }


@router.get("/query", tags=["discovery"], summary="Natural language query across datasets")
async def query_images(
    request: Request,
    query: str = Query(description="Free-text search"),
    source: Optional[str] = Query(default=None, description="Museum source filter"),
    classification: Optional[str] = Query(default=None, description="Art classification filter"),
    limit: int = Query(default=20, le=100),
    x_payment: Optional[str] = Header(None, alias="X-PAYMENT"),
):
    """Search images by metadata filters. Requires x402 payment ($0.02 USDC)."""
    payment = await _require_payment(x_payment, 0.02, "Metadata search query")

    bucket = request.state.data_bucket

    from google.cloud import storage as gcs

    client = gcs.Client()
    bucket_obj = client.bucket(bucket)
    manifest_blob = bucket_obj.blob("02-training-optimized/manifest.jsonl")

    results = []
    try:
        if manifest_blob.exists():
            manifest_text = manifest_blob.download_as_text()
            query_lower = query.lower()

            for line in manifest_text.split("\n"):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if source and entry.get("source") != source:
                    continue
                if classification and (
                    classification.lower() not in entry.get("classification", "").lower()
                ):
                    continue

                searchable = " ".join([
                    entry.get("title", ""),
                    entry.get("artist", ""),
                    entry.get("classification", ""),
                    entry.get("medium", ""),
                ]).lower()

                if query_lower in searchable:
                    results.append({
                        "id": entry.get("id", ""),
                        "source": entry.get("source"),
                        "title": entry.get("title"),
                        "artist": entry.get("artist"),
                        "classification": entry.get("classification"),
                        "date": entry.get("date"),
                    })

                if len(results) >= limit:
                    break
    except Exception as e:
        logger.warning("Manifest search failed: %s", e)

    await log_transaction(
        request, endpoint="query", amount_usd=0.02,
        tx_hash=payment.tx_hash,
        extra={"query": query, "total_results": len(results)},
    )
    return {
        "query": query,
        "filters": {"source": source, "classification": classification},
        "total_results": len(results),
        "results": results,
        "payment": {
            "amount": 0.02,
            "currency": "USDC",
            "tx_hash": payment.tx_hash,
        },
        "purchase_endpoint": "/agent/artifact/{id} (Human_Standard) or /agent/artifact/{id}/oracle (Hybrid_Premium)",
    }


# ---------------------------------------------------------------------------
# POST /agent/enrich — Agent-submitted image enrichment
# ---------------------------------------------------------------------------

ENRICH_TIER_PRICES = {
    "oracle_only": 0.20,
    "oracle_plus_infuse": 0.30,
    "full_certified": 0.50,
}

ENRICH_TIER_STEPS = {
    "oracle_only": ["nova_oracle"],
    "oracle_plus_infuse": ["nova_oracle", "atlas_infuse", "hash_register"],
    "full_certified": ["nova_oracle", "atlas_infuse", "hash_register", "c2pa_sign"],
}


@router.post("/enrich", tags=["enrichment"], summary="Submit your image for Golden Codex enrichment (from $0.16 USDC)")
async def enrich_agent_image(
    enrich: EnrichRequest,
    request: Request,
    x_payment: Optional[str] = Header(None, alias="X-PAYMENT"),
):
    """PAID — Submit YOUR image for Golden Codex enrichment.

    Tiers (Genesis Epoch prices shown):
      - oracle_only: $0.16 — Returns Golden Codex JSON (111-field NEST analysis)
      - oracle_plus_infuse: $0.24 — JSON + XMP-infused image + GCX hash registry
      - full_certified: $0.40 — JSON + infused + C2PA signed + hash registered

    Custom fields are merged with Oracle analysis. Your values take priority
    for factual fields (title, artist, copyright). The Oracle adds analytical
    fields (composition, emotional_resonance, technique_analysis).
    """
    if enrich.tier not in ENRICH_TIER_PRICES:
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"Invalid tier: {enrich.tier}",
                "valid_tiers": list(ENRICH_TIER_PRICES.keys()),
                "guide": "/agent/guide",
            },
        )

    base_price = ENRICH_TIER_PRICES[enrich.tier]
    price = round(base_price * GENESIS_DISCOUNT, 2) if is_genesis_epoch() else base_price

    payment = await _require_payment(
        x_payment, price,
        f"Agent enrichment ({enrich.tier}): {enrich.image_url[:80]}",
    )

    # Generate job ID
    job_id = str(uuid.uuid4())

    # Store job in Firestore
    db = request.state.db
    job_doc = {
        "job_id": job_id,
        "image_url": enrich.image_url,
        "tier": enrich.tier,
        "steps": ENRICH_TIER_STEPS[enrich.tier],
        "custom_fields": enrich.custom_fields or {},
        "callback_url": enrich.callback_url,
        "status": "queued",
        "created_at": datetime.now(timezone.utc),
        "payment": {
            "amount": price,
            "currency": "USDC",
            "tx_hash": payment.tx_hash,
        },
        "results": {},
    }

    try:
        await db.collection("agent_enrichment_jobs").document(job_id).set(job_doc)
    except Exception as exc:
        logger.error("Failed to create enrichment job: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create enrichment job")

    # Fire background enrichment pipeline
    import asyncio
    asyncio.create_task(_run_agent_enrichment(db, job_id, enrich, price))

    # Track volume
    wallet = request.headers.get("X-WALLET", "")
    if wallet:
        await volume_tracker.record_purchase(wallet, 1, price, f"enrich_{enrich.tier}")

    await log_transaction(
        request, endpoint=f"enrich_{enrich.tier}",
        amount_usd=price, tx_hash=payment.tx_hash,
        extra={"job_id": job_id, "tier": enrich.tier},
    )

    return {
        "job_id": job_id,
        "status": "queued",
        "tier": enrich.tier,
        "steps": ENRICH_TIER_STEPS[enrich.tier],
        "estimated_time": "30-120 seconds",
        "poll_url": f"/agent/enrich/{job_id}",
        "payment": {
            "amount": price,
            "currency": "USDC",
            "tx_hash": payment.tx_hash,
        },
        "custom_fields_submitted": list((enrich.custom_fields or {}).keys()),
        **_genesis_info(),
    }


@router.get("/enrich/{job_id}", tags=["enrichment"], summary="Check enrichment job status")
async def get_enrich_status(
    job_id: str,
    request: Request,
):
    """FREE — Poll the status of an agent enrichment job."""
    db = request.state.db
    try:
        doc = await db.collection("agent_enrichment_jobs").document(job_id).get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

        data = doc.to_dict()
        response = {
            "job_id": job_id,
            "status": data.get("status", "unknown"),
            "tier": data.get("tier"),
            "steps": data.get("steps", []),
            "created_at": str(data.get("created_at", "")),
        }

        if data.get("status") == "completed":
            response["results"] = data.get("results", {})
            response["completed_at"] = str(data.get("completed_at", ""))

        if data.get("status") == "failed":
            response["error"] = data.get("error", "Unknown error")

        return response
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get enrichment job %s: %s", job_id, exc)
        raise HTTPException(status_code=500, detail="Failed to retrieve job status")


async def _run_agent_enrichment(db, job_id: str, enrich: EnrichRequest, price: float):
    """Background task: run the enrichment pipeline for an agent-submitted image."""
    import httpx

    try:
        await db.collection("agent_enrichment_jobs").document(job_id).update(
            {"status": "in_progress"}
        )

        results = {}

        # Step 1: Nova Oracle enrichment
        if "nova_oracle" in ENRICH_TIER_STEPS[enrich.tier]:
            try:
                nova_payload = {
                    "image_url": enrich.image_url,
                    "user_id": "agent_enrichment",
                    "image_id": job_id,
                    "parameters": {"analysis_depth": "full"},
                }
                # Pass custom fields as metadata for Nova to merge
                if enrich.custom_fields:
                    nova_payload["custom_metadata"] = enrich.custom_fields

                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(f"{NOVA_AGENT_URL}/enrich", json=nova_payload)
                    if resp.status_code == 200:
                        nova_result = resp.json()
                        golden_codex = nova_result.get("golden_codex", {})

                        # Merge custom fields (submitter values take priority for factual fields)
                        if enrich.custom_fields:
                            field_mapping = {
                                "title": "title",
                                "artist": ("provenance_and_lineage", "artist_information"),
                                "copyright_holder": ("ownership_and_rights", "copyright_holder"),
                                "creation_year": ("provenance_and_lineage", "creation_date"),
                                "medium": ("technical_details", "medium_and_technique"),
                            }
                            for field, value in enrich.custom_fields.items():
                                if field in field_mapping:
                                    path = field_mapping[field]
                                    if isinstance(path, tuple):
                                        section, key = path
                                        if section not in golden_codex:
                                            golden_codex[section] = {}
                                        golden_codex[section][key] = value
                                        golden_codex[section][f"_{key}_source"] = "submitter"
                                    else:
                                        golden_codex[path] = value
                                        golden_codex[f"_{path}_source"] = "submitter"

                        results["golden_codex"] = golden_codex
                        results["nova_status"] = "success"
                    else:
                        results["nova_status"] = f"error: HTTP {resp.status_code}"
                        raise Exception(f"Nova returned {resp.status_code}")
            except Exception as exc:
                logger.error("Nova enrichment failed for job %s: %s", job_id, exc)
                results["nova_status"] = f"error: {exc}"
                await db.collection("agent_enrichment_jobs").document(job_id).update({
                    "status": "failed",
                    "error": f"Nova enrichment failed: {exc}",
                    "results": results,
                })
                return

        # Step 2: Atlas infusion (XMP metadata embedding + hash registration)
        if "atlas_infuse" in ENRICH_TIER_STEPS[enrich.tier]:
            try:
                atlas_payload = {
                    "image_url": enrich.image_url,
                    "user_id": "agent_enrichment",
                    "golden_codex": results.get("golden_codex", {}),
                    "metadata_mode": "full_gcx",
                }
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(f"{ATLAS_AGENT_URL}/infuse", json=atlas_payload)
                    if resp.status_code == 200:
                        atlas_result = resp.json()
                        results["infusion"] = {
                            "soulmark": atlas_result.get("soulmark"),
                            "perceptual_hash": atlas_result.get("perceptual_hash"),
                            "final_url": atlas_result.get("final_url"),
                            "artifact_id": atlas_result.get("artifact_id"),
                        }
                        if atlas_result.get("arweave"):
                            results["arweave"] = atlas_result["arweave"]
                        results["atlas_status"] = "success"
                    else:
                        results["atlas_status"] = f"error: HTTP {resp.status_code}"
            except Exception as exc:
                logger.warning("Atlas infusion failed for job %s: %s", job_id, exc)
                results["atlas_status"] = f"error: {exc}"

        # Step 3: Hash registration (included in atlas_infuse, but explicit)
        if "hash_register" in ENRICH_TIER_STEPS[enrich.tier]:
            results["hash_registered"] = results.get("atlas_status") == "success"

        # Step 4: C2PA signing
        if "c2pa_sign" in ENRICH_TIER_STEPS[enrich.tier]:
            # C2PA signing happens within Atlas infuse for full_gcx mode
            results["c2pa_signed"] = results.get("atlas_status") == "success"

        # Mark complete
        await db.collection("agent_enrichment_jobs").document(job_id).update({
            "status": "completed",
            "completed_at": datetime.now(timezone.utc),
            "results": results,
        })

        # Webhook callback
        if enrich.callback_url:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    await client.post(enrich.callback_url, json={
                        "job_id": job_id,
                        "status": "completed",
                        "tier": enrich.tier,
                        "results": results,
                    })
            except Exception as exc:
                logger.warning("Callback failed for job %s: %s", job_id, exc)

    except Exception as exc:
        logger.error("Enrichment pipeline failed for job %s: %s", job_id, exc)
        try:
            await db.collection("agent_enrichment_jobs").document(job_id).update({
                "status": "failed",
                "error": str(exc),
            })
        except Exception:
            pass
