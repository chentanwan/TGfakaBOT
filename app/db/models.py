import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    Base,
    CardStatus,
    FulfillmentJobStatus,
    OrderStatus,
    PaymentStatus,
    ProductStatus,
    TimestampMixin,
    UserStatus,
    UUIDPrimaryKeyMixin,
)


def enum_column(enum_type: type, name: str, default: object) -> Mapped:
    return mapped_column(
        Enum(
            enum_type,
            name=name,
            native_enum=False,
            create_constraint=True,
            values_callable=lambda members: [member.value for member in members],
        ),
        default=default,
        nullable=False,
    )


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str | None] = mapped_column(String(128))
    language_code: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    balance_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    points: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tier: Mapped[str] = mapped_column(String(32), default="bronze", nullable=False)
    total_spent_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    order_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    referral_code: Mapped[str | None] = mapped_column(String(80), unique=True)
    referred_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    referral_reward_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    status: Mapped[UserStatus] = enum_column(UserStatus, "user_status", UserStatus.ACTIVE)


class Category(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "categories"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Product(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "products"

    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[ProductStatus] = enum_column(
        ProductStatus, "product_status", ProductStatus.DRAFT
    )


class SKU(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "skus"
    __table_args__ = (
        CheckConstraint("price_xtr >= 1", name="positive_price_xtr"),
        Index(
            "uq_skus_one_default_per_product",
            "product_id",
            unique=True,
            postgresql_where=text("is_default = true"),
        ),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), default="Default", nullable=False)
    price_xtr: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stars_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SupplierProduct(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "supplier_products"
    __table_args__ = (
        UniqueConstraint("provider", "external_product_id"),
        UniqueConstraint("sku_id", "provider"),
    )

    sku_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skus.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    external_product_id: Mapped[str] = mapped_column(String(255), nullable=False)
    cost_currency: Mapped[str] = mapped_column(String(12), nullable=False)
    cost_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    in_stock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_customer_email: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_for_profit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    suspension_reason: Mapped[str | None] = mapped_column(Text)


class ProductPrice(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "product_prices"
    __table_args__ = (
        CheckConstraint("amount_minor >= 1", name="positive_amount_minor"),
        UniqueConstraint("sku_id", "provider"),
    )

    sku_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skus.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    currency: Mapped[str] = mapped_column(String(12), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class Order(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("total_amount >= 1", name="positive_total_amount"),
        Index("ix_orders_user_created", "user_id", "created_at"),
    )

    public_id: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[OrderStatus] = enum_column(
        OrderStatus, "order_status", OrderStatus.PENDING_PAYMENT
    )
    payment_provider: Mapped[str] = mapped_column(
        String(80), default="telegram_stars", nullable=False
    )
    currency: Mapped[str] = mapped_column(String(12), default="XTR", nullable=False)
    total_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    invoice_payload: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    reservation_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    precheckout_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_exception_reason: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    resolution_action: Mapped[str | None] = mapped_column(String(40))


class OrderItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("unit_price >= 1", name="positive_unit_price"),
        CheckConstraint("quantity >= 1", name="positive_quantity"),
        CheckConstraint("line_total = unit_price * quantity", name="valid_line_total"),
        UniqueConstraint("order_id", "sku_id"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    sku_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skus.id"), nullable=False)
    product_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    sku_name_snapshot: Mapped[str] = mapped_column(String(120), nullable=False)
    unit_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    line_total: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Card(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "cards"
    __table_args__ = (
        CheckConstraint(
            "(status IN ('available','disabled') AND order_item_id IS NULL) OR "
            "(status IN ('reserved','delivered') AND order_item_id IS NOT NULL)",
            name="valid_card_assignment",
        ),
        Index(
            "ix_cards_allocatable",
            "sku_id",
            "id",
            postgresql_where=text("status = 'available'"),
        ),
        Index("ix_cards_expiry", "status", "reserved_until"),
        Index("ix_cards_order_item", "order_item_id"),
    )

    sku_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skus.id"), nullable=False)
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    secret_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    status: Mapped[CardStatus] = enum_column(CardStatus, "card_status", CardStatus.AVAILABLE)
    order_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("order_items.id"))
    reserved_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupplierOrder(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "supplier_orders"
    __table_args__ = (
        UniqueConstraint("provider", "external_order_id"),
        Index("ix_supplier_orders_provider_status", "provider", "status"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id"), unique=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    external_order_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    cost_currency: Mapped[str] = mapped_column(String(12), nullable=False)
    cost_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    response_data: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    recovery_note: Mapped[str | None] = mapped_column(Text)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovered_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger)


class PluginPayment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plugin_payments"
    __table_args__ = (
        CheckConstraint("amount_minor >= 1", name="positive_amount_minor"),
        UniqueConstraint("provider", "external_id"),
        Index("ix_plugin_payments_provider_status", "provider", "status"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id"), unique=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="creating", nullable=False)
    currency: Mapped[str] = mapped_column(String(12), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checkout_url: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transaction_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    provider_data: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    last_event: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)


class PaymentCharge(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "payment_charges"
    __table_args__ = (
        CheckConstraint("currency = 'XTR'", name="currency_xtr"),
        CheckConstraint("total_amount >= 1", name="positive_total_amount"),
        Index(
            "uq_payment_charge_order",
            "order_id",
            unique=True,
            postgresql_where=text("order_id IS NOT NULL"),
        ),
    )

    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"))
    related_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"))
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_payment_charge_id: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False
    )
    provider_payment_charge_id: Mapped[str | None] = mapped_column(String(255))
    telegram_update_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    invoice_payload: Mapped[str] = mapped_column(String(128), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[PaymentStatus] = enum_column(
        PaymentStatus, "payment_status", PaymentStatus.SUCCEEDED
    )
    resolution_status: Mapped[str] = mapped_column(String(20), default="unmatched", nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refund_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refund_last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refund_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    refund_last_error: Mapped[str | None] = mapped_column(Text)
    refund_requested_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    refund_claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    refund_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refund_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    resolution_note: Mapped[str | None] = mapped_column(Text)


class FulfillmentJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "fulfillment_jobs"

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id"), unique=True, nullable=False
    )
    status: Mapped[FulfillmentJobStatus] = enum_column(
        FulfillmentJobStatus, "fulfillment_job_status", FulfillmentJobStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(80))
    last_error: Mapped[str | None] = mapped_column(Text)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)


class Setting(TimestampMixin, Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class BalanceTopup(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "balance_topups"
    __table_args__ = (
        CheckConstraint("amount_minor >= 10", name="minimum_topup_amount"),
        UniqueConstraint("method", "proof"),
        Index("ix_balance_topups_status_created", "status", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    method: Mapped[str] = mapped_column(String(40), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    proof: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    rejection_reason: Mapped[str | None] = mapped_column(Text)


class UserApiCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_api_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), unique=True, nullable=False
    )
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    key_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
