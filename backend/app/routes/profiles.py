from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import StrategyProfile
from app.schemas import StrategyProfileCreate, StrategyProfileUpdate, StrategyProfileResponse
from app.routes.auth import get_current_user

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


@router.get("", response_model=list[StrategyProfileResponse])
def list_profiles(user=Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(StrategyProfile).all()


@router.post("", response_model=StrategyProfileResponse)
def create_profile(data: StrategyProfileCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    profile = StrategyProfile(**data.model_dump())
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/{profile_id}", response_model=StrategyProfileResponse)
def get_profile(profile_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.query(StrategyProfile).filter(StrategyProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, "Profile not found")
    return profile


@router.patch("/{profile_id}", response_model=StrategyProfileResponse)
def update_profile(profile_id: int, data: StrategyProfileUpdate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.query(StrategyProfile).filter(StrategyProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, "Profile not found")
    
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    
    db.commit()
    db.refresh(profile)
    return profile


@router.delete("/{profile_id}")
def delete_profile(profile_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.query(StrategyProfile).filter(StrategyProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, "Profile not found")
    db.delete(profile)
    db.commit()
    return {"status": "deleted"}


@router.post("/{profile_id}/activate", response_model=StrategyProfileResponse)
def activate_profile(profile_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    # Disable all profiles
    db.query(StrategyProfile).update({StrategyProfile.enabled: False})
    
    # Enable the selected one
    profile = db.query(StrategyProfile).filter(StrategyProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, "Profile not found")
    
    profile.enabled = True
    db.commit()
    db.refresh(profile)
    return profile