"""Business modules. Each owns its tables and exposes functions to the others.

A module is a package `app/modules/<name>/` with:

- `domain/`: pure rules. No FastAPI, SQLAlchemy, network or infrastructure imports, and the
  clock is injected. import-linter enforces this for every module (see pyproject.toml).
- `service.py`: commands and queries that open transactions and call the domain.
- `api.py`: FastAPI routes and Pydantic contracts.

A module never writes to another module's tables.
"""
