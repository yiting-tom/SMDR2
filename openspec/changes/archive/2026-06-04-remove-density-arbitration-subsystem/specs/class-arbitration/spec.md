## REMOVED Requirements

### Requirement: Arbitration group declaration

**Reason**: The neighbour-density arbitration subsystem is removed. Same-geometry classes (BGABall/FiducialCircle) are disambiguated by mutually exclusive view constraints (see `template-library`), not by a density registry. `CLASS_ARBITRATION_GROUPS`, `ArbitrationGroup`, `MinNeighbors`/`MaxNeighbors`, and `arbitration_group_for` are deleted.

### Requirement: Auto-derived grid pitch

**Reason**: Part of the removed density heuristic (`derive_pitch`). No longer computed.

### Requirement: Neighbour-count classification

**Reason**: Part of the removed density heuristic (`count_neighbors` + `classify`). Classification is now by view, not neighbour count.

### Requirement: Population fallback

**Reason**: Part of the removed density heuristic (`min_population` floor) — the fragile edge case that motivated retiring the subsystem.

### Requirement: Integration with Match JSON serialisation

**Reason**: The `arbitrate()` step is removed from the prematch, save-match, and scan-all pipelines; `out` flows directly from `split_matches_by_side` (which applies the view constraints) to persistence/collapse.

### Requirement: Deterministic ordering

**Reason**: Concerned the determinism of the removed `arbitrate()` output.

### Requirement: Single-class pool short-circuits arbitration

**Reason**: A guard inside the removed `arbitrate()`; moot once the subsystem is deleted.
