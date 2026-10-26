# Plugin development

TGfakaBOT core is provider-neutral. Optional functionality belongs in a Python package under `plugins/` and exports `create_plugin()`.

A plugin may provide:

- an Aiogram router;
- catalog payment buttons;
- product-admin buttons;
- HTTP callback routes.

Core owns users, inventory reservation, orders, generic plugin prices, generic payment receipts, and idempotent fulfillment. Plugins own provider configuration, API signing, transport, callback parsing, and provider-specific metadata. A plugin must never bypass `app.services.plugin_payments` to deliver inventory.

Security rules:

- Verify callback signatures before database access.
- Store provider receipts before acknowledging successful payment.
- Use provider and external/transaction IDs as idempotency keys.
- Treat network timeouts during payment creation as ambiguous; do not silently create a second transaction.
- Do not place wallet keys or API tokens in source control.
- Expose callbacks through HTTPS and restrict direct access to the internal HTTP port.
