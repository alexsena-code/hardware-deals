from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, Text, Index
from sqlalchemy.orm import Mapped, mapped_column
from models.database import Base


class Deal(Base):
    __tablename__ = "deals"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20))  # olx, ebay
    external_id: Mapped[str] = mapped_column(String(100))
    item_name: Mapped[str] = mapped_column(String(100))  # matched config item
    title: Mapped[str] = mapped_column(String(500))
    price: Mapped[float] = mapped_column(Float)
    url: Mapped[str] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(20))  # gpu, cpu-kit, ram
    found_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (
        Index("ix_deals_source_external", "source", "external_id", unique=True),
        Index("ix_deals_item_name", "item_name"),
        Index("ix_deals_price", "price"),
    )


class PriceHistory(Base):
    """Track average prices over time for each item."""
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_name: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(20))
    avg_price: Mapped[float] = mapped_column(Float)
    min_price: Mapped[float] = mapped_column(Float)
    max_price: Mapped[float] = mapped_column(Float)
    deal_count: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_price_history_item", "item_name", "recorded_at"),
    )


class ManualPrice(Base):
    """Prices filled in manually (new prices, AliExpress reference, etc)."""
    __tablename__ = "manual_prices"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_name: Mapped[str] = mapped_column(String(100), unique=True)
    price_new: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_aliexpress: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_reference: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
