## ADDED Requirements

### Requirement: DXF parsing uses strict-first with recover fallback

The system SHALL open user-uploaded DXF files by first calling
`ezdxf.readfile` (strict). When that call succeeds the parser
SHALL proceed exactly as before — there is no change to the
downstream flatten / circle-detection / bbox path. When the strict
call raises any of ezdxf's parser exception classes
(`ezdxf.DXFStructureError`, `ezdxf.DXFTagError`, or any subclass of
`ezdxf.DXFError` raised inside `readfile`), the parser SHALL fall
back to `ezdxf.recover.readfile` and continue with the recovered
`(doc, auditor)`. Non-parser exceptions (`FileNotFoundError`,
`PermissionError`, OS-level IO errors) SHALL NOT trigger the
fallback and SHALL propagate unchanged.

When the recover path is taken, the parser SHALL:
- Emit a `WARNING`-level server log carrying the file id (or path
  when no id is available yet), the strict-mode exception class
  name and message, and an Auditor summary
  (`n_fixed`, `n_unrecoverable`, and the first ≤ 5 audit
  messages).
- Persist that summary as a JSON-serialisable dict on the
  `FileRecord.dxf_recover_notes` field. The dict's shape SHALL be:
  `{"strict_error": "<ExceptionClassName>: <msg>",
    "n_fixed": <int>, "n_unrecoverable": <int>,
    "audit_messages": ["<msg>", …]}`.
  Files that succeed via strict SHALL leave the field `null`.

When both strict and recover raise, the parser SHALL re-raise an
exception whose message includes the strict exception (class +
message) and the recover exception (class + message) separated by
a marker (`" | recover: "` or equivalent), and the worker's
exception handler SHALL log it at `ERROR` level. The file SHALL
transition to the `error` lifecycle status with that combined
message captured in `FileRecord.error`.

Numerical output for files that succeed via strict SHALL be
byte-identical to the prior behaviour. Files that succeed via
recover SHALL produce the geometric output ezdxf's recover yields;
the system makes no claim that recovered geometry matches what a
hypothetical strict parse would have produced — by definition the
strict parse did not produce one.

#### Scenario: Strict-OK file leaves recover notes null
- **WHEN** an uploaded DXF parses successfully via `ezdxf.readfile`
- **THEN** the resulting `FileRecord.dxf_recover_notes` is `null`
- **AND** no `WARNING` log line is emitted for the upload

#### Scenario: Recover-OK file populates recover notes and logs WARNING
- **WHEN** an uploaded DXF raises `DXFStructureError` from
  `ezdxf.readfile` and is then parsed successfully via
  `ezdxf.recover.readfile`
- **THEN** the file's status reaches `ready_to_match` as normal
- **AND** `FileRecord.dxf_recover_notes` is a dict containing
  `strict_error`, `n_fixed`, `n_unrecoverable`, and
  `audit_messages` (≤ 5 entries)
- **AND** the server log contains a single `WARNING` line
  identifying the file and quoting the strict exception + audit
  counts

#### Scenario: Both-fail file reaches error status with combined detail
- **WHEN** an uploaded DXF raises `DXFStructureError` from
  `ezdxf.readfile` and `ezdxf.recover.readfile` also raises
- **THEN** the file's status becomes `error`
- **AND** `FileRecord.error` contains both exception class names
  and messages (strict and recover) in a single string
- **AND** the server log contains an `ERROR` line covering both
  exceptions
- **AND** `FileRecord.dxf_recover_notes` is `null`

#### Scenario: Non-parser exception is not recovered
- **WHEN** opening an uploaded DXF raises `FileNotFoundError` (the
  file was deleted between upload registration and worker start)
- **THEN** the parser SHALL NOT call `ezdxf.recover.readfile`
- **AND** the file's status becomes `error` with the original
  exception captured in `FileRecord.error`

## MODIFIED Requirements

### Requirement: File lifecycle status

Each uploaded file SHALL track exactly one status value at any time
from: `preprocessing`, `ready_to_match`, `checking_rules`, `report`,
`error`. Initial state SHALL be `preprocessing`; successful preprocess
SHALL transition to `ready_to_match`; preprocess failure SHALL
transition to `error` with the captured exception in `error`.

The `error` field SHALL capture either:
- a single exception message and traceback (the historical case,
  e.g. an OS error or a downstream pipeline failure), **or**
- a combined strict + recover exception string when both DXF parse
  paths failed (see `DXF parsing uses strict-first with recover
  fallback`).

The `dxf_recover_notes` field SHALL be populated independently of
the lifecycle status: a file may reach `ready_to_match` with
non-null `dxf_recover_notes` (the recover path succeeded), or
`error` with null `dxf_recover_notes` (recover did not save it, or
the failure was not DXF-parse related).

#### Scenario: Successful preprocess
- **WHEN** the preprocess worker returns successfully for a file
- **THEN** the file's status becomes `ready_to_match`
- **AND** `parsed_at`, `primitive_count`, `bbox`, and `background` are populated

#### Scenario: Preprocess failure
- **WHEN** the preprocess worker raises an exception
- **THEN** the file's status becomes `error`
- **AND** the `error` field captures the exception message and traceback

#### Scenario: Ready file may carry recover notes
- **WHEN** a file's preprocess succeeds via the recover fallback
- **THEN** the file's status is `ready_to_match`
- **AND** `FileRecord.dxf_recover_notes` is a non-null dict carrying
  the audit summary
