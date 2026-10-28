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

Products support one or more independent SKU plans. Each plan can have its own balance price and inventory, while Stars and payment plugins remain independently configurable.
商品支持一个或多个独立 SKU 规格；每个规格可拥有独立余额价格和库存，Stars 与支付插件价格仍可独立配置。

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

## Operational limitations / 运行限制

- Long polling is the default Telegram update mode.
- User-facing main/account menus support seven languages; some provider-specific and deep workflow messages remain English or Chinese.
- CryptoBot production verification requires an operator-provided Crypto Pay token and public HTTPS callback.
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

## ProdSeller supplier plugin

`plugins.prodseller` integrates the [ProdSeller v1 API](https://prodseller.com/api-docs/) as an upstream inventory provider. Product costs and BEpusdt sale prices are denominated in **USD cents**. The API key is read only from `PRODSELLER_API_KEY`; never commit it.

Admin workflow:

1. Enable `plugins.prodseller` in `PLUGIN_MODULES`.
2. Use `/admin` → **ProdSeller balance** to verify authentication.
3. Select **Import ProdSeller product** and paste an upstream product ID.
4. The bot imports instant-delivery products only, imports them as draft, disables Stars for those SKUs, and creates a disabled BEpusdt USD price with the configured markup.
5. Review and enable the BEpusdt price before selling. **Sync upstream** refreshes cost and stock for imported products.

After a successful customer payment, the supplier purchase uses `Idempotency-Key: tgfakabot-<public order id>`. Before purchase it re-reads the authoritative product detail and verifies current total cost, exact stock, quantity, and email requirements. Quantity purchases are sent upstream as one idempotent order; every returned key is individually validated, encrypted into the normal card vault, and committed atomically. Products requiring customer email activation are intentionally rejected until a dedicated customer-email consent flow exists.

Each supplier plugin owns independent pricing and synchronization variables. ProdSeller calculates the suggested USD sale price as `cost + percentage profit + fixed USD profit`, using `PRODSELLER_PROFIT_PERCENT` and `PRODSELLER_PROFIT_FIXED`. Its product catalog automatically refreshes every `PRODSELLER_PRODUCT_SYNC_MINUTES` minutes (default 60); pending fulfillment polling remains independently configured with `PRODSELLER_ORDER_POLL_SECONDS`.


### Supplier operations and recovery

The ProdSeller admin menu now includes a paginated upstream catalog with cost, suggested USD sale price, stock, and one-click draft import. The supplier exception queue lists pending, paid, failed, and unknown purchases. A recovery action requires an administrator evidence note, queries the authoritative upstream order, records the administrator/time/note audit, and only delivers a key when the upstream status is `delivered`.

Automatic product synchronization enforces the configured profit floor. If an active BEpusdt sale price falls below `upstream cost + percentage profit + fixed profit`, the payment price is disabled and the supplier mapping is marked as profit-suspended; it is never silently increased. An administrator must review and re-enable a safe price.


### Supplier operations controls

The ProdSeller exception queue supports `all`, `failed`, `unknown`, `pending`, and `paid` filters with pagination. Each record has a redacted audit view and an authoritative recovery query. If an order-creation response is lost, the plugin scans recent upstream orders without creating another purchase; a unique safe match is recovered automatically, while ambiguous matches require an administrator to inspect and explicitly select a candidate. Background catalog sync and broad reconciliation stop at the `PRODSELLER_RATE_LIMIT_RESERVE` threshold so paid-order fulfillment retains API quota. When profit protection disables a price, every configured administrator receives a Telegram alert with **use suggested price** and **custom price** actions. Custom prices below the current configured profit floor are rejected.

## Full storefront capabilities / 完整商城能力

The current version supports seven user languages (`en`, `fr`, `ar`, `ru`, `zh`, `es`, `id`), USD account balances, points and configurable tiers, referrals, multi-plan products, quantity purchases, transactionally safe balance delivery, Telegram Stars, BEpusdt, ProdSeller, manual top-up review, optional CryptoBot top-ups, customer API keys, restock broadcasts, and administrator user/broadcast controls.

### Required account configuration

Set brand values (`BOT_USERNAME`, `BRAND_NAME`, `CHANNEL_HANDLE`, `SUPPORT_HANDLE`), tier thresholds, points rules and referral percentage in `.env`. Keep `REFERRAL_REWARD_PERCENT=0` until the commercial reward policy is approved.

### Initialize example products

After the database migration and environment configuration:

```bash
python -m scripts.init_demo
```

This command is idempotent. It creates three example products from `examples/products.json`, one balance-enabled plan per product and two encrypted inventory entries per plan. The `DEMO-*` delivery values are examples only and must never be used as real products.

### Complete acceptance walkthrough

1. Send `/start`; the user is registered and receives a referral code.
2. Submit a Binance Pay or BEP20 top-up from **Top up**.
3. Open `/admin` → **充值审核** and approve the record; the user balance increases once.
4. Open **Products**, select a plan and quantity, and confirm a balance purchase.
5. Verify that balance, inventory, points, total spending, tier and optional referral reward update together.
6. Open **My orders** and reveal the delivery again.
7. Open **API**, generate a key once, then query `GET /api/v1/account` or `/api/v1/products` with `Authorization: Bearer <key>`.
8. Rotate the API key and verify the previous key returns HTTP 401.

### Payment and deployment notes

- CryptoBot requires a real `CRYPTOBOT_API_TOKEN` and a public HTTPS webhook route to `/plugins/cryptobot/notify`; leave `plugins.cryptobot` out of `PLUGIN_MODULES` until configured.
- BEpusdt and ProdSeller remain independent plugins and retain their existing callback, idempotency and recovery controls.
- `RESTOCK_BROADCAST_CHAT_ID` is optional. The bot must be permitted to post in that channel/group.
- Run `alembic upgrade head` before every release. Back up PostgreSQL and the card encryption key before production migration.

### Assumptions requiring operator confirmation

- Account money and manual top-ups are denominated in USD cents.
- Default tier thresholds are $100 / $500 / $1000.
- Referral rewards apply to successful balance purchases and credit the referrer balance.
- CryptoBot invoices use USDT and credit the submitted USD amount after an exact callback amount match.
- Public customer API access should be placed behind HTTPS and reverse-proxy rate limiting in addition to application limits.
