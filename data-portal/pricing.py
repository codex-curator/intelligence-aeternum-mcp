"""
Pricing logic for the Intelligence Aeternum Data Portal.
FINALIZED PRICING V2 — Effective 2026-02-23

Three channels: SaaS (GCX tokens on golden-codex.com), Agent x402 (USDC on Base L2),
Enterprise (Stripe/invoiced). No channel undercuts paying SaaS subscribers.

Volume discounts tracked per agent wallet on 30-day rolling windows.
Launch pricing (Genesis Epoch): 20% off for first 90 days.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Launch pricing configuration
# ---------------------------------------------------------------------------

GENESIS_EPOCH_START = datetime(2026, 2, 23, tzinfo=timezone.utc)
GENESIS_EPOCH_DAYS = 90
GENESIS_DISCOUNT = 0.80  # 20% off

def is_genesis_epoch() -> bool:
    """Check if we're still in the 90-day Genesis Epoch launch period."""
    now = datetime.now(timezone.utc)
    elapsed = (now - GENESIS_EPOCH_START).days
    return elapsed < GENESIS_EPOCH_DAYS

def genesis_days_remaining() -> int:
    """Days remaining in Genesis Epoch. Returns 0 if expired."""
    now = datetime.now(timezone.utc)
    remaining = GENESIS_EPOCH_DAYS - (now - GENESIS_EPOCH_START).days
    return max(0, remaining)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class UserType(str, Enum):
    ACADEMIC = "academic"
    INDIVIDUAL = "individual"
    AGENT = "agent"
    CORPORATE = "corporate"
    ENTERPRISE = "enterprise"


class PricingTier(BaseModel):
    per_image: float = 0.0
    flat_price: Optional[float] = None
    min_quantity: int = 1
    label: str
    currency: str = "USD"
    launch_eligible: bool = True  # Whether Genesis Epoch discount applies


# ---------------------------------------------------------------------------
# PRICING TIERS — V2 APPROVED (2026-02-23)
# ---------------------------------------------------------------------------

