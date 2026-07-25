from fastapi import APIRouter

from app.api.v1 import (
    analytics,
    auth,
    backups,
    bot,
    broadcasts,
    dashboard,
    health,
    nodes,
    payments,
    promo,
    referrals,
    settings,
    users,
    webhooks,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(settings.router)
api_router.include_router(dashboard.router)
api_router.include_router(nodes.router)
api_router.include_router(users.router)
api_router.include_router(bot.router)
api_router.include_router(payments.router)
api_router.include_router(promo.router)
api_router.include_router(referrals.router)
api_router.include_router(webhooks.router)
api_router.include_router(broadcasts.router)
api_router.include_router(analytics.router)
api_router.include_router(backups.router)
