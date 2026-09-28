from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.audit import AuditMiddleware
from app.db import init_db
from app.routes import auth, agent_keys, bot_config, credentials, dashboard, profiles

app = FastAPI(title="OpenQuant Agent API", version="1.2.0")

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added last, so it is the *outermost* middleware and therefore sees the final
# status code after FastAPI has turned a raised HTTPException into a response.
# That is what makes a 403 auditable: the route never ran, so nothing
# downstream logged the refusal.
app.add_middleware(AuditMiddleware)

# Include routers
app.include_router(auth.router)
app.include_router(credentials.router)
app.include_router(profiles.router)
app.include_router(bot_config.router)
app.include_router(dashboard.router)
# Human-only: minting and revoking agent keys is how access is granted, so an
# agent key must not be able to mint itself another one.
app.include_router(agent_keys.router)


@app.on_event("startup")
async def startup():
    init_db()


@app.get("/health")
async def health():
    return {"status": "healthy"}
