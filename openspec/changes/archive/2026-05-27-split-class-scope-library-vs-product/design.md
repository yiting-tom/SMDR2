## Context

The current `templates` table (`app/library.py:319`) has a single
ownership column: `library_id`. Every template a user commits ends up
owned by the file's library and visible to every product bound to that
library. The Store's two consumers of the table are:

- `Store.insert_template(library_id, t)` — single row insert keyed by
  the library only (`app/library.py:559`).
- `Store.load_library(library_id)` — returns `(classes, configs,
  templates_by_class)` for the library (`app/library.py:589`),
  consumed by the prematch and Match workers (`app/jobs.py:178`,
  `app/jobs.py:734`) and by Scan All (`app/main.py:1039`).

The commit endpoint (`app/main.py:1010`) wraps `library.add_template`
which in turn calls `Store.insert_template(self.library_id, t)`.

Files already carry both `library_id` and `product_id`
(`app/files.py:96-97`), so a file record alone is enough context to
decide which product a Scan All or commit should attribute templates
to. Products carry a `library_id` (`app/products.py:42`); the
`product_id → library_id` relation is a many-to-one.

This change introduces a second ownership axis. Eight classes
become **product-scoped**: substrate / lid / die / ball / protrusion
geometry is per-design and was wrongly being shared. Nine generic
classes (SMDs, fiducials, pin marker, barcode) stay **library-scoped**
where reuse across products is the whole point.

## Goals / Non-Goals

**Goals:**
- Make the eight product-specific classes store templates per product,
  invisible to other products under the same library.
- Keep the nine generic classes storing templates per library, reused
  across every product bound to that library (no behaviour change).
- Make the migration self-healing and idempotent: existing
  product-scoped templates that were mis-stored at library scope are
  dropped on next boot; the user re-commits per product.
- Keep the change invisible to UI / DRC / rule-checker code.

