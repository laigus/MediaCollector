from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models import ProviderConfigUpdate, ProviderInfo

router = APIRouter(prefix="/api/providers", tags=["providers"])


def _get_tm():
    from app.main import task_manager
    return task_manager


def _get_db():
    from app.main import db
    return db


@router.get("", response_model=list[ProviderInfo])
async def list_providers():
    tm = _get_tm()
    result = []
    for p in tm.list_providers():
        result.append(
            ProviderInfo(
                name=p.name,
                display_name=p.display_name,
                description=p.description,
                config_schema=p.get_config_schema(),
            )
        )
    return result


@router.get("/{provider_name}/config")
async def get_provider_config(provider_name: str):
    database = _get_db()
    config = await database.get_provider_config(provider_name)
    masked = {}
    for k, v in config.items():
        if isinstance(v, str) and len(v) > 10:
            masked[k] = v[:6] + "***" + v[-4:]
        else:
            masked[k] = v
    return {"provider": provider_name, "config": masked}


@router.get("/{provider_name}/collects")
async def get_collects_list(provider_name: str):
    """获取抖音收藏夹列表。"""
    tm = _get_tm()
    provider = tm.get_provider(provider_name)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    if not hasattr(provider, "fetch_collects_list"):
        raise HTTPException(status_code=400, detail="Provider does not support collects")
    try:
        folders = await provider.fetch_collects_list()
        return {"folders": folders}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{provider_name}/config")
async def update_provider_config(provider_name: str, body: ProviderConfigUpdate):
    tm = _get_tm()
    database = _get_db()

    provider = tm.get_provider(provider_name)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    existing = await database.get_provider_config(provider_name)
    merged = dict(existing)
    for k, v in body.config.items():
        if isinstance(v, str) and "***" in v:
            continue
        merged[k] = v

    await database.save_provider_config(provider_name, merged)

    if hasattr(provider, "set_cookie") and "cookie" in merged:
        provider.set_cookie(merged["cookie"])

    return {"ok": True}
