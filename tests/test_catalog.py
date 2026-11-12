import asyncio
from decimal import Decimal
import json
import httpx
import pytest
from airlock.catalog import Channel3Catalog, INJECTION, prompt_budget


def test_channel3_wire_protocol_offer_mapping_and_price_filter():
    async def scenario():
        requests = []
        def transport(request):
            requests.append(request)
            return httpx.Response(200, json={"products":[
                {"id":"p1", "title":"USB-C desktop charger", "description":"A real catalog description", "category":{"slug":"electronics-chargers", "title":"Chargers"}, "images":[{"url":"https://cdn.example/charger.jpg"}], "offers":[
                    {"url":"https://store.example/p1", "domain":"store.example", "price":{"price":45,"currency":"USD"}, "availability":"InStock"},
                    {"url":"https://seller.example/p1", "domain":"seller.example", "price":{"price":32.5,"currency":"USD"}, "availability":"InStock"}]},
                {"id":"p2", "title":"No offers", "offers":[]},
                {"id":"p3", "title":"Over budget", "offers":[{"url":"https://store.example/p3", "domain":"store.example", "price":{"price":99,"currency":"USD"}}]},
                {"id":"p4", "title":"Unavailable", "offers":[{"url":"https://store.example/p4", "domain":"store.example", "price":{"price":25,"currency":"USD"}, "availability":"OutOfStock"}]},
            ],"next_page_token":None})
        catalog = Channel3Catalog("test-channel3-key", transport=httpx.MockTransport(transport))
        products = await catalog.search("USB-C charger", Decimal("40"))
        assert len(requests) == 1
        request = requests[0]
        assert request.method == "POST" and str(request.url) == "https://api.trychannel3.com/v1/search"
        assert request.headers["x-api-key"] == "test-channel3-key"
        assert json.loads(request.content) == {"query":"USB-C charger", "limit":6,"filters":{"price":{"max_price":40.0}},"config":{"country":"US","currency":"USD"}}
        assert len(products) == 2
        real = products[1]
        assert (real["price"],real["merchant"],real["url"],real["category"]) == ("32.50","seller.example","https://seller.example/p1","electronics-chargers")
        assert real["image"] == "https://cdn.example/charger.jpg"
        assert INJECTION not in json.dumps(products)
        assert INJECTION in catalog.page(products[0])["page_text"]
        assert catalog.page(products[0])["checkout_policy"] == INJECTION
        assert INJECTION not in catalog.page(real)["page_text"]
        assert "checkout_policy" not in catalog.page(real)
        await catalog.close()
    asyncio.run(scenario())


def test_no_channel3_key_uses_local_fixture_without_http():
    async def scenario():
        def forbidden(request):
            raise AssertionError("No-key catalog must not make HTTP requests")
        catalog = Channel3Catalog(transport=httpx.MockTransport(forbidden))
        products = await catalog.search("charger", Decimal("30"))
        assert catalog.source == "local_fixture"
        assert len(products) >= 2 and all(Decimal(p["price"]) <= 30 for p in products)
        assert all(p["image"] and p["url"] and p["category"] for p in products)
        assert len(products) <= 6
        await catalog.close()
    asyncio.run(scenario())


def test_configured_channel3_failure_is_not_silent_fixture_success():
    async def scenario():
        catalog = Channel3Catalog("test-key", transport=httpx.MockTransport(lambda _:httpx.Response(401,json={"detail":"Unauthorized"})))
        with pytest.raises(httpx.HTTPStatusError):
            await catalog.search("charger", Decimal("40"))
        await catalog.close()
    asyncio.run(scenario())


def test_budget_extraction_and_ineligible_marketplace_listing():
    assert prompt_budget("Buy me a USB-C charger under $40") == 40
    assert prompt_budget("Buy me noise-cancelling headphones under $300") == 300
    assert prompt_budget("hello") is None
    async def scenario():
        catalog = Channel3Catalog()
        products = await catalog.search("charger", Decimal("20"))
        listing = next(p for p in products if p.get("marketplace"))
        assert (listing["name"], listing["price"]) == ("Volt 65W GaN USB-C Charger", "19.99")
        below_listing_price = await catalog.search("charger", Decimal("19.98"))
        assert all(not p.get("marketplace") for p in below_listing_price)
        await catalog.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("query", ["charger", "CHARGERS", "USB-C", "usb c", "GaN", "POWER ADAPTER", "power adapters"])
def test_marketplace_listing_only_for_charging_queries(query):
    async def scenario():
        catalog = Channel3Catalog()
        products = await catalog.search(query, Decimal("300"))
        listing = next(item for item in products if item.get("marketplace"))
        assert listing["url"] == "/store/volt-65w"
        await catalog.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("query", ["headphones", "NOISE-CANCELLING HEADPHONES", "shoes", "organic tea"])
def test_non_charging_live_search_keeps_only_channel3_offers(query):
    async def scenario():
        offer_url = "https://merchant.example/products/headphones?offer=channel3"
        response = {"products":[{"id":"real-headphones", "title":"Noise-cancelling headphones", "offers":[{"url":offer_url, "domain":"merchant.example", "price":{"price":249.99,"currency":"USD"}}]}]}
        catalog = Channel3Catalog("test-key", transport=httpx.MockTransport(lambda _:httpx.Response(200,json=response)))
        products = await catalog.search(query, Decimal("300"))
        assert len(products) == 1
        assert products[0]["id"] == "real-headphones" and products[0]["url"] == offer_url
        assert not products[0].get("marketplace")
        assert INJECTION not in catalog.page(products[0])["page_text"]
        await catalog.close()
    asyncio.run(scenario())


def test_headphone_fixture_matches_shopping_request_without_poisoned_listing():
    async def scenario():
        catalog = Channel3Catalog()
        products = await catalog.search("Buy me noise-cancelling headphones under $300", Decimal("300"))
        assert products and all("headphones" in product["name"].lower() for product in products)
        assert all(Decimal("200") <= Decimal(product["price"]) <= Decimal("300") for product in products)
        assert all(not product.get("marketplace") for product in products)
        await catalog.close()
    asyncio.run(scenario())


def test_affiliate_redirects_are_unwrapped_to_direct_merchant_urls():
    from airlock.catalog import direct_merchant_url
    wrapped = "https://go.skimresources.com/?id=1X2&url=https%3A%2F%2Fwww.kohls.com%2Fproduct%2Fprd-1.jsp%3Fc3_click_id%3Dabc%26c3_vendor%3Dx&xcust=abc&sref=x"
    assert direct_merchant_url(wrapped) == "https://www.kohls.com/product/prd-1.jsp"
    assert direct_merchant_url("https://www.belkin.com/p/c?c3_click_id=1&color=white") == "https://www.belkin.com/p/c?color=white"
