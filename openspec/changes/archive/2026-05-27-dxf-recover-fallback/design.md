## Context

Today `flatten_for_render` (`app/dxf.py:506`) opens user DXFs with
`ezdxf.readfile`, which uses ezdxf's strict tag stream. AutoCAD
silently fixes a long tail of DXF spec violations on read — missing
group codes, out-of-order attributes, truncated sections, stray
binary in ASCII files — but `ezdxf.readfile` raises on every one of
them. Today these failures surface to the operator as a generic
"upload error" with no server-log diagnostic and no path to recover
the file short of round-tripping it through AutoCAD again.

`ezdxf.recover.readfile` exists for exactly this case. It parses the
file in a more permissive mode and returns
`(doc, auditor)` where `auditor.fixes` enumerates per-entity
corrections and `auditor.errors` lists entities that could not be
patched (which it drops). Numerically, recover does not alter
correctly-encoded entities — geometry values are byte-identical to
strict — so any file currently parsing via strict will keep
producing the same output.

The operator's pain has two halves: (a) recoverable files just
need to be parsed, and (b) when something does fail, the server
log is silent about the cause. Both fall out of the same
intervention point.

## Goals / Non-Goals

**Goals:**

- Recoverable DXFs upload cleanly without operator intervention.
- Files that strict parsed continue to use strict — zero change to
  their numeric output and zero new log noise.
- Server log gives a single-line diagnostic (file id + strict
  exception + audit summary) every time a recover path is taken,
  and a two-stage diagnostic when both paths fail.
- The dashboard surfaces a non-blocking visual hint
  (`ℹ recovered (N patched)`) for any file that took the fallback,
  so the operator knows the file is in use but is structurally
  non-standard.

**Non-Goals:**

- Replacing strict mode globally. Strict's stricter validation is
  the right default — recover is only the rescue.
- Retroactively re-parsing already-uploaded files. The change
  affects new uploads only; previously errored rows stay errored
  unless re-uploaded.
- Surfacing the per-entity audit messages in the UI. The dashboard
  pill only shows counts; the full audit lives in the server log
  and in `dxf_recover_notes` for developers.
- Cancelling a recover when it patches "too much." Recover decides
  what's salvageable; we trust its output.

## Decisions

### Decision 1: Strict-first / recover-fallback, not recover-always

Recover's permissiveness is double-edged: it will accept files that
strict rejects, but it also accepts files that should have been
rejected (e.g. a truncated file where the missing entities matter).
Keeping strict as the default means files that *should* fail still
fail visibly; recover only intervenes when strict has already
declined to parse.

**Alternative considered:** swap to `recover.readfile` unconditionally.
Rejected — it would change the silent-acceptance threshold for
*every* file, including correct ones, and we'd lose the bright-line
"strict accepted this" guarantee that the rest of the pipeline
implicitly relies on.

### Decision 2: Fallback is triggered by exception class, not by user opt-in

The trigger is "strict raised", not a UI toggle or a per-file
setting. Caught classes: `ezdxf.DXFStructureError`,
`ezdxf.DXFTagError`, and any other parser exception ezdxf raises
inside `readfile`. Non-parser errors (e.g. `FileNotFoundError`,
`PermissionError`) are NOT recovered — they propagate, because
recover can't fix them either and would just produce a more
confusing error.

**Alternative considered:** a `?recover=true` query parameter or
upload flag. Rejected — operators don't know which files need
recover; opt-in just shifts the failure mode to "didn't tick the
right box."

### Decision 3: `dxf_recover_notes` is a JSON blob, not normalised columns

The audit summary is small (a handful of integers + a few first
messages), but the *shape* may evolve as ezdxf's auditor evolves.
Storing it as a serialised JSON blob in one TEXT column keeps the
migration to a one-line `ALTER TABLE` and avoids schema churn each
time we want to surface a new audit field.

**Alternative considered:** separate columns for `fixed_count`,
`unrecoverable_count`, `first_audit_message`. Rejected — three
columns instead of one, plus every future audit field needs another
migration. The blob trade-off (no SQL filtering on individual
fields) is fine because the dashboard reads the whole row anyway.

### Decision 4: Log levels — WARNING for recover-OK, ERROR for both-fail

WARNING for the recover-OK path so it shows up in the default log
output without polluting `tail -f` with INFO noise from successful
strict parses. ERROR for both-fail because the file is unusable
and the operator needs to be told.

A single combined log line per file at WARNING — not one line per
auditor fix — keeps the log volume bounded for files with hundreds
of small patches.

### Decision 5: Dashboard pill mirrors the existing `rescaled` pattern

The `rescaled` and `unit_scale_warning` pills (viewer-ui spec,
~line 344) already establish the visual + payload convention for
"this file has a non-blocking note." `dxf_recover_notes` plugs in
as a third pill, same shape, same colour family
(neutral-informational). No new UX vocabulary needed.

### Decision 6: Tests use monkey-patching, not fixture DXFs

Crafting a real DXF that strict rejects but recover accepts is
fragile across ezdxf versions. The lifecycle tests monkeypatch
`ezdxf.readfile` and `ezdxf.recover.readfile` to script the three
outcomes (strict-OK, strict-fail-recover-OK, both-fail) and assert
on the resulting FileRecord + log lines. Geometry correctness for
recovered files is ezdxf's responsibility, not ours.

## Risks / Trade-offs

- **Risk:** A file that strict would have rejected for good reason
  (genuinely broken data — say half the modelspace is missing)
  gets accepted by recover and produces silently incomplete
  matches downstream. → **Mitigation:** the dashboard pill makes
  the recover-path explicit, and the audit count gives the
  operator a quick read on how invasive the patching was. We do
  not block use, but we surface enough information for the
  operator to make their own call.

- **Risk:** Adding the `dxf_recover_notes` column triggers a
  schema migration on every existing deployment. → **Mitigation:**
  the column add follows the existing
  `if "<col>" not in cols: ALTER TABLE` idiom (see
  `app/files.py:299`) so the migration is idempotent and
  no-downtime.

- **Risk:** Recover is slower than strict and we shift cost into
  the worker. → **Mitigation:** the slow path only fires for
  files strict already rejected — i.e. files that today produce
  no output at all. The net throughput change for normal files is
  zero.

- **Risk:** The audit summary blob grows unbounded if a file has
  thousands of patches. → **Mitigation:** the persisted summary
  is capped to counts plus the first N audit messages
  (N ≈ 5); the full audit is logged, not persisted.

- **Trade-off:** introducing a new optional payload field is a
  soft API change. → **Mitigation:** documented in the proposal
  as additive; the dashboard treats `null` and missing as
  equivalent. No client breaks.

- **Trade-off:** the dashboard now has three pill categories
  (rescaled, unit-scale-warning, recover). At some point this
  becomes visual noise. → **Mitigation:** the three pills don't
  stack — a file rarely takes more than one — and the pill family
  shares a single style so the dashboard doesn't get louder.
