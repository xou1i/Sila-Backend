from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import require_investor
from app.core.errors import error_responses
from app.core.rate_limit import rate_limit
from app.models import User
from app.modules.ai import advisor, service
from app.modules.ai.schemas import (
    AdvisorIn,
    AdvisorOut,
    InsightsOut,
    MatchIn,
    MatchOut,
    RiskAnalysisIn,
    RiskAnalysisOut,
)

router = APIRouter(prefix="/api/ai", tags=["AI Engine"], dependencies=[rate_limit("ai")])


@router.post(
    "/match",
    response_model=MatchOut,
    responses=error_responses(401, 403, 422, 429, 503),
    summary="Smart matching (free): budget → ranked listings; risk profile read from account",
)
def smart_match(
    body: MatchIn, user: User = Depends(require_investor), db: Session = Depends(get_db)
) -> MatchOut:
    return service.match(db, user, body.budget_iqd)


@router.post(
    "/risk-analysis",
    response_model=RiskAnalysisOut,
    responses=error_responses(401, 403, 404, 422, 429, 503),
    summary="Pre-checkout risk insight (never blocks checkout)",
)
def risk_analysis(
    body: RiskAnalysisIn, user: User = Depends(require_investor), db: Session = Depends(get_db)
) -> RiskAnalysisOut:
    return service.risk_analysis(db, user, body.asset_id, body.weight_grams)


@router.get(
    "/insights",
    response_model=InsightsOut,
    responses=error_responses(401, 403, 429, 503),
    summary="Premium insights: alerts, market trend, portfolio performance",
)
def premium_insights(
    user: User = Depends(require_investor), db: Session = Depends(get_db)
) -> InsightsOut:
    return service.insights(db, user)


@router.post(
    "/advisor",
    response_model=AdvisorOut,
    responses=error_responses(401, 403, 422, 429, 503),
    summary="AI Advisor (free): an Arabic answer to a question + offers from the matcher",
)
def ai_advisor(
    body: AdvisorIn, user: User = Depends(require_investor), db: Session = Depends(get_db)
) -> AdvisorOut:
    return advisor.advise(db, user, body)