PRICING_TIERS: dict[str, PricingTier] = {
    # ── Free tiers ────────────────────────────────────────────────────────
    "academic": PricingTier(
        per_image=0.00,
        label="Free with attribution (non-commercial research)",
        launch_eligible=False,
    ),
    "individual": PricingTier(
        per_image=0.00,
        label="Free with attribution (artists & researchers)",
        launch_eligible=False,
    ),
    "raw_free": PricingTier(
        per_image=0.00,
        label="Raw museum images (CC0, no enrichment)",
        launch_eligible=False,
    ),

    # ── Human_Standard (museum API + LLM structured, ~300-800 tokens) ─────
    # Includes metadata + image download as a single package
    "curated_agent": PricingTier(
        per_image=0.05,
        label="Human_Standard — agent x402 (metadata + image)",
        currency="USDC",
    ),
    "curated_agent_batch": PricingTier(
        per_image=0.04,
        min_quantity=100,
        label="Human_Standard — agent batch x402 (metadata + image)",
        currency="USDC",
    ),
    "curated_corporate": PricingTier(
        per_image=0.05,
        min_quantity=1000,
        label="Human_Standard — corporate license",
    ),

    # ── Hybrid_Premium (full 111-field, 2,000-6,000+ tokens) ──────────────
    # Includes metadata + image download as a single package
    "oracle_agent": PricingTier(
        per_image=0.20,
        label="Hybrid_Premium — agent x402 (metadata + image)",
        currency="USDC",
    ),
    "oracle_agent_batch": PricingTier(
        per_image=0.15,
        min_quantity=100,
        label="Hybrid_Premium — agent batch x402 (25% off)",
        currency="USDC",
    ),
    "oracle_agent_volume": PricingTier(
        per_image=0.125,
        min_quantity=500,
        label="Hybrid_Premium — agent volume x402 (37% off)",
        currency="USDC",
    ),
    "oracle_agent_loyalty": PricingTier(
        per_image=0.10,
        min_quantity=2000,
        label="Hybrid_Premium — agent loyalty x402 (50% off)",
        currency="USDC",
    ),
    "oracle_corporate": PricingTier(
        per_image=1.00,
        min_quantity=100,
        label="Hybrid_Premium — corporate license",
    ),

    # ── Certified Hybrid_Premium (Hybrid_Premium + C2PA + Hash Registry) ──
    "certified_agent": PricingTier(
        per_image=0.30,
        label="Certified Hybrid_Premium — agent x402",
        currency="USDC",
    ),
    "certified_corporate": PricingTier(
        per_image=2.00,
        min_quantity=100,
        label="Certified Hybrid_Premium — corporate license",
    ),

    # ── Full Pipeline (Oracle + upscale + infusion) ───────────────────────
    "full_pipeline": PricingTier(
        per_image=0.50,
        label="Full Pipeline — agent x402",
        currency="USDC",
    ),

    # ── Mint (Arweave L1 + NFT) ──────────────────────────────────────────
    "mint_agent": PricingTier(
        per_image=2.50,
        label="Mint Aeternum Asset — agent x402",
        currency="USDC",
    ),

    # ── Bot Bulk Packages ─────────────────────────────────────────────────
    "bot_curated_1k": PricingTier(
        flat_price=40.00,
        label="Bot Pack — 1K Human_Standard records ($0.04/ea)",
    ),
    "bot_curated_10k": PricingTier(
        flat_price=300.00,
        label="Bot Pack — 10K Human_Standard records ($0.03/ea)",
    ),
    "bot_curated_50k": PricingTier(
        flat_price=1200.00,
        label="Bot Pack — 50K Human_Standard records ($0.024/ea)",
    ),
    "bot_oracle_1k": PricingTier(
        flat_price=175.00,
        label="Bot Pack — 1K Hybrid_Premium records ($0.175/ea)",
    ),
    "bot_oracle_10k": PricingTier(
        flat_price=1250.00,
        label="Bot Pack — 10K Hybrid_Premium records ($0.125/ea)",
    ),
    "bot_foundation_1m": PricingTier(
        flat_price=63750.00,
        label="Bot Foundation — 1M Human_Standard records ($0.064/ea)",
    ),
    "bot_foundation_oracle_1m": PricingTier(
        flat_price=212500.00,
        label="Bot Foundation — 1M Hybrid_Premium records ($0.213/ea)",
    ),
    "bot_daily_sub": PricingTier(
        flat_price=99.00,
        label="Bot Daily API — unlimited Human_Standard (rate-limited), $99/month",
    ),

    # ── Dataset Bundles (Parquet/ZIP download) ────────────────────────────
    "dataset_sample_1k": PricingTier(
        flat_price=25.00,
        label="Dataset — 1K Human_Standard sample",
    ),
    "dataset_museum_single": PricingTier(
        flat_price=200.00,
        label="Dataset — single museum (~8K records)",
    ),
    "dataset_full_curated": PricingTier(
        flat_price=1000.00,
        label="Dataset — full 53K+ Human_Standard collection",
    ),
    "dataset_oracle_core": PricingTier(
        flat_price=2500.00,
        label="Dataset — 10K Hybrid_Premium core",
    ),
    "dataset_complete": PricingTier(
        flat_price=5000.00,
        label="Dataset — complete Alexandria (Human_Standard + Hybrid_Premium)",
    ),

    # ── Enterprise (all-access) ──────────────────────────────────────────
    "enterprise_curated": PricingTier(
        flat_price=8000.00,
        label="Enterprise — full Human_Standard + provenance manifest",
    ),
    "enterprise_oracle": PricingTier(
        flat_price=45000.00,
        label="Enterprise — full Hybrid_Premium + compliance manifest",
    ),
    "enterprise_certified": PricingTier(
        flat_price=85000.00,
        label="Enterprise — Certified Hybrid_Premium + C2PA + Aegis API (1M queries/yr)",
    ),
    "enterprise_full": PricingTier(
        flat_price=150000.00,
        label="Enterprise — Full Pipeline + Arweave + legal attestation",
    ),
    "enterprise_foundation": PricingTier(
        flat_price=250000.00,
        label="Enterprise — Foundation Model License (custom enrichment)",
    ),

    # ── Compliance Manifests (standalone) ─────────────────────────────────
    "compliance_audit": PricingTier(
        flat_price=2500.00,
        label="Compliance Audit Report (read-only)",
    ),
    "compliance_basic": PricingTier(
        flat_price=12000.00,
        label="Compliance Manifest — EU AI Act Art.53 basic",
    ),
    "compliance_full": PricingTier(
        flat_price=35000.00,
        label="Compliance Manifest — full + legal attestation",
    ),
    "compliance_annual": PricingTier(
        flat_price=8000.00,
        label="Compliance Subscription — annual updates",
    ),

    # ── Agent Enrichment (agent-submitted images) ─────────────────────────
    "enrich_oracle_only": PricingTier(
        per_image=0.20,
        label="Agent Enrichment — Hybrid_Premium reading (returns Golden Codex JSON)",
        currency="USDC",
    ),
    "enrich_oracle_plus_infuse": PricingTier(
        per_image=0.30,
        label="Agent Enrichment — Hybrid_Premium + XMP infusion + hash registry",
        currency="USDC",
    ),
    "enrich_full_certified": PricingTier(
        per_image=0.50,
        label="Agent Enrichment — Hybrid_Premium + infusion + C2PA + registry",
        currency="USDC",
    ),
}


