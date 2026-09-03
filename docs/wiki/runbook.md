# Public operational runbook

The demo is deliberately self-contained and is not production infrastructure.
For a production-like deployment, use a dedicated non-root MariaDB account,
private storage for uploads/backups, HTTPS termination and an isolated restore
environment. Run the relevant release checks before publishing a build.

Django 5.2 LTS patch updates must be validated with the project test suite and
`release/check.py check`; dependency versions are intentionally pinned in
`app/requirements.txt`.
