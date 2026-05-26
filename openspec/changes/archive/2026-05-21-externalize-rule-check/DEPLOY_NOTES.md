# Deploy notes — externalize-rule-check

This change is BREAKING for the persisted `data/rule_check/{product_id}.json`
files. They were written in the old shape (`from`/`to` as lists, no `tol` /
`tol_text` field) and the new viewer / adapter expect the new shape.

## Deploy step (one-time, after pulling this change)

```bash
# From the SMDR2 deploy host (or wherever data/ lives):
rm -f data/rule_check/*.json
```

Users with existing rule-check results will see "rule check not yet run" on
the dashboard for affected products; they just need to click "Run Rule
Check" again. No data is lost — Match JSONs and DXFs are untouched.

## External rule module

The adapter (`app/rule_check.py:check_rules`) imports from
`app/external_rule_check.py`. Until the external rule-checking team commits
their real implementation, that file is a stub that raises
`NotImplementedError`. Any rule-check job submitted before the real module
lands will land in `status: error` with that message — that's the expected
fail-loud behaviour.

When the external team commits their module:

1. Replace the stub body in `app/external_rule_check.py` (or merge their
   PR if they're contributing through git).
2. No other code changes — the adapter forwards `(product_id, bundle_dir)`
   verbatim.
3. Run `pytest tests/test_rule_check.py tests/test_rule_check_job.py` to
   confirm the adapter + worker pipe stays green.

## Rollback

```bash
git revert <this-commit>
rm -f data/rule_check/*.json  # the rolled-forward results would be in the new shape
```

Old `check_rules` and viewer can be restored by reverting the branch. As
the proposal noted, the rollback path is symmetric — wipe `data/rule_check/`
again and users re-run.
