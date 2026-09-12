"""
Analytics router — administrator view of pipeline processing history.

GET /analysis               — all analytics records (newest first)
GET /analysis?summary=true  — aggregated stats only

Administrator-only. The records span every user of the installation and carry
user identifiers, recording identifiers and pipeline error messages, so this is
an operational view rather than a user-level one.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query

from routers.auth import require_admin
from services.analytics import fetch_all_analytics, fetch_analytics_summary

logger = logging.getLogger(__name__)
router = APIRouter(
    tags=["analytics"],
    dependencies=[Depends(require_admin)],
)


@router.get("/analysis")
async def get_analytics(
    summary: bool = Query(
        default=False,
        description="If true, return only aggregated summary stats instead of all records.",
    ),
):
    """
    Return stored processing analytics. Administrator-only.

    Without ?summary: returns every analytics row (up to 1 000) in a
    structured format grouped by pipeline stage, suitable for building
    dashboards and spotting performance bottlenecks.

    With ?summary=true: returns aggregated KPIs (averages, totals, rates)
    computed across all stored records.
    """
    now_str = datetime.now(timezone.utc).isoformat()

    if summary:
        stats = await fetch_analytics_summary()
        return {
            "generated_at": now_str,
            "summary": stats,
        }

    records = await fetch_all_analytics()
    return {
        "count": len(records),
        "generated_at": now_str,
        "analytics": records,
    }