**Non-Goals:**
- Toolbar UI changes (the 17 classes still show up the same way).
- Cross-product template borrowing for product-scoped classes (a
  product's substrate stays in that product).
- Per-class strategy duplication per product (`match_strategy`,
  `bbox_ratio` stay per-library — they describe how to match the class
  generically).
- Product-scoping for custom user classes added outside
  `DEFAULT_CLASSES`. Those default to library-scoped (backwards-compat
  safe — existing custom classes don't disappear at boot).
- Adding new indexes for the new column. Volume today is small;
  re-evaluate when Scan All perf degrades.

## Decisions

### Storage: one `templates` table with nullable `product_id`

Add `templates.product_id TEXT NULL` rather than introducing a second
table `templates_product`. Two reasons:

- **Single read path.** `load_library` already returns
  `templates_by_class: dict[str, list[Template]]`. Merging two ranges
  from two tables would require parallel queries and de-duplication;
  one query with `WHERE library_id = ? AND (product_id IS NULL OR
  product_id = ?)` is shorter and matches the existing pattern.
- **Smaller blast radius.** Every existing call to `delete_template`,
  `update_template_class`, the `entity_kinds` migration, and the
  prematch worker stays untouched — they operate on rows by primary
  key, which doesn't care about scope.

**Alternatives considered:**
- *Two tables (`templates_library`, `templates_product`).* Cleaner
  type-level separation but doubles the surface area of every CRUD
  method and requires UNION-style reads. Not worth it at current
  volume.
- *Boolean column `is_product_scoped` + always-present `product_id`.*
  Encodes the same information twice and makes the legal `(scope,
  product_id)` combinations ambiguous. Nullable `product_id` is the
  single source of truth.

### Class-scope registry: a `frozenset[str]` in `library.py`

A new module-level constant:

```python
PRODUCT_SCOPED_CLASSES: frozenset[str] = frozenset({
    "Substrate", "Lid", "LidOuter", "LidInner", "DieArea",
    "C4Ball", "BGABall", "Protrusion",
})
```

paired with a helper:

```python
def is_product_scoped(class_name: str) -> bool:
    return class_name in PRODUCT_SCOPED_CLASSES
```

Co-located with `DEFAULT_CLASSES`, `CLASS_JSON_KEY`,
`CLASS_VIEW_CONSTRAINTS`. Single Python-side source of truth.

**Alternatives considered:**
- *Per-class column in `classes` table (`scope`)*. Pushes the policy
  into the DB and would let admins flip a class's scope at runtime,
  but no one has asked for that flexibility and runtime mutation of
  scope has nasty implications (where do existing rows go?).
  Keep the partition immutable at the code level.
- *Subclass annotation on each `Class` row*. Same problem; over-engineering.

### `load_library` signature change

```python
def load_library(
    self,
    library_id: str,
    *,
    product_id: str | None = None,
) -> tuple[list[str], dict[str, dict], dict[str, list[Template]]]:
```

Callers:

- `app/jobs.py:178` (prematch) → pass `rec.product_id`
- `app/jobs.py:734` (match) → pass `rec.product_id`
- `app/main.py` Scan All → pass `rec.product_id`
- Library admin listings (e.g. `/api/libraries/{id}/templates`) →
  do **not** pass `product_id`; they continue to show only
  library-scoped templates. This is the "library admin's view" and
  matches the new mental model.

The keyword-only `product_id` parameter means existing callers that
just pass `library_id` keep compiling and keep returning only the
library-scoped templates — a safer default than auto-merging.

SQL inside `load_library`:

```sql
SELECT ... FROM templates
WHERE library_id = ?
  AND (product_id IS NULL OR product_id = ?)
ORDER BY created_at ASC
```

If `product_id` is `None`, drop the `OR product_id = ?` and bind
nothing. Use two distinct query strings rather than passing `NULL`
through the OR clause (which would still work but is harder to read).

### `insert_template` signature change

```python
def insert_template(
    self,
    library_id: str,
    t: Template,
    *,
    product_id: str | None = None,
) -> None:
```

`Library.add_template(self, tmpl)` (`app/library.py:701`) doesn't have
a `product_id` to consult. To keep the `Library` abstraction clean,
introduce a new entry point that does:

```python
class Library:
    def add_template_for_file(
        self,
        tmpl: Template,
        *,
        product_id: str | None,
    ) -> None:
        scope_pid = product_id if is_product_scoped(tmpl.class_name) else None
        self.store.insert_template(
            self.library_id, tmpl, product_id=scope_pid
        )
        # …existing in-memory cache update…
```

The commit endpoint switches from `lib.add_template(tmpl)` to
`lib.add_template_for_file(tmpl, product_id=rec.product_id)`. Keep
the existing `add_template` as a thin wrapper that passes
`product_id=None` for any code (e.g. tests, fixtures) that doesn't
have a file context.

**Alternative considered:**
- *Pass `product_id` through `add_template` directly.* Simpler but
  collides with the historic signature in tests/fixtures; introducing
  a parallel method documents the new contract explicitly.

### Migration: drop-and-reseed, idempotent

Add to `Store._migrate()`:

```sql
DELETE FROM templates
 WHERE class_name IN (<PRODUCT_SCOPED_CLASSES values>)
   AND product_id IS NULL;
```

Plus the column add:

```sql
ALTER TABLE templates ADD COLUMN product_id TEXT;
```

Both are idempotent — the `ALTER` is gated on a `PRAGMA table_info`
check (matches existing pattern in `_migrate`), and the `DELETE`
matches zero rows on subsequent boots because committed
product-scoped templates have a non-null `product_id`.

**No re-mapping** of existing library-scoped product-class templates
to any specific product. The user has confirmed clean-slate. This is
the only safe default: a library may have many products and there is
no signal in the data telling us which product an old "library-scoped
Substrate" template was meant for.

### What library-admin endpoints show

- `GET /api/libraries/{id}/classes` — unchanged. Class metadata is
  per-library and not scoped to a product.
- `GET /api/libraries/{id}/templates` — returns **only library-scoped**
  templates (rows where `product_id IS NULL`). This is the right
  affordance for "library administration" use cases: an admin
  reviewing the library's reusable parts shouldn't see the per-product
  geometry. A future `/api/products/{id}/templates` endpoint MAY be
  added for the symmetric product-admin view — out of scope for v1.

### Toolbar counts

The viewer toolbar's per-class chip counts come from `lib.summary()`
which counts templates in the library cache. After this change the
in-memory cache only mirrors the library-scoped + (current product's)
product-scoped templates loaded by the active `load_library` call
that hydrated this `Library` instance. There is no user-visible
behavioural change because every viewer context already lives inside
a file, which has a `product_id`. The summary semantics shift from
"all templates in this library" to "templates this file would see"
— matching what the matcher actually loads.

If a future use case needs cross-product or library-only counts, add
explicit methods (`summary_library_only()`, `summary_for_product()`)
rather than overloading the existing one.

## Risks / Trade-offs

- **Risk**: Users lose every product-scoped template at boot and have
  to re-commit per product. → **Mitigation**: explicitly user-signed
  off ("clean slate"). The viewer's "Save Match → re-commit"
  affordance already exists; the toolbar shows zero counts on the
  affected classes as a visible cue that re-commit is needed.
- **Risk**: A custom user class that happens to be named identically
  to a default product-scoped class (e.g. user creates `Substrate` in
  a library that had no `DEFAULT_CLASSES` seed for some reason — only
  reachable if the seeding boot loop didn't run). The new
  `is_product_scoped` check would mark its templates as
  product-scoped. → **Mitigation**: `DEFAULT_CLASSES` is seeded on
  every boot for every library; the only way to have a custom
  class collide with a default name is to delete the seeded one,
  which is itself an explicit user action. No code change can defend
  against the user doing this deliberately.
- **Risk**: Tests that rely on `load_library(library_id)` returning
  product-scoped templates (none today, but future tests might) get
  silently empty results. → **Mitigation**: the keyword-only
  parameter is explicit; new tests for product-scoped classes MUST
  pass `product_id`. Linting / type-checks will not catch this; the
  spec scenarios below document the expected behaviour.
- **Risk**: A class's scope changes in future (e.g. someone decides
  `Protrusion` should be library-scoped after all). → **Mitigation**:
  flipping the constant alone is insufficient — existing
  product-scoped rows would need to be promoted to library-scoped
  (and which library? — but every product points to exactly one
  library, so this is mechanical) or dropped. Out of scope for v1;
  document the constraint with an inline comment in
  `PRODUCT_SCOPED_CLASSES`.
- **Trade-off**: `load_library` now does slightly more work
  (two `IS NULL`-vs-`=` predicate evaluations per template row when
  `product_id` is supplied). Negligible at current volume; revisit
  with an index if N grows past ~10⁴ templates per library-product
  pair.

## Migration Plan

1. **Code change ships** (one PR). Boot of any environment running the
   new code triggers `Store._migrate()`:
   - Idempotent `ALTER TABLE templates ADD COLUMN product_id`.
   - Idempotent `DELETE` of library-scoped templates for
     product-scoped classes.
2. **User impact at first boot after deploy**:
   - Toolbar chips for the 8 product-scoped classes show count 0 on
     every product.
   - User re-commits the affected templates per product. Each commit
     under the new code lands with `product_id` set, isolating it.
3. **Rollback strategy**: there is no automatic rollback (templates
   are deleted, not soft-deleted). If a critical regression is found,
   revert the code, leave the new column in place (an unread column
   doesn't break the old code), and accept that the deleted rows are
   gone. Practitioners on this branch should snapshot
   `data/library.sqlite` before deploy.

## Open Questions

- *Should an immediate "are you sure?" banner show in the UI on the
  first boot after this change, listing the dropped classes?*
  Deferred — the empty chips are themselves a strong cue and no API
  signal of "first boot" exists today.
- *Should `GET /api/libraries/{id}/templates` gain a
  `?include_product_scoped=true` flag for admin inspection?*
  Deferred to a follow-up if the use case appears.
