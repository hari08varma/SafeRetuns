import pytest

from returns_agent.graph.conditions import ConditionError, evaluate

DATA = {"facts": {"eligible": True, "days": 12, "category": "apparel", "tags": ["gift"]}}


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ({"var": "facts.days"}, 12),
        ({"var": ["facts.missing", "dflt"]}, "dflt"),
        ({"==": [{"var": "facts.eligible"}, True]}, True),
        ({"!=": [{"var": "facts.category"}, "innerwear"]}, True),
        ({"<=": [{"var": "facts.days"}, 30]}, True),
        ({"<": [0, {"var": "facts.days"}, 10]}, False),  # between
        ({">": [{"var": "facts.days"}, 30]}, False),
        ({">=": [{"var": "facts.days"}, 12]}, True),
        ({"and": [{"var": "facts.eligible"}, {"<": [{"var": "facts.days"}, 30]}]}, True),
        ({"or": [False, {"==": [{"var": "facts.category"}, "apparel"]}]}, True),
        ({"!": [{"var": "facts.eligible"}]}, False),
        ({"!!": [{"var": "facts.tags"}]}, True),
        ({"in": ["gift", {"var": "facts.tags"}]}, True),
        ({"in": ["x", {"var": "facts.nope"}]}, False),
        ({"<": [{"var": "facts.nope"}, 5]}, False),  # missing value never passes a comparison
    ],
)
def test_operators(rule: dict[str, object], expected: object) -> None:
    assert evaluate(rule, DATA) == expected


def test_rejects_unknown_operator() -> None:
    with pytest.raises(ConditionError):
        evaluate({"exec": ["rm -rf /"]}, DATA)


def test_rejects_multiple_operators() -> None:
    with pytest.raises(ConditionError):
        evaluate({"==": [1, 1], "!=": [1, 2]}, DATA)
