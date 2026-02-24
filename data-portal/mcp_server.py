#!/usr/bin/env python3
"""
Intelligence Aeternum MCP Server — V2.1 (PROPOSED_PRICING_V2 aligned)
======================================================================
Model Context Protocol server for AI agent discovery and interaction
with the Alexandria Aeternum dataset marketplace.

Run standalone:  python mcp_server.py
Transport: stdio (standard MCP transport)

Tools provided:
  FREE:
  - search_alexandria      -- Search 56,500+ museum artworks
  - get_curated_metadata   -- Full human-curated metadata (5 free/day)
  - get_oracle_metadata    -- Hybrid_Premium VLM deep analysis ($0.20, $0.16 Genesis)
  - get_compliance_manifest -- AB 2013 + EU AI Act Article 53 manifests
  - search_datasets        -- Browse the 7 museum dataset catalog
  - preview_dataset        -- Sample images from a dataset
  - get_pricing            -- Calculate pricing for a purchase
  - list_enrichment_tiers  -- List enrichment tiers with pricing
  - get_agent_guide        -- Complete API workflow documentation
  - enrich_agent_image     -- Submit YOUR image for Golden Codex enrichment

  PAID (x402 USDC on Base L2):
  - purchase_dataset       -- Initiate a dataset purchase
"""

from __future__ import annotations

import json
import os
from typing import Optional

from fastmcp import FastMCP

from pricing import (
    PRICING_TIERS,
    UserType,
    calculate_price,
    get_tier_for_quantity,
    is_genesis_epoch,
    genesis_days_remaining,
    GENESIS_DISCOUNT,
)
from compliance import generate_ab2013_manifest, generate_eu_ai_act_article53_manifest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_API_URL = os.environ.get(
    "DATA_PORTAL_URL",
    "https://data-portal-172867820131.us-west1.run.app",
)
BASE_WALLET = os.environ.get(
    "BASE_WALLET_ADDRESS",
    "0xFE141943a93c184606F3060103D975662327063B",
)


def _oracle_price() -> str:
    return f"{round(0.20 * GENESIS_DISCOUNT, 2):.2f}" if is_genesis_epoch() else "0.20"

def _genesis_note() -> str:
    if is_genesis_epoch():
        return f" (Genesis Epoch: 20% off, {genesis_days_remaining()} days remaining)"
    return ""


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "intelligence-aeternum",
    instructions=(
        "Intelligence Aeternum — Compliance-ready AI training datasets from "
        "2M+ museum artworks across 7 world-class institutions. "
        "111-field Golden Codex provenance schema. "
        "The antidote to model collapse and synthetic data degradation.\n\n"
        "Every purchase is a PACKAGE: metadata + image together. No standalone image price.\n\n"
        "FREE TOOLS: search_alexandria, get_human_standard_metadata (5/day), "
        "get_compliance_manifest, search_datasets, preview_dataset, get_pricing, "
        "get_agent_guide, list_enrichment_tiers.\n\n"
        f"PAID TOOLS (x402 USDC on Base L2): get_hybrid_premium_metadata (${_oracle_price()}), "
        "enrich_agent_image (from $0.20), purchase_dataset.\n\n"
        "Payment protocol: When a paid endpoint returns HTTP 402, extract the "
        "x402 payment details (amount, currency, network, recipient), execute "
        "a USDC transfer on Base L2, then re-call with X-PAYMENT header.\n\n"
        "Compliance: Every transaction includes auto-generated AB 2013 (California) "
        "and EU AI Act Article 53 provenance manifests at no additional cost.\n\n"
        "Two-tier architecture:\n"
        f"  - Human_Standard (FREE, 5/day): 500-1,200 tokens + image, zero synthetic content\n"
        f"  - Hybrid_Premium (${_oracle_price()}): 2,000-6,000 tokens + image, VLM deep visual analysis\n\n"
        "NEW: Submit YOUR images for Golden Codex enrichment via enrich_agent_image.\n"
        "Custom fields (title, artist, copyright) are merged with Hybrid_Premium analysis.\n\n"
        "Volume discounts (Hybrid_Premium): 100+ records 25% off, 500+ 37% off, 2000+ 50% off "
        "(automatic per wallet).\n\n"
        "Research: The Density Imperative (DOI: 10.5281/zenodo.18667735) shows "
        "dense metadata improves VLM visual perception by +25.5% while sparse "
        "captions destroy model capabilities by -54.4%.\n\n"
        "Enterprise: Starting at $8,000 for full dataset access with compliance manifests. "
        "Contact enterprise@iaeternum.ai."
    ),
)

