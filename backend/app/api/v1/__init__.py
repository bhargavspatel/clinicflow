from fastapi import APIRouter, Depends

from app.api.v1.endpoints import appointments, auth, billing, patients, portal, providers, webhooks, ws
from app.core.plan_gate import require_active_subscription

router = APIRouter()

_gated = [Depends(require_active_subscription)]

router.include_router(auth.router,         prefix="/auth",         tags=["auth"])
router.include_router(patients.router,     prefix="/patients",     tags=["patients"],     dependencies=_gated)
router.include_router(providers.router,    prefix="/providers",    tags=["providers"],    dependencies=_gated)
router.include_router(appointments.router, prefix="/appointments", tags=["appointments"], dependencies=_gated)
router.include_router(webhooks.router,     prefix="/webhooks",     tags=["webhooks"])
router.include_router(portal.router,       prefix="/portal",       tags=["portal"])
router.include_router(billing.router,      prefix="/billing",      tags=["billing"])
router.include_router(ws.router,                                   tags=["websocket"])
