## 1. Grey absent class buttons

- [x] 1.1 `app/static/canvas.js` `renderClassToolbar`: add `absent` class to a button when `scanAllSummary` exists and the class's match count is not > 0 (and it isn't the active add-mode class).
- [x] 1.2 `app/static/style.css`: `.class-btn.absent` — muted grey text/border + reduced opacity; still clickable; hover lifts the opacity.

## 2. Verify

- [x] 2.1 `node --check app/static/canvas.js` — OK.
- [ ] 2.2 **[USER]** Manual: open a file → classes the prematch found show full colour, the rest greyed. Scan All updates the greying. Greyed button still enters add-mode on click. Clear overlay removes greying.

## 3. Archive

- [ ] 3.1 `/opsx:archive viewer-grey-absent-class-buttons` after manual verification.
