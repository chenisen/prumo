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


# --- the size ---------------------------------------------------------------------------
#
# The shape says what was ASKED; the size says how much was TOUCHED, and it is the size
# that decides how much machinery a delivery buys: a two-file fix that ran three whole
# suites of twenty minutes, a surveyor, a plan, a writer, a sceptic and a review was the
# method charging every item the price of its largest one (2026-11-13).
#
#   small     a handful of files and lines, on a shape with no gate, away from the
#             paths the profile calls wide: the tests it touched, no sceptic.
#   standard  everything else that is one item: the whole suite, once; the sceptic, once.
#   large     a story: its tasks are written one after another and proven together.
SMALL_DEFAULT = {"max_files": 3, "max_lines": 80}
SIZES = ("small", "standard", "large")


def small_limits(block: dict) -> dict:
    declared = block.get("small") or {}
    return {k: int(declared.get(k, v)) for k, v in SMALL_DEFAULT.items()}


def size_of(shape: str, files: list[str], lines: int | None, block: dict) -> dict:
    """`{size, reason}` — a pure function over what the change touches. `lines` is None
    before anything is written: the measurement names files, not lines, and the size is
    then provisional; `conclude` asks again over the diff that landed."""
    from common import casa_algum
    rules = SHAPES[shape]
    if rules["decomposes"]:
        return {"size": "large", "reason": f"a {shape} is written task by task"}
    if rules["gate"]:
        return {"size": "standard", "reason": f"a {shape} waits for a person: never small"}
    wide = [f for f in files if casa_algum(f, list(block.get("wide_paths") or []))]
    if wide:
        return {"size": "standard", "reason": f"touches a wide path: {', '.join(wide)}"}
    limit = small_limits(block)
    if len(files) > limit["max_files"]:
        return {"size": "standard", "reason": f"{len(files)} files, small is up to {limit['max_files']}"}
    if lines is not None and lines > limit["max_lines"]:
        return {"size": "standard", "reason": f"{lines} lines, small is up to {limit['max_lines']}"}
    return {"size": "small", "reason": f"{len(files)} file(s)"
            + (f", {lines} line(s)" if lines is not None else "") + ", no wide path"}


def budget_for(block: dict, shape: str) -> dict | None:
    """The profile's budget, scaled by the shape, rounded up — a half of 3 files is 2.
    None when the profile declares none: a budget is a repository's decision, never a
    default of the method, and where nobody decided one no delivery stops for its size."""
    import math
    f = SHAPES[shape]["budget"]
    b = block.get("budget")
    if not b:
        return None
    return {"max_files": math.ceil(b.get("max_files", 0) * f) if f else 0,
            "max_lines": math.ceil(b.get("max_lines", 0) * f) if f else 0}
