from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import StrategyProfile, BotConfig
from app.schemas import StrategyProfileCreate, StrategyProfileUpdate, StrategyProfileResponse
from app.authz import SCOPE_CONFIG_WRITE, SCOPE_READ, require_scope

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.get("", response_model=list[StrategyProfileResponse])
def list_profiles(user=Depends(require_scope(SCOPE_READ)), db: Session = Depends(get_db)):
    return db.query(StrategyProfile).all()


@router.post("", response_model=StrategyProfileResponse)
def create_profile(data: StrategyProfileCreate, user=Depends(require_scope(SCOPE_CONFIG_WRITE)), db: Session = Depends(get_db)):
    profile = StrategyProfile(**data.model_dump())
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/{profile_id}", response_model=StrategyProfileResponse)
def get_profile(profile_id: int, user=Depends(require_scope(SCOPE_READ)), db: Session = Depends(get_db)):
    profile = db.query(StrategyProfile).filter(StrategyProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, "Profile not found")
    return profile


@router.patch("/{profile_id}", response_model=StrategyProfileResponse)
def update_profile(profile_id: int, data: StrategyProfileUpdate, user=Depends(require_scope(SCOPE_CONFIG_WRITE)), db: Session = Depends(get_db)):
    profile = db.query(StrategyProfile).filter(StrategyProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, "Profile not found")
    
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    
    db.commit()
    db.refresh(profile)
    return profile


@router.delete("/{profile_id}")
def delete_profile(profile_id: int, user=Depends(require_scope(SCOPE_CONFIG_WRITE)), db: Session = Depends(get_db)):
    profile = db.query(StrategyProfile).filter(StrategyProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, "Profile not found")
    db.delete(profile)
    db.commit()
    return {"status": "deleted"}


@router.post("/{profile_id}/activate", response_model=StrategyProfileResponse)
def activate_profile(profile_id: int, user=Depends(require_scope(SCOPE_CONFIG_WRITE)), db: Session = Depends(get_db)):
    # Look the profile up BEFORE mutating anything. Disabling the other
    # profiles and re-pointing the bot first would leave a dangling
    # active_profile_id (and no enabled profile at all) if the id is bad.
    profile = db.query(StrategyProfile).filter(StrategyProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, "Profile not found")

    db.query(StrategyProfile).update({StrategyProfile.enabled: False})
    profile.enabled = True

    # `enabled` alone does not decide what the bot trades. The worker resolves
    # the profile through bot_config.active_profile_id:
    #
    #     StrategyProfile.id == bot_config.active_profile_id,
    #     StrategyProfile.enabled == True
    #
    # so flipping `enabled` without re-pointing the bot leaves the UI calling
    # one profile Active while the cycle keeps trading another. Both fields are
    # written together here, which is also what POST /bot/start validates.
    config = db.query(BotConfig).first()
    if not config:
        config = BotConfig()
        db.add(config)
    config.active_profile_id = profile.id

    db.commit()
    db.refresh(profile)
    return profile