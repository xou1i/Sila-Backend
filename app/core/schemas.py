"""Shared Pydantic building blocks. Decimals serialize as JSON strings (Pydantic v2 default)."""

from decimal import Decimal
from typing import Annotated, Generic, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.core.money import MILLIGRAM, SUPPORTED_KARATS

# Weight in grams: > 0, max 3 decimals, fits NUMERIC(10,3).
Grams = Annotated[
    Decimal,
    Field(gt=0, max_digits=10, decimal_places=3, examples=["84.250"]),
    AfterValidator(lambda v: v.quantize(MILLIGRAM)),  # canonical form: "40" → "40.000"
]
# IQD amount: > 0, max 2 decimals, fits NUMERIC(16,2).
AmountIQD = Annotated[
    Decimal,
    Field(gt=0, max_digits=16, decimal_places=2, examples=["1500000"]),
]


def _check_karat(value: int) -> int:
    if value not in SUPPORTED_KARATS:
        raise ValueError("karat must be one of 18, 21, 22, 24")
    return value


# For query strings (a Literal of ints does not coerce "21" there).
KaratParam = Annotated[int, AfterValidator(_check_karat)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class MessageOut(BaseModel):
    message: str
