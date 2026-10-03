"""Customer-facing money formatting (Indian digit grouping). Paise never reach a customer."""


def format_inr(paise: int) -> str:
    """129900 -> '₹1,299.00'; 12999900 -> '₹1,29,999.00' (lakh/crore grouping)."""
    sign = "-" if paise < 0 else ""
    rupees, rem = divmod(abs(int(paise)), 100)
    digits = str(rupees)
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        head, group = head[:-2], head[-2:]
        groups.insert(0, group)
    if head:
        groups.insert(0, head)
    grouped = ",".join([*groups, tail]) if groups else tail
    return f"{sign}₹{grouped}.{rem:02d}"
