from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.db import init_db
from app.routes import auth, credentials, profiles, bot_config, dashboard

app = FastAPI(title="Alpaca Paper Trading Bot", version="1.0.0")

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router)
app.include_router(credentials.router)
app.include_router(profiles.router)
app.include_router(bot_config.router)
app.include_router(dashboard.router)


@app.on_event("startup")
async def startup():
    init_db()


@app.get("/health")
async def health():
    return {"status": "healthy"}