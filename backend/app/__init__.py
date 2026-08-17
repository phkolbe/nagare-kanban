"""Nagare Kanban application package.

Layout:
    config.py    settings, canonical feature flags and plans
    db.py        sqlite3 connection factory, migrations, query helpers
    models.py    dataclasses mirroring the DDL
    schemas.py   pydantic request/response contract shared by every router
    security.py  PBKDF2 password hashing + hand-rolled HS256 JWT
    deps.py      FastAPI dependencies: db, current user, RBAC, feature flags
    errors.py    ApiError + exception handlers
    seed.py      deterministic demo workspace generator
    main.py      app factory
"""

__version__ = "1.0.0"
