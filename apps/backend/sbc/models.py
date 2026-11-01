"""Datenmodell (PostgreSQL in Produktion, SQLite nur für lokale Tests).

Grundsätze:
* Jede fachliche Tabelle trägt ``org_id`` (Vorbereitung Mandantenfähigkeit).
* Eine EAN ist KEIN globaler Schlüssel: Identifikatoren liegen in ``product_identifiers``
  (n:1 zu Produkten, nicht eindeutig).
* Geldbeträge: ``Numeric(14, 4)`` + Währung + Steuerbehandlung (net/gross) + Bezugsmenge.
* Kalkulationen speichern den vollständigen Input-Snapshot (reproduzierbar).
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB(), "postgresql")
Money = Numeric(14, 4)
Rate = Numeric(9, 6)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONType, list[Any]: JSONType}


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _org_fk() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


def _enum(e: type[enum.Enum], name: str) -> Enum:
    return Enum(e, name=name, native_enum=False, length=40, validate_strings=True)


# --------------------------------------------------------------------------- Enums


class Role(str, enum.Enum):
    admin = "admin"
    buyer = "buyer"
    reviewer = "reviewer"
    viewer = "viewer"


class ImportStatus(str, enum.Enum):
    uploaded = "uploaded"
    mapping_review = "mapping_review"
    queued = "queued"
    processing = "processing"
    completed = "completed"
    completed_with_errors = "completed_with_errors"
    failed = "failed"


class RowStatus(str, enum.Enum):
    ok = "ok"
    error = "error"
    duplicate = "duplicate"
    skipped = "skipped"


class IdentifierType(str, enum.Enum):
    GTIN = "GTIN"  # normalisiert auf 14 Stellen (EAN-13/UPC-12/EAN-8)
    ASIN = "ASIN"
    MPN = "MPN"
    MODEL = "MODEL"
    SUPPLIER_SKU = "SUPPLIER_SKU"
    EBAY_EPID = "EBAY_EPID"


class MatchClass(str, enum.Enum):
    EXACT_MATCH = "EXACT_MATCH"
    VARIANT_MATCH = "VARIANT_MATCH"
    MULTIPACK = "MULTIPACK"
    BUNDLE = "BUNDLE"
    PACKAGING_DIFFERENCE = "PACKAGING_DIFFERENCE"
    POSSIBLE_MATCH = "POSSIBLE_MATCH"
    NO_MATCH = "NO_MATCH"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ReviewStatus(str, enum.Enum):
    pending = "pending"
    auto_accepted = "auto_accepted"
    confirmed = "confirmed"
    rejected = "rejected"
    deferred = "deferred"


class RelationType(str, enum.Enum):
    SAME = "SAME"
    VARIANT = "VARIANT"
    MULTIPACK_OF = "MULTIPACK_OF"
    BUNDLE_CONTAINS = "BUNDLE_CONTAINS"
    ACCESSORY_OF = "ACCESSORY_OF"
    PACKAGING_VARIANT = "PACKAGING_VARIANT"


class OpportunityStatus(str, enum.Enum):
    new = "new"
    watching = "watching"
    shortlisted = "shortlisted"
    ignored = "ignored"
    purchased = "purchased"


class RecommendationStatus(str, enum.Enum):
    proposed = "proposed"
    approved = "approved"
    rejected = "rejected"
    ordered = "ordered"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    dead = "dead"


# --------------------------------------------------------------------------- Tenancy & Users


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200))


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[Role] = mapped_column(_enum(Role, "role"), default=Role.viewer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKey(Base):
    """Widerrufbare API-Schlüssel für Automatisierung (n8n, Skripte). Nur der Hash wird gespeichert."""

    __tablename__ = "api_keys"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppSetting(Base, TimestampMixin):
    """Konfigurierbare Parameter (Grenzwerte, Score-Gewichte, Budgets) je Organisation."""

    __tablename__ = "app_settings"
    __table_args__ = (UniqueConstraint("org_id", "key"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[dict[str, Any]] = mapped_column(JSONType)


# --------------------------------------------------------------------------- Suppliers


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("org_id", "code"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    code: Mapped[str] = mapped_column(String(50))  # Lieferanten-ID
    name: Mapped[str] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(2))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    contact: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    payment_terms: Mapped[str | None] = mapped_column(String(200))
    incoterm: Mapped[str | None] = mapped_column(String(20))
    shipping_cost_per_order: Mapped[Decimal | None] = mapped_column(Money)
    shipping_cost_per_unit: Mapped[Decimal | None] = mapped_column(Money)
    free_shipping_from: Mapped[Decimal | None] = mapped_column(Money)
    min_order_value: Mapped[Decimal | None] = mapped_column(Money)
    default_moq: Mapped[int | None] = mapped_column(Integer)
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    import_duty_rate: Mapped[Decimal | None] = mapped_column(Rate)
    discount_tiers: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    categories: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    brands: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    prices_include_vat: Mapped[bool] = mapped_column(Boolean, default=False)
    offer_valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))  # 0..5
    email_import_rules: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    feeds: Mapped[list[SupplierFeed]] = relationship(back_populates="supplier")


class SupplierFeed(Base, TimestampMixin):
    __tablename__ = "supplier_feeds"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    feed_type: Mapped[str] = mapped_column(String(20))  # upload|email|url|sftp|api
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)  # ohne Secrets
    secret_ref: Mapped[str | None] = mapped_column(String(100))  # Name der Env-Variable
    import_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("import_profiles.id"))
    schedule_cron: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    supplier: Mapped[Supplier] = relationship(back_populates="feeds")


class ImportProfile(Base, TimestampMixin):
    """Lieferantenspezifische Spaltenzuordnung, wiederverwendbar für Folgeimporte."""

    __tablename__ = "import_profiles"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    header_signature: Mapped[str | None] = mapped_column(String(64), index=True)
    sheet_name: Mapped[str | None] = mapped_column(String(100))
    header_row: Mapped[int] = mapped_column(Integer, default=0)
    column_mapping: Mapped[dict[str, Any]] = mapped_column(JSONType)  # header -> field
    options: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


# --------------------------------------------------------------------------- Imports


class ImportJob(Base, TimestampMixin):
    __tablename__ = "import_jobs"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), index=True)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("import_profiles.id"))
    feed_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("supplier_feeds.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[ImportStatus] = mapped_column(
        _enum(ImportStatus, "import_status"), default=ImportStatus.uploaded, index=True
    )
    source: Mapped[str] = mapped_column(String(20), default="upload")
    file_name: Mapped[str] = mapped_column(String(300))
    file_key: Mapped[str] = mapped_column(String(500))
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    file_size: Mapped[int] = mapped_column(Integer)
    file_type: Mapped[str] = mapped_column(String(10))
    sheet_name: Mapped[str | None] = mapped_column(String(100))
    header_row: Mapped[int] = mapped_column(Integer, default=0)
    detected_columns: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    column_mapping: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    mapping_source: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    options: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    total_rows: Mapped[int | None] = mapped_column(Integer)
    processed_rows: Mapped[int] = mapped_column(Integer, default=0)
    error_rows: Mapped[int] = mapped_column(Integer, default=0)
    last_processed_row: Mapped[int] = mapped_column(Integer, default=-1)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    file_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportRow(Base):
    __tablename__ = "import_rows"
    __table_args__ = (UniqueConstraint("job_id", "row_number"),)
    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_jobs.id", ondelete="CASCADE"))
    row_number: Mapped[int] = mapped_column(Integer)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType)
    normalized: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    status: Mapped[RowStatus] = mapped_column(_enum(RowStatus, "row_status"), index=True)
    errors: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    warnings: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    row_hash: Mapped[str] = mapped_column(String(64))
    supplier_offer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("supplier_offers.id"))


# --------------------------------------------------------------------------- Products


class Product(Base, TimestampMixin):
    """Kanonisches Produkt (eine konkrete Verkaufseinheit, z. B. 'Einzelstück' oder '2er-Pack')."""

    __tablename__ = "products"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    title: Mapped[str] = mapped_column(String(500))
    brand: Mapped[str | None] = mapped_column(String(200), index=True)
    model: Mapped[str | None] = mapped_column(String(200))
    mpn: Mapped[str | None] = mapped_column(String(200), index=True)
    category: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    unit_quantity: Mapped[int] = mapped_column(Integer, default=1)
    condition: Mapped[str] = mapped_column(String(20), default="new")
    is_bundle: Mapped[bool] = mapped_column(Boolean, default=False)
    normalized_title: Mapped[str | None] = mapped_column(String(500))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    restriction_note: Mapped[str | None] = mapped_column(Text)

    identifiers: Mapped[list[ProductIdentifier]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    images: Mapped[list[ProductImage]] = relationship(cascade="all, delete-orphan")


class ProductIdentifier(Base):
    __tablename__ = "product_identifiers"
    __table_args__ = (
        UniqueConstraint("product_id", "id_type", "value", "scope"),
        Index("ix_identifier_lookup", "org_id", "id_type", "value"),
    )
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    id_type: Mapped[IdentifierType] = mapped_column(_enum(IdentifierType, "identifier_type"))
    value: Mapped[str] = mapped_column(String(100))
    raw_value: Mapped[str | None] = mapped_column(String(100))
    # Scope: Lieferant (für SKUs) oder Marktplatz (für ASIN je Land), sonst ''
    scope: Mapped[str] = mapped_column(String(60), default="")
    source: Mapped[str] = mapped_column(String(40))

    product: Mapped[Product] = relationship(back_populates="identifiers")


class ProductAttribute(Base):
    __tablename__ = "product_attributes"
    id: Mapped[uuid.UUID] = _pk()
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))


class ProductImage(Base):
    __tablename__ = "product_images"
    id: Mapped[uuid.UUID] = _pk()
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str | None] = mapped_column(String(1000))
    storage_key: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(40))
    position: Mapped[int] = mapped_column(Integer, default=0)


class ProductRelation(Base, TimestampMixin):
    __tablename__ = "product_relations"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    from_product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    to_product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    relation_type: Mapped[RelationType] = mapped_column(_enum(RelationType, "relation_type"))
    quantity: Mapped[int | None] = mapped_column(Integer)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class BundleComponent(Base):
    __tablename__ = "bundle_components"
    id: Mapped[uuid.UUID] = _pk()
    bundle_product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    component_product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    preferred_supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id"))
    variant_conditions: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


# --------------------------------------------------------------------------- Offers & Listings


class SupplierOffer(Base):
    __tablename__ = "supplier_offers"
    __table_args__ = (UniqueConstraint("supplier_id", "offer_key"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), index=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"), index=True)
    last_import_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("import_jobs.id"))
    offer_key: Mapped[str] = mapped_column(String(200))  # SKU, sonst GTIN, sonst MPN
    supplier_sku: Mapped[str | None] = mapped_column(String(200))
    gtin: Mapped[str | None] = mapped_column(String(14), index=True)
    mpn: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(500))
    brand: Mapped[str | None] = mapped_column(String(200))
    # Preis: immer netto gespeichert; Originalwert & Steuerbehandlung im raw-Snapshot
    unit_price_net: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    price_basis_qty: Mapped[int] = mapped_column(Integer, default=1)  # Preis gilt für X Einheiten
    unit_quantity: Mapped[int] = mapped_column(Integer, default=1)  # Stück je Lieferanteneinheit
    tier_prices: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    rrp_gross: Mapped[Decimal | None] = mapped_column(Money)
    stock_qty: Mapped[int | None] = mapped_column(Integer)
    moq: Mapped[int | None] = mapped_column(Integer)
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    condition: Mapped[str] = mapped_column(String(20), default="new")
    image_urls: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_price_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    previous_unit_price_net: Mapped[Decimal | None] = mapped_column(Money)

    supplier: Mapped[Supplier] = relationship()
    product: Mapped[Product | None] = relationship()


class MarketplaceListing(Base, TimestampMixin):
    __tablename__ = "marketplace_listings"
    __table_args__ = (UniqueConstraint("org_id", "marketplace", "country", "external_id"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"), index=True)
    marketplace: Mapped[str] = mapped_column(String(20))  # amazon|ebay|kaufland
    country: Mapped[str] = mapped_column(String(2))
    external_id: Mapped[str] = mapped_column(String(100))  # ASIN / eBay itemId
    title: Mapped[str | None] = mapped_column(String(1000))
    brand: Mapped[str | None] = mapped_column(String(200))
    manufacturer: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(200))
    mpn: Mapped[str | None] = mapped_column(String(200))
    gtins: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    package_quantity: Mapped[int | None] = mapped_column(Integer)
    number_of_items: Mapped[int | None] = mapped_column(Integer)
    category: Mapped[str | None] = mapped_column(String(500))
    image_urls: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    # aktuelle Kennzahlen (Brutto-Verkaufspreise, wie am Marktplatz angezeigt)
    buybox_price: Mapped[Decimal | None] = mapped_column(Money)
    new_price: Mapped[Decimal | None] = mapped_column(Money)
    amazon_price: Mapped[Decimal | None] = mapped_column(Money)
    avg30_price: Mapped[Decimal | None] = mapped_column(Money)
    avg90_price: Mapped[Decimal | None] = mapped_column(Money)
    min90_price: Mapped[Decimal | None] = mapped_column(Money)
    price_cv90: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    sales_rank: Mapped[int | None] = mapped_column(Integer)
    sales_rank_drops30: Mapped[int | None] = mapped_column(Integer)
    sales_rank_drops90: Mapped[int | None] = mapped_column(Integer)
    monthly_sold: Mapped[int | None] = mapped_column(Integer)
    offer_count_new: Mapped[int | None] = mapped_column(Integer)
    amazon_is_seller: Mapped[bool | None] = mapped_column(Boolean)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    source: Mapped[str] = mapped_column(String(30))  # keepa|sp-api|ebay-browse|fixture
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ListingGtin(Base):
    """Index GTIN → Listing (ein Listing kann mehrere GTINs haben, eine GTIN mehrere Listings)."""

    __tablename__ = "listing_gtins"
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"), primary_key=True
    )
    gtin: Mapped[str] = mapped_column(String(14), primary_key=True, index=True)


class MarketplaceOffer(Base):
    __tablename__ = "marketplace_offers"
    id: Mapped[uuid.UUID] = _pk()
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"), index=True
    )
    seller_id: Mapped[str | None] = mapped_column(String(100))
    is_fba: Mapped[bool | None] = mapped_column(Boolean)
    is_amazon: Mapped[bool | None] = mapped_column(Boolean)
    condition: Mapped[str | None] = mapped_column(String(30))
    price: Mapped[Decimal] = mapped_column(Money)
    shipping: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    price_kind: Mapped[str] = mapped_column(String(20), default="listing")  # listing|sold
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PriceHistory(Base):
    __tablename__ = "price_history"
    __table_args__ = (
        Index("ix_price_history_subject", "subject_type", "subject_id", "metric", "observed_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    subject_type: Mapped[str] = mapped_column(String(20))  # supplier_offer|listing
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    metric: Mapped[str] = mapped_column(String(30))  # supplier_net|buybox|new|amazon|sales_rank
    value: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    currency: Mapped[str | None] = mapped_column(String(3))
    tax_basis: Mapped[str | None] = mapped_column(String(5))  # net|gross
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(30))


class SalesEstimate(Base):
    __tablename__ = "sales_estimates"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"), index=True
    )
    method: Mapped[str] = mapped_column(String(50))
    source: Mapped[str] = mapped_column(String(50))
    period_days: Mapped[int] = mapped_column(Integer, default=30)
    market_units: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    own_units: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    low: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    high: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FeeProfile(Base, TimestampMixin):
    """Gebühren-, Steuer- und Versandprofil je Marktplatz/Land/Kanal.

    Startwerte sind ANNAHMEN (is_assumption=True) und müssen fachlich geprüft werden.
    """

    __tablename__ = "fee_profiles"
    __table_args__ = (UniqueConstraint("org_id", "marketplace", "country", "channel", "category"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    marketplace: Mapped[str] = mapped_column(String(20))
    country: Mapped[str] = mapped_column(String(2))
    channel: Mapped[str] = mapped_column(String(10))  # FBA|FBM|EBAY|KAUFLAND
    category: Mapped[str] = mapped_column(String(100), default="default")
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    vat_rate: Mapped[Decimal] = mapped_column(Rate)
    referral_rate: Mapped[Decimal | None] = mapped_column(Rate)
    referral_min: Mapped[Decimal | None] = mapped_column(Money)
    fixed_fee: Mapped[Decimal | None] = mapped_column(Money)
    fulfillment_fee: Mapped[Decimal | None] = mapped_column(Money)
    shipping_cost: Mapped[Decimal | None] = mapped_column(Money)
    storage_cost_per_month: Mapped[Decimal | None] = mapped_column(Money)
    packaging_cost: Mapped[Decimal | None] = mapped_column(Money)
    payment_fee_rate: Mapped[Decimal | None] = mapped_column(Rate)
    return_rate: Mapped[Decimal | None] = mapped_column(Rate)
    return_cost: Mapped[Decimal | None] = mapped_column(Money)
    ad_cost_rate: Mapped[Decimal | None] = mapped_column(Rate)
    other_cost: Mapped[Decimal | None] = mapped_column(Money)
    is_assumption: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)


class FeeEstimate(Base):
    __tablename__ = "fee_estimates"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(10))
    price_gross: Mapped[Decimal] = mapped_column(Money)
    referral_fee: Mapped[Decimal | None] = mapped_column(Money)
    fulfillment_fee: Mapped[Decimal | None] = mapped_column(Money)
    other_fees: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    source: Mapped[str] = mapped_column(String(30))  # sp-api|profile
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------- Matching


class MatchingResult(Base, TimestampMixin):
    __tablename__ = "matching_results"
    __table_args__ = (UniqueConstraint("supplier_offer_id", "listing_id"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    supplier_offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier_offers.id", ondelete="CASCADE"), index=True
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"), index=True
    )
    classification: Mapped[MatchClass] = mapped_column(_enum(MatchClass, "match_class"), index=True)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    calibrated: Mapped[bool] = mapped_column(Boolean, default=False)
    units_required: Mapped[int | None] = mapped_column(Integer)  # Lieferanteneinheiten je Listing
    stage: Mapped[str] = mapped_column(String(20))  # identifier|text|llm|vision|manual
    matched_attributes: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    mismatched_attributes: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    reasoning: Mapped[str | None] = mapped_column(Text)
    sources: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    engine_version: Mapped[str] = mapped_column(String(30))
    model_version: Mapped[str | None] = mapped_column(String(60))
    review_status: Mapped[ReviewStatus] = mapped_column(
        _enum(ReviewStatus, "review_status"), default=ReviewStatus.pending, index=True
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    supplier_offer: Mapped[SupplierOffer] = relationship()
    listing: Mapped[MarketplaceListing] = relationship()


class MatchingFeedback(Base):
    __tablename__ = "matching_feedback"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    matching_result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("matching_results.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(30))
    previous_classification: Mapped[str] = mapped_column(String(30))
    new_classification: Mapped[str | None] = mapped_column(String(30))
    score_at_review: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    is_positive: Mapped[bool | None] = mapped_column(Boolean)  # Label für Kalibrierung
    corrections: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------- Profit & Opportunities


class ProfitCalculation(Base):
    __tablename__ = "profit_calculations"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    supplier_offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier_offers.id", ondelete="CASCADE"), index=True
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketplace_listings.id", ondelete="CASCADE")
    )
    matching_result_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("matching_results.id"))
    channel: Mapped[str] = mapped_column(String(10))
    scenario: Mapped[str] = mapped_column(String(30))
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONType)
    outputs: Mapped[dict[str, Any]] = mapped_column(JSONType)
    missing: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    is_complete: Mapped[bool] = mapped_column(Boolean)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    engine_version: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Opportunity(Base, TimestampMixin):
    __tablename__ = "opportunities"
    __table_args__ = (UniqueConstraint("supplier_offer_id", "listing_id", "channel"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    supplier_offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier_offers.id", ondelete="CASCADE"), index=True
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"), index=True
    )
    matching_result_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("matching_results.id"))
    calculation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profit_calculations.id"))
    channel: Mapped[str] = mapped_column(String(10))
    status: Mapped[OpportunityStatus] = mapped_column(
        _enum(OpportunityStatus, "opportunity_status"), default=OpportunityStatus.new, index=True
    )
    score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), index=True)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    exclusion_reasons: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    is_excluded: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    buy_price_net: Mapped[Decimal | None] = mapped_column(Money)
    sell_price_gross: Mapped[Decimal | None] = mapped_column(Money)
    contribution: Mapped[Decimal | None] = mapped_column(Money)
    contribution_margin: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    roi: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    demand_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    competition_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    match_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)

    supplier_offer: Mapped[SupplierOffer] = relationship()
    listing: Mapped[MarketplaceListing] = relationship()
    matching_result: Mapped[MatchingResult | None] = relationship()
    calculation: Mapped[ProfitCalculation | None] = relationship()


class PurchaseRecommendation(Base, TimestampMixin):
    __tablename__ = "purchase_recommendations"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"))
    kind: Mapped[str] = mapped_column(String(20))  # test|reorder
    quantity: Mapped[int] = mapped_column(Integer)
    unit_cost_net: Mapped[Decimal] = mapped_column(Money)
    total_cost_net: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    rationale: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    status: Mapped[RecommendationStatus] = mapped_column(
        _enum(RecommendationStatus, "recommendation_status"),
        default=RecommendationStatus.proposed,
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    opportunity: Mapped[Opportunity] = relationship()


# --------------------------------------------------------------------------- Ops


class APIUsage(Base):
    __tablename__ = "api_usage"
    __table_args__ = (Index("ix_api_usage_provider_time", "provider", "created_at"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    provider: Mapped[str] = mapped_column(String(30))  # keepa|anthropic|sp-api|ebay
    endpoint: Mapped[str] = mapped_column(String(100))
    units: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=0)  # Tokens o. ä.
    unit_type: Mapped[str] = mapped_column(String(20))  # keepa_tokens|llm_tokens|requests
    cost_eur: Mapped[Decimal | None] = mapped_column(Money)
    status_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    items: Mapped[int] = mapped_column(Integer, default=1)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class BackgroundJob(Base, TimestampMixin):
    """Protokoll aller Hintergrundjobs (Monitoring, Dedup, Dead-Letter-Sicht)."""

    __tablename__ = "background_jobs"
    __table_args__ = (Index("ix_bg_jobs_dedup", "dedup_key", "status"),)
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    kind: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus, "job_status"), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    dedup_key: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