# ---------------------------------------------------------------------------
# Dataset catalog
# ---------------------------------------------------------------------------

CATALOG = {
    "met-museum": {
        "id": "met-museum",
        "name": "Metropolitan Museum of Art - Open Access",
        "description": "375,000 CC0 artworks spanning 5,000 years. Golden Codex enriched.",
        "image_count": 375_000,
        "institution": "The Metropolitan Museum of Art",
        "license": "CC0 1.0 (images), Commercial (enrichment metadata)",
    },
    "smithsonian": {
        "id": "smithsonian",
        "name": "Smithsonian Open Access",
        "description": "185,000 artworks from 21 Smithsonian museums. American art focus.",
        "image_count": 185_000,
        "institution": "Smithsonian Institution",
        "license": "CC0 1.0 (images), Commercial (enrichment metadata)",
    },
    "nga": {
        "id": "nga",
        "name": "National Gallery of Art - Open Data",
        "description": "130,000 European and American masterworks.",
        "image_count": 130_000,
        "institution": "National Gallery of Art",
        "license": "CC0 1.0 (images), Commercial (enrichment metadata)",
    },
    "rijksmuseum": {
        "id": "rijksmuseum",
        "name": "Rijksmuseum - Rijksstudio",
        "description": "709,000 objects. Crown jewel of Dutch Golden Age art.",
        "image_count": 709_000,
        "institution": "Rijksmuseum",
        "license": "CC0 1.0 (images), Commercial (enrichment metadata)",
    },
    "chicago": {
        "id": "chicago",
        "name": "Art Institute of Chicago - Open Access",
        "description": "120,000 works. Strong Impressionism, American, and Asian art.",
        "image_count": 120_000,
        "institution": "Art Institute of Chicago",
        "license": "CC0 1.0 (images), Commercial (enrichment metadata)",
    },
    "cleveland": {
        "id": "cleveland",
        "name": "Cleveland Museum of Art - Open Access",
        "description": "61,000 works from ancient Egypt to contemporary art.",
        "image_count": 61_000,
        "institution": "Cleveland Museum of Art",
        "license": "CC0 1.0 (images), Commercial (enrichment metadata)",
    },
    "paris-elite": {
        "id": "paris-elite",
        "name": "Paris Elite Collection (Curated)",
        "description": "45,000 curated masterworks from Louvre, Orsay, Rodin, and more.",
        "image_count": 45_000,
        "institution": "Louvre, Orsay, Rodin, and others",
        "license": "CC0 1.0 / Open License (images), Commercial (enrichment metadata)",
    },
}


# ---------------------------------------------------------------------------
# FREE Tools — Discovery + Curated Tier
# ---------------------------------------------------------------------------


@mcp.tool()
def search_alexandria(
    query: str,
    museum: str = "",
    limit: int = 20,
) -> str:
    """FREE — Search 2M+ museum artworks in the Alexandria Aeternum catalog.

    Searches the Firestore manifest (primary) with Vertex AI fallback.
    Returns artifact IDs, titles, artists, dates, and classification.
    Use get_curated_metadata() for Human_Standard metadata + image (5 free/day).
    Use get_oracle_metadata() for Hybrid_Premium VLM deep analysis + image.

    Args:
        query: Free-text search (e.g., "impressionist landscape", "Rembrandt portrait").
        museum: Filter by museum: met, nga, chicago, cleveland, rijksmuseum, smithsonian, paris.
        limit: Max results (default 20, max 100).
    """
    return json.dumps({
        "action": "GET",
        "url": f"{BASE_API_URL}/agent/search",
        "params": {"q": query, "museum": museum or None, "limit": min(limit, 100)},
        "note": "This is a FREE endpoint. No payment required. Searches 2M+ artworks.",
        "next_steps": {
            "Human_Standard": f"{BASE_API_URL}/agent/artifact/{{artifact_id}} (FREE 5/day, metadata + image)",
            "Hybrid_Premium": f"{BASE_API_URL}/agent/artifact/{{artifact_id}}/oracle (${_oracle_price()} x402, metadata + image)",
            "on_demand_delivery": f"{BASE_API_URL}/deliver/order (fetch + enrich + deliver specific artifacts)",
            "enrich_your_image": f"{BASE_API_URL}/agent/enrich (from ${_oracle_price()} x402)",
            "guide": f"{BASE_API_URL}/agent/guide",
        },
    }, indent=2)