# ---------------------------------------------------------------------------
# Agent batch tier (legacy compat for batch endpoint)
# ---------------------------------------------------------------------------

# The batch endpoint uses "agent_batch" as a virtual tier
_AGENT_BATCH_PER_IMAGE = 0.05


# ---------------------------------------------------------------------------
# Volume discount tiers (Oracle, 30-day rolling per wallet)
# ---------------------------------------------------------------------------

VOLUME_TIERS = [
    {"min": 2000, "per_record": 0.10, "discount": "50%", "label": "Loyalty floor"},
    {"min": 500,  "per_record": 0.125, "discount": "37%", "label": "Volume"},
    {"min": 100,  "per_record": 0.15, "discount": "25%", "label": "Batch"},
    {"min": 0,    "per_record": 0.20, "discount": "0%",  "label": "Standard"},
]

ENTERPRISE_OUTREACH_THRESHOLD_USD = 200.00


def get_volume_price(cumulative_records_30d: int) -> dict:
    """Get the current Oracle per-record price based on 30-day volume.

    Returns dict with per_record, discount, label, and whether enterprise
    outreach should be triggered.
    """
    for tier in VOLUME_TIERS:
        if cumulative_records_30d >= tier["min"]:
            result = {
                "per_record": tier["per_record"],
                "discount": tier["discount"],
                "label": tier["label"],
                "cumulative_records": cumulative_records_30d,
            }
            # Apply Genesis Epoch discount if active
            if is_genesis_epoch():
                result["per_record"] = round(tier["per_record"] * GENESIS_DISCOUNT, 4)
                result["genesis_epoch"] = True
                result["genesis_days_remaining"] = genesis_days_remaining()
                result["full_price"] = tier["per_record"]
            return result

    # Fallback (should not reach here)
    return {"per_record": 0.20, "discount": "0%", "label": "Standard", "cumulative_records": 0}


# ---------------------------------------------------------------------------
# Price calculation
# ---------------------------------------------------------------------------

def calculate_price(tier: str, quantity: int = 1) -> dict:
    """Calculate the total price for a given tier and quantity.

    Applies Genesis Epoch discount automatically for eligible tiers.

    Returns:
        dict with keys: tier, quantity, per_image, flat_price, total,
        currency, label, genesis_epoch, genesis_days_remaining
    """
    # Handle legacy "agent_batch" tier used by batch endpoint
    if tier == "agent_batch":
        per_image = _AGENT_BATCH_PER_IMAGE
        if is_genesis_epoch():
            per_image = round(per_image * GENESIS_DISCOUNT, 4)
        total = round(per_image * quantity, 2)
        return {
            "tier": "agent_batch",
            "quantity": quantity,
            "per_image": per_image,
            "flat_price": None,
            "total": total,
            "currency": "USDC",
            "label": "Agent batch download ($0.05/image)",
            "genesis_epoch": is_genesis_epoch(),
            "genesis_days_remaining": genesis_days_remaining() if is_genesis_epoch() else 0,
        }

    if tier not in PRICING_TIERS:
        raise ValueError(f"Unknown pricing tier: {tier}. Valid tiers: {list(PRICING_TIERS.keys())}")

    t = PRICING_TIERS[tier]

    if quantity < t.min_quantity:
        raise ValueError(
            f"Tier '{tier}' requires minimum {t.min_quantity} images. Requested: {quantity}"
        )

    if t.flat_price is not None:
        total = t.flat_price
        per_image = t.per_image
        # Apply Genesis discount to flat-price tiers
        if t.launch_eligible and is_genesis_epoch():
            total = round(total * GENESIS_DISCOUNT, 2)
    else:
        per_image = t.per_image
        if t.launch_eligible and is_genesis_epoch():
            per_image = round(per_image * GENESIS_DISCOUNT, 4)
        total = round(per_image * quantity, 2)

    result = {
        "tier": tier,
        "quantity": quantity,
        "per_image": per_image,
        "flat_price": t.flat_price,
        "total": total,
        "currency": t.currency,
        "label": t.label,
    }

    if t.launch_eligible and is_genesis_epoch():
        result["genesis_epoch"] = True
        result["genesis_days_remaining"] = genesis_days_remaining()
        result["full_price_per_image"] = t.per_image
        result["full_price_total"] = round(t.per_image * quantity, 2) if t.flat_price is None else t.flat_price
    else:
        result["genesis_epoch"] = False

    return result


