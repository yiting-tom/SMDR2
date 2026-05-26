## 1. HTML — move readouts out of header

- [x] 1.1 In `app/templates/viewer.html`, delete `<span id="status">`, `<span class="spacer">`, `<span id="mode-hint">`, `<span id="handle-info">`, and `<span id="cursor-coords">` from `<header>` (lines ~31–35).
- [x] 1.2 In the same file, add `<footer id="canvas-statusbar">` as the last child of `<main>` (after `#measure-readout` and the two `<aside>` panels). Inside it, place the same five elements in their original left-to-right order: `#status`, `.spacer`, `#mode-hint`, `#handle-info`, `#cursor-coords`. The `class="hint"` on `#mode-hint` stays.

## 2. CSS — restyle and reposition

- [x] 2.1 In `app/static/style.css`, delete the rules `header .spacer`, `header #status`, `header #cursor-coords`, `header #handle-info`, `header #handle-info.empty`, and `header #mode-hint` (lines ~90–122).
- [x] 2.2 Add a `#canvas-statusbar` rule: `position: absolute; left: 0; right: 0; bottom: 0; display: flex; align-items: center; gap: 0.6rem; padding: 0.2rem 0.8rem; font-size: 0.78rem; background: rgba(15, 19, 24, 0.72); backdrop-filter: blur(2px); border-top: 1px solid #2a3340; z-index: 1; pointer-events: none;`.
- [x] 2.3 Re-add the per-readout rules scoped to the new container: `#canvas-statusbar .spacer { flex: 1; }`, `#canvas-statusbar #status { color: #9aa5b1; pointer-events: auto; }`, `#canvas-statusbar #cursor-coords { color: #9aa5b1; font-family: ui-monospace, monospace; pointer-events: auto; }`, `#canvas-statusbar #handle-info { color: #00ffff; font-family: ui-monospace, monospace; min-width: 12ch; text-align: right; pointer-events: auto; }`, `#canvas-statusbar #handle-info.empty { color: #4a5868; }`, `#canvas-statusbar #mode-hint { color: #ffb84d; font-weight: 600; font-size: 0.8rem; letter-spacing: 0.04em; pointer-events: auto; }`.

## 3. Verify

- [x] 3.1 `grep -n "id=\"status\"\|id=\"mode-hint\"\|id=\"handle-info\"\|id=\"cursor-coords\"" app/templates/viewer.html` returns exactly four matches, all inside `<footer id="canvas-statusbar">`, none inside `<header>`.
- [x] 3.2 Manual: open the viewer in a browser. Confirm the four readouts appear as a thin bar at the bottom of the canvas; `#status` text updates on file load + scan; `#handle-info` and `#cursor-coords` update on mouse move; `#mode-hint` shows when entering add-mode / measure-mode.
- [x] 3.3 Manual: click on a polyline near the bottom edge of the drawing where the status bar overlaps — confirm the click still selects the polyline (the bar's `pointer-events: none` lets the click fall through).
- [x] 3.4 Manual: open the visibility panel and the rule sidebar — confirm both render above the status bar (z-index sanity check).
