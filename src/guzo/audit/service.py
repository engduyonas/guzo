from typing import Any

from pymongo.asynchronous.client_session import AsyncClientSession

from guzo.audit.models import AuditEntry
from guzo.common.clock import utcnow
from guzo.identity.models import User


async def record(
    actor: User,
    action: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any] | None = None,
    *,
    session: AsyncClientSession | None = None,
) -> None:
    """Log an operations action. Pass the session to commit it with the change it describes."""
    entry = AuditEntry(
        actor_id=str(actor.id),
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details or {},
        at=utcnow(),
    )
    await entry.insert(session=session)
