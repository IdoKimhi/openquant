from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import BotConfig, StrategyProfile
from app.schemas import BotConfigResponse, BotConfigUpdate
from app.authz import (
    SCOPE_BOT_CONTROL,
    SCOPE_BOT_KILL,
    SCOPE_CONFIG_WRITE,
    SCOPE_READ,
    require_scope,
)

router = APIRouter(prefix="/bot", tags=["bot"])


@router.get("/config", response_model=BotConfigResponse)
def get_bot_config(user=Depends(require_scope(SCOPE_READ)), db: Session = Depends(get_db)):
    config = db.query(BotConfig).first()
    if not config:
        config = BotConfig()
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


@router.patch("/config", response_model=BotConfigResponse)
def update_bot_config(data: BotConfigUpdate, user=Depends(require_scope(SCOPE_CONFIG_WRITE)), db: Session = Depends(get_db)):
    config = db.query(BotConfig).first()
    if not config:
        config = BotConfig()
        db.add(config)
    
    updates = data.model_dump(exclude_unset=True)

    # Validate before mutating anything, so a bad id cannot half-apply.
    if "active_profile_id" in updates and updates["active_profile_id"]:
        if not db.query(StrategyProfile).filter(
            StrategyProfile.id == updates["active_profile_id"]
        ).first():
            raise HTTPException(400, "Profile not found")

    for field, value in updates.items():
        setattr(config, field, value)

    # Repointing the bot at a different profile has to move the `enabled` flag
    # with it. The worker resolves the profile through BOTH conditions:
    #
    #     StrategyProfile.id == config.active_profile_id,
    #     StrategyProfile.enabled == True
    #
    # Patching active_profile_id on its own left `enabled` on the old profile,
    # so the lookup matched nothing and every cycle bailed with "Active profile
    # not found or disabled, skipping cycle" - a silent trading stop with no
    # error anywhere in the UI. Clearing it disables the rest for the same
    # reason: an enabled profile with nothing pointing at it is misleading.
    if "active_profile_id" in updates:
        db.query(StrategyProfile).update({StrategyProfile.enabled: False})
        if config.active_profile_id:
            db.query(StrategyProfile).filter(
                StrategyProfile.id == config.active_profile_id
            ).update({StrategyProfile.enabled: True})

    db.commit()
    db.refresh(config)
    return config


@router.post("/start")
def start_bot(user=Depends(require_scope(SCOPE_BOT_CONTROL)), db: Session = Depends(get_db)):
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
def stop_bot(user=Depends(require_scope(SCOPE_BOT_CONTROL)), db: Session = Depends(get_db)):
    config = db.query(BotConfig).first()
    if not config:
        config = BotConfig()
        db.add(config)
    config.is_running = False
    db.commit()
    return {"status": "stopped", "is_running": False}


@router.post("/pause")
def pause_bot(user=Depends(require_scope(SCOPE_BOT_CONTROL)), db: Session = Depends(get_db)):
    # Same as stop for now
    config = db.query(BotConfig).first()
    if not config:
        config = BotConfig()
        db.add(config)
    config.is_running = False
    db.commit()
    return {"status": "paused", "is_running": False}


@router.post("/kill-switch")
async def kill_switch(user=Depends(require_scope(SCOPE_BOT_KILL)), db: Session = Depends(get_db)):
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