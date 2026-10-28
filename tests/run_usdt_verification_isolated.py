import asyncio
from types import SimpleNamespace

from aiohttp import web

from app.services.usdt_verification import _tron_hex, verify_usdt_payment

TRANSFER = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


async def main() -> None:
    destination = "0x" + "a" * 40
    token = "0x" + "b" * 40
    tron_address = "TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj"
    tron_hex = _tron_hex(tron_address)
    state = {"evm_amount": 1_000_000, "evm_status": "0x1", "latest": 111}

    async def rpc(request: web.Request) -> web.Response:
        payload = await request.json()
        if payload["method"] == "eth_blockNumber":
            result = hex(state["latest"])
        else:
            result = {
                "status": state["evm_status"],
                "blockNumber": hex(100),
                "logs": [
                    {
                        "address": token,
                        "topics": [
                            "0x" + TRANSFER,
                            "0x" + "0" * 64,
                            "0x" + destination[2:].rjust(64, "0"),
                        ],
                        "data": hex(state["evm_amount"]),
                    }
                ],
            }
        return web.json_response({"jsonrpc": "2.0", "id": 1, "result": result})

    async def tron_info(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "blockNumber": 100,
                "receipt": {"result": "SUCCESS"},
                "log": [
                    {
                        "address": tron_hex[2:],
                        "topics": [TRANSFER, "0" * 64, tron_hex[2:].rjust(64, "0")],
                        "data": hex(1_000_000)[2:],
                    }
                ],
            }
        )

    async def tron_block(_: web.Request) -> web.Response:
        return web.json_response({"block_header": {"raw_data": {"number": 130}}})

    app = web.Application()
    app.router.add_post("/rpc", rpc)
    app.router.add_post("/wallet/gettransactioninfobyid", tron_info)
    app.router.add_post("/wallet/getnowblock", tron_block)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    settings = SimpleNamespace(
        usdt_bep20_rpc_url=f"http://127.0.0.1:{port}/rpc",
        usdt_bep20_token_contract=token,
        usdt_arbitrum_rpc_url=f"http://127.0.0.1:{port}/rpc",
        usdt_arbitrum_token_contract=token,
        usdt_tron_api_url=f"http://127.0.0.1:{port}",
        usdt_tron_api_key="",
        usdt_trc20_token_contract=tron_address,
        usdt_verify_timeout_seconds=5,
        usdt_token_decimals=6,
        usdt_evm_confirmations=12,
        usdt_tron_confirmations=20,
    )
    try:
        result = await verify_usdt_payment(
            settings,
            method_id="usdt_bep20",
            proof="0x" + "1" * 64,
            destination=destination,
            expected_amount_minor=100,
        )
        assert result.state == "confirmed" and result.confirmations == 12
        state["latest"] = 105
        result = await verify_usdt_payment(
            settings,
            method_id="usdt_bep20",
            proof="0x" + "1" * 64,
            destination=destination,
            expected_amount_minor=100,
        )
        assert result.state == "pending" and result.confirmations == 6
        state["latest"] = 111
        state["evm_amount"] = 2_000_000
        result = await verify_usdt_payment(
            settings,
            method_id="usdt_arbitrum",
            proof="0x" + "2" * 64,
            destination=destination,
            expected_amount_minor=100,
        )
        assert result.state == "rejected" and result.reason == "到账金额不匹配"
        state["evm_amount"] = 1_000_000
        state["evm_status"] = "0x0"
        result = await verify_usdt_payment(
            settings,
            method_id="usdt_bep20",
            proof="0x" + "3" * 64,
            destination=destination,
            expected_amount_minor=100,
        )
        assert result.state == "rejected" and result.reason == "链上交易执行失败"
        result = await verify_usdt_payment(
            settings,
            method_id="usdt_trc20",
            proof="4" * 64,
            destination=tron_address,
            expected_amount_minor=100,
        )
        assert result.state == "confirmed" and result.confirmations == 31
        result = await verify_usdt_payment(
            settings,
            method_id="usdt_binance",
            proof="ORDER123",
            destination="123456",
            expected_amount_minor=100,
        )
        assert result.state == "unsupported"
        print("isolated_usdt_verification_ok")
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
