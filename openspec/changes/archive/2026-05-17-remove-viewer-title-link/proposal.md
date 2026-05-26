## Why

The viewer header currently jams three unrelated concerns into one row:
navigation chrome, action buttons, and live readouts. As a first cleanup
pass, the `SMDR2` title link is the lowest-cost win: it sits immediately
to the left of `← Products` and points to the same `/` destination, so
it is purely redundant clutter. Removing it makes room without changing
any behaviour.

## What Changes

- The viewer header (`app/templates/viewer.html`) drops the
  `<a class="title" href="/">SMDR2</a>` element. The `← Products`
  anchor next to it is the sole way back to the dashboard from the
  viewer.
- No other surface is touched: the dashboard still shows `SMDR2`, the
  HTML `<title>` tag stays `SMDR2 Viewer`, and the favicon / brand
  references elsewhere are unchanged.
- No spec-level behaviour changes — navigation back to `/` is
  preserved, just via a single affordance instead of two.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

(none — this is presentation-only chrome below the spec layer; no
requirement in `viewer-ui` is being altered or removed)

## Impact

- `app/templates/viewer.html` — one element removed from `<header>`.
- `app/static/style.css` — the `.title` rule inside the header may be
  unreferenced in the viewer after the change; verify it is still used
  by the dashboard before deleting (the dashboard reuses the same
  `class="title"` for its own brand element).
- No JS, no API, no DB, no tests.
