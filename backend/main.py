from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from .core.config import get_settings
from .core.database import Base, engine
from .models import user, profile, budget as budget_model, mf_transaction  # noqa: テーブル作成のためimport
from .api import auth, profile as profile_api, simulate, budget, household

settings = get_settings()

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Future Compass API",
    description="ライフプランシミュレーター API",
    version="0.1.0",
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


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "Future Compass"}


# フロントエンド配信
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
