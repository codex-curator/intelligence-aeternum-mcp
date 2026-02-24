"""
Dataset Catalog Routes
=======================
Browse available datasets, view details, preview samples, and check stats.

Public endpoints (no authentication required for browsing).
Preview endpoints are rate-limited to 10/day per API key or IP.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from auth import generate_signed_url, require_rate_limit
from pricing import PRICING_TIERS, calculate_price

router = APIRouter(prefix="/catalog", tags=["catalog"])

DATA_BUCKET = os.environ.get("DATA_BUCKET", "alexandria-download-1m")

# ---------------------------------------------------------------------------
# Golden Codex metadata schema sample (shown in dataset detail view)
# ---------------------------------------------------------------------------

GOLDEN_CODEX_SCHEMA_SAMPLE: dict[str, Any] = {
    "identity": {
        "artwork_id": "AETR-0001",
        "title": "The Starry Night",
        "artist": "Vincent van Gogh",
        "date": "1889",
        "medium": "Oil on canvas",
        "dimensions": "73.7 cm x 92.1 cm",
    },
    "visual_dna": {
        "dominant_palette": ["#1B3A8C", "#4A7CB5", "#F5D76E", "#2E5930"],
        "composition": "Dynamic swirling sky over quiet village",
        "technique_markers": ["impasto", "bold brushwork", "post-impressionist"],
    },
    "technique_analysis": {
        "brushwork": "Thick, expressive strokes with visible texture",
        "color_theory": "Complementary contrast between blue and yellow",
        "innovation": "Emotional intensity through distorted natural forms",
    },
    "emotional_resonance": {
        "primary_mood": "Turbulent wonder",
        "viewer_experience": "A sense of cosmic movement contained within intimate scale",
    },
    "art_historical_context": {
        "movement": "Post-Impressionism",
        "influences": ["Japanese woodblocks", "Impressionist color theory"],
        "significance": "Foundational work for Expressionism",
    },
    "contemporary_relevance": {
        "training_value": "High — iconic composition for style transfer and generative models",
        "cultural_impact": "One of the most recognized paintings in Western art",
    },
    "collector_notes": {
        "condition": "Excellent (museum-held since 1941)",
        "provenance_chain": "Van Gogh > Theo van Gogh > MoMA (1941-present)",
    },
    "enrichment_source": "Nova Agent (Gemini 2.5 Pro) + Artiswa Voice (GPT-4o)",
    "provenance": {
        "soulmark": "sha256:a1b2c3d4e5f6...",
        "phash": "0xABCDEF1234567890",
        "c2pa_signed": True,
    },
}


# ---------------------------------------------------------------------------
# Dataset definitions (hardcoded initial catalog)
# ---------------------------------------------------------------------------


class DatasetInfo(BaseModel):
    id: str
    name: str
    description: str
    image_count: int
    source: str
    institution: str
    license: str
    preview_available: bool
    gcs_prefix: str
    pricing: dict[str, Any]


def _build_pricing_summary() -> dict[str, Any]:
    """Build a pricing summary dict safe for serialisation."""
    summary: dict[str, Any] = {}
    for tier_name, tier in PRICING_TIERS.items():
        try:
            qty = max(tier.min_quantity, 1)
            summary[tier_name] = calculate_price(tier_name, qty)
        except ValueError:
            summary[tier_name] = {"label": tier.label, "error": "min_quantity not met"}
    return summary


_PRICING_SUMMARY = _build_pricing_summary()


DATASETS: dict[str, DatasetInfo] = {
    "met-museum": DatasetInfo(
        id="met-museum",
        name="Metropolitan Museum of Art - Open Access",
        description=(
            "Over 375,000 CC0 public domain artworks from The Met's encyclopedic "
            "collection spanning 5,000 years of art. Each image includes Golden Codex "
            "8-section AI enrichment metadata."
        ),
        image_count=375_000,
        source="https://www.metmuseum.org/art/collection",
        institution="The Metropolitan Museum of Art",
        license="CC0 1.0",
        preview_available=True,
        gcs_prefix="met-museum/",
        pricing=_PRICING_SUMMARY,
    ),
    "smithsonian": DatasetInfo(
        id="smithsonian",
        name="Smithsonian Open Access",
        description=(
            "Selected fine art holdings from across the Smithsonian Institution's "
            "21 museums. Focus on American art, portraiture, and design. "
            "Golden Codex enriched."
        ),
        image_count=185_000,
        source="https://www.si.edu/openaccess",
        institution="Smithsonian Institution",
        license="CC0 1.0",
        preview_available=True,
        gcs_prefix="smithsonian/",
        pricing=_PRICING_SUMMARY,
    ),
    "nga": DatasetInfo(
        id="nga",
        name="National Gallery of Art - Open Data",
        description=(
            "European and American paintings, sculpture, and works on paper from "
            "one of the world's premier art museums. Includes old masters and "
            "impressionists."
        ),
        image_count=130_000,
        source="https://www.nga.gov/open-access-images.html",
        institution="National Gallery of Art",
        license="CC0 1.0",
        preview_available=True,
        gcs_prefix="nga/",
        pricing=_PRICING_SUMMARY,
    ),
    "rijksmuseum": DatasetInfo(
        id="rijksmuseum",
        name="Rijksmuseum - Rijksstudio",
        description=(
            "The crown jewel of Dutch Golden Age art. Rembrandt, Vermeer, and "
            "over 700,000 objects from the Netherlands' national museum. "
            "High-resolution scans."
        ),
        image_count=709_000,
        source="https://www.rijksmuseum.nl/en/rijksstudio",
        institution="Rijksmuseum",
        license="CC0 1.0",
        preview_available=True,
        gcs_prefix="rijksmuseum/",
        pricing=_PRICING_SUMMARY,
    ),
    "chicago": DatasetInfo(
        id="chicago",
        name="Art Institute of Chicago - Open Access",
        description=(
            "One of the oldest and largest art museums in the US. Strong holdings "
            "in Impressionism, American art, and Asian art. Full API access."
        ),
        image_count=120_000,
        source="https://www.artic.edu/open-access",
        institution="Art Institute of Chicago",
        license="CC0 1.0",
        preview_available=True,
        gcs_prefix="chicago/",
        pricing=_PRICING_SUMMARY,
    ),
    "cleveland": DatasetInfo(
        id="cleveland",
        name="Cleveland Museum of Art - Open Access",
        description=(
            "Diverse global collection from ancient Egypt to contemporary art. "
            "Known for medieval European and Asian holdings. Full open API."
        ),
        image_count=61_000,
        source="https://www.clevelandart.org/open-access",
        institution="Cleveland Museum of Art",
        license="CC0 1.0",
        preview_available=True,
        gcs_prefix="cleveland/",
        pricing=_PRICING_SUMMARY,
    ),
    "paris-elite": DatasetInfo(
        id="paris-elite",
        name="Paris Elite Collection (Curated)",
        description=(
            "A curated selection of masterworks from Parisian institutions "
            "including the Louvre, Musee d'Orsay, and Rodin Museum. "
            "Hand-selected for quality and art-historical significance."
        ),
        image_count=45_000,
        source="Multiple Paris institutions",
        institution="Louvre, Orsay, Rodin, and others",
        license="CC0 1.0 / Open License",
        preview_available=True,
        gcs_prefix="paris-elite/",
        pricing=_PRICING_SUMMARY,
    ),
}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/datasets")
async def list_datasets(
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
):
    """List all available datasets with metadata and pricing tiers."""
    all_datasets = list(DATASETS.values())
    page = all_datasets[offset : offset + limit]
    return {
        "datasets": [d.model_dump() for d in page],
        "total": len(all_datasets),
        "limit": limit,
        "offset": offset,
        "payment_protocols": ["x402 (USDC on Base L2)", "Stripe (card/ACH)"],
        "enterprise": {
            "annual_license": "$150,000/year",
            "includes": "Synchronized GCS mirror, legal indemnification, Article 53 templates",
            "contact": "enterprise@iaeternum.ai",
        },
        "contact": "data@iaeternum.ai",
    }


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: str):
    """Get detailed info for a single dataset, including sample metadata schema."""
    if dataset_id not in DATASETS:
        raise HTTPException(
            status_code=404,
            detail=f"Dataset '{dataset_id}' not found. Available: {list(DATASETS.keys())}",
        )

    ds = DATASETS[dataset_id]
    return {
        **ds.model_dump(),
        "metadata_schema_sample": GOLDEN_CODEX_SCHEMA_SAMPLE,
        "pricing_tiers": {k: v.model_dump() for k, v in PRICING_TIERS.items()},
        "preview_url": f"/catalog/datasets/{dataset_id}/preview",
        "purchase_url": "/orders",
        "agent_endpoint": "/agent/artifact/{artifact_id}",
    }


@router.get(
    "/datasets/{dataset_id}/preview",
    dependencies=[Depends(require_rate_limit("preview", 10, 86400))],
)
async def preview_dataset(
    dataset_id: str,
    request: Request,
    limit: int = Query(default=5, le=5),
):
    """Return up to 5 sample signed image URLs and metadata from a dataset.

    Rate limited to 10 requests per day per API key or client IP.
    Signed URLs expire after 1 hour.
    """
    if dataset_id not in DATASETS:
        raise HTTPException(
            status_code=404,
            detail=f"Dataset '{dataset_id}' not found",
        )

    ds = DATASETS[dataset_id]
    if not ds.preview_available:
        raise HTTPException(status_code=403, detail="Preview not available for this dataset")

    bucket = request.state.data_bucket

    # Generate signed URLs for numbered sample images
    samples = []
    for i in range(1, limit + 1):
        blob_path = f"{ds.gcs_prefix}samples/sample_{i:04d}.jpg"
        meta_path = f"{ds.gcs_prefix}samples/sample_{i:04d}_meta.json"
        try:
            image_url = generate_signed_url(bucket, blob_path, expiration_hours=1)
            meta_url = generate_signed_url(bucket, meta_path, expiration_hours=1)
        except Exception:
            # If signing fails (e.g., no SA credentials in dev), return direct paths
            image_url = f"https://storage.googleapis.com/{bucket}/{blob_path}"
            meta_url = f"https://storage.googleapis.com/{bucket}/{meta_path}"

        samples.append({
            "index": i,
            "image_url": image_url,
            "metadata_url": meta_url,
            "metadata_schema": "golden_codex_v1",
        })

    return {
        "dataset_id": dataset_id,
        "dataset_name": ds.name,
        "samples": samples,
        "total_available": ds.image_count,
        "note": "Preview samples are representative of dataset quality and metadata format.",
    }


@router.get("/compliance/{dataset_id}")
async def get_compliance_manifests(dataset_id: str):
    """FREE — Get AB 2013 + EU AI Act Article 53 compliance manifests for a dataset.

    Returns auto-generated regulatory compliance documents ready for submission.
    No authentication required — compliance transparency is a core value.
    """
    if dataset_id not in DATASETS:
        raise HTTPException(
            status_code=404,
            detail=f"Dataset '{dataset_id}' not found. Available: {list(DATASETS.keys())}",
        )

    from compliance import generate_ab2013_manifest, generate_eu_ai_act_article53_manifest

    ds = DATASETS[dataset_id]
    order_stub = {
        "order_id": f"compliance-preview-{dataset_id}",
        "dataset_id": dataset_id,
        "quantity": ds.image_count,
        "total_price": 0,
        "payment_method": "preview",
        "pricing_tier": "compliance_preview",
    }

    ab2013 = generate_ab2013_manifest(order_stub, dataset_id)
    eu_art53 = generate_eu_ai_act_article53_manifest(order_stub, dataset_id)

    return {
        "dataset_id": dataset_id,
        "institution": ds.institution,
        "compliance_frameworks": ["AB 2013 (California)", "EU AI Act Article 53"],
        "ab_2013": ab2013,
        "eu_ai_act_article_53": eu_art53,
        "note": "These manifests are auto-generated for preview. Purchase-specific manifests include exact order quantities and payment details.",
    }


@router.get("/stats")
async def catalog_stats(request: Request):
    """Aggregate statistics across all datasets.

    Queries Firestore manifest for live counts when available.
    """
    total_images = sum(ds.image_count for ds in DATASETS.values())

    # Try to get live manifest counts
    manifest_counts = {}
    try:
        db = request.state.db
        for museum in ["met", "chicago", "nga", "cleveland", "smithsonian", "paris", "rijksmuseum"]:
            query = db.collection("alexandria_manifest").where("museum", "==", museum).count()
            result = (await query.get())[0]
            manifest_counts[museum] = result[0].value
    except Exception:
        manifest_counts = None

    response = {
        "total_datasets": len(DATASETS),
        "total_images": total_images,
        "total_downloads": 0,
        "institutions": sorted({ds.institution for ds in DATASETS.values()}),
        "last_updated": "2026-02-23",
    }

    if manifest_counts:
        response["manifest_counts"] = manifest_counts
        response["manifest_total"] = sum(manifest_counts.values())

    return response
