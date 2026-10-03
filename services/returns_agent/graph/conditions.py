"""Minimal, safe JSONLogic evaluator (no code execution).

Supported operators: var, ==, !=, <, <=, >, >=, and, or, !, !!, in, +, -.
Used for graph edge conditions now and policy rules in Phase 2.
"""

import operator
from collections.abc import Callable
from typing import Any


class ConditionError(ValueError):
    pass


def resolve_var(path: Any, data: dict[str, Any]) -> Any:
    default = None
    if isinstance(path, list):
        path, default = (path + [None])[:2]
    if path in (None, ""):
        return data
    current: Any = data
    for part in str(path).split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return default
    return current


def evaluate(logic: Any, data: dict[str, Any]) -> Any:
    if not isinstance(logic, dict):
        return logic
    if len(logic) != 1:
        raise ConditionError(f"rule must have exactly one operator: {logic!r}")
    op, raw_args = next(iter(logic.items()))
    if op == "var":
        return resolve_var(raw_args, data)
    args = raw_args if isinstance(raw_args, list) else [raw_args]

    if op == "and":
        result: Any = True
        for a in args:
            result = evaluate(a, data)
            if not result:
                return result
        return result
    if op == "or":
        result = False
        for a in args:
            result = evaluate(a, data)
            if result:
                return result
        return result

    values = [evaluate(a, data) for a in args]
    if op == "!":
        return not values[0]
    if op == "!!":
        return bool(values[0])
    if op == "==":
        return bool(values[0] == values[1])
    if op == "!=":
        return bool(values[0] != values[1])
    if op in ("<", "<="):
        cmp: Callable[[Any, Any], bool] = operator.lt if op == "<" else operator.le
        try:
            return all(cmp(values[i], values[i + 1]) for i in range(len(values) - 1))
        except TypeError:
            return False  # e.g. comparing against a missing (None) value
    if op in (">", ">="):
        try:
            return values[0] > values[1] if op == ">" else values[0] >= values[1]
        except TypeError:
            return False
    if op in ("+", "-"):
        if any(isinstance(v, bool) or not isinstance(v, int | float) for v in values):
            raise ConditionError(f"'{op}' needs numbers: {values!r}")
        if op == "+":
            return sum(values)
        return -values[0] if len(values) == 1 else values[0] - values[1]
    if op == "in":
        container = values[1]
        return container is not None and values[0] in container
    raise ConditionError(f"unsupported operator: {op}")
