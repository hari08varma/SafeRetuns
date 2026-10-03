"""Refund amounts in integer paise. Pure functions; the only place money is computed.

Guarantees (property-tested):
- a refund never exceeds what was paid for the returned units;
- returning a line in several partial returns refunds exactly the same total as one full return;
- allocations across payment methods always sum to the refund total.
"""

from typing import Literal

from pydantic import BaseModel, Field

Destination = Literal["source", "store_credit", "bank_transfer", "upi"]


class RefundLine(BaseModel):
    unit_price_minor: int = Field(ge=0)
    qty_ordered: int = Field(ge=1)
    line_discount_minor: int = Field(ge=0)  # coupon/BOGO share already allocated to this line
    qty_returning: int = Field(ge=1)
    qty_already_returned: int = Field(default=0, ge=0)


class Payment(BaseModel):
    method: str  # card | upi | wallet | netbanking | cod | store_credit | ...
    amount_minor: int = Field(ge=0)


class Allocation(BaseModel):
    method: str
    amount_minor: int


class RefundBreakdown(BaseModel):
    items_minor: int
    restocking_fee_minor: int
    shipping_minor: int
    total_minor: int
    allocations: list[Allocation]


class RefundError(ValueError):
    pass


def line_paid(line: RefundLine) -> int:
    return line.unit_price_minor * line.qty_ordered - line.line_discount_minor


def refundable_for_line(line: RefundLine) -> int:
    """Cumulative-floor split: sum over any sequence of partial returns == line_paid."""
    if line.qty_already_returned + line.qty_returning > line.qty_ordered:
        raise RefundError("returning more units than were ordered")
    paid = line_paid(line)
    if paid < 0:
        raise RefundError("line discount exceeds line price")
    after = line.qty_already_returned + line.qty_returning
    return paid * after // line.qty_ordered - paid * line.qty_already_returned // line.qty_ordered


def _split(total: int, weights: list[int]) -> list[int]:
    """Largest-remainder split of `total` in proportion to `weights` (sums exactly)."""
    weight_sum = sum(weights)
    if weight_sum == 0:
        return [0] * len(weights)
    shares = [total * w // weight_sum for w in weights]
    remainders = sorted(range(len(weights)), key=lambda i: -(total * weights[i] % weight_sum))
    for i in remainders[: total - sum(shares)]:
        shares[i] += 1
    return shares


def compute_refund(
    lines: list[RefundLine],
    payments: list[Payment],
    *,
    restocking_fee_pct: int = 0,
    shipping_fee_minor: int = 0,
    shipping_refundable: bool = True,
    returns_whole_order: bool = False,
    already_refunded_minor: int = 0,
    destination: Destination = "source",
    cod_destination: Literal["bank_transfer", "upi", "store_credit"] = "bank_transfer",
) -> RefundBreakdown:
    if not lines:
        raise RefundError("nothing to refund")
    if not 0 <= restocking_fee_pct <= 100:
        raise RefundError("restocking fee must be 0-100%")
    items = sum(refundable_for_line(line) for line in lines)
    fee = items * restocking_fee_pct // 100  # rounds down, in the customer's favour
    shipping = shipping_fee_minor if (shipping_refundable and returns_whole_order) else 0
    paid_total = sum(p.amount_minor for p in payments)
    total = min(items - fee + shipping, max(0, paid_total - already_refunded_minor))

    if destination != "source":
        allocations = [Allocation(method=destination, amount_minor=total)] if total else []
    else:
        shares = _split(total, [p.amount_minor for p in payments])
        merged: dict[str, int] = {}
        for payment, share in zip(payments, shares, strict=True):
            # Cash cannot go back to its source; it goes where the customer chose.
            method = cod_destination if payment.method == "cod" else payment.method
            merged[method] = merged.get(method, 0) + share
        allocations = [Allocation(method=m, amount_minor=a) for m, a in merged.items() if a]
    return RefundBreakdown(
        items_minor=items,
        restocking_fee_minor=fee,
        shipping_minor=shipping,
        total_minor=total,
        allocations=allocations,
    )