def get_tier_for_quantity(
    quantity: int,
    user_type: UserType = UserType.CORPORATE,
    product: str = "curated",
) -> str:
    """Recommend the best tier for a given quantity, user type, and product."""
    if user_type == UserType.ACADEMIC:
        return "academic"
    if user_type == UserType.INDIVIDUAL:
        return "individual"

    if product == "raw":
        return "raw_free"

    if user_type == UserType.AGENT:
        if product == "curated":
            return "curated_agent_batch" if quantity >= 100 else "curated_agent"
        elif product == "oracle":
            if quantity >= 2000:
                return "oracle_agent_loyalty"
            elif quantity >= 500:
                return "oracle_agent_volume"
            elif quantity >= 100:
                return "oracle_agent_batch"
            return "oracle_agent"
        elif product == "certified":
            return "certified_agent"
        elif product == "full_pipeline":
            return "full_pipeline"
        elif product == "mint":
            return "mint_agent"
        return "curated_agent"

    if user_type == UserType.ENTERPRISE:
        if product == "oracle":
            return "enterprise_oracle"
        elif product == "certified":
            return "enterprise_certified"
        elif product == "full_pipeline":
            return "enterprise_full"
        elif product == "foundation":
            return "enterprise_foundation"
        return "enterprise_curated"

    # Corporate
    if product == "curated":
        return "curated_corporate" if quantity >= 1000 else "curated_agent"
    elif product == "oracle":
        return "oracle_corporate" if quantity >= 100 else "oracle_agent"
    elif product == "certified":
        return "certified_corporate" if quantity >= 100 else "certified_agent"

    return "curated_agent"


def validate_tier_access(user_type: UserType, tier: str) -> bool:
    """Check whether a user type is allowed to use a pricing tier."""
    agent_tiers = {
        "raw_free", "curated_agent", "curated_agent_batch",
        "oracle_agent", "oracle_agent_batch", "oracle_agent_volume", "oracle_agent_loyalty",
        "certified_agent", "full_pipeline", "mint_agent",
        "enrich_oracle_only", "enrich_oracle_plus_infuse", "enrich_full_certified",
        "bot_curated_1k", "bot_curated_10k", "bot_curated_50k",
        "bot_oracle_1k", "bot_oracle_10k",
        "bot_foundation_1m", "bot_foundation_oracle_1m",
        "bot_daily_sub",
    }
    corporate_tiers = {
        "raw_free", "curated_corporate",
        "oracle_corporate", "certified_corporate",
        "enterprise_curated", "enterprise_oracle", "enterprise_certified",
        "enterprise_full", "enterprise_foundation",
        "dataset_sample_1k", "dataset_museum_single", "dataset_full_curated",
        "dataset_oracle_core", "dataset_complete",
        "compliance_audit", "compliance_basic", "compliance_full", "compliance_annual",
    }
    allowed: dict[UserType, set[str]] = {
        UserType.ACADEMIC: {"academic", "raw_free"},
        UserType.INDIVIDUAL: {"individual", "raw_free"},
        UserType.AGENT: agent_tiers,
        UserType.CORPORATE: corporate_tiers,
        UserType.ENTERPRISE: set(PRICING_TIERS.keys()),
    }

    return tier in allowed.get(user_type, set())
