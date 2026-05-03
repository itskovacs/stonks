"""
models.py
=========
ORM table models — persistence only.
API request/response shapes live in models/schemas.py.

Design principles
-----------------
• Every user-owned table has: user FK → user.username (CASCADE)
• Transaction references its envelope via envelope_id FK only.
  There is no denormalized envelope_name column — names are resolved
  via the relationship at read time (always loaded with selectinload).
  This eliminates the two-source-of-truth problem and makes envelope
  renames a single-row UPDATE with no cascades required.
• cash_available on Envelope is a maintained running balance updated
  atomically inside the same DB transaction as every write. It is the
  single authoritative cash figure — the dashboard reads it directly.
• Naming convention applied to SQLModel.metadata for deterministic migrations.
"""

from datetime import UTC, datetime

from sqlalchemy import Index, MetaData, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from models.schemas import TransactionType

_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SQLModel.metadata = MetaData(naming_convention=_convention)


# =============================================================================
# USER
# =============================================================================


class User(SQLModel, table=True):
    """
    Auth entity. username is the natural PK — short, human-readable, immutable.
    All owned rows cascade-delete when the user is deleted.
    """

    username: str = Field(primary_key=True, max_length=50)
    hashed_password: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    watchlist_items: list["WatchlistItem"] = Relationship(
        back_populates="owner", cascade_delete=True
    )
    envelopes: list["Envelope"] = Relationship(
        back_populates="owner", cascade_delete=True
    )
    transactions: list["Transaction"] = Relationship(
        back_populates="owner", cascade_delete=True
    )


# =============================================================================
# WATCHLIST ITEM
# =============================================================================


class WatchlistItem(SQLModel, table=True):
    """One row per (user, ticker) pair. Duplicate tickers rejected at DB level."""

    __tablename__ = "watchlist_item"
    __table_args__ = (
        UniqueConstraint("user", "ticker", name="uq_watchlist_item_user_ticker"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user: str = Field(foreign_key="user.username", ondelete="CASCADE", index=True)
    ticker: str = Field(max_length=20)
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    owner: User | None = Relationship(back_populates="watchlist_items")


# =============================================================================
# ENVELOPE  (portfolio container)
# =============================================================================


class Envelope(SQLModel, table=True):
    """
    Named portfolio container owned by a user.

    cash_available is a maintained running balance:
      DEPOSIT  → +amount
      WITHDRAW → -amount
      BUY      → -total
      SELL     → +total (after fees)
      DIVIDEND → +total

    Updated atomically inside the same session.commit() as the Transaction
    insert/delete, so it is always consistent with the ledger.
    No recomputation from scratch is needed at read time.
    """

    __table_args__ = (
        UniqueConstraint("user", "name", name="uq_envelope_user_name"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user: str = Field(foreign_key="user.username", ondelete="CASCADE", index=True)
    name: str = Field(max_length=100)
    color: str = Field(default="#fafafa", max_length=20)
    cash_available: float = Field(default=0.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    owner: User | None = Relationship(back_populates="envelopes")
    transactions: list["Transaction"] = Relationship(
        back_populates="envelope", cascade_delete=True
    )


# =============================================================================
# TRANSACTION
# =============================================================================


class Transaction(SQLModel, table=True):
    """
    Immutable financial ledger row.

    envelope_name is NOT stored here — it is resolved via the envelope
    relationship at read time.  This means renaming an envelope is a
    single UPDATE on the envelope row; all transactions automatically
    reflect the new name through the FK join.

    total is pre-computed by the router and never recalculated after write.
    Price and fee history may change, but the executed trade value is fixed.

    date defaults to UTC now if not provided, but callers should pass the
    actual trade date for backfilled transactions.

    Indexed on (user, date) for fast per-user chronological queries.
    """

    __table_args__ = (Index("idx_transaction_user_date", "user", "date"),)

    id: int | None = Field(default=None, primary_key=True)
    user: str = Field(foreign_key="user.username", ondelete="CASCADE", index=True)
    envelope_id: int = Field(foreign_key="envelope.id", ondelete="CASCADE", index=True)

    date: datetime = Field(
        default_factory=lambda: datetime.now(UTC), index=True
    )
    type: TransactionType
    ticker: str | None = Field(default=None, max_length=20)
    shares: float = Field(default=0.0, ge=0)
    price: float = Field(ge=0)
    fees: float = Field(default=0.0, ge=0)
    total: float
    note: str | None = Field(default=None, max_length=500)

    owner: User | None = Relationship(back_populates="transactions")
    envelope: Envelope | None = Relationship(back_populates="transactions")
