# Engineering Release Process

## Release Approval
Production releases require passing unit tests, integration tests, security checks, and approval from the service owner. High-risk changes require a rollback plan before deployment.

## Change Freeze
Quarter-end change freeze applies to billing, invoicing, and revenue reporting systems. Emergency fixes may proceed with director approval and incident commander oversight.

--- page ---

## Rollback
Teams must define rollback criteria before major releases. Rollback is required when error rate exceeds 2 percent for 10 minutes, p95 latency doubles, or data integrity checks fail.
