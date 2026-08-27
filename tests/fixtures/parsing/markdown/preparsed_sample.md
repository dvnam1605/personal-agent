# Deployment Runbook

## Preconditions

Confirm the release tag exists and CI is green before starting.

| Step | Command | Owner |
| --- | --- | --- |
| Build | make release | platform |
| Verify | make smoke-test | qa |

## Rollout

- Announce the window in the status channel
- Apply migrations with the locked deploy account
- Watch error dashboards for ten minutes

### Rollback

Revert to the previous artifact and re-run smoke tests.
