## 1. Category registry (`app/library.py`)

- [x] 1.1 Add `CLASS_CATEGORY: dict[str, str]` (every default class → category key) and `CLASS_CATEGORY_ORDER: list[tuple[str, str]]` = `[("structure","Structure"),("balls","Balls & Bumps"),("smd","SMD Pads"),("marks","Fiducials & Marks")]`, near `CLASS_VIEW_CONSTRAINTS`.
- [x] 1.2 Add an import-time invariant: every `DEFAULT_CLASSES` member is in `CLASS_CATEGORY`, and every `CLASS_CATEGORY` value is a key in `CLASS_CATEGORY_ORDER` (assert, mirroring the existing `PRODUCT_SCOPED_CLASSES` subset assertion).

## 2. Frontend: registry mirror + floating panels (`canvas.js`, `viewer.html`, `style.css`)

- [x] 2.1 Mirror `CLASS_CATEGORY` and `CLASS_CATEGORY_ORDER` into `canvas.js` between `CLASS_CATEGORY_BEGIN/_END` and `CLASS_CATEGORY_ORDER_BEGIN/_END` sentinel comments (same style as `CLASS_VIEW_CONSTRAINTS`).
- [x] 2.2 `renderClassToolbar`: render the grouped buttons into the two floating panels — `marks` → right body, every other category + "Other" → left body; the SMD More/Less toggle lives inside its group; hide a panel with no groups. Add the panel-body DOM refs + collapse-button wiring. Preserve per-button behaviour (collapse `COLLAPSED_TOOLBAR_CLASSES`, active/add-mode, `+ → ✓`, count badge). Do NOT touch the hotkey handler — `HOTKEYS[idx] → classes[idx]` is by index, unaffected.
- [x] 2.3 `app/templates/viewer.html`: add the two `<aside class="floating-panel class-panel">` panels (left "Objects", right "Marks") inside `<main>`, each with header + collapse button + body div.
- [x] 2.4 `style.css`: re-scope the class-button rules `nav#class-toolbar .class-*` → `.class-*` (so they apply inside the panels); add `.class-panel` layout (left/right position, vertical groups, wrapping buttons), the in-panel `.class-toolbar-group` header override, and `.floating-collapse` / collapse styles.
- [x] 2.5 `viewer.html`: relocate the action buttons (Library, Scan All, Save Match, Measure, Layers, Rules) from the header into the `nav#class-toolbar` row next to Chain/Views, giving each `class="tool-btn"` (ids unchanged → wiring intact; `.tool-btn` style ≡ old `header button`). The ⚙ dev-params toggle stays in the header.
- [x] 2.6 `viewer.html`: relabel the `sides-btn` button text from `Sides` to `Views` (id/behaviour unchanged; it marks the top/bottom/side view regions).

## 3. Tests

- [x] 3.1 `tests/test_library.py`: assert every `DEFAULT_CLASSES` member has a `CLASS_CATEGORY` entry; every category value is a `CLASS_CATEGORY_ORDER` key; `CLASS_CATEGORY_ORDER` keys == `["structure","balls","smd","marks"]`; spot-check `DAM→structure`, `Pin-1/2DBarcode/Fiducial*→marks`, `SMD-*→smd`, `C4Ball/BGABall→balls`.
- [x] 3.2 `tests/test_canvas_constants.py`: add a Python↔JS drift test parsing the `CLASS_CATEGORY` / `CLASS_CATEGORY_ORDER` literals from `canvas.js` (between sentinels) and asserting equality with the Python registries.

## 4. Verify

- [x] 4.1 Full backend suite (`pytest`) green, deterministic order.
- [x] 4.2 `canvas.js` syntax OK; Playwright on the viewer: left panel = Structure/Balls/SMD(/Other), right panel = Marks; DAM under Structure; SMD "More" expands 3T/8T/14T within the SMD group; `nav#class-toolbar` has no class buttons (only Chain/Sides); panels collapse.
- [x] 4.3 `openspec validate class-toolbar-categories` passes.
