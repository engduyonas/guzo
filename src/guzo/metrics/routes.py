from typing import Annotated

from fastapi import APIRouter, Query

from guzo.identity.deps import Ops
from guzo.metrics.service import WeekMetrics, weekly_metrics

router = APIRouter(prefix="/ops/metrics", tags=["ops"])


@router.get("/weekly")
async def get_weekly_metrics(
    user: Ops, city_id: str = "addis", weeks: Annotated[int, Query(ge=1, le=26)] = 8
) -> list[WeekMetrics]:
    """This week and the weeks before it, newest first."""
    return await weekly_metrics(city_id, weeks)
