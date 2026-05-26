## 1. Reusable dot-batch flush

- [x] 1.1 Extract a small helper next to the existing main-pass flush block (`canvas.js:565–583`) that takes a `Map<color, Float32Array-ish>` of (x, y) world positions and emits one device-pixel `Path2D` per colour. Reused by the main pass + every highlight pass.
- [x] 1.2 Keep the device-pixel `setTransform(1,0,0,1,0,0)` + restore pattern from the existing main-pass block; the helper SHALL not assume a specific transform on entry beyond the existing world-space transform applied by `render()`.

## 2. Highlight passes batch sub-pixel circles as dots

- [x] 2.1 In the `scanAllByHandle` pass (around `canvas.js:590–602`): for each visible primitive `p`, if `p.type === "circle" && p.r < dotR`, push `(p.center[0], p.center[1])` into a per-`classColor(cls)` bucket and skip the existing `drawPrimitive(...)` call. After the loop, call the helper to flush.
- [x] 2.2 In the `nearMissSet` pass: same pattern, single bucket keyed on `NEARMISS_COLOR`.
- [x] 2.3 In the `selection.size || matchSet.size` pass: same pattern, single bucket keyed on `HIGHLIGHT_COLOR`. This is the hot path on a 400 k-match BGA scan.
- [x] 2.4 In the `hoverSet || pinnedSet` pass: same pattern, single bucket keyed on `HOVER_COLOR`.
- [x] 2.5 Make sure each highlight pass's existing per-primitive culling (`isLayerVisible`, `bboxInView`, selection-conflict guards) runs *before* the dot decision so we don't accidentally batch culled handles.

## 3. Spec update

- [x] 3.1 Update `openspec/specs/viewer-ui/spec.md` "Sub-pixel circle LOD batching" — drop the "highlights continue to draw at fattened stroke width regardless of LOD" clause; replace with the batching rule.
- [x] 3.2 Keep the "Selected sub-pixel circle still shows its highlight" scenario but reword to expect a dot at the circle's screen position (not a halo).

## 4. Verification

- [x] 4.1 Manual: open the BGA reference file, frame-select one ball, confirm the cyan-dot field appears at zoom-out, pan/zoom feels smooth, and zooming in past the LOD threshold transitions cleanly from dots to fattened strokes.
- [x] 4.2 Manual: hover a rule-check item that pins 100 + entities; confirm yellow dots appear at zoom-out and yellow fat-stroke at zoom-in.
- [x] 4.3 Regression sanity: with a small file (single SMD with a 2-entity match), zoom-in path still draws the fat-stroke highlight (i.e., the dot threshold gate is the only thing different).
