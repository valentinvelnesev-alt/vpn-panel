import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.core.config import settings

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("panel")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Панель запускается (режим: %s)", settings.deploy_mode)
    yield
    from app.db.session import engine
    from app.services import cache, remnawave_provider

    await remnawave_provider.close_all()
    await cache.close()
    await engine.dispose()


app = FastAPI(
    title="VPN Panel API",
    version="0.1.0",
    lifespan=lifespan,
    # Схему наружу не отдаём — панель ставят на публичный адрес.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Панель и API отдаются с одного origin через Caddy, поэтому CORS нужен
# только для локальной разработки с Vite dev-сервером.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
