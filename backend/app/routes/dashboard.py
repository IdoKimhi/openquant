from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import TradeLog, EquitySnapshot, ApiCredentials, BotConfig
from app.schemas import (
    AccountResponse, PositionResponse, OrderResponse, EquityPoint,
    TradeLogResponse, MarketClockResponse
)
from app.alpaca_client import AlpacaClient
from app.security import decrypt
from app.routes.auth import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def get_alpaca_client(db: Session) -> AlpacaClient | None:
    creds = db.query(ApiCredentials).first()
    if not creds:
        return None
    return AlpacaClient(decrypt(creds.key_id_encrypted), decrypt(creds.secret_key_encrypted))


@router.get("/account", response_model=AccountResponse)
async def get_account(user=Depends(get_current_user), db: Session = Depends(get_db)):
    client = get_alpaca_client(db)
    if not client:
        raise HTTPException(400, "No credentials stored")
    
    account = await client.get_account()
    day_pl = await client.get_day_pl()
    
    return {
        "equity": float(account.equity),
        "portfolio_value": float(account.portfolio_value),
        "cash": float(account.cash),
        "buying_power": float(account.buying_power),
        "day_pl": day_pl,
        "total_pl": float(account.equity) - float(account.last_equity),
        "status": account.status
    }


@router.get("/positions", response_model=list[PositionResponse])
async def get_positions(user=Depends(get_current_user), db: Session = Depends(get_db)):
    client = get_alpaca_client(db)
    if not client:
        raise HTTPException(400, "No credentials stored")
    
    positions = await client.get_positions()
    result = []
    for pos in positions:
        result.append({
            "symbol": pos.symbol,
            "qty": float(pos.qty),
            "avg_entry_price": float(pos.avg_entry_price),
            "market_value": float(pos.market_value),
            "cost_basis": float(pos.cost_basis),
            "current_price": float(pos.current_price),
            "unrealized_pl": float(pos.unrealized_pl),
            "unrealized_plpc": float(pos.unrealized_plpc),
            "side": pos.side.value
        })
    return result


@router.get("/orders", response_model=list[OrderResponse])
async def get_orders(user=Depends(get_current_user), db: Session = Depends(get_db)):
    client = get_alpaca_client(db)
    if not client:
        raise HTTPException(400, "No credentials stored")
    
    orders = await client.get_orders()
    result = []
    for order in orders:
        result.append({
            "id": order.id,
            "symbol": order.symbol,
            "qty": float(order.qty),
            "side": order.side.value,
            "order_type": order.order_type.value,
            "limit_price": float(order.limit_price) if order.limit_price else None,
            "status": order.status.value,
            "filled_price": float(order.filled_avg_price) if order.filled_avg_price else None,
            "filled_qty": float(order.filled_qty) if order.filled_qty else None,
            "submitted_at": order.submitted_at
        })
    return result


@router.get("/equity-curve", response_model=list[EquityPoint])
def get_equity_curve(user=Depends(get_current_user), db: Session = Depends(get_db)):
    # Get last 30 days of equity snapshots
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=30)
    snapshots = db.query(EquitySnapshot).filter(
        EquitySnapshot.timestamp >= cutoff
    ).order_by(EquitySnapshot.timestamp).all()
    
    return [
        {"timestamp": s.timestamp, "equity": s.equity}
        for s in snapshots
    ]


@router.get("/logs", response_model=list[TradeLogResponse])
def get_logs(
    user=Depends(get_current_user), 
    db: Session = Depends(get_db),
    limit: int = 100,
    profile_id: int | None = None
):
    query = db.query(TradeLog).order_by(TradeLog.timestamp.desc()).limit(limit)
    if profile_id:
        query = query.filter(TradeLog.profile_id == profile_id)
    return query.all()


@router.get("/market-clock", response_model=MarketClockResponse)
async def get_market_clock(user=Depends(get_current_user), db: Session = Depends(get_db)):
    client = get_alpaca_client(db)
    if not client:
        raise HTTPException(400, "No credentials stored")
    
    clock = await client.get_clock()
    return {
        "is_open": clock.is_open,
        "next_open": clock.next_open,
        "next_close": clock.next_close
    }