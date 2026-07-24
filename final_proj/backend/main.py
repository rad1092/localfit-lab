from fastapi import FastAPI, APIRouter, Depends
from fastapi.middleware.cors import CORSMiddleware
from app.database import engine, Base
from app.core.settings import CORS_ORIGINS
from app.dependencies import require_environment_admin
from app.routers import (
    admin,
    admin_ops,
    areas,
    auth,
    chatbot,
    community,
    events,
    favorites,
    rankings,
    reports,
    search,
    spatial,
)
from app.runtime_schema import ensure_runtime_schema

# Create tables
Base.metadata.create_all(bind=engine)
ensure_runtime_schema()

app = FastAPI(title="Commercial Area Intelligence API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_router = APIRouter(prefix="/api")
api_router.include_router(areas.router)
api_router.include_router(rankings.router)
api_router.include_router(search.router)
api_router.include_router(favorites.router)
api_router.include_router(reports.router)
api_router.include_router(chatbot.router)
api_router.include_router(auth.router)
api_router.include_router(admin.public_router)
api_router.include_router(
    admin.router,
    dependencies=[Depends(require_environment_admin)],
)
api_router.include_router(
    admin_ops.router,
    dependencies=[Depends(require_environment_admin)],
)
api_router.include_router(spatial.router)
api_router.include_router(community.router)
api_router.include_router(events.router)

app.include_router(api_router)

@app.get("/")
def read_root():
    return {"message": "Welcome to Commercial Area Intelligence API"}
