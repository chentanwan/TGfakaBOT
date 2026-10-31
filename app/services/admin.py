import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import card_vault
from app.db.base import CardStatus, ProductStatus
from app.db.models import SKU, Card, Product, ProductPrice
from app.services.audit import record_admin_action


@dataclass(slots=True)
class AdminProduct:
    id: uuid.UUID
    name: str
    price_usd_minor: int | None
    status: str
    available: int


@dataclass(slots=True)
class RestockResult:
    inserted: int
    duplicates: int
    current_stock: int
    unit_price_usd_minor: int | None
    product_name: str


async def create_product(
    session: AsyncSession,
    *,
    name: str,
    description: str,
    price_usd_minor: int,
    admin_telegram_id: int | None = None,
) -> Product:
    if price_usd_minor < 1:
        raise ValueError("美元价格必须大于 0")
    async with session.begin():
        product = Product(
            name=name.strip(),
            description=description.strip(),
            status=ProductStatus.ACTIVE,
        )
        session.add(product)
        await session.flush()
        sku = SKU(
            product_id=product.id,
            code=f"default-{product.id}",
            name="Default",
            price_xtr=1,
            stars_enabled=False,
            is_default=True,
            is_active=True,
        )
        session.add(sku)
        await session.flush()
        session.add_all(
            [
                ProductPrice(
                    sku_id=sku.id,
                    provider=provider,
                    currency="USD",
                    amount_minor=price_usd_minor,
                    is_active=True,
                )
                for provider in ("usdt", "balance")
            ]
        )
        if admin_telegram_id is not None:
            await record_admin_action(
                session,
                admin_telegram_id=admin_telegram_id,
                action="product_create",
                target_type="product",
                target_id=str(product.id),
                details={"name": product.name, "price_usd_minor": price_usd_minor},
            )
    return product


async def list_admin_products(session: AsyncSession) -> list[AdminProduct]:
    available = func.count(Card.id).filter(Card.status == CardStatus.AVAILABLE)
    rows = (
        await session.execute(
            select(Product, SKU, ProductPrice, available.label("available"))
            .join(SKU, SKU.product_id == Product.id)
            .outerjoin(
                ProductPrice,
                (ProductPrice.sku_id == SKU.id)
                & (ProductPrice.provider == "usdt")
                & ProductPrice.is_active.is_(True),
            )
            .outerjoin(Card, Card.sku_id == SKU.id)
            .where(SKU.is_default.is_(True))
            .group_by(Product.id, SKU.id, ProductPrice.id)
            .order_by(Product.created_at.desc())
        )
    ).all()
    return [
        AdminProduct(
            id=product.id,
            name=product.name,
            price_usd_minor=price.amount_minor if price else None,
            status=product.status.value,
            available=int(stock),
        )
        for product, sku, price, stock in rows
    ]


async def import_cards(
    session: AsyncSession,
    *,
    product_id: uuid.UUID,
    secrets: list[str],
    admin_telegram_id: int | None = None,
) -> RestockResult:
    normalized = list(dict.fromkeys(secret.strip() for secret in secrets if secret.strip()))
    if not normalized:
        raise ValueError("没有可导入的卡密")
    if len(normalized) > 500:
        raise ValueError("单次最多导入 500 条卡密")
    async with session.begin():
        sku = await session.scalar(
            select(SKU).where(
                SKU.product_id == product_id,
                SKU.is_default.is_(True),
            )
        )
        if sku is None:
            raise ValueError("商品不存在")
        product_name = await session.scalar(select(Product.name).where(Product.id == product_id))
        inserted = 0
        for secret in normalized:
            result = await session.execute(
                insert(Card)
                .values(
                    sku_id=sku.id,
                    secret_ciphertext=card_vault.encrypt(secret),
                    secret_fingerprint=card_vault.fingerprint(secret),
                    status=CardStatus.AVAILABLE.value,
                )
                .on_conflict_do_nothing(index_elements=["secret_fingerprint"])
            )
            inserted += result.rowcount or 0
        usd_price = await session.scalar(
            select(ProductPrice.amount_minor).where(
                ProductPrice.sku_id == sku.id,
                ProductPrice.provider == "usdt",
                ProductPrice.is_active.is_(True),
            )
        )
        current_stock = await session.scalar(
            select(func.count(Card.id)).where(
                Card.sku_id == sku.id, Card.status == CardStatus.AVAILABLE
            )
        )
        if admin_telegram_id is not None:
            await record_admin_action(
                session,
                admin_telegram_id=admin_telegram_id,
                action="inventory_import",
                target_type="product",
                target_id=str(product_id),
                details={"inserted": inserted, "duplicates": len(normalized) - inserted},
            )
    return RestockResult(
        inserted=inserted,
        duplicates=len(normalized) - inserted,
        current_stock=int(current_stock or 0),
        unit_price_usd_minor=usd_price,
        product_name=product_name or "",
    )


async def toggle_product(
    session: AsyncSession, product_id: uuid.UUID, admin_telegram_id: int | None = None
) -> ProductStatus:
    async with session.begin():
        product = await session.scalar(
            select(Product).where(Product.id == product_id).with_for_update()
        )
        if product is None:
            raise ValueError("商品不存在")
        product.status = (
            ProductStatus.DRAFT if product.status == ProductStatus.ACTIVE else ProductStatus.ACTIVE
        )
        if admin_telegram_id is not None:
            await record_admin_action(
                session,
                admin_telegram_id=admin_telegram_id,
                action="product_toggle",
                target_type="product",
                target_id=str(product_id),
                details={"status": product.status.value},
            )
        return product.status


@dataclass(slots=True)
class AdminUser:
    telegram_user_id: int
    username: str | None
    display_name: str
    balance_minor: int
    points: int
    order_count: int
    total_spent_minor: int
    status: str


async def find_admin_user(session: AsyncSession, telegram_user_id: int) -> AdminUser | None:
    from app.db.models import User

    user = await session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
    if user is None:
        return None
    return AdminUser(
        telegram_user_id=user.telegram_user_id,
        username=user.username,
        display_name=user.display_name or "",
        balance_minor=user.balance_minor,
        points=user.points,
        order_count=user.order_count,
        total_spent_minor=user.total_spent_minor,
        status=user.status.value,
    )


async def adjust_user_balance(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    delta_minor: int,
    admin_telegram_id: int | None = None,
) -> int:
    from app.db.models import User

    if delta_minor == 0:
        raise ValueError("调整金额不能为 0")
    async with session.begin():
        user = await session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id).with_for_update()
        )
        if user is None:
            raise ValueError("用户不存在")
        if user.balance_minor + delta_minor < 0:
            raise ValueError("余额不能变成负数")
        user.balance_minor += delta_minor
        if admin_telegram_id is not None:
            await record_admin_action(
                session,
                admin_telegram_id=admin_telegram_id,
                action="balance_adjust",
                target_type="user",
                target_id=str(telegram_user_id),
                details={"delta_minor": delta_minor, "balance_minor": user.balance_minor},
            )
        return user.balance_minor


async def list_broadcast_user_ids(session: AsyncSession) -> list[int]:
    from app.db.base import UserStatus
    from app.db.models import User

    return list(
        await session.scalars(select(User.telegram_user_id).where(User.status == UserStatus.ACTIVE))
    )
