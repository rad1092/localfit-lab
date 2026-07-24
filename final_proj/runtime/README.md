# Runtime directory

This directory contains local, reproducible product artifacts and is excluded from Git.

- `db/`: the SQLite service database and local backups
- `reports/`: generated report bundles and charts
- `exports/`: user-facing export files

Disposable logs, caches, and rendering previews do not belong in this directory. Development process logs are written to the operating system's temporary directory.

Canonical source, silver, gold, validation, and research materials do not belong here. They remain under the workspace-level `datacorpus/`, `research/`, and `docs/` directories.
