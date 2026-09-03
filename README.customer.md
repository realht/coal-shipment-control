# Runtime package note

This document is retained for the release packaging tooling. The public portfolio
demo is started with `docker compose up --build` and uses synthetic SQLite data.
It is not a production deployment guide and contains no customer infrastructure
details, credentials, backups, or runtime artifacts.

For a production-like environment, configure a dedicated non-root MariaDB user,
private uploads and backups storage, HTTPS termination, and the security
environment variables documented in `.env.example`.
