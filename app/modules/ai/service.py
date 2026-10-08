"""AI Engine: smart matching (free), risk analysis, premium insights (DECISIONS D-21).

A deterministic rule-based engine computes everything. The optional LLM (llm.py) only rewrites
the explanation text.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.money import (
    compute_breakdown,
    format_iqd,
    karat_price,
    max_affordable_grams,
    round_money,
)
from app.models import (
    AssetListing,
    FractionalOwnershipRecord,
    ListingStatus,
    PriceSnapshot,
    RiskProfile,
    Transaction,
    User,
)
from app.modules.ai import llm
from app.modules.ai.schemas import (
    InsightsOut,
    KaratHolding,
    MarketTrend,
    MatchOut,
    MatchResult,
    PortfolioPerformance,
    RiskAnalysisOut,
)
from app.modules.listings import service as listings
from app.modules.market import service as market
from app.modules.orders.schemas import RiskInsightOut

_MAX_RESULTS = 5
_RISK_AR = {RiskProfile.low: "منخفض", RiskProfile.medium: "متوسط", RiskProfile.high: "مرتفع"}
# Karat preference per risk profile (0..1): conservative → purest, adventurous → more grams.
_KARAT_PREF: dict[RiskProfile, dict[int, Decimal]] = {
    RiskProfile.low: {24: Decimal("1"), 22: Decimal("0.8"), 21: Decimal("0.5"), 18: Decimal("0.2")},
    RiskProfile.medium: {
        22: Decimal("1"),
        21: Decimal("0.9"),
        24: Decimal("0.8"),
        18: Decimal("0.5"),
    },
    RiskProfile.high: {
        18: Decimal("1"),
        21: Decimal("0.9"),
        22: Decimal("0.7"),
        24: Decimal("0.5"),
    },
}
_KARAT_NOTE = {
    24: "عيار 24 الأنقى والأسهل تسييلاً",
    22: "عيار 22 توازن بين النقاء والسعر",
    21: "عيار 21 الأكثر تداولاً بالسوق العراقي",
    18: "عيار 18 يعطيك غرامات أكثر بنفس الميزانية",
}


def _snapshot_or_unavailable(db: Session) -> PriceSnapshot:
    snapshot = market.latest_snapshot(db)
    if snapshot is None:
        raise AppError(ErrorCode.AI_UNAVAILABLE)
    return snapshot


# ---------------------------------------------------------------- smart matching


def match(db: Session, investor: User, budget: Decimal) -> MatchOut:
    snapshot = _snapshot_or_unavailable(db)
    risk = investor.risk_profile or RiskProfile.medium
    min_grams = get_settings().ai_match_min_grams
    now = datetime.now(UTC)
    active = db.scalars(
        select(AssetListing)
        .options(joinedload(AssetListing.seller))
        .where(AssetListing.status == ListingStatus.active, AssetListing.available_weight_grams > 0)
    ).all()

    scored: list[tuple[Decimal, AssetListing, Decimal, Decimal, str]] = []
    for listing in active:
        price = karat_price(snapshot.gold_24k_iqd_per_gram, listing.karat)
        grams = min(max_affordable_grams(budget, price), listing.available_weight_grams)
        if grams < min_grams:
            continue
        total = compute_breakdown(grams, price).total_paid_by_investor
        usage = total / budget
        score = (
            Decimal("0.55") * usage
            + Decimal("0.40") * _KARAT_PREF[risk][listing.karat]
            + (Decimal("0.05") if listing.seller.kyc_verified else Decimal("0"))
        ).quantize(Decimal("0.0001"))
        pct = round_money(usage * 100)
        capped = grams == listing.available_weight_grams
        reason = f"{_KARAT_NOTE[listing.karat]} ويناسب ملف مخاطرة {_RISK_AR[risk]}. " + (
            f"الكمية المتاحة كاملة ({grams} غرام) ضمن ميزانيتك بإجمالي {format_iqd(total)} دينار."
            if capped
            else f"يمكنك شراء {grams} غرام بإجمالي {format_iqd(total)} دينار ({pct}% من ميزانيتك)."
        )
        scored.append((score, listing, grams, price, reason))

    scored.sort(key=lambda s: (s[0], s[1].created_at), reverse=True)
    top = scored[:_MAX_RESULTS]
    if not top:
        return MatchOut(
            budget_iqd=budget,
            risk_profile=investor.risk_profile,
            engine="rules",
            message="ماكو عروض تناسب هذي الميزانية حالياً",
            results=[],
        )

    reasons = [s[4] for s in top]
    rewritten = llm.rewrite_many(reasons)
    engine = "llm" if rewritten else "rules"
    reasons = rewritten or reasons
    price_24k = snapshot.gold_24k_iqd_per_gram
    results = []
    for rank, ((score, listing, grams, price, _), reason) in enumerate(
        zip(top, reasons, strict=True), 1
    ):
        breakdown = compute_breakdown(grams, price)
        results.append(
            MatchResult(
                rank=rank,
                listing=listings.to_out(listing, price_24k, now),
                suggested_weight_grams=grams,
                execution_price_per_gram=price,
                estimated_total_iqd=breakdown.total_paid_by_investor,
                commission_rate=breakdown.commission_rate,
                budget_usage_pct=round_money(breakdown.total_paid_by_investor / budget * 100),
                score=score,
                reason=reason,
            )
        )
    return MatchOut(
        budget_iqd=budget,
        risk_profile=investor.risk_profile,
        engine=engine,
        message=f"وجدنا {len(results)} عروض مناسبة لميزانيتك",
        results=results,
    )


# ---------------------------------------------------------------- risk analysis


def risk_insight_rules(
    db: Session,
    investor: User,
    listing: AssetListing,
    grams: Decimal,
    snapshot: PriceSnapshot,
) -> RiskInsightOut:
    """Instant rule-based insight (used inside preview; never raises for business reasons)."""
    signals: list[str] = []
    points = 0

    change_24h = market.change_since(db, snapshot, timedelta(hours=24))
    if change_24h is not None:
        if change_24h >= Decimal("2"):
            points += 1
            signals.append(f"السعر ارتفع {change_24h}% خلال 24 ساعة")
        elif change_24h <= Decimal("-2"):
            signals.append(f"السعر انخفض {abs(change_24h)}% خلال 24 ساعة، فرصة شراء محتملة")

    avg_7d, _, _ = market.stats_since(db, timedelta(days=7))
    if avg_7d:
        premium = market.pct_change(avg_7d, snapshot.gold_24k_iqd_per_gram)
        if premium is not None and premium >= Decimal("3"):
            points += 1
            signals.append(f"السعر الحالي أعلى من متوسط الأسبوع بـ {premium}%")
        elif premium is not None and premium <= Decimal("-3"):
            signals.append(f"السعر الحالي أقل من متوسط الأسبوع بـ {abs(premium)}%")

    if grams > Decimal("200"):
        points += 1
        signals.append("صفقة كبيرة: فكّر بتقسيم الشراء على أكثر من مرة")

    holdings = db.scalar(
        select(FractionalOwnershipRecord.total_accumulated_grams).where(
            FractionalOwnershipRecord.investor_id == investor.id
        )
    ) or Decimal("0")
    if holdings > 0 and grams > holdings:
        signals.append("هذه الصفقة أكبر من رصيدك الحالي بالكامل")

    if investor.risk_profile == RiskProfile.low and listing.karat == 18:
        points += 1
        signals.append("عيار 18 أقل نقاءً مما يناسب ملف المخاطرة المنخفض")

    if market.is_stale(snapshot):
        points += 1
        signals.append("آخر تحديث للسعر قديم نسبياً")

    level = "low" if points == 0 else "medium" if points == 1 else "high"
    headline = {
        "low": "مخاطر هذه الصفقة منخفضة حسب بيانات السوق الحالية.",
        "medium": "مخاطر هذه الصفقة متوسطة، راجع الملاحظات قبل التأكيد.",
        "high": "مخاطر هذه الصفقة مرتفعة نسبياً، تأنَّ قبل التأكيد.",
    }[level]
    insight = headline + (f" {signals[0]}." if signals else " السعر مستقر مقارنة بالأيام الماضية.")
    return RiskInsightOut(level=level, insight=insight, signals=signals, engine="rules")


def risk_analysis(
    db: Session, investor: User, asset_id: uuid.UUID, grams: Decimal
) -> RiskAnalysisOut:
    listing = listings.get_listing(db, asset_id)
    snapshot = _snapshot_or_unavailable(db)
    result = risk_insight_rules(db, investor, listing, grams, snapshot)
    rewritten = llm.rewrite(result.insight)
    return RiskAnalysisOut(
        **result.model_dump(exclude={"insight", "engine"}),
        insight=rewritten or result.insight,
        engine="llm" if rewritten else "rules",
        asset_id=asset_id,
        purchased_weight_grams=grams,
    )


# ---------------------------------------------------------------- premium insights


def insights(db: Session, investor: User) -> InsightsOut:
    now = datetime.now(UTC)
    # The expiry date decides, never the tier field alone (Workflow 07 §3).
    if investor.subscription_expiry_date is None or investor.subscription_expiry_date <= now:
        raise AppError(ErrorCode.SUBSCRIPTION_REQUIRED)
    snapshot = _snapshot_or_unavailable(db)
    price_24k = snapshot.gold_24k_iqd_per_gram

    change_24h = market.change_since(db, snapshot, timedelta(hours=24))
    change_7d = market.change_since(db, snapshot, timedelta(days=7))
    change_30d = market.change_since(db, snapshot, timedelta(days=30))
    _, low_7d, high_7d = market.stats_since(db, timedelta(days=7))
    trend = "flat"
    if change_7d is not None and change_7d > Decimal("1"):
        trend = "up"
    elif change_7d is not None and change_7d < Decimal("-1"):
        trend = "down"
    trend_ar = {"up": "صاعد", "down": "هابط", "flat": "مستقر"}[trend]
    summary = f"اتجاه الذهب خلال الأسبوع {trend_ar}" + (
        f" ({change_7d}%)." if change_7d is not None else "."
    )

    rows = db.execute(
        select(
            AssetListing.karat,
            func.sum(Transaction.purchased_weight_grams),
            func.sum(Transaction.total_paid_by_investor),
            func.count(Transaction.id),
        )
        .join(AssetListing, Transaction.asset_id == AssetListing.id)
        .where(Transaction.investor_id == investor.id)
        .group_by(AssetListing.karat)
        .order_by(AssetListing.karat.desc())
    ).all()
    by_karat = [
        KaratHolding(karat=k, grams=g, current_value_iqd=round_money(g * karat_price(price_24k, k)))
        for k, g, _, _ in rows
    ]
    total_grams = sum((h.grams for h in by_karat), Decimal("0"))
    total_paid = sum((r[2] for r in rows), Decimal("0"))
    value = sum((h.current_value_iqd for h in by_karat), Decimal("0"))
    pnl = value - total_paid
    pnl_pct = market.pct_change(total_paid, value) if total_paid > 0 else None

    alerts: list[str] = []
    if change_24h is not None and change_24h <= Decimal("-1"):
        alerts.append(f"تنبيه: الذهب انخفض {abs(change_24h)}% خلال 24 ساعة، قد تكون فرصة شراء.")
    if change_24h is not None and change_24h >= Decimal("1"):
        alerts.append(f"تنبيه: الذهب ارتفع {change_24h}% خلال 24 ساعة.")
    if high_7d is not None and price_24k >= high_7d:
        alerts.append("السعر عند أعلى مستوى له خلال الأسبوع.")
    if low_7d is not None and price_24k <= low_7d:
        alerts.append("السعر عند أدنى مستوى له خلال الأسبوع.")
    if pnl_pct is not None:
        word = "ربح" if pnl >= 0 else "خسارة"
        alerts.append(f"محفظتك بحالة {word} غير محقق بنسبة {abs(pnl_pct)}%.")
    if not rows:
        alerts.append("ما عندك صفقات بعد، ابدأ أول استثمار لتظهر تقارير الأداء.")
    if market.is_stale(snapshot):
        alerts.append("تنبيه: آخر تحديث للأسعار قديم نسبياً.")

    rewritten = llm.rewrite(summary)
    return InsightsOut(
        engine="llm" if rewritten else "rules",
        alerts=alerts,
        market=MarketTrend(
            price_24k_per_gram=price_24k,
            change_24h_pct=change_24h,
            change_7d_pct=change_7d,
            change_30d_pct=change_30d,
            high_7d=high_7d,
            low_7d=low_7d,
            trend=trend,
            summary=rewritten or summary,
        ),
        portfolio=PortfolioPerformance(
            total_grams=total_grams,
            total_paid_iqd=total_paid,
            current_value_iqd=value,
            unrealized_pnl_iqd=pnl,
            unrealized_pnl_pct=pnl_pct,
            transactions_count=sum(r[3] for r in rows),
            by_karat=by_karat,
        ),
    )
