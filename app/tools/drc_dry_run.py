"""Dry-run helper for the external rule-checking team.

Given a SMDR2 ``product_id``, materialise the same DRC handoff bundle
the worker would produce, run ``app.external_rule_check.check_rules``
against it, and pretty-print the result. No SMDR2 server needed — the
script reads `data/` directly via the in-process stores.

Usage::

    python -m app.tools.drc_dry_run <product_id>
    python -m app.tools.drc_dry_run <product_id> --keep-bundle /tmp/out

The second form keeps the materialised bundle around for inspection so
the external team can ``unzip``-style poke at the manifest / DXFs /
Match JSONs their function consumed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def _print_result(result: dict) -> None:
    print(json.dumps(result, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.tools.drc_dry_run",
        description="Materialise a product's DRC bundle and run the "
                    "external rule function against it.",
    )
    parser.add_argument("product_id", help="SMDR2 product id (UUID).")
    parser.add_argument(
        "--keep-bundle",
        metavar="DIR",
        default=None,
        help="Copy the materialised bundle to DIR after the run so its "
             "contents can be inspected. The temp bundle is still cleaned "
             "up automatically; this is a snapshot.",
    )
    args = parser.parse_args(argv)

    # Imports inside main so `--help` works without spinning up the
    # SQLite stores.
    from app.drc_bundle import materialise_bundle
    from app.external_rule_check import check_rules
    from app.files import FILE_STORE
    from app.products import PRODUCT_STORE

    product = PRODUCT_STORE.get(args.product_id)
    if product is None:
        print(f"product {args.product_id!r} not found", file=sys.stderr)
        return 1
    files = [f for f in FILE_STORE.list_by_product(product.id) if f.dxf_role]
    if not files:
        print(f"product {product.id!r} has no role-attached DXFs",
              file=sys.stderr)
        return 1
    missing = [f.dxf_role for f in files if not f.match_saved]
    if missing:
        print(f"these roles still need Save Match: "
              f"{', '.join(sorted(set(missing)))}", file=sys.stderr)
        return 1

    try:
        with materialise_bundle(product, files) as bundle_dir:
            if args.keep_bundle:
                dst = Path(args.keep_bundle)
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(bundle_dir, dst)
                print(f"# bundle snapshot: {dst}", file=sys.stderr)
            result = check_rules(product.id, str(bundle_dir))
    except NotImplementedError as e:
        # Friendly message when the stub is still in place; bypass the
        # full traceback since this is the expected pre-handoff state.
        print(f"external rule module not implemented: {e}", file=sys.stderr)
        return 2

    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
