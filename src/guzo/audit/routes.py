from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from guzo.audit.models import AuditEntry
from guzo.identity.deps import Ops

router = APIRouter(prefix="/ops/audit", tags=["ops"])


class AuditEntryResponse(BaseModel):
    actor_id: str
    action: str
    target_type: str
    target_id: str
    details: dict[str, Any]
    at: datetime


@router.get("")
async def list_audit_entries(
    user: Ops, target_type: str | None = None, target_id: str | None = None
) -> list[AuditEntryResponse]:
    """Newest first. Filter by the record the actions were taken on."""
    query = {k: v for k, v in {"target_type": target_type, "target_id": target_id}.items() if v}
    entries = await AuditEntry.find(query).sort("-at", "-_id").limit(200).to_list()
    return [AuditEntryResponse(**e.model_dump(exclude={"id"})) for e in entries]
