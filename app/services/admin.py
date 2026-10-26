import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import card_vault
from app.db.base import CardStatus, ProductStatus
from app.db.models import SKU, Card, Product


@dataclass(slots=True)
class AdminProduct:
    id: uuid.UUID
    name: str
    price_xtr: int
    status: str
    available: int


async def create_product(
    session: AsyncSession, *, name: str, description: str, price_xtr: int
) -> Product:
    if price_xtr < 1:
        raise ValueError("Stars 价格必须为正整数")
    async with session.begin():
        product = Product(
            name=name.strip(),
            description=description.strip(),
            status=ProductStatus.ACTIVE,
        )
        session.add(product)
        await session.flush()
        session.add(
            SKU(
                product_id=product.id,
                code=f"default-{product.id}",
                name="Default",
                price_xtr=price_xtr,
                is_default=True,
                is_active=True,
            )
        )
    return product


async def list_admin_products(session: AsyncSession) -> list[AdminProduct]:
    available = func.count(Card.id).filter(Card.status == CardStatus.AVAILABLE)
    rows = (
        await session.execute(
            select(Product, SKU, available.label("available"))
            .join(SKU, SKU.product_id == Product.id)
            .outerjoin(Card, Card.sku_id == SKU.id)
            .where(SKU.is_default.is_(True))
            .group_by(Product.id, SKU.id)
            .order_by(Product.created_at.desc())
        )
    ).all()
    return [
        AdminProduct(
            id=product.id,
            name=product.name,
            price_xtr=sku.price_xtr,
            status=product.status.value,
            available=int(stock),
        )
        for product, sku, stock in rows
    ]


async def import_cards(
    session: AsyncSession, *, product_id: uuid.UUID, secrets: list[str]
) -> tuple[int, int]:
    normalized = list(dict.fromkeys(secret.strip() for secret in secrets if secret.strip()))
    if not normalized:
        return 0, 0
    if len(normalized) > 500:
        raise ValueError("单次最多导入 500 条卡密")
    async with session.begin():
        sku_id = await session.scalar(
            select(SKU.id).where(
                SKU.product_id == product_id,
                SKU.is_default.is_(True),
            )
        )
        if sku_id is None:
            raise ValueError("商品不存在")
        inserted = 0
        for secret in normalized:
            result = await session.execute(
                insert(Card)
                .values(
                    sku_id=sku_id,
                    secret_ciphertext=card_vault.encrypt(secret),
                    secret_fingerprint=card_vault.fingerprint(secret),
                    status=CardStatus.AVAILABLE.value,
                )
                .on_conflict_do_nothing(index_elements=["secret_fingerprint"])
            )
            inserted += result.rowcount or 0
    return inserted, len(normalized) - inserted


async def toggle_product(session: AsyncSession, product_id: uuid.UUID) -> ProductStatus:
    async with session.begin():
        product = await session.scalar(
            select(Product).where(Product.id == product_id).with_for_update()
        )
        if product is None:
            raise ValueError("商品不存在")
        product.status = (
            ProductStatus.DRAFT if product.status == ProductStatus.ACTIVE else ProductStatus.ACTIVE
        )
        return product.status
