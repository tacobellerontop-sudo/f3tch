"""Provider management."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..context import AppContext
from ..models import ActivityLog, Provider, User
from ..providers.base import ProviderAdapter
from ..schemas import CredentialsIn, ProviderPatchIn
from .deps import AdminUser, ApiError, Ctx, CurrentUser, Db, client_ip

router = APIRouter(prefix="/providers", tags=["providers"])


def _describe(adapter: ProviderAdapter, row: Provider | None, *, admin: bool) -> dict:
    data = adapter.describe()
    credential = row.credential if row else None
    data.update(
        {
            "enabled": bool(row.enabled) if row else False,
            "configured": credential is not None,
            # Ready = can download right now given its credential requirements.
            "ready": bool(row and row.enabled)
            and (not adapter.capabilities.requires_credentials or credential is not None),
        }
    )
    if admin:
        data.update(
            {
                "health": row.health if row else "unknown",
                "health_message": row.health_message if row else "",
                "checked_at": row.checked_at.isoformat() + "Z" if row and row.checked_at else None,
                # Only the *names* of the stored fields; values never leave the server.
                "credential_fields_set": credential.fields.split(", ") if credential else [],
                "credentials_updated_at": credential.updated_at.isoformat() + "Z" if credential else None,
            }
        )
    else:
        data.pop("credential_fields", None)
    return data


def _adapter(ctx: AppContext, provider_id: str) -> ProviderAdapter:
    adapter = ctx.registry.get(provider_id)
    if adapter is None:
        raise ApiError(404, "not_found", "Provider not found.")
    return adapter


@router.get("")
def list_providers(user: CurrentUser, ctx: Ctx, db: Db) -> list[dict]:
    rows = {row.id: row for row in db.query(Provider).all()}
    return [_describe(a, rows.get(a.id), admin=user.is_admin) for a in ctx.registry.all()]


@router.patch("/{provider_id}")
def set_enabled(provider_id: str, body: ProviderPatchIn, request: Request, admin: AdminUser, ctx: Ctx, db: Db) -> dict:
    adapter = _adapter(ctx, provider_id)
    row = db.get(Provider, provider_id)
    row.enabled = body.enabled
    db.commit()
    ctx.activity.record(
        "admin", "provider.enabled" if body.enabled else "provider.disabled",
        f"{adapter.name} {'enabled' if body.enabled else 'disabled'}",
        user_id=admin.id, ip=client_ip(request, ctx), target=provider_id,
    )
    ctx.bus.publish("providers.changed", {}, audience="all")
    return _describe(adapter, db.get(Provider, provider_id), admin=True)


@router.put("/{provider_id}/credentials")
def set_credentials(provider_id: str, body: CredentialsIn, request: Request, admin: AdminUser, ctx: Ctx, db: Db) -> dict:
    adapter = _adapter(ctx, provider_id)
    db.commit()
    try:
        fields = ctx.registry.set_credentials(provider_id, body.values, admin.id)
    except ValueError as exc:
        raise ApiError(422, "invalid_credentials", str(exc)) from exc
    ctx.activity.record(
        "admin", "provider.credentials_set", f"Credentials updated for {adapter.name}",
        user_id=admin.id, ip=client_ip(request, ctx), target=provider_id, detail={"fields": fields},
    )
    ctx.bus.publish("providers.changed", {}, audience="all")
    db.expire_all()
    return _describe(adapter, db.get(Provider, provider_id), admin=True)


@router.delete("/{provider_id}/credentials")
def clear_credentials(provider_id: str, request: Request, admin: AdminUser, ctx: Ctx, db: Db) -> dict:
    adapter = _adapter(ctx, provider_id)
    db.commit()
    if ctx.registry.clear_credentials(provider_id):
        ctx.activity.record(
            "admin", "provider.credentials_cleared", f"Credentials removed for {adapter.name}",
            user_id=admin.id, ip=client_ip(request, ctx), target=provider_id,
        )
        ctx.bus.publish("providers.changed", {}, audience="all")
    db.expire_all()
    return _describe(adapter, db.get(Provider, provider_id), admin=True)


@router.post("/{provider_id}/test")
async def test_provider(provider_id: str, request: Request, admin: AdminUser, ctx: Ctx, db: Db) -> dict:
    adapter = _adapter(ctx, provider_id)
    db.commit()
    report = await ctx.registry.test(provider_id)
    ctx.activity.record(
        "provider", "provider.test", f"{adapter.name} connectivity test: {report.message}",
        user_id=admin.id, level="info" if report.ok else "warning",
        ip=client_ip(request, ctx), target=provider_id,
    )
    ctx.bus.publish("providers.changed", {}, audience="all")
    return {"ok": report.ok, "message": report.message, "account": report.account}


@router.get("/{provider_id}/errors")
def provider_errors(provider_id: str, admin: AdminUser, ctx: Ctx, db: Db, limit: int = 50) -> list[dict]:
    _adapter(ctx, provider_id)
    rows = (
        db.query(ActivityLog, User.username)
        .outerjoin(User, User.id == ActivityLog.user_id)
        .filter(ActivityLog.category == "provider", ActivityLog.target == provider_id)
        .order_by(ActivityLog.id.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    return [
        {
            "id": log.id,
            "created_at": log.created_at.isoformat() + "Z",
            "level": log.level,
            "action": log.action,
            "message": log.message,
            "username": username or "",
            "code": (log.detail or {}).get("code", ""),
        }
        for log, username in rows
    ]
