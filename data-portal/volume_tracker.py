"""
Volume Discount Tracker — 30-day rolling window per agent wallet.

Tracks cumulative Oracle record purchases per wallet address over a
30-day rolling window. Automatically computes the current discount tier
and triggers enterprise outreach metadata when spend exceeds $200.

Storage: Firestore collection `agent_volume_tracking/{wallet_address}`
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from google.cloud.firestore import AsyncClient

from pricing import get_volume_price, ENTERPRISE_OUTREACH_THRESHOLD_USD

logger = logging.getLogger("data-portal.volume")

COLLECTION = "agent_volume_tracking"
WINDOW_DAYS = 30


class VolumeTracker:
    """Firestore-backed volume discount tracker."""

    def __init__(self):
        self._db: AsyncClient | None = None

    def set_db(self, db: AsyncClient):
        self._db = db

    async def record_purchase(
        self,
        wallet_address: str,
        records: int,
        amount_usd: float,
        endpoint: str = "oracle",
    ) -> dict:
        """Record a purchase and return the current volume tier.

        Args:
            wallet_address: The x402 buyer wallet address.
            records: Number of records purchased in this transaction.
            amount_usd: USD amount paid.
            endpoint: Which endpoint was called.

        Returns:
            dict with current volume tier info and enterprise outreach flag.
        """
        if not self._db or not wallet_address:
            return get_volume_price(0)

        doc_ref = self._db.collection(COLLECTION).document(wallet_address)
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=WINDOW_DAYS)

        try:
            doc = await doc_ref.get()

            if doc.exists:
                data = doc.to_dict()
                # Prune events older than 30 days
                events = [
                    e for e in data.get("events", [])
                    if e.get("timestamp", now) > window_start
                ]
                events.append({
                    "timestamp": now,
                    "records": records,
                    "amount_usd": amount_usd,
                    "endpoint": endpoint,
                })

                total_records = sum(e.get("records", 0) for e in events)
                total_spend = sum(e.get("amount_usd", 0) for e in events)

                await doc_ref.set({
                    "wallet_address": wallet_address,
                    "events": events,
                    "records_30d": total_records,
                    "spend_30d": round(total_spend, 2),
                    "last_updated": now,
                    "first_seen": data.get("first_seen", now),
                })
            else:
                total_records = records
                total_spend = amount_usd
                await doc_ref.set({
                    "wallet_address": wallet_address,
                    "events": [{
                        "timestamp": now,
                        "records": records,
                        "amount_usd": amount_usd,
                        "endpoint": endpoint,
                    }],
                    "records_30d": total_records,
                    "spend_30d": round(total_spend, 2),
                    "last_updated": now,
                    "first_seen": now,
                })

            # Calculate current tier
            tier_info = get_volume_price(total_records)
            tier_info["spend_30d"] = round(total_spend, 2)
            tier_info["enterprise_outreach"] = total_spend >= ENTERPRISE_OUTREACH_THRESHOLD_USD

            if tier_info["enterprise_outreach"]:
                tier_info["enterprise_message"] = (
                    "You've spent ${:.2f} in the last 30 days. "
                    "Enterprise licenses start at $8,000 with full compliance manifests "
                    "and unlimited API access. Contact enterprise@iaeternum.ai"
                ).format(total_spend)

            return tier_info

        except Exception as exc:
            logger.warning("Volume tracking failed for %s: %s", wallet_address[:10], exc)
            return get_volume_price(0)

    async def get_tier(self, wallet_address: str) -> dict:
        """Get the current volume tier for a wallet without recording a purchase."""
        if not self._db or not wallet_address:
            return get_volume_price(0)

        try:
            doc = await self._db.collection(COLLECTION).document(wallet_address).get()
            if doc.exists:
                data = doc.to_dict()
                return get_volume_price(data.get("records_30d", 0))
        except Exception as exc:
            logger.warning("Volume tier lookup failed: %s", exc)

        return get_volume_price(0)


# Singleton instance — connected to Firestore at startup via main.py
volume_tracker = VolumeTracker()
