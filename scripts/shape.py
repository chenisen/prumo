#!/usr/bin/env python3
"""The shape of an item decides how deep the plan goes before anything is written.

Six shapes, closed. Each carries the plan's depth, whether a person approves the plan
before the first line, the kinds that may deliver it, the factor on the profile's budget
and the effort the review reads it with. The shape is never inferred from the item's
text: it is the tracker's type, mapped in `prumo.yml`, or the invocation's `--shape`.

Usage (as a module):
    from shape import SHAPES, resolve
"""
from __future__ import annotations

SHAPES: dict[str, dict] = {
    "debt":     {"plan": "measure",   "gate": False, "kinds": ("coverage-gap", "behaviour-change"),
                 "budget": 1.0, "review": "minimal",  "deliverable": True,  "decomposes": False},
    "bug":      {"plan": "reproduce", "gate": False, "kinds": ("behaviour-change",),
                 "budget": 1.0, "review": "standard", "deliverable": True,  "decomposes": False},
    "hotfix":   {"plan": "reproduce", "gate": True,  "kinds": ("behaviour-change",),
                 "budget": 0.5, "review": "full",     "deliverable": True,  "decomposes": False,
                 "base": "hotfix_base"},
    "refactor": {"plan": "preserve",  "gate": True,  "kinds": ("refactor",),
                 "budget": 2.0, "review": "standard", "deliverable": True,  "decomposes": False},
    "story":    {"plan": "design",    "gate": True,  "kinds": ("behaviour-change",),
                 "budget": 3.0, "review": "full",     "deliverable": True,  "decomposes": True},
    "epic":     {"plan": "decompose", "gate": True,  "kinds": (),
                 "budget": 0.0, "review": None,       "deliverable": False, "decomposes": True},
}


class UnknownShape(ValueError):
    """A shape outside the six, or a tracker type the installation did not map."""


def resolve(shape: str | None, tracker_type: str | None, mapping: dict | None) -> str:
    """`--shape` wins; else the tracker's type through the installation's mapping; else
    a refusal that names what was received and what is known."""
    if shape:
        if shape not in SHAPES:
            raise UnknownShape(f"shape `{shape}` is not one of: {', '.join(SHAPES)}")
        return shape
    if tracker_type is not None:
        hit = (mapping or {}).get(tracker_type)
        if hit in SHAPES:
            return hit
        raise UnknownShape(
            f"tracker type `{tracker_type}` maps to no shape — declare it under "
            f"`tracker.shapes` in prumo.yml (one of: {', '.join(SHAPES)})")
    raise UnknownShape(f"no shape: pass --shape or --type (one of: {', '.join(SHAPES)})")


def budget_for(block: dict, shape: str) -> dict:
    """The profile's budget, scaled by the shape, rounded up — a half of 3 files is 2."""
    import math
    f = SHAPES[shape]["budget"]
    b = block.get("budget") or {}
    return {"max_files": math.ceil(b.get("max_files", 0) * f) if f else 0,
            "max_lines": math.ceil(b.get("max_lines", 0) * f) if f else 0}
