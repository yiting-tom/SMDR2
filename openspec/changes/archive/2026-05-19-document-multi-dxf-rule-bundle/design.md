# Design

## Why retroactively spec an already-shipped contract

`c01a923` introduced the merge logic and quietly extended the
`dxfs_by_role[role]` shape. The change was correct and shipped
working code, but no `## Requirement:` block now exists in the
`design-rule-checking` capability that describes:

- What keys a `RoleBundle` dict carries.
- Which keys are "first-file fallbacks" of a list and which are the
  authoritative list.
- The handle-prefix rule (when applied, how it's constructed).
- The invariant that rule logic treats handles as opaque strings.

That gap is a real review-time hazard: anyone reading the spec to
understand "what does a rule actually see" gets the wrong answer.
Adding the requirement retroactively is cheap (no behaviour change),
makes the spec self-consistent, and gives the test
`openspec validate --strict` something to grip on the next time the
merge logic is touched.

## Prefix shape: `{file_id[:8]}:{handle}` vs alternatives

The actual code uses `f"{f.id[:8]}:"` (first 8 hex chars of the
file_id, then colon). Alternatives considered when this design was
codified:

| Scheme | Rejected because |
|---|---|
| Full file_id (`{file_id}:{handle}`) | 16-char prefix on every handle bloats the merged `match_json` ~10× for BGA scans; the first 8 hex chars of a SHA-256-prefix-derived id are unique within any realistic product (collision probability ~10⁻¹⁰ at 10⁴ files). |
| Numeric index (`{0|1|2}:{handle}`) | Index depends on stable ordering of `role_files`; readers can't tell which DXF a handle is in without re-deriving the order. file_id prefix is self-describing. |
| Separator other than `:` | DXF handles are uppercase hex (`[0-9A-F]+`) and never contain `:`, so colon is a safe split character with zero collision risk. `/` would conflict with path-like display, `_` would conflict with file_id chars. |

The current scheme is documented as-is rather than changed — the
production data on disk already uses it.

## Why `_split_handle_prefix` instead of changing the rule signature

Two ways to give rules access to file-of-origin:

1. Change `check_rules(product_id, dxfs_by_role)` so that every
   handle list throughout the bundle becomes a list of
   `(file_id, handle)` tuples instead of bare strings.
2. Keep handles as strings; add a one-shot helper
   `_split_handle_prefix(h)` that returns `(file_id_short | None, h)`.

(1) breaks every existing rule and every test (~200 lines of code
re-typing for ~3 helpers that don't use the info), forces every rule
author to handle the file_id even when they don't care, and conflicts
with the "handles are opaque strings" invariant that lets us add a
new prefix scheme later without touching rules.

(2) is opt-in: rules that don't care keep using
`_first_match_handles` / `_shortest_distance` exactly as today;
rules that do care call the helper at the one place they need to
branch on file. The function is also small enough to inline test:
`_split_handle_prefix("a3f12b9c:7AF") == ("a3f12b9c", "7AF")`,
`_split_handle_prefix("7AF") == (None, "7AF")`.

The cost is that rules can't tell, just from the bundle, whether a
role is multi-file or not — they have to look at `len(file_ids)`.
That's fine: the rule that needs file-of-origin already knows it
wants to fan out by file, and the `file_ids` field is the natural
loop iterator.

## What goes in the skill doc, what goes in the spec

- **Spec** (`design-rule-checking`) — the **contract**: shape of
  the bundle, prefix rule, invariants. Stable, terse, sceanrio-
  driven, the source of truth.
- **Skill doc** (`add-rule/SKILL.md`) — the **how-to**: input-shape
  table the agent reads when authoring a rule, worked example for a
  cross-file rule, pitfalls. Verbose, example-heavy, evolves as
  patterns are discovered.

The skill doc references the spec for "the formal contract" so the
two stay in sync; the spec doesn't reference the skill (specs are
self-contained per OpenSpec convention).

## What we deliberately don't add

- **A "iterate-this-role's-files" helper** like
  `_for_each_file(bundle, fn)`. Premature — the first rule that needs
  it can shape the API; one synthesised helper is a guess.
- **A bundle dataclass.** The skill doc treats the bundle as a dict
  by design (rules pull a few well-known keys), and a dataclass would
  force every rule + every test to import the type. The spec
  documents the contract; the dict shape stays.
- **Validation that `len(file_ids) >= 1`** etc. in `check_rules`.
  Today every code path that builds a bundle (only one — the rule-
  check endpoint) already guarantees this; adding a runtime check
  would only fire on internal bugs, which tests catch.

## Open questions

- **Should the prefix grow longer than 8 hex chars** if a product
  ever holds enough files to risk collision? At 10⁴ files in one
  product the collision probability is ~10⁻¹⁰; we're orders of
  magnitude away from the limit. No action now.
- **Should we expose `file_id_full` (the full id) when splitting?**
  The helper returns the 8-char prefix because that's what's in the
  handle. A rule that needs the full id can look it up in
  `bundle["file_ids"]` by `startswith`. Skipping for now; document
  the pattern in the skill if a rule ever needs it.
