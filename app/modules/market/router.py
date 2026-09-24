from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import error_responses
from app.core.schemas import KaratParam
from app.modules.market import service
from app.modules.market.schemas import HistoryRange, MarketPricesOut, PriceHistoryOut

router = APIRouter(prefix="/api/market", tags=["Market Data"])


@router.get(
    "/prices",
    response_model=MarketPricesOut,
    responses=error_responses(503),
    summary="Latest gold prices (all karats) + USD/IQD, served from the internal cache",
)
def prices(db: Session = Depends(get_db)) -> MarketPricesOut:
    return service.market_prices(db)


@router.get(
    "/prices/history",
    response_model=PriceHistoryOut,
    responses=error_responses(422),
    summary="Downsampled price history for charts",
)
def price_history(
    karat: KaratParam = Query(24),
    range_: HistoryRange = Query("1D", alias="range"),
    db: Session = Depends(get_db),
) -> PriceHistoryOut:
    return service.price_history(db, karat, range_)
