import httpx
import pytest

from bukmatika.main import app, settings


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
async def test_web_origin_can_preflight_provider_mutations(method: str) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        response = await client.options(
            "/v1/ai/routing-policy",
            headers={
                "Origin": settings.web_origin,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == settings.web_origin
    allowed_methods = {
        item.strip()
        for item in response.headers["access-control-allow-methods"].split(",")
    }
    assert method in allowed_methods
