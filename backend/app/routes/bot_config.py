from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import BotConfig, StrategyProfile
from app.schemas import BotConfigResponse, BotConfigUpdate
from app.routes.auth import get_current_user

router = APIRouter(prefix="/bot", tags=["bot"])


@router.get("/config", response_model=BotConfigResponse)
def get_bot_config(user=Depends(get_current_user), db: Session = Depends(get_db)):
    config = db.query(BotConfig).first()
    if not config:
        config = BotConfig()
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


@router.patch("/config", response_model=BotConfigResponse)
def update_bot_config(data: BotConfigUpdate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    config = db.query(BotConfig).first()
    if not config:
        config = BotConfig()
        db.add(config)
    
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(config, field, value)
    
    db.commit()
    db.refresh(config)
    return config


@router.post("/start")
def start_bot(user=Depends(get_current_user), db: Session = Depends(get_db)):
    config = db.query(BotConfig).first()
    if not config:
        config = BotConfig()
        db.add(config)
    
    # Verify active profile exists and is enabled
    if not config.active_profile_id:
        raise HTTPException(400, "No active profile selected")
    
    profile = db.query(StrategyProfile).filter(
        StrategyProfile.id == config.active_profile_id,
        StrategyProfile.enabled == True
    ).first()
    if not profile:
        raise HTTPException(400, "Active profile not found or not enabled")
    
    config.is_running = True
    db.commit()
    return {"status": "started", "is_running": True}


@router.post("/stop")
def stop_bot(user=Depends(get_current_user), db: Session = Depends(get_db)):
    config = db.query(BotConfig).first()
    if not config:
        config = BotConfig()
        db.add(config)
    config.is_running = False
    db.commit()
    return {"status": "stopped", "is_running": False}


@router.post("/pause")
def pause_bot(user=Depends(get_current_user), db: Session = Depends(get_db)):
    # Same as stop for now
    config = db.query(BotConfig).first()
    if not config:
        config = BotConfig()
        db.add(config)
    config.is_running = False
    db.commit()
    return {"status": "paused", "is_running": False}


@router.post("/kill-switch")
async def kill_switch(user=Depends(get_current_user), db: Session = Depends(get_db)):
    # Get credentials
    from app.models import ApiCredentials
    from app.security import decrypt
    from app.alpaca_client import AlpacaClient
    
    creds = db.query(ApiCredentials).first()
    if not creds:
        raise HTTPException(400, "No credentials stored")
    
    client = AlpacaClient(decrypt(creds.key_id_encrypted), decrypt(creds.secret_key_encrypted))
    
    try:
        # Cancel all open orders
        await client.cancel_all_orders()
        # Close all positions
        await client.close_all_positions()
        
        # Stop bot
        config = db.query(BotConfig).first()
        if config:
            config.is_running = False
            db.commit()
        
        return {"status": "kill_switch_activated", "message": "All orders canceled, all positions closed"}
    except Exception as e:
        raise HTTPException(500, f"Kill switch failed: {e}")