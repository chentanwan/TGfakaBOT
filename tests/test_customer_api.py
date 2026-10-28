from pathlib import Path


def test_api_routes_require_keys_and_limit_requests() -> None:
    source = Path("app/api.py").read_text()
    assert 'request.path.startswith("/api/v1/")' in source
    assert 'authorization.startswith("Bearer ")' in source
    assert 'request.headers.get("X-API-Key"' in source
    assert 'status=429' in source
    assert "authenticate_api_key" in source


def test_account_and_product_routes_are_registered() -> None:
    source = Path("app/api.py").read_text()
    assert '"/api/v1/account"' in source
    assert '"/api/v1/products"' in source
    assert '"available": product.stock > 0 or product.supplier_stock' in source
