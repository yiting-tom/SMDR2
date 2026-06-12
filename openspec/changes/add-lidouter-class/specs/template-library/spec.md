## MODIFIED Requirements

### Requirement: Canonical structure classes are Lid, LidOuter, RingOuter, RingInner

The canonical class set SHALL include `LidOuter` as an independent structure
class (lid outer edge — distinct from the stiffener-ring edges RingOuter /
RingInner), seeded into every library (new and existing, via the idempotent
boot pass) ordered directly after `Lid`. `LidOuter` SHALL default to
signature matching (bbox_ratio 0.0001), serialise to match-JSON key
`lid_outer`, and belong to the `structure` toolbar group with a purple-family
colour. The legacy rename pass SHALL NOT rename `LidOuter` away; the
snake_case legacy id `lid_outer` SHALL rename to `LidOuter`. `LidInner`
remains deleted (legacy rows continue to rename to `RingInner`).

#### Scenario: Existing library gains LidOuter on boot

- **WHEN** a library created before this change is next loaded
- **THEN** it carries a `LidOuter` class ranked after `Lid`, with
  signature/0.0001 default config

#### Scenario: LidOuter survives reboots

- **WHEN** an editor commits templates under `LidOuter` and the app restarts
- **THEN** the class and its templates keep their name (no legacy rename
  applies)

#### Scenario: Match JSON key

- **WHEN** a version with LidOuter instances saves its match JSON
- **THEN** the instances serialise under the `lid_outer` key
