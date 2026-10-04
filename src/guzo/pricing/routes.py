from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from guzo.common.ids import object_id
from guzo.common.money import Money
from guzo.errors import NotFound
from guzo.identity.deps import Booker
from guzo.pricing.models import Quote, QuoteRequest
from guzo.pricing.service import create_quote

router = APIRouter(prefix="/quotes", tags=["quotes"])


class QuoteResponse(BaseModel):
    id: str
    request: QuoteRequest
    price: Money
    expires_at: datetime

    @classmethod
    def from_quote(cls, quote: Quote) -> "QuoteResponse":
        return cls(
            id=str(quote.id), request=quote.request, price=quote.price, expires_at=quote.expires_at
        )


@router.post("", status_code=201)
async def post_quote(body: QuoteRequest, user: Booker) -> QuoteResponse:
    return QuoteResponse.from_quote(await create_quote(str(user.id), body))


@router.get("/{quote_id}")
async def get_quote(quote_id: str, user: Booker) -> QuoteResponse:
    quote = await Quote.get(object_id(quote_id, "quote"))
    if quote is None or quote.booker_id != str(user.id):
        raise NotFound("quote not found")
    return QuoteResponse.from_quote(quote)
