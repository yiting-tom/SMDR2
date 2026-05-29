## MODIFIED Requirements

### Requirement: Population fallback

The system SHALL reassign every instance in the group's pooled set to `default_class` when both of the following hold: (a) at least one **non-default** member class would receive **fewer than `min_population` instances** after per-instance classification, AND (b) `default_class` itself has at least one instance in the pool (i.e. at least one match-result was emitted from a `default_class` template via the pre-arbitration matching pass). The default-class count is NOT subject to the floor — realistic substrates may legitimately have only 4 fiducials, and that should not force fallback.

The `default_class`-in-pool precondition (b) prevents the fallback from inventing labels for which the library has no template: when the default class produced zero matches (e.g. the library has no `default_class` template, or its template did not match anything in this DXF), there is no evidence the safe direction is in play, and collapsing to it would create handle assignments under a class key backed by no template.

This guards against the degenerate case where a DXF contains, say, only fiducials (no BGA pattern at all): without this rule, the four corner circles would form their own pseudo-grid and be misclassified as BGA. The precondition does NOT weaken this guard — in that scenario the default-class (FiducialCircle) templates DID produce the original matches, so `default_in_pool=True` and the fallback still fires.

#### Scenario: BGA candidates below floor collapse to fiducials
- **WHEN** the BGA/Fiducial group has `min_population = 8`
- **AND** classification produced 4 instances labelled `"BGABall"`
  and 0 labelled `"FiducialCircle"`
- **AND** at least one of the pool's instances has `original_class == "FiducialCircle"`
  (e.g. the library has a FiducialCircle template that contributed matches)
- **THEN** all 4 instances SHALL be reassigned to `"FiducialCircle"`
- **AND** no instance remains labelled `"BGABall"`

#### Scenario: Non-default population above floor with thin default is preserved
- **WHEN** the group has `min_population = 8` and `default_class == "FiducialCircle"`
- **AND** classification produced 96 `"BGABall"` and 4 `"FiducialCircle"`
- **THEN** assignments are preserved as-is
  (96 BGA, 4 fiducials in the output)
- **AND** the fiducial count of 4 does NOT trigger fallback because
  `FiducialCircle` is the default class (it has no floor)

#### Scenario: Default class absent from the pool suppresses fallback
- **WHEN** the BGA/Fiducial group has `min_population = 8`
- **AND** the library contains only `"BGABall"` templates (no `"FiducialCircle"` template)
- **AND** scan-all / save-match produces 4 BGABall matches and 0 FiducialCircle matches
- **AND** every pool instance has `original_class == "BGABall"`
- **THEN** the fallback SHALL NOT trigger
- **AND** every instance SHALL keep the class assigned by `classify()` (BGABall)
- **AND** no `fiducial_circle.*` key SHALL appear in the rewritten output
- **AND** `GroupCounts.population_fallback_triggered` SHALL be `False`

#### Scenario: Degenerate fiducial-only DXF still triggers fallback
- **WHEN** a DXF has only FiducialCircle template matches (the original guard scenario)
- **AND** classification mis-labels the 4 corner fiducials as BGABall via
  the tight-grid heuristic
- **AND** `per_class_pre[BGABall] = 4 < 8`
- **AND** every pool instance has `original_class == "FiducialCircle"`
  (`default_in_pool == True`)
- **THEN** fallback SHALL trigger and reassign all 4 to FiducialCircle
- **AND** the precondition introduced by this change does NOT weaken
  this safety net