@mcp.tool()
def get_curated_metadata(artifact_id: str) -> str:
    """FREE (5/day) — Get Human_Standard metadata + image for an artifact.

    Returns 500-1,200 tokens of 100% human-sourced metadata PLUS a signed
    image download URL. Every response is a package: metadata + image together.
    Zero synthetic content. Sources: Museum API + Wikipedia + Wikidata + Getty ULAN.

    After 5 free requests per day, requires $0.05 USDC via x402.

    Args:
        artifact_id: The artifact ID (e.g., "met_10049", "nga_1234").
    """
    return json.dumps({
        "action": "GET",
        "url": f"{BASE_API_URL}/agent/artifact/{artifact_id}",
        "note": "FREE — 5 requests per day. After quota, $0.05 USDC via x402. Includes metadata + image URL.",
        "data_tier": "Human_Standard",
        "schema_version": "1.0.0-curated",
        "token_range": "500-1,200",
        "synthetic_content": "NONE",
        "image_included": True,
        "sections": [
            "_identifiers", "artistic_statement", "contextual_graph",
            "symbolism_and_iconography", "cultural_and_artistic_context",
            "provenance_and_lineage", "technical_details",
            "ownership_and_rights", "archival", "museum_extended",
            "authority_references", "data_provenance",
        ],
        "upgrade": {
            "tool": "get_oracle_metadata",
            "price": f"${_oracle_price()} USDC{_genesis_note()}",
            "adds": "VLM deep visual analysis: composition, color palette, emotional journey, symbolism (2,000-6,000 tokens + image)",
            "data_tier": "Hybrid_Premium",
        },
    }, indent=2)


@mcp.tool()
def get_oracle_metadata(artifact_id: str) -> str:
    """PAID — Get Hybrid_Premium metadata + image with VLM deep visual analysis.

    Returns 2,000-6,000 tokens including everything in Human_Standard PLUS:
    visual_analysis, emotional_and_thematic_journey, deep symbolism,
    and archetypal analysis. Image download URL included in response.

    Args:
        artifact_id: The artifact ID (e.g., "met_10049").
    """
    price = _oracle_price()
    return json.dumps({
        "action": "GET",
        "url": f"{BASE_API_URL}/agent/artifact/{artifact_id}/oracle",
        "headers": {"X-PAYMENT": "<x402 payment proof>"},
        "data_tier": "Hybrid_Premium",
        "image_included": True,
        "payment": {
            "amount": price,
            "currency": "USDC",
            "network": "base",
            "facilitator": "https://x402.org/facilitator",
            "recipient": BASE_WALLET,
        },
        "x402_flow": [
            "1. Call endpoint WITHOUT X-PAYMENT header",
            "2. Receive HTTP 402 with payment details",
            "3. Execute USDC transfer on Base L2 to recipient address",
            "4. Re-call endpoint WITH X-PAYMENT header containing tx proof",
            "5. Receive Hybrid_Premium metadata + image download URL",
        ],
        "schema_version": "1.0.0",
        "token_range": "2,000-6,000",
        "volume_discounts": "Hybrid_Premium: 100+ records 25% off, 500+ 37% off, 2000+ 50% off (automatic per wallet)",
    }, indent=2)


