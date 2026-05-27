## Context

The DXF upload pipeline today is a strict two-phase flow:

1. **Phase 1** — `_discover_layers_worker` (`app/jobs.py:392`):
   ezdxf parse + full geometry flatten + per-layer SVG thumbnail
   render + write `layers.json`. The file lands at
   `awaiting_layers`.
2. **Layer picker UI** — operator opens the picker, ticks layers,
   POSTs `/api/files/{file_id}/layers`.
3. **Phase 2** — `_preprocess_worker` (`app/jobs.py:63`): same
   parse (or re-use of the transient cache Phase 1 wrote),
   apply the layer filter, write `parsed/{file_id}.json` and
   `prematch/{file_id}.json`, transition to `ready_to_match`.

Phase 2 already accepts `selected_layers=None`, which means
"keep every primitive" — Phase 1's job exists purely to let the
operator narrow the layer set. For dev workflows that don't care
about layer filtering, every step from Phase 1 onward is wasted
click + wasted CPU + wasted disk.

The Phase 2 worker's main cost (`flatten_for_render`) is paid once
regardless of which path we take: today it's typically paid in
Phase 1 and Phase 2 reads the transient cache; on the skip path
Phase 2 pays it itself. Net wall time per file is roughly the
same; the win is eliminating the operator click + the per-layer
SVG render + the cross-phase queueing.

## Goals / Non-Goals

**Goals:**

- One dev-mode-gated checkbox lets developers bypass the layer
  picker for an entire upload batch (the checkbox state is
  sticky, so a dev who flips it on stays in skip mode across
  uploads).
- The file lifecycle is `preprocessing` → `ready_to_match` (or
  `error`) — `discovering_layers` and `awaiting_layers` are
  never observed on this path.
- Non-dev / unchecked behaviour is byte-identical to today
  (every existing scenario unchanged).
- Re-uploads of bytes-identical content with the flag on still
  honour the skip request, even when the existing row is stuck
  at `awaiting_layers`.

**Non-Goals:**

- Globally hiding the layer picker. Production users keep the
  picker; this is dev-only ergonomics.
- Speeding up the parsing itself. `flatten_for_render`'s ezdxf
  cost is unchanged.
- Removing Phase 1 from the codebase. Production needs it.
- Server-side enforcement of dev mode. The flag is honoured
  unconditionally on the server side, same as existing
  `dev-overrides`.

## Decisions

### Decision 1: Form-field flag, not a query parameter or env var

`skip_layer_pick: bool` lives in the multipart form body alongside
`file`, `dxf_role`, `replace_file_id`. Same plumbing as the other
upload fields, no FastAPI plumbing change, no env-var per-deploy
gymnastics. Client opts in per request.

**Alternative considered:** `?skip_layer_pick=true` query param.
Rejected — mixes upload semantics across body and querystring
unnecessarily. The form-field convention is what
`upload_product_file` already uses.

### Decision 2: No server-side dev-mode validation

The server accepts the flag from any client. This matches the
project's existing posture for `dev-overrides` and the dev-mode
download endpoints — dev mode is a UX hint surfaced through the
dashboard; it's not a security boundary. A non-dev client that
manually sends `skip_layer_pick=true` simply gets the same
behaviour, and that's fine because it's their own data.

**Alternative considered:** require an `X-Dev-Mode` header or
HMAC-signed flag. Rejected — adds protocol complexity for no
gain. If we ever need real gating, we'll do it at the auth layer
across all dev affordances at once, not piecemeal.

### Decision 3: Reuse the existing `selected_layers=None` semantics

`_preprocess_worker` already interprets `selected_layers=None` as
"no filter — keep every primitive" (`app/jobs.py:127`). We
don't add a new "skip filter" enum value or a separate worker
function; we just submit the existing worker with `None` and a
fresh starting status.

### Decision 4: Initial status is `PREPROCESSING`, not `DISCOVERING_LAYERS`

`FILE_STORE.register(..., initial_status=PREPROCESSING)` directly,
so the dashboard's status-driven UI never shows the
`discovering_layers` / `awaiting_layers` spinner for skip-path
uploads. The deduped-rebind branch performs the equivalent
`UPDATE files SET status = PREPROCESSING, selected_layers = NULL`.

The benefit is visual: the operator sees the file move from
upload → preprocessing → ready, without a Phase 1 detour they're
not interested in.

### Decision 5: Checkbox state persists in localStorage, sticky across sessions

Developers iterating on a batch don't want to re-tick the box for
every upload. The state key is
`smdr2.dashboard.skipLayerPick`, mirroring the existing
`smdr2.dashboard.foldedCustomers` / `smdr2.dashboard.devMode`
pattern. Reset is one click.

The checkbox is **only rendered** when `getDevMode()` is true;
the localStorage state is read regardless but only acted on when
both the checkbox is checked AND dev mode is on at upload time.

### Decision 6: Dedup-rebind honours the flag

The existing dedup branch (re-upload of bytes-identical content
to a different product slot) reuses the existing row. Today it
sets `status = DISCOVERING_LAYERS` and re-runs Phase 1 so the
rebound file gets a fresh layer manifest. When `skip_layer_pick`
is true, the rebind sets `status = PREPROCESSING` and
`selected_layers = NULL` (forcing Phase 2 to see "no filter")
and submits Phase 2 directly. The previously-rendered
`layers.json` and per-layer SVGs (if any) are not deleted, but
they're also not re-read by the skip path — they're dead data
that the next non-skip upload would overwrite.

**Alternative considered:** clear the existing manifest /
thumbnails on the skip rebind. Rejected — the cleanup pass adds
complexity for no functional benefit; production never reaches
the skip path so the artifacts stay valid for any future
non-skip re-upload.

## Risks / Trade-offs

- **Risk:** A non-dev client (e.g. a scripted test) sends
  `skip_layer_pick=true` and silently bypasses the layer picker
  in a production-ish environment. → **Mitigation:** this is by
  design; the flag is honoured unconditionally because the
  project's dev-mode posture is consistently UX-hint, not
  security. If the flag is being sent, the caller is taking
  ownership of the consequence.

- **Risk:** A file with malformed geometry that today errors out
  inside Phase 1's per-layer SVG render (catching the broken
  entities before the operator sees them) now fails inside
  Phase 2 instead. → **Mitigation:** Phase 2 already runs
  `flatten_for_render`, so the same parse errors surface at the
  same place. The only thing missed is the per-layer SVG
  rendering, which would have surfaced font / glyph issues —
  but the recently-shipped `TextPolicy.IGNORE` change removed
  that failure mode for both paths.

- **Risk:** Operator forgets the checkbox is on and uploads a
  production file expecting to pick layers. → **Mitigation:**
  the checkbox is only visible in dev mode (which itself
  requires intentional toggling), and its label explicitly says
  `(dev: use all layers)`. Production users don't see the
  affordance at all.

- **Trade-off:** Two upload code paths (Phase 1 → Phase 2 vs
  direct Phase 2) increase the surface to maintain. → The diff
  is small (~10 lines in the handler) and the second path uses
  the existing `_preprocess_worker` unchanged, so the
  maintenance burden is bounded.

- **Trade-off:** The dedup-rebind skip path leaves stale
  `layers.json` / per-layer SVG on disk from prior non-skip
  passes. → They're under
  `data/layer_preview/{file_id}/`, scoped to one file, and a
  future non-skip upload would naturally overwrite them. Not
  worth a cleanup pass.
