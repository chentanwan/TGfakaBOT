from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminAuditLog


async def record_admin_action(
    session: AsyncSession,
    *,
    admin_telegram_id: int,
    action: str,
    target_type: str,
    target_id: str | None = None,
    details: dict | None = None,
) -> None:
    session.add(
        AdminAuditLog(
            admin_telegram_id=admin_telegram_id,
            action=action[:80],
            target_type=target_type[:80],
            target_id=target_id[:255] if target_id else None,
            details=details or {},
        )
    )
    await session.flush()
