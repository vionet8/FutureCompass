from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import os
import threading

from .core.config import get_settings
from .core.database import Base, engine, SessionLocal
from .models import user, profile, budget as budget_model, mf_transaction, auto_import, performance  # noqa: テーブル作成のためimport
from .api import auth, profile as profile_api, simulate, budget, household, performance as performance_api
from .services.mf_import import scan_all_users

settings = get_settings()

Base.metadata.create_all(bind=engine)

# ── 自動取込ワーカー（フォルダ監視、60秒間隔） ──────────
AUTO_IMPORT_INTERVAL_SECONDS = 60
_watcher_stop = threading.Event()


def _auto_import_worker():
    while not _watcher_stop.is_set():
        scan_all_users(SessionLocal)
        _watcher_stop.wait(AUTO_IMPORT_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    watcher = threading.Thread(target=_auto_import_worker, daemon=True, name="mf-auto-import")
    watcher.start()
    yield
    _watcher_stop.set()


app = FastAPI(
    title="Future Compass API",
    description="ライフプランシミュレーター API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(profile_api.router, prefix="/api")
app.include_router(simulate.router, prefix="/api")
app.include_router(budget.router, prefix="/api")
app.include_router(household.router, prefix="/api")
app.include_router(performance_api.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "Future Compass"}


# フロントエンド配信
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
