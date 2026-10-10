"""integrated platform: investor resale, admin, notifications, price alerts, interest

Additive only: existing rows keep their meaning (every current listing is a seller listing,
every user stays active).

Revision ID: 7c1d5e2a9b40
Revises: 44a22f3b90df
Create Date: 2026-10-10 09:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "7c1d5e2a9b40"
down_revision: str | Sequence[str] | None = "44a22f3b90df"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    # New values on existing enum types (PostgreSQL >= 12 allows this inside a transaction;
    # the values are not used before the migration commits)
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'admin'")
    op.execute("ALTER TYPE listing_status ADD VALUE IF NOT EXISTS 'withdrawn'")

    bind = op.get_bind()
    for name, values in (
        ("listing_type", ("seller_listing", "investor_resale")),
        ("alert_direction", ("above", "below")),
        ("alert_status", ("active", "triggered", "cancelled")),
        ("reset_request_status", ("pending", "resolved", "dismissed")),
    ):
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    # users
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column(
        "users",
        sa.Column(
            "must_change_password", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.add_column(
        "users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True)
    )

    # asset_listings
    op.add_column(
        "asset_listings",
        sa.Column(
            "listing_type",
            _enum("listing_type", "seller_listing", "investor_resale"),
            server_default="seller_listing",
            nullable=False,
        ),
    )
    op.create_index(
        "idx_listings_type_seller", "asset_listings", ["listing_type", "seller_id"], unique=False
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.String(length=1000), nullable=False),
        sa.Column("link", sa.String(length=200), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_notifications_user_created", "notifications", ["user_id", "created_at"], unique=False
    )

    op.create_table(
        "price_alerts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("karat", sa.SmallInteger(), nullable=False),
        sa.Column("direction", _enum("alert_direction", "above", "below"), nullable=False),
        sa.Column("target_price_per_gram", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "status",
            _enum("alert_status", "active", "triggered", "cancelled"),
            server_default="active",
            nullable=False,
        ),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("karat IN (18, 21, 22, 24)", name="ck_alerts_karat"),
        sa.CheckConstraint("target_price_per_gram > 0", name="ck_alerts_target_positive"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_alerts_user", "price_alerts", ["user_id"], unique=False)
    op.create_index("idx_alerts_status", "price_alerts", ["status"], unique=False)

    op.create_table(
        "password_reset_requests",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            _enum("reset_request_status", "pending", "resolved", "dismissed"),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_reset_requests_status",
        "password_reset_requests",
        ["status", "created_at"],
        unique=False,
    )

    op.create_table(
        "interest_signups",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("asset_class", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("asset_class IN ('real_estate', 'oil')", name="ck_interest_asset_class"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_interest_email_asset", "interest_signups", ["email", "asset_class"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_interest_email_asset", table_name="interest_signups")
    op.drop_table("interest_signups")
    op.drop_index("idx_reset_requests_status", table_name="password_reset_requests")
    op.drop_table("password_reset_requests")
    op.drop_index("idx_alerts_status", table_name="price_alerts")
    op.drop_index("idx_alerts_user", table_name="price_alerts")
    op.drop_table("price_alerts")
    op.drop_index("idx_notifications_user_created", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("idx_listings_type_seller", table_name="asset_listings")
    op.drop_column("asset_listings", "listing_type")
    op.drop_column("users", "password_changed_at")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "is_active")
    for name in ("reset_request_status", "alert_status", "alert_direction", "listing_type"):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
    # PostgreSQL cannot drop a value from an enum type: 'admin' and 'withdrawn' stay defined
