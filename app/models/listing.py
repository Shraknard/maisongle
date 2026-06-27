from sqlalchemy import (
    Column, String, Integer, Float, DateTime, Text, JSON, Boolean,
    ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Listing(Base):
    """A scraped property listing, normalized across all sources.

    Stable identity is ``(source, source_id)``. The normalized shape mirrors
    the ``Favorite`` model so favorites/enrichment work without translation.
    """
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_listing_source"),
        Index("ix_listing_search", "transaction_type", "property_type", "city_insee"),
        Index("ix_listing_dedup", "dedup_key"),
    )

    id = Column(Integer, primary_key=True, index=True)

    # Stable identity
    source = Column(String(32), nullable=False, index=True)  # 'bienici', 'pap', ...
    source_id = Column(String(255), nullable=False)

    # Basic property info (same shape as Favorite)
    title = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    price = Column(Float, nullable=True)
    price_per_meter = Column(Float, nullable=True)
    surface = Column(Float, nullable=True)
    land_surface = Column(Float, nullable=True)
    room = Column(Integer, nullable=True)
    bedroom = Column(Integer, nullable=True)
    floor = Column(Integer, nullable=True)

    property_type = Column(Integer, nullable=True)     # 0=apartment, 1=house, ...
    transaction_type = Column(Integer, nullable=True)  # 0=sale, 1=rent

    # Location
    city_name = Column(String(255), nullable=True)
    city_zipcode = Column(String(10), nullable=True)
    city_insee = Column(String(10), nullable=True, index=True)
    department_code = Column(String(5), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    # Energy
    energy_category = Column(String(5), nullable=True)
    energy_value = Column(Integer, nullable=True)
    ghg_category = Column(String(5), nullable=True)
    ghg_value = Column(Integer, nullable=True)

    # Contact / links
    agency = Column(String(255), nullable=True)
    url = Column(String(1000), nullable=True)
    pictures = Column(JSON, nullable=True)

    # Deduplication / clustering
    geohash = Column(String(12), nullable=True, index=True)
    dedup_key = Column(String(64), nullable=True)

    # Lifecycle
    first_seen = Column(DateTime(timezone=True), server_default=func.now())
    last_seen = Column(DateTime(timezone=True), server_default=func.now())
    active = Column(Boolean, default=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    price_history = relationship(
        "PriceHistory", back_populates="listing", cascade="all, delete-orphan"
    )

    @property
    def uid(self) -> str:
        """Public, URL-safe stable identifier used by favorites and detail pages."""
        return f"{self.source}:{self.source_id}"

    def __repr__(self):
        return f"<Listing {self.uid}: {self.title}>"


class PriceHistory(Base):
    """One observed price for a listing at a point in time."""
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, index=True)
    listing_id = Column(Integer, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, index=True)
    price = Column(Float, nullable=False)
    observed_at = Column(DateTime(timezone=True), server_default=func.now())

    listing = relationship("Listing", back_populates="price_history")


class Hidden(Base):
    """A listing the user has hidden — never shown again, survives re-scrapes/purges."""
    __tablename__ = "hidden"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_hidden_source"),
    )

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(32), nullable=False)
    source_id = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ScrapeRun(Base):
    """Journal of a refresh run for a given criteria signature (also drives the throttle)."""
    __tablename__ = "scrape_runs"

    id = Column(Integer, primary_key=True, index=True)
    signature = Column(String(64), nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(16), default="ok")  # ok | partial | error
    n_found = Column(Integer, default=0)
    n_new = Column(Integer, default=0)
    detail = Column(JSON, nullable=True)  # {source: {found, new, error}}