@mcp.tool()
def get_compliance_manifest(
    dataset_id: str,
    regulation: str = "all",
) -> str:
    """FREE — Get auto-generated regulatory compliance manifests for a dataset.

    Returns AB 2013 (California) and/or EU AI Act Article 53 provenance manifests.
    "Buy from us, get instant California + EU AI compliance."

    Args:
        dataset_id: The dataset ID (e.g., "met-museum", "rijksmuseum").
        regulation: Which regulation: "ab2013", "eu_ai_act", or "all" (default).
    """
    if dataset_id not in CATALOG and dataset_id not in (
        "met", "nga", "chicago", "cleveland", "rijksmuseum", "smithsonian", "paris-elite",
    ):
        return json.dumps({"error": f"Dataset '{dataset_id}' not found", "available": list(CATALOG.keys())})

    order_stub = {
        "order_id": f"compliance-preview-{dataset_id}",
        "dataset_id": dataset_id,
        "quantity": 0, "total_price": 0,
        "payment_method": "preview", "pricing_tier": "preview",
    }

    result = {"dataset_id": dataset_id, "regulation": regulation}
    if regulation in ("ab2013", "all"):
        result["ab_2013"] = generate_ab2013_manifest(order_stub, dataset_id)["json"]
    if regulation in ("eu_ai_act", "all"):
        result["eu_ai_act_article_53"] = generate_eu_ai_act_article53_manifest(order_stub, dataset_id)["json"]

    result["note"] = "Preview manifests. Purchase-specific manifests include exact order details."
    result["api_endpoint"] = f"{BASE_API_URL}/agent/compliance/{dataset_id}"
    return json.dumps(result, indent=2)


@mcp.tool()
def get_agent_guide() -> str:
    """FREE — Get complete API workflow documentation for agents.

    Returns the full agent guide with endpoints, pricing, custom fields schema,
    volume discounts, and recommended workflow.
    """
    return json.dumps({
        "action": "GET",
        "url": f"{BASE_API_URL}/agent/guide",
        "note": "Returns complete JSON documentation for the Alexandria Aeternum API.",
        "quick_start": [
            f"1. GET /agent/search?q=landscape — FREE search",
            f"2. GET /agent/artifact/{{id}} — Human_Standard metadata + image (FREE 5/day)",
            f"3. GET /agent/artifact/{{id}}/oracle — Hybrid_Premium metadata + image (${_oracle_price()} USDC)",
            f"4. POST /agent/enrich — from ${_oracle_price()} enrich YOUR image",
        ],
    }, indent=2)


# ---------------------------------------------------------------------------
# Catalog Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def search_datasets(
    query: str = "",
    domain: str = "",
    min_images: int = 0,
) -> str:
    """FREE — Search the Alexandria Aeternum dataset catalog (7 museums, 1.6M+ images)."""
    results = []
    query_lower = query.lower()
    for ds_id, ds in CATALOG.items():
        if min_images and ds["image_count"] < min_images:
            continue
        if domain and domain.lower() not in ds_id.lower() and domain.lower() not in ds["institution"].lower():
            continue
        if query_lower:
            searchable = f"{ds['name']} {ds['description']} {ds['institution']}".lower()
            if query_lower not in searchable:
                continue
        results.append(ds)

    return json.dumps({
        "total": len(results),
        "datasets": results,
        "api": f"{BASE_API_URL}/catalog/datasets",
        "compliance": f"{BASE_API_URL}/agent/compliance/{{dataset_id}}",
        "contact": "data@iaeternum.ai",
    }, indent=2)


@mcp.tool()
def preview_dataset(dataset_id: str, limit: int = 5) -> str:
    """FREE (10/day) — Preview sample images from a dataset."""
    if dataset_id not in CATALOG:
        return json.dumps({"error": f"Dataset '{dataset_id}' not found", "available": list(CATALOG.keys())})
    ds = CATALOG[dataset_id]
    return json.dumps({
        "action": "GET",
        "url": f"{BASE_API_URL}/catalog/datasets/{dataset_id}/preview",
        "params": {"limit": min(limit, 5)},
        "note": "FREE — Rate limited to 10 per day.",
        "dataset_name": ds["name"],
        "total_available": ds["image_count"],
    }, indent=2)


