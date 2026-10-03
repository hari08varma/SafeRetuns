"""Deterministic synthetic data: customers, products and orders for dev, tests and evals.

Money is always integer paise. Same seed + same as_of date => identical output.
Usage: uv run python -m returns_agent.seed.generator --out seed.json --customers 50
"""

import argparse
import json
import random
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel

Category = Literal[
    "apparel", "footwear", "electronics", "beauty", "innerwear", "perishable", "customised"
]
Tier = Literal["standard", "silver", "gold"]
Status = Literal["delivered", "in_transit", "rto", "cancelled"]
Payment = Literal["cod", "upi", "card", "wallet"]
TIERS: list[Tier] = ["standard", "silver", "gold"]
STATUSES: list[Status] = ["delivered", "in_transit", "rto", "cancelled"]
PAYMENTS: list[Payment] = ["cod", "upi", "card", "wallet"]
NON_RETURNABLE: set[str] = {"innerwear", "perishable", "customised"}

# (sku prefix, title, category, price in rupees, variants)
CATALOG: list[tuple[str, str, Category, int, list[str]]] = [
    ("KUR", "Cotton Kurta", "apparel", 1299, ["S", "M", "L", "XL"]),
    ("JNS", "Slim Fit Jeans", "apparel", 1899, ["30", "32", "34", "36"]),
    ("TSH", "Graphic T-Shirt", "apparel", 599, ["S", "M", "L", "XL"]),
    ("SNK", "Running Sneakers", "footwear", 3499, ["7", "8", "9", "10"]),
    ("SND", "Leather Sandals", "footwear", 1499, ["7", "8", "9"]),
    ("EBD", "Wireless Earbuds", "electronics", 2999, ["black", "white"]),
    ("PHN", "Smartphone 128GB", "electronics", 42999, ["blue", "black"]),
    ("SWT", "Smartwatch", "electronics", 5999, ["black"]),
    ("SRM", "Vitamin C Serum", "beauty", 799, ["30ml"]),
    ("INR", "Innerwear Pack", "innerwear", 699, ["M", "L"]),
    ("MNG", "Alphonso Mangoes 1kg", "perishable", 899, ["1kg"]),
    ("MUG", "Personalised Mug", "customised", 499, ["11oz"]),
]
FIRST = [
    "Aarav",
    "Priya",
    "Rohan",
    "Ananya",
    "Vikram",
    "Sneha",
    "Arjun",
    "Kavya",
    "Rahul",
    "Meera",
    "Karthik",
    "Divya",
    "Harish",
    "Lakshmi",
    "Imran",
    "Fatima",
    "Suresh",
    "Neha",
]
LAST = ["Sharma", "Reddy", "Patel", "Iyer", "Singh", "Nair", "Gupta", "Rao", "Khan", "Das"]
CITIES = [
    ("Hyderabad", "500081"),
    ("Bengaluru", "560034"),
    ("Mumbai", "400050"),
    ("Delhi", "110017"),
    ("Chennai", "600040"),
    ("Pune", "411014"),
    ("Kolkata", "700091"),
]


class Customer(BaseModel):
    id: str
    name: str
    phone: str
    email: str
    city: str
    pincode: str
    tier: Tier
    account_age_days: int


class Product(BaseModel):
    sku: str
    title: str
    category: Category
    variant: str
    price_minor: int
    returnable: bool
    image_uri: str


class OrderItem(BaseModel):
    id: str
    sku: str
    qty: int
    unit_price_minor: int
    discount_alloc_minor: int
    final_sale: bool


class Order(BaseModel):
    id: str
    customer_id: str
    placed_at: datetime
    delivered_at: datetime | None
    status: Status
    payment_method: Payment
    coupon_minor: int
    total_minor: int
    items: list[OrderItem]


class SeedData(BaseModel):
    seed: int
    as_of: datetime
    customers: list[Customer]
    products: list[Product]
    orders: list[Order]


