from returns_agent.seed.generator import NON_RETURNABLE, allocate_discount, generate


def test_deterministic() -> None:
    assert generate(seed=7, customers=10) == generate(seed=7, customers=10)
    assert generate(seed=7, customers=10) != generate(seed=8, customers=10)


def test_discount_allocation_sums_exactly() -> None:
    assert allocate_discount([129900, 59900, 59900], 24970) == [12990, 5990, 5990]
    assert allocate_discount([333, 333, 334], 100) == [33, 33, 34]  # remainder to largest line
    for lines, discount in [([100], 7), ([333, 333, 334], 100), ([1, 2, 3], 0), ([0, 0], 0)]:
        assert sum(allocate_discount(lines, discount)) == discount


def test_order_invariants() -> None:
    data = generate(seed=42, customers=50)
    products = {p.sku: p for p in data.products}
    assert data.orders
    for order in data.orders:
        line_totals = sum(i.unit_price_minor * i.qty for i in order.items)
        assert order.total_minor == line_totals - order.coupon_minor
        assert sum(i.discount_alloc_minor for i in order.items) == order.coupon_minor
        assert all(isinstance(i.unit_price_minor, int) for i in order.items)
        if order.status == "delivered":
            assert order.delivered_at is not None
            assert order.placed_at < order.delivered_at <= data.as_of
        else:
            assert order.delivered_at is None
        for item in order.items:
            assert item.sku in products


def test_covers_cases_the_evals_need() -> None:
    data = generate(seed=42, customers=50)
    assert any(o.payment_method == "cod" for o in data.orders)
    assert any(len(o.items) > 1 for o in data.orders)
    assert any(o.coupon_minor > 0 for o in data.orders)
    assert any(o.status != "delivered" for o in data.orders)
    assert {p.category for p in data.products if not p.returnable} == NON_RETURNABLE