@mcp.tool()
def get_pricing(dataset_id: str, quantity: int = 1) -> str:
    """FREE — Get pricing information for a dataset purchase.

    Args:
        dataset_id: The dataset ID.
        quantity: Number of images to price.
    """
    if dataset_id not in CATALOG:
        return json.dumps({"error": f"Dataset '{dataset_id}' not found"})

    ds = CATALOG[dataset_id]
    tier_prices = {}
    for tier_name, tier in PRICING_TIERS.items():
        try:
            if quantity >= tier.min_quantity:
                tier_prices[tier_name] = calculate_price(tier_name, quantity)
        except ValueError:
            pass

    recommendations = {}
    for ut in UserType:
        try:
            rec_tier = get_tier_for_quantity(quantity, ut)
            rec_price = calculate_price(rec_tier, max(quantity, PRICING_TIERS[rec_tier].min_quantity))
            recommendations[ut.value] = {"tier": rec_tier, "price": rec_price}
        except ValueError:
            pass

    return json.dumps({
        "dataset_id": dataset_id,
        "dataset_name": ds["name"],
        "quantity": quantity,
        "available_tiers": tier_prices,
        "recommendations": recommendations,
        "volume_discounts": {
            "Hybrid_Premium_100+": "25% off ($0.15/record)",
            "Hybrid_Premium_500+": "37% off ($0.125/record)",
            "Hybrid_Premium_2000+": "50% off ($0.10/record, loyalty floor)",
        },
        "payment_methods": {
            "x402": "USDC micropayments on Base L2 (AI agents — preferred)",
            "stripe": "Credit card / ACH (human buyers)",
        },
        "enterprise": {
            "curated": "$8,000",
            "oracle": "$45,000",
            "certified": "$85,000",
            "full_pipeline": "$150,000",
            "foundation_model": "$250,000+",
            "contact": "enterprise@iaeternum.ai",
        },
    }, indent=2)


@mcp.tool()
def purchase_dataset(
    dataset_id: str,
    quantity: int,
    payment_method: str = "x402",
) -> str:
    """Initiate a dataset purchase (x402 or Stripe)."""
    if dataset_id not in CATALOG:
        return json.dumps({"error": f"Dataset '{dataset_id}' not found"})
    if payment_method not in ("stripe", "x402"):
        return json.dumps({"error": "payment_method must be 'stripe' or 'x402'"})

    if payment_method == "x402":
        tier = "agent_batch" if quantity >= 100 else "agent_single"
    else:
        tier = get_tier_for_quantity(quantity, UserType.CORPORATE)

    try:
        price = calculate_price(tier, max(quantity, PRICING_TIERS.get(tier, PRICING_TIERS["curated_agent"]).min_quantity))
    except ValueError as e:
        return json.dumps({"error": str(e)})

    order_preview = {
        "order_id": "preview", "dataset_id": dataset_id,
        "quantity": quantity, "total_price": price["total"],
        "payment_method": payment_method, "pricing_tier": tier,
    }
    manifest = generate_ab2013_manifest(order_preview, dataset_id)

    instructions = {
        "action": "POST",
        "url": f"{BASE_API_URL}/orders",
        "body": {"dataset_id": dataset_id, "quantity": quantity, "payment_method": payment_method, "pricing_tier": tier},
        "pricing": price,
        "compliance_manifest_preview": manifest["json"],
    }

    if payment_method == "x402":
        instructions["x402_info"] = {
            "currency": "USDC", "network": "Base L2",
            "facilitator": "https://x402.org/facilitator",
            "recipient": BASE_WALLET,
        }
    else:
        instructions["stripe_info"] = {"note": "Include email in request body for checkout URL."}
        instructions["body"]["email"] = "<your_email>"

    return json.dumps(instructions, indent=2)


# ---------------------------------------------------------------------------
# Enrichment Tools
# ---------------------------------------------------------------------------

