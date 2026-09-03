# Runtime architecture

The application is a Django web service with MariaDB in a production-like setup
and filesystem storage for uploaded documents. The public demo uses SQLite only.
The app container exposes HTTP behind a reverse proxy; a separate scheduler
process executes queued backup and restore operations.
