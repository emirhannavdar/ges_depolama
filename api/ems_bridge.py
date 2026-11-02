"""Optional local single-user integration for existing /optimize and EPİAŞ code.

Add `from api.ems_bridge import router as ems_router` and
`app.include_router(ems_router)` to api/api.py, after `app=FastAPI(...)`.
Only expose on 127.0.0.1. This module never writes credentials to disk.
"""
from __future__ import annotations

import asyncio
import inspect
import sys
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/ems", tags=["EMS dashboard"])
_guard = RLock()
_credentials: tuple[str, str] | None = None

class Credentials(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)

class EMSOptimizationRequest(BaseModel):
    # Existing request passes through as-is; frontend adds panel details.
    model_config = {"extra": "allow"}
    pv_panel_count: int = Field(gt=0, le=20_000_000)
    pv_panel_power_wp: float = Field(gt=0, le=5000)
    installed_capacity_kwp: float = Field(gt=0)


def _epias():
    import api.epias as module
    return module

def _logged_in():
    return _credentials is not None

def _has_token():
    try:
        return bool(getattr(_epias(), "TGT_TOKEN", None))
    except Exception:
        return False

def _view(message="Kimlik bilgileri yalnızca bu Python işleminde bellekte tutulur."):
    return {"configured": _logged_in(), "token_present": _has_token(), "message": message}

@router.get('/epias/status')
def epias_status():
    return _view()


def _refresh():
    if _credentials is None:
        raise HTTPException(401, "EPİAŞ hesabınızı önce Entegrasyon bölümünden bağlayın.")
    module = _epias()
    method = getattr(module, "post_TGT_token", None)
    if not callable(method):
        raise HTTPException(500, "api.epias.post_TGT_token fonksiyonu bulunamadı.")
    # Existing function was written with username/password arguments.
    if inspect.iscoroutinefunction(method):
        raise HTTPException(500, "post_TGT_token async; bu adapter için async yenileme işlevi gerekiyor.")
    try:
        result = method(username=_credentials[0], password=_credentials[1])
    except Exception as exc:
        raise HTTPException(502, f"EPİAŞ TGT isteği başarısız: {type(exc).__name__}") from exc
    token = getattr(module, "TGT_TOKEN", None)
    # Some implementations return a token but do not set the global.
    if not token:
        candidate = result if isinstance(result, str) else getattr(result, 'data', None)
        if isinstance(candidate, dict):
            candidate = candidate.get('token') or candidate.get('tgt')
        if isinstance(candidate, str) and candidate.startswith('TGT-'):
            module.TGT_TOKEN = candidate
            token = candidate
    if not token:
        raise HTTPException(502, "EPİAŞ TGT alınamadı. Mevcut post_TGT_token fonksiyonunun yanıtını kontrol edin.")

@router.post('/epias/login')
def epias_login(data:Credentials):
    global _credentials
    with _guard:
        _credentials = (data.username, data.password)
        try:
            _refresh()
        except Exception:
            _credentials = None
            raise
    return _view("EPİAŞ TGT oluşturuldu. Yeni token yalnızca gerektiğinde istenecek.")

@router.post('/epias/logout')
def epias_logout():
    global _credentials
    with _guard:
        _credentials = None
        try:
            _epias().TGT_TOKEN = None
        except Exception:
            pass
    return _view("Bu işlemde tutulan EPİAŞ bilgileri temizlendi.")

async def _call_optimize(payload:dict[str,Any]):
    try:
        import httpx
        from api.api import app
    except ImportError as exc:
        raise HTTPException(500, "httpx eksik: python -m pip install httpx") from exc
    # Calls the exact existing FastAPI route; does not implement new prices or economics.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,base_url="http://local-ems",timeout=120) as client:
        return await client.post('/optimize',json=payload)

@router.post('/optimize')
async def optimize(request: EMSOptimizationRequest):
    import importlib
    import httpx
    power = request.pv_panel_count * request.pv_panel_power_wp / 1000
    if abs(power - request.installed_capacity_kwp) > 0.001:
        raise HTTPException(422, "Kurulu güç ile panel sayısı × panel gücü uyuşmuyor.")
    if not _has_token():
        with _guard:
            _refresh()

    # Compatibility adapter for the existing config-based forecast. It changes
    # the runtime value only for this request, then restores it. The existing
    # pricing/forecast code must read its module variable at call time.
    modules = []
    for name in ('config', 'api.forecast', 'api.pricing', 'api.api', 'api.optimizer'):
        try:
            modules.append(importlib.import_module(name))
        except ImportError:
            pass
    payload = request.model_dump(exclude={'pv_panel_count', 'pv_panel_power_wp', 'installed_capacity_kwp'}, exclude_unset=True)
    old_values=[]
    # Only this bridge's optimization calls are serialized. Local development only.
    async with _optimization_guard:
        try:
            for module in modules:
                if hasattr(module, 'INSTALLED_CAPACITY_KWP'):
                    old_values.append((module, getattr(module, 'INSTALLED_CAPACITY_KWP')))
                    setattr(module, 'INSTALLED_CAPACITY_KWP', power)
            if not old_values:
                raise HTTPException(501, "Backend kurulu güç değişkeni bulunamadı. api.forecast.py entegrasyonu gerekli.")
            response = await _call_optimize(payload)
            if response.status_code in (401, 406) and _logged_in():
                with _guard:
                    _epias().TGT_TOKEN = None
                    _refresh()
                response = await _call_optimize(payload)  # One re-authentication retry only.
            if response.status_code >= 400:
                try:
                    detail = response.json().get('detail', response.text)
                except Exception:
                    detail = response.text[:300]
                raise HTTPException(response.status_code, detail)
            result = response.json()
            if isinstance(result, dict):
                result['ems_metadata'] = {
                    'installed_capacity_kwp': power,
                    'pv_panel_count': request.pv_panel_count,
                    'pv_panel_power_wp': request.pv_panel_power_wp,
                    'source': 'runtime_config_override',
                    'caution': 'Modül seviyesi güç parametresi geçici ayarlandı; forecast.py fonksiyonlarının parametreyi çağrı anında kullandığını doğrulayın.'
                }
            return result
        finally:
            for module, previous in reversed(old_values):
                setattr(module, 'INSTALLED_CAPACITY_KWP', previous)

_optimization_guard = asyncio.Lock()
