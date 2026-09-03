# Deployment checklist

- Set a unique secret and non-root database credentials outside the repository.
- Use private persistent storage for uploads and backups.
- Terminate HTTPS at a reviewed reverse proxy and run Django deploy checks.
- Run migrations, seed groups and field configuration.
- Verify login, permissions, document access, backup and restore before release.
