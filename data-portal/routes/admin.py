"""
Admin Routes
=============
Internal monitoring endpoints for the Data Portal.

Protected by X-ADMIN-KEY header. Set ADMIN_API_KEY env var in Cloud Run.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

logger = logging.getLogger("data-portal.admin")

ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")


async def require_admin_key(x_admin_key: str = Header(alias="X-ADMIN-KEY", default="")):
    """Verify the admin API key is present and correct."""
    if not ADMIN_API_KEY or len(ADMIN_API_KEY) < 16:
        raise HTTPException(
            status_code=503,
            detail="Admin endpoints disabled — ADMIN_API_KEY not configured.",
        )
    if not x_admin_key or x_admin_key != ADMIN_API_KEY:
        raise HTTPException(
            status_code=403,
            detail="Forbidden — invalid or missing X-ADMIN-KEY header.",
        )


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_key)])


@router.get("/transactions")
async def list_transactions(
    request: Request,
    limit: int = Query(default=50, le=200, description="Number of recent transactions to return"),
    endpoint: str | None = Query(default=None, description="Filter by endpoint: oracle, image, batch, query"),
):
    """Return recent transactions from the ``data_portal_transactions`` collection.

    Results are ordered by timestamp descending (most recent first).
    Optionally filter by endpoint type.
    """
    db = request.state.db

    try:
        ref = db.collection("data_portal_transactions")
        query_ref = ref.order_by("timestamp", direction="DESCENDING")

        if endpoint:
            query_ref = query_ref.where("endpoint", "==", endpoint)

        query_ref = query_ref.limit(limit)

        docs = query_ref.stream()
        transactions = []
        total_usd = 0.0

        async for doc in docs:
            data = doc.to_dict()
            # Convert Firestore timestamp to ISO string for JSON serialisation
            ts = data.get("timestamp")
            if ts:
                data["timestamp"] = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            data["id"] = doc.id
            total_usd += data.get("amount_usd", 0.0)
            transactions.append(data)

        return {
            "count": len(transactions),
            "total_usd": round(total_usd, 4),
            "filter": {"endpoint": endpoint} if endpoint else None,
            "transactions": transactions,
        }

    except Exception as exc:
        logger.error("Failed to fetch transactions: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to fetch transactions: {exc}")


@router.get("/transactions/summary")
async def transaction_summary(request: Request):
    """Aggregate transaction totals grouped by endpoint.

    Scans all documents in ``data_portal_transactions`` and returns
    per-endpoint counts and revenue totals.
    """
    db = request.state.db

    try:
        docs = db.collection("data_portal_transactions").stream()

        by_endpoint: dict[str, dict] = {}
        grand_total = 0.0
        grand_count = 0

        async for doc in docs:
            data = doc.to_dict()
            ep = data.get("endpoint", "unknown")
            amount = data.get("amount_usd", 0.0)

            if ep not in by_endpoint:
                by_endpoint[ep] = {"count": 0, "total_usd": 0.0}

            by_endpoint[ep]["count"] += 1
            by_endpoint[ep]["total_usd"] = round(by_endpoint[ep]["total_usd"] + amount, 4)
            grand_total += amount
            grand_count += 1

        return {
            "grand_total_usd": round(grand_total, 4),
            "grand_count": grand_count,
            "by_endpoint": by_endpoint,
        }

    except Exception as exc:
        logger.error("Failed to generate transaction summary: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to generate summary: {exc}")
