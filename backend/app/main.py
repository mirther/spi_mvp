from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.base import Base
from app.db.session import engine
from app.models.auth import AuthUser, AuthSession, AuthLoginAttempt  # noqa: F401
from app.models.data_asset import DataAsset  # noqa: F401
from app.models.project import Project, PipelineNode, PipelineConnection, ProjectRun, RunExecution  # noqa: F401
from app.models.cluster_configuration import ClusterConfiguration  # noqa: F401
from app.models.hpc_access import HPCAccessLease  # noqa: F401
from app.api.routes.hpc import router as hpc_router
from app.api.routes.projects import router as projects_router
from app.api.routes.cluster_configurations import router as cluster_configurations_router
from app.api.routes.data_assets import router as data_assets_router
from app.api.routes.auth import router as auth_router
from app.core.security import require_session
from app.core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Creates new MVP tables without changing existing Data Assets tables.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="SPI API", version="0.3.0", lifespan=lifespan)

# Vite's local development origins. This is only for the local Windows app.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_browser_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-SPI-CSRF"],
)

app.include_router(auth_router, prefix="/api/auth", tags=["Authentication"])
app.include_router(
    hpc_router, prefix="/api/hpc", tags=["HPC"], dependencies=[Depends(require_session)]
)
app.include_router(
    projects_router, prefix="/api/projects", tags=["Projects"], dependencies=[Depends(require_session)]
)
app.include_router(
    cluster_configurations_router,
    prefix="/api/cluster-configurations",
    tags=["Cluster Configurations"],
    dependencies=[Depends(require_session)],
)
app.include_router(
    data_assets_router, prefix="/api/data-assets", tags=["Data Assets"], dependencies=[Depends(require_session)]
)


@app.get("/api/health")
def health_check():
    return {"application": "SPI", "status": "running"}


@app.get("/api/health/database")
def database_health(_user: AuthUser = Depends(require_session)):
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"database": "connected"}
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database connection failed") from exc