def allocate_discount(line_totals: list[int], discount: int) -> list[int]:
    """Split a discount across lines in proportion to line totals; sums exactly."""
    total = sum(line_totals)
    if total == 0 or discount == 0:
        return [0] * len(line_totals)
    shares = [discount * lt // total for lt in line_totals]
    shares[line_totals.index(max(line_totals))] += discount - sum(shares)
    return shares


def generate(
    seed: int = 42,
    customers: int = 50,
    as_of: datetime = datetime(2026, 10, 1, tzinfo=UTC),
) -> SeedData:
    rng = random.Random(seed)
    products = [
        Product(
            sku=f"{prefix}-{variant}",
            title=title,
            category=cat,
            variant=variant,
            price_minor=price * 100,
            returnable=cat not in NON_RETURNABLE,
            image_uri=f"catalog/{prefix.lower()}-{variant.lower()}.jpg",
        )
        for prefix, title, cat, price, variants in CATALOG
        for variant in variants
    ]
    custs: list[Customer] = []
    orders: list[Order] = []
    for c in range(1, customers + 1):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        city, pincode = rng.choice(CITIES)
        cust = Customer(
            id=f"CUST-{c:04d}",
            name=f"{first} {last}",
            phone=f"+91 9{rng.randint(100000000, 999999999)}",
            email=f"{first.lower()}.{last.lower()}{c}@example.com",
            city=city,
            pincode=pincode,
            tier=rng.choices(TIERS, [70, 20, 10])[0],
            account_age_days=rng.randint(1, 2000),
        )
        custs.append(cust)
        for _ in range(rng.randint(1, 4)):
            orders.append(_order(rng, len(orders) + 1, cust.id, products, as_of))
    return SeedData(seed=seed, as_of=as_of, customers=custs, products=products, orders=orders)


def _order(
    rng: random.Random, n: int, customer_id: str, products: list[Product], as_of: datetime
) -> Order:
    picked = rng.sample(products, rng.choices([1, 2, 3], [60, 30, 10])[0])
    qtys = [rng.choices([1, 2], [85, 15])[0] for _ in picked]
    line_totals = [p.price_minor * q for p, q in zip(picked, qtys, strict=True)]
    subtotal = sum(line_totals)
    coupon = min(subtotal // 10, 50000) if rng.random() < 0.3 else 0
    allocations = allocate_discount(line_totals, coupon)
    placed_at = as_of - timedelta(days=rng.randint(1, 60), hours=rng.randint(0, 23))
    status: Status = rng.choices(STATUSES, [80, 10, 5, 5])[0]
    delivered_at = placed_at + timedelta(days=rng.randint(2, 7)) if status == "delivered" else None
    if delivered_at and delivered_at > as_of:
        delivered_at, status = None, "in_transit"
    items = [
        OrderItem(
            id=f"ORD-{n:05d}-{i}",
            sku=p.sku,
            qty=q,
            unit_price_minor=p.price_minor,
            discount_alloc_minor=alloc,
            final_sale=p.category == "apparel" and rng.random() < 0.1,
        )
        for i, (p, q, alloc) in enumerate(zip(picked, qtys, allocations, strict=True), start=1)
    ]
    return Order(
        id=f"ORD-{n:05d}",
        customer_id=customer_id,
        placed_at=placed_at,
        delivered_at=delivered_at,
        status=status,
        payment_method=rng.choices(PAYMENTS, [35, 35, 20, 10])[0],
        coupon_minor=coupon,
        total_minor=subtotal - coupon,
        items=items,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="seed.json")
    parser.add_argument("--customers", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    data = generate(seed=args.seed, customers=args.customers)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data.model_dump(mode="json"), f, indent=2)
    print(
        f"wrote {len(data.customers)} customers, {len(data.products)} products, "
        f"{len(data.orders)} orders to {args.out}"
    )


if __name__ == "__main__":
    main()
