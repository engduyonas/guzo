from datetime import timedelta

from guzo.catalog.models import Product
from guzo.common.clock import utcnow
from guzo.config import get_settings
from guzo.errors import NotFound, Unprocessable
from guzo.pricing.models import Quote, QuoteRequest
from guzo.pricing.strategies import STRATEGIES


async def create_quote(booker_id: str, request: QuoteRequest) -> Quote:
    now = utcnow()
    product = await Product.find_one(
        Product.city_id == request.city_id, Product.code == request.product_code
    )
    if product is None or not product.active:
        raise NotFound("product not found", code="unknown_product")
    if request.vehicle_class not in product.vehicle_classes:
        raise Unprocessable("vehicle class not offered for this product", code="vehicle_class")
    if request.scheduled_at <= now:
        raise Unprocessable("scheduled_at must be in the future", code="scheduled_in_past")
    price = await STRATEGIES[product.pricing_strategy].quote(request, product)
    quote = Quote(
        booker_id=booker_id,
        request=request,
        price=price,
        expires_at=now + timedelta(minutes=get_settings().quote_ttl_minutes),
        created_at=now,
    )
    await quote.insert()
    return quote
