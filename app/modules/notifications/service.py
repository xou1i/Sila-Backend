"""In-app notifications (the topbar bell). No e-mail or push (decision 2026-10-09).

notify() joins the caller's DB transaction, so a notification exists only if the change it
announces was committed.
"""

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Notification, User, UserRole

MAX_PAGE = 50


class NotificationOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    body: str
    link: str | None
    read: bool
    created_at: datetime


class NotificationsOut(BaseModel):
    items: list[NotificationOut]
    unread_count: int


class MarkReadIn(BaseModel):
    # Empty or missing = mark every notification as read
    ids: list[uuid.UUID] | None = None


def notify(
    db: Session, user_id: uuid.UUID, kind: str, title: str, body: str, link: str | None = None
) -> None:
    db.add(Notification(user_id=user_id, kind=kind, title=title, body=body, link=link))


def notify_admins(db: Session, kind: str, title: str, body: str, link: str | None = None) -> None:
    admin_ids = db.scalars(
        select(User.id).where(User.role == UserRole.admin, User.is_active.is_(True))
    ).all()
    for admin_id in admin_ids:
        notify(db, admin_id, kind, title, body, link)


def _out(n: Notification) -> NotificationOut:
    return NotificationOut(
        id=n.id,
        kind=n.kind,
        title=n.title,
        body=n.body,
        link=n.link,
        read=n.read_at is not None,
        created_at=n.created_at,
    )


def list_for(db: Session, user: User, limit: int) -> NotificationsOut:
    rows = db.scalars(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc(), Notification.id)
        .limit(min(limit, MAX_PAGE))
    ).all()
    unread = db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
    )
    return NotificationsOut(items=[_out(n) for n in rows], unread_count=unread or 0)


def mark_read(db: Session, user: User, ids: list[uuid.UUID] | None) -> NotificationsOut:
    query = update(Notification).where(
        Notification.user_id == user.id, Notification.read_at.is_(None)
    )
    if ids:
        query = query.where(Notification.id.in_(ids))
    db.execute(query.values(read_at=datetime.now(UTC)))
    db.commit()
    return list_for(db, user, 20)
