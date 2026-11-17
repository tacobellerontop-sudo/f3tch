"""What the dashboard needs to start, and the few preferences it can save."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from .. import __version__
from ..usage import usage_summary
from .deps import Ctx, CurrentUser, Db

router = APIRouter(tags=["app"])


class PreferencesIn(BaseModel):
    theme: Literal["", "dark", "light", "system"] | None = None


@router.get("/bootstrap")
def bootstrap(user: CurrentUser, ctx: Ctx, db: Db) -> dict:
    settings = ctx.settings.get()
    return {
        "app_name": settings.general.app_name,
        "version": __version__,
        "default_theme": settings.general.theme,
        "language": settings.general.language,
        "theme": user.theme,
        "usage": usage_summary(db, ctx.settings.get(), ctx.storage, user.id),
    }


@router.patch("/preferences")
def update_preferences(body: PreferencesIn, user: CurrentUser, db: Db) -> dict:
    if body.theme is not None:
        user.theme = body.theme
    db.flush()
    return {"theme": user.theme}
