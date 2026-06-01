## Why

The viewer's class-toolbar buttons always render in their full per-class colour, whether or not that object actually appears in the open drawing. An operator can't tell at a glance which classes have already been extracted (matched) in this image and which haven't — they have to read the small `×N` match-count chip on each button. Greying out the buttons for classes with no matches in the current image makes "found vs not-found" obvious.

## What Changes

- In `renderClassToolbar` (`app/static/canvas.js`): a button gets the `absent` CSS class unless its class has ≥1 match in the current image (`scanAllSummary.byClass`, populated by the auto-run prematch on load and by an explicit Scan All). Greyed is the **default** — it applies both before any scan has run and to any zero-match class afterwards. Classes currently in add-mode are exempt (they keep their active styling).
- `app/static/style.css`: `.class-btn.absent` dims the button (muted grey text/border, reduced opacity) while keeping it fully clickable.
- Before any match data exists, every button is greyed (nothing is confirmed present yet); as soon as the prematch/scan data lands, the found classes light up in their colour.

## Capabilities

### Modified Capabilities

- `viewer-ui`: ADDS a requirement that class-toolbar buttons are greyed when their class has no matches in the current image.

## Impact

- **Code**: `app/static/canvas.js` (one conditional in `renderClassToolbar`), `app/static/style.css` (one rule). JS passes `node --check`.
- **Tests**: none added — the frontend has no automated test harness (known gap). Manual verification below.
- **Behaviour**: purely visual; no change to matching, counts, or click behaviour.
- **Manual verification**: open a file whose prematch found some classes but not others → matched classes show full colour, unmatched show greyed. Run Scan All → greying updates to the scan result. Click a greyed button → still enters add-mode (lights up). Clear scan overlay → greying removed.
