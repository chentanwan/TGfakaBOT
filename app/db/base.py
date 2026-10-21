import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"


class ProductStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class CardStatus(str, enum.Enum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    DELIVERED = "delivered"
    DISABLED = "disabled"


class OrderStatus(str, enum.Enum):
    PENDING_PAYMENT = "pending_payment"
    PAYMENT_PROCESSING = "payment_processing"
    PAID = "paid"
    FULFILLED = "fulfilled"
    EXPIRED = "expired"
    PAYMENT_EXCEPTION = "payment_exception"
    REFUND_PENDING = "refund_pending"
    REFUNDED = "refunded"


class PaymentStatus(str, enum.Enum):
    SUCCEEDED = "succeeded"
    REFUND_PENDING = "refund_pending"
    REFUNDED = "refunded"
    REFUND_FAILED = "refund_failed"


class FulfillmentJobStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    RETRY = "retry"
    DEAD = "dead"


TelegramId = BigInteger
