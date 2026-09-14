# Migrations

V1 creates the schema with `Base.metadata.create_all` from `scripts/seed_database.py`
and on API startup, which is sufficient while the schema is still moving.

Alembic is in `requirements.txt` and the model layer is Alembic-ready. To switch:

```bash
alembic init migrations
# point sqlalchemy.url at DATABASE_URL and target_metadata at app.db.models.Base.metadata
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

Do this before the first production deployment; card-data versioning lives in
`card_versions`, not in migrations, so card updates never require one.