ENRICHMENT_TIERS = {
    "oracle_only": {
        "name": "Hybrid_Premium Reading",
        "price_usdc": 0.20,
        "launch_price_usdc": round(0.20 * GENESIS_DISCOUNT, 2),
        "description": "111-field NEST Hybrid_Premium reading. Returns Golden Codex JSON.",
        "output": ["golden_codex_json"],
    },
    "oracle_plus_infuse": {
        "name": "Hybrid_Premium + Infusion + Registry",
        "price_usdc": 0.30,
        "launch_price_usdc": round(0.30 * GENESIS_DISCOUNT, 2),
        "description": "Hybrid_Premium reading + XMP metadata infusion + GCX hash registry entry.",
        "output": ["golden_codex_json", "infused_image", "soulmark", "phash", "registry_entry"],
    },
    "full_certified": {
        "name": "Full Certified Pipeline",
        "price_usdc": 0.50,
        "launch_price_usdc": round(0.50 * GENESIS_DISCOUNT, 2),
        "description": "Hybrid_Premium + infusion + C2PA Content Credentials + hash registry.",
        "output": ["golden_codex_json", "infused_image", "c2pa_manifest", "soulmark", "phash", "registry_entry"],
    },
}

# Legacy enrichment tiers (for existing /enrich endpoint)
LEGACY_ENRICHMENT_TIERS = {
    "nest_only": {
        "name": "NEST Oracle Reading (legacy)",
        "price_usdc": 1.00,
        "description": "Full 111-field analysis via Gemini.",
        "output": ["golden_codex_json"],
    },
    "certified": {
        "name": "NEST + C2PA + Hash Registry (legacy)",
        "price_usdc": 2.00,
        "description": "Full Oracle plus C2PA signing, soulmark, and registry.",
        "output": ["golden_codex_json", "c2pa_manifest", "soulmark", "phash", "registry_entry"],
    },
    "full_pipeline": {
        "name": "Full Golden Codex Pipeline (legacy)",
        "price_usdc": 5.00,
        "description": "Complete enrichment + Arweave + NFT minting.",
        "output": ["golden_codex_json", "c2pa_manifest", "soulmark", "phash", "registry_entry", "arweave_tx", "nft_token_id"],
    },
}


@mcp.tool()
def list_enrichment_tiers() -> str:
    """FREE — List available enrichment tiers for agent-submitted images.

    NEW: Submit YOUR images via POST /agent/enrich with custom fields.
    Your metadata is merged with Oracle analysis. Submitter values take priority.
    """
    genesis = is_genesis_epoch()
    return json.dumps({
        "agent_enrichment_tiers": {
            k: {**v, "current_price": v["launch_price_usdc"] if genesis else v["price_usdc"]}
            for k, v in ENRICHMENT_TIERS.items()
        },
        "custom_fields": {
            "description": "Submit custom_fields to merge with Oracle analysis. Your values take priority.",
            "accepted": ["title", "artist", "copyright_holder", "creation_year", "medium",
                         "dimensions", "commercial_use", "collection_name", "description", "tags"],
        },
        "genesis_epoch": genesis,
        "genesis_days_remaining": genesis_days_remaining() if genesis else 0,
        "volume_discounts": "Automatic per-wallet Hybrid_Premium: 100+ 25% off, 500+ 37% off, 2000+ 50% off",
        "research": {
            "paper": "The Density Imperative (Metavolve Labs, 2026)",
            "doi": "10.5281/zenodo.18667735",
            "key_finding": "Dense metadata: +160% semantic coverage, +25.5% visual perception",
        },
        "api_endpoint": f"{BASE_API_URL}/agent/enrich",
        "guide": f"{BASE_API_URL}/agent/guide",
    }, indent=2)


