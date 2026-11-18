"""Network adapters, multi-connection downloads and live speeds."""

from __future__ import annotations

from fastapi import APIRouter

from .deps import Ctx, CurrentUser

router = APIRouter(tags=["network"])


@router.get("/network")
async def read_network(user: CurrentUser, ctx: Ctx) -> dict:
    paths = ctx.netpaths
    if not paths.adapters() or (paths.enabled and any(a.online is None for a in paths.adapters())):
        await paths.refresh(probe=paths.enabled)
    return paths.snapshot()


@router.post("/network/refresh")
async def refresh_network(user: CurrentUser, ctx: Ctx) -> dict:
    """List the adapters again and check which ones reach the internet."""
    await ctx.netpaths.refresh(force=True)
    ctx.bus.publish("network.changed", {}, audience="all")
    return ctx.netpaths.snapshot()
