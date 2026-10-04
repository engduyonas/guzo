from enum import StrEnum

from pydantic import BaseModel, ConfigDict, StrictInt


class Currency(StrEnum):
    ETB = "ETB"
    USD = "USD"


class Money(BaseModel):
    """An amount in minor units (santim, cents). Never a float."""

    model_config = ConfigDict(frozen=True)

    amount_minor: StrictInt
    currency: Currency

    def _check(self, other: "Money") -> None:
        if other.currency != self.currency:
            raise ValueError(f"currency mismatch: {self.currency} vs {other.currency}")

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(amount_minor=self.amount_minor + other.amount_minor, currency=self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(amount_minor=self.amount_minor - other.amount_minor, currency=self.currency)

    def percent(self, pct: int) -> "Money":
        """Integer percentage, rounded half up."""
        return Money(amount_minor=(self.amount_minor * pct + 50) // 100, currency=self.currency)

    def is_zero(self) -> bool:
        return self.amount_minor == 0