@mcp.tool()
def enrich_agent_image(
    image_url: str,
    tier: str = "oracle_only",
    callback_url: str = "",
    custom_fields: str = "",
) -> str:
    """Submit YOUR image for Golden Codex enrichment (Enrichment-as-a-Service).

    Tiers:
      - oracle_only: Returns Golden Codex JSON (111-field Hybrid_Premium analysis)
      - oracle_plus_infuse: JSON + XMP-infused image + GCX hash registry
      - full_certified: JSON + infused + C2PA signed + hash registered

    Custom fields are merged with Hybrid_Premium analysis. Submit as JSON string.
    Your values take priority for factual fields (title, artist, copyright).

    Args:
        image_url: Public URL of the image to enrich.
        tier: oracle_only, oracle_plus_infuse, or full_certified.
        callback_url: Optional webhook URL for completion notification.
        custom_fields: JSON string of custom metadata fields to merge.
    """
    if tier not in ENRICHMENT_TIERS:
        return json.dumps({"error": f"Invalid tier '{tier}'", "valid_tiers": list(ENRICHMENT_TIERS.keys())})

    tier_info = ENRICHMENT_TIERS[tier]
    genesis = is_genesis_epoch()
    price = tier_info["launch_price_usdc"] if genesis else tier_info["price_usdc"]

    body = {
        "image_url": image_url,
        "tier": tier,
    }
    if callback_url:
        body["callback_url"] = callback_url
    if custom_fields:
        try:
            body["custom_fields"] = json.loads(custom_fields)
        except json.JSONDecodeError:
            return json.dumps({"error": "custom_fields must be valid JSON string"})

    return json.dumps({
        "action": "POST",
        "url": f"{BASE_API_URL}/agent/enrich",
        "headers": {
            "Content-Type": "application/json",
            "X-PAYMENT": f"<x402 payment proof for ${price:.2f} USDC>",
        },
        "body": body,
        "tier": {**tier_info, "current_price": price},
        "x402_flow": [
            "1. POST to endpoint WITHOUT X-PAYMENT header",
            "2. Receive HTTP 402 with payment details",
            "3. Execute USDC transfer on Base L2",
            "4. Re-POST with X-PAYMENT header containing tx proof",
            "5. Receive job_id — poll GET /agent/enrich/{job_id} for results",
        ],
        "poll_endpoint": f"{BASE_API_URL}/agent/enrich/{{job_id}}",
        "custom_fields_example": {
            "title": "Sunset Over Barcelona",
            "artist": "Your Name",
            "copyright_holder": "Your Studio LLC",
            "creation_year": "2026",
            "medium": "Digital Photography",
            "commercial_use": True,
        },
        "genesis_epoch": genesis,
    }, indent=2)


@mcp.tool()
def deliver_artifacts(
    artifact_ids: str,
    tier: str = "hybrid_premium",
) -> str:
    """Create an on-demand delivery order for specific manifest artifacts.

    Artifacts are fetched from museum APIs, optimized, enriched (Nova + Atlas),
    and delivered as infused .png + golden_codex.json with signed download URLs.

    Args:
        artifact_ids: Comma-separated artifact IDs (e.g., "met_436965,chicago_27992").
        tier: Delivery tier: human_standard ($0.05) or hybrid_premium ($0.20).
    """
    ids = [a.strip() for a in artifact_ids.split(",") if a.strip()]
    if not ids:
        return json.dumps({"error": "No artifact_ids provided"})

    genesis = is_genesis_epoch()
    base_price = 0.20 if tier == "hybrid_premium" else 0.05
    unit_price = round(base_price * GENESIS_DISCOUNT, 2) if genesis else base_price
    total = round(unit_price * len(ids), 2)

    return json.dumps({
        "action": "POST",
        "url": f"{BASE_API_URL}/deliver/order",
        "body": {"artifact_ids": ids, "tier": tier},
        "pricing": {
            "unit_price": unit_price,
            "count": len(ids),
            "total": total,
            "currency": "USDC",
        },
        "flow": [
            "1. POST /deliver/order — create order, get payment instructions",
            "2. Execute USDC payment on Base L2",
            "3. POST /deliver/order/{order_id}/fulfill — with X-PAYMENT header",
            "4. GET /deliver/order/{order_id} — poll until fulfilled",
            "5. Download infused.png + golden_codex.json via signed URLs",
        ],
        "deliverables": {
            "human_standard": ["optimized.jpg (2048px)"],
            "hybrid_premium": ["infused.png (XMP metadata)", "golden_codex.json (111-field)"],
        },
        "genesis_epoch": genesis,
    }, indent=2)


@mcp.tool()
def get_enrichment_status(job_id: str) -> str:
    """Poll the status of a Golden Codex enrichment job.

    Args:
        job_id: The job ID returned from the enrich endpoint.
    """
    return json.dumps({
        "action": "GET",
        "url": f"{BASE_API_URL}/agent/enrich/{job_id}",
        "note": "Poll until status is 'completed' or 'failed'. Typical: 30-120 seconds.",
        "possible_statuses": ["queued", "in_progress", "completed", "failed"],
    }, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
