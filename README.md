# TGfakaBOT

Telegram-native digital goods storefront powered by Telegram Stars.

TGfakaBOT 是一个完全通过 Telegram Bot 完成购物与管理操作的开源数字商品发卡系统。商品、库存、卡密、订单和支付由本项目自行管理，不依赖外部商城后台。

> Status: **v0.1 MVP / early development**. Review the code and test with Telegram's test environment before production use.

## Contact / 联系方式

- Telegram: [@cclchat_bot](https://t.me/cclchat_bot)

## Features / 功能

### User / 用户端

- Browse available digital products in Telegram / TG 内浏览商品
- Pay with Telegram Stars (`XTR`) / 使用 Telegram Stars 支付
- Atomic card reservation to prevent overselling / 原子预留卡密，防止超卖
- Idempotent payment settlement / 支付流水幂等处理
- Automatic card-secret delivery / 支付成功自动发卡
- Retrieve delivered secrets with `/orders` / 通过 `/orders` 找回卡密

### Admin / 管理端

- Create products and set integer Stars prices / 创建商品并设置 Stars 整数价格
- Enable or disable products / 商品启停
- Import up to 500 card secrets per batch / 每批导入最多 500 条卡密
- HMAC fingerprint deduplication / HMAC 指纹去重
- Card secrets encrypted at rest / 卡密加密存储

## Architecture / 架构

```text
Telegram Bot (Aiogram long polling)
        │
        ├── PostgreSQL: products, inventory, orders, Stars payments
        ├── Redis: FSM state
        └── cleanup task: release expired unpaid reservations
```

The first release uses one default SKU per product while keeping an explicit SKU table for future multi-SKU support.
第一版每个商品使用一个默认 SKU，但从第一天起保留独立 SKU 表，便于后续扩展。

## Quick Start / 快速开始

### 1. Create a bot

Create a bot with [@BotFather](https://t.me/BotFather) and obtain its token.

### 2. Configure

```bash
cp .env.example .env
```

Generate independent card protection keys:

```bash
python - <<'PY'
import secrets
from cryptography.fernet import Fernet
print("CARD_ENCRYPTION_KEY=" + Fernet.generate_key().decode())
print("CARD_FINGERPRINT_KEY=" + secrets.token_urlsafe(48))
PY
```

Fill in `.env`:

- `BOT_TOKEN`
- `ADMIN_IDS`
- `POSTGRES_PASSWORD`
- matching password inside `DATABASE_URL`
- generated `CARD_ENCRYPTION_KEY`
- generated `CARD_FINGERPRINT_KEY`

Never commit `.env` or production secrets.

### 3. Start

```bash
docker compose up -d --build
docker compose logs -f bot
```

The bot automatically runs `alembic upgrade head` before startup.

### 4. Initialize the shop

1. Send `/admin` as an administrator.
2. Create a product.
3. Open **商品与库存** and import card secrets, one per line.
4. Users can send `/shop` to buy with Stars.

## Telegram Stars payment rules

Digital goods sold inside Telegram use Stars:

- currency: `XTR`
- provider token: empty
- prices: integer Stars
- delivery happens only after `successful_payment`
- `telegram_payment_charge_id` is stored uniquely for idempotency

Official documentation: [Telegram Stars Payments](https://core.telegram.org/bots/payments-stars).

Before operating publicly, configure `/terms`, `/support`, and `/paysupport` content suitable for your jurisdiction and support process.

## Development / 开发

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env  # fill local values
alembic upgrade head
pytest
ruff check .
python -m app.main
```

Create a migration after changing models:

```bash
alembic revision --autogenerate -m "describe change"
```

## Security model / 安全模型

- PostgreSQL row locks and `SKIP LOCKED` prevent concurrent allocation of the same card.
- Orders snapshot product names and Stars prices.
- Invoice payloads are random and unique.
- Pre-checkout validates user, payload, currency, amount, status and reservation.
- Payment charge IDs are globally unique.
- Card secrets are encrypted with Fernet and deduplicated with keyed HMAC fingerprints.
- Logs must never include plaintext card secrets or secret keys.
- Paid cards are never automatically returned to inventory.

## Current MVP limitations / 当前限制

- One card and one default SKU per order.
- Long polling only.
- Chinese-first UI; full i18n is planned.
- Payment exceptions can be fulfilled with a reserved/new card or refunded from the admin panel.
- Dangerous admin actions use Redis-backed, single-use confirmation tokens.
- `/orders` is paginated; `/terms`, `/support`, and `/paysupport` support Stars users.
- Refund API timeouts move to `refund_unknown`; automatic retries are blocked until manual reconciliation.
- Unmatched and duplicate/conflict payment charges appear in a charge-centric admin queue.
- Refund calls use database claim tokens and leases to prevent concurrent duplicate API calls.
- Unknown refund results require an evidence note and administrator reconciliation.
- Payment receipts commit before order matching, so fulfillment failures cannot erase financial records.
- GitHub Actions runs Ruff, unit tests, Alembic migrations, and PostgreSQL/Redis integration tests.
- Strict exactly-once Telegram message delivery is not possible; `/orders` remains the recovery path.
- PostgreSQL/Redis test services and opt-in integration tests are provided in `docker-compose.test.yml`.
- The bot health check verifies PostgreSQL and Redis; external Telegram polling monitoring is recommended.

## Integration tests / 集成测试

```bash
docker compose -f docker-compose.test.yml up -d
RUN_INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://tgfakabot:test-password@127.0.0.1:55432/tgfakabot_test pytest tests/integration -q
docker compose -f docker-compose.test.yml down
```

Integration tests apply Alembic migrations to the test database before running.

## License

MIT

## Plugin architecture

Optional features live outside the core application under `plugins/` and are loaded with `PLUGIN_MODULES`. Core exposes only generic extension points for Telegram routers, catalog/admin buttons, HTTP callback routes, product prices, and idempotent payment fulfillment. New provider-specific code must not be added to `app/services/orders.py` or other provider-neutral core modules.

The first plugin is `plugins.bepusdt`, compatible with the self-hosted [BEpusdt gateway](https://github.com/v03413/BEpusdt):

1. Set `PLUGIN_MODULES=plugins.bepusdt`.
2. Configure `BEPUSDT_BASE_URL`, `BEPUSDT_API_TOKEN`, and HTTPS `BEPUSDT_PUBLIC_BASE_URL`.
3. Route `BEPUSDT_PUBLIC_BASE_URL/plugins/bepusdt/notify` to the bot container's plugin HTTP port (default `8081`).
4. In `/admin` → product list, use **设置 BEpusdt 价格**. A product only displays the USDT option after a fiat price is enabled.

BEpusdt remains a separately deployed gateway. TGfakaBOT does not hold wallet private keys. Callbacks are signature-verified, stored idempotently, and a unique chain transaction ID cannot fulfill more than one order. Gateway timeout/ambiguous creation results are quarantined instead of automatically recreating a payment transaction.
