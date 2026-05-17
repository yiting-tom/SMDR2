## Context

`app/templates/viewer.html` opens its `<header>` with two anchors that
both navigate to `/`:

```html
<a class="title" href="/" title="Back to dashboard">SMDR2</a>
<a id="back-btn" href="/" title="Back to product dashboard">← Products</a>
```

The first ("SMDR2") was the original brand element; the second was
added later when the dashboard became product-scoped. They now serve
the same function — the user just has two ways to get back to `/` two
characters apart. This proposal is the first cut at the broader header
cleanup discussion; see also [[remove-viewer-title-link]] in
conversation for the longer-term plan to move live readouts into a
canvas-bottom status bar.

## Goals / Non-Goals

**Goals:**
- Remove the duplicate `SMDR2` anchor from the viewer header.
- Leave navigation behaviour intact: `← Products` remains the back-out.
- Touch nothing else on the page so this change can ship independently
  of the larger header refactor.

**Non-Goals:**
- Restructuring the header into clusters / dropdowns / status-bar
  (separate proposal once this lands).
- Renaming or restyling `← Products`.
- Removing `SMDR2` from the dashboard, the `<title>` tag, the favicon,
  or anywhere else outside the viewer header.

## Decisions

**Drop the `<a class="title">` element entirely, do not just hide it
with CSS.** Hiding leaves dead markup that the next reader has to
reason about; deletion is what the proposal actually means.

**Verify the `.title` CSS rule is still used by the dashboard before
removing it.** A quick grep across `app/templates/*.html` decides:

- If `class="title"` still appears in `dashboard.html`, leave the CSS
  rule in `style.css` alone — it is now dashboard-only.
- If no other template uses `class="title"`, drop the orphaned rule
  from `style.css` in the same commit.

**No JS or test changes.** The element has no event handlers attached
and no tests assert its presence.

## Risks / Trade-offs

- **[Risk]** A user's muscle memory clicks where `SMDR2` used to be
  and hits empty space → **Mitigation:** `← Products` sits immediately
  to its right; misclick lands on the same destination. Low cost.
- **[Trade-off]** This is a one-line change shipped on its own rather
  than batched with the bigger header refactor → accepted on the
  grounds that small reversible changes are easier to review and the
  bigger refactor is still being designed.
