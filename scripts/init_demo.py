"""Idempotently create demo products and encrypted inventory."""

import asyncio

from sqlalchemy import select

from app.core.security import card_vault
from app.db.base import CardStatus, ProductStatus
from app.db.models import SKU, Card, Product, ProductPrice
from app.db.session import SessionFactory

DEMO_PRODUCTS = [
    ("Demo VPN 30 Days", "vpn-30d", "30 Days", 50, 499, ["DEMO-VPN-A", "DEMO-VPN-B"]),
    ("Demo Streaming", "stream-premium", "Premium", 80, 799, ["DEMO-STREAM-A", "DEMO-STREAM-B"]),
    ("Demo Gift Card", "gift-10", "$10 Card", 100, 1099, ["DEMO-GIFT-A", "DEMO-GIFT-B"]),
]


async def main() -> None:
    async with SessionFactory() as session, session.begin():
        for name, code, plan, stars, usd_minor, secrets in DEMO_PRODUCTS:
            product = await session.scalar(select(Product).where(Product.name == name))
            if product is None:
                product = Product(
                    name=name, description=f"Example product: {name}", status=ProductStatus.ACTIVE
                )
                session.add(product)
                await session.flush()
            sku = await session.scalar(select(SKU).where(SKU.code == code))
            if sku is None:
                sku = SKU(
                    product_id=product.id,
                    code=code,
                    name=plan,
                    price_xtr=stars,
                    is_default=True,
                    is_active=True,
                )
                session.add(sku)
                await session.flush()
            price = await session.scalar(
                select(ProductPrice).where(
                    ProductPrice.sku_id == sku.id, ProductPrice.provider == "balance"
                )
            )
            if price is None:
                session.add(
                    ProductPrice(
                        sku_id=sku.id,
                        provider="balance",
                        currency="USD",
                        amount_minor=usd_minor,
                        is_active=True,
                        metadata_json={"demo": True},
                    )
                )
            for secret in secrets:
                fingerprint = card_vault.fingerprint(secret)
                exists = await session.scalar(
                    select(Card.id).where(Card.secret_fingerprint == fingerprint)
                )
                if exists is None:
                    session.add(
                        Card(
                            sku_id=sku.id,
                            secret_ciphertext=card_vault.encrypt(secret),
                            secret_fingerprint=fingerprint,
                            status=CardStatus.AVAILABLE,
                        )
                    )
    print(f"Initialized {len(DEMO_PRODUCTS)} demo products with two inventory items each.")


if __name__ == "__main__":
    asyncio.run(main())
