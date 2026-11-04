#!/usr/bin/env python3
"""Rewrite a Portuguese `prumo.yml` (or profile) into the English contract.

The prumo was written in Portuguese and its configuration carried Portuguese keys. The
rewrite into English renames them, and renaming a contract breaks every file already
written against it.

`providers.py` accepts both for one cycle, and says so on every run. This script closes
the loop: it rewrites the file so the warning stops, and so the next person to read the
configuration reads one language instead of two.

It is deliberately **not** automatic. A tool that silently rewrites a file a human wrote
removes the moment where that human sees what changed — and this file is the one place
where an installation says where its repositories live and who its provider is. You run
it, you read the diff, you commit it.

Usage:
    migrate_config.py prumo.yml                 # print the rewritten file
    migrate_config.py prumo.yml --write         # rewrite in place, keeping a .bak
    migrate_config.py profiles/*.yml --write    # profiles too
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from providers import LEGACY_FIELDS, LEGACY_SECTIONS, LEGACY_TRACKER_TYPES

# The Portuguese spelling is the KEY — it is what we recognise — and the English one is
# the value. A blanket rename over this file turns every key into its own value and the
# migrator silently stops migrating; it has happened, twice. `test_migrate_config.py`
# pins it.
PROFILE_KEYS = {
    "perfil": "profile", "versao": "version", "confirmado_por": "confirmed_by",
    "confirmado_em": "confirmed_at", "familia": "family", "manifestos": "manifests",
    "convencoes": "conventions", "registro_de_prova": "proof_registry",
    "raiz": "root", "observacao": "note", "documentacao_viva": "living_docs",
    "checagens": "checks", "comando": "command", "obrigatoria": "required",
    "vermelhos_preexistentes": "preexisting_reds",
    "dados_sensiveis": "sensitive_data", "campos": "fields",
    "destinos_proibidos": "forbidden_destinations", "tamanho": "size",
    "aceitavel": "acceptable", "dividir": "split",
    "teto_de_arquivo": "file_ceiling", "revisor_local": "local_reviewer",
    "ancoras": "anchors", "caminhos": "paths", "padroes": "patterns",
    "nao_se_aplica": "not_applicable", "motivo": "reason", "aprovador": "approver",
    "data": "date", "pendencias": "pending", "tipo": "type", "marcador": "marker",
    "validade": "expires", "dispensa": "waiver", "verificacao": "verification",
    "modelos": "models", "classe": "class", "espera": "expects", "card": "item",
    # The five check ROLES are keys under `checks:` and part of the contract — the judge
    # and the preflight look them up by name.
    "segredos": "secrets", "estatico": "static",
    "dependencias": "dependencies", "padrao_de_falha": "failure_pattern",
}

# Values that are part of the contract, not free text. A profile saying
# `status: rascunho` has to become `status: draft` or the judge stops recognising it.
VALUES = {
    "rascunho": "draft", "confirmado": "confirmed", "ausente": "absent",
    "specs-no-repo": "specs-in-repo", "disparar": "fire", "silencio": "silence",
    "agente": "agent",
}


def rewrite(text: str) -> tuple[str, list[str]]:
    """Rename keys in the raw TEXT, not in a parsed tree.

    Parsing and re-dumping would be shorter and would throw away every comment in the
    file — and in a `prumo.yml` the comments are half the documentation: they say why a
    path is relative, what a preset carries, which provider is which. A migration that
    costs you the comments is one nobody runs twice.
    """
    changed: list[str] = []
    out = text

    def swap(old: str, new: str, label: str) -> None:
        nonlocal out
        # Only at the start of a key: `  old:` — never inside a value or a comment.
        pattern = re.compile(rf"^(\s*(?:-\s+)?){re.escape(old)}(\s*:)", re.M)
        if pattern.search(out):
            out = pattern.sub(rf"\1{new}\2", out)
            changed.append(label)

    for old, new in {**LEGACY_SECTIONS, **PROFILE_KEYS}.items():
        if old != new:
            swap(old, new, old)
    for section, mapping in LEGACY_FIELDS.items():
        for old, new in mapping.items():
            if old != new:
                swap(old, new, f"{section}.{old}")

    for old, new in LEGACY_TRACKER_TYPES.items():
        pattern = re.compile(rf"^(\s*type\s*:\s*){re.escape(old)}\s*$", re.M)
        if pattern.search(out):
            out = pattern.sub(rf"\g<1>{new}", out)
            changed.append(f"type: {old}")

    # `status:` and `type:` carry a contract value; so does `expects:`, which the eval
    # set uses. A value elsewhere is free text and is left alone — translating prose
    # inside a profile is not this tool's job.
    for old, new in VALUES.items():
        if old == new:
            continue
        pattern = re.compile(
            rf"^(\s*(?:status|type|expects|espera)\s*:\s*){re.escape(old)}\s*$", re.M)
        if pattern.search(out):
            out = pattern.sub(rf"\g<1>{new}", out)
            changed.append(f"value {old}")

    return out, sorted(set(changed))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="+")
    p.add_argument("--write", action="store_true",
                   help="rewrite in place, keeping the original as `.bak`")
    args = p.parse_args()

    touched = 0
    for name in args.files:
        path = pathlib.Path(name)
        if not path.is_file():
            print(f"✗ {path}: not a file")
            continue
        original = path.read_text(encoding="utf-8")
        new, changed = rewrite(original)
        if not changed:
            print(f"· {path}: already in English")
            continue
        touched += 1
        if args.write:
            path.with_suffix(path.suffix + ".bak").write_text(original, encoding="utf-8")
            path.write_text(new, encoding="utf-8")
            print(f"✓ {path}: {len(changed)} keys — original kept at {path.name}.bak")
        else:
            print(f"--- {path}: {len(changed)} keys would change ---")
            print(new)
        for c in changed:
            print(f"    {c}")
    if touched and not args.write:
        print("\nNothing was written. Re-run with --write once the diff looks right.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
