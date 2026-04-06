from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, Text, Index, JSON
from sqlalchemy.orm import Mapped, mapped_column
from models.database import Base


class OlxCategory(Base):
    """OLX search categories — managed via API, seeded from config."""
    __tablename__ = "olx_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(200), unique=True)
    label: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(default=True)


class Proxy(Base):
    """Proxy pool — managed via API, seeded from .env on first run."""
    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(Text, unique=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(200), nullable=True)


class SearchItem(Base):
    """Items to search for — managed via API, seeded from config.yaml."""
    __tablename__ = "search_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    max_price: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(20))
    specs: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(default=True)
    scrape_enabled: Mapped[bool] = mapped_column(default=True)


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
