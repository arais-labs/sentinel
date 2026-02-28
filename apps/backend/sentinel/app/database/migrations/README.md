SQL files executed at startup by `init_db()`.

How it works:
- `app/database/database.py` scans `migrations/*.sql` in filename order.
- Every file is executed each startup.

Rules:
- Use ordered names, for example: `0001_add_trigger_indexes.sql`, `0002_backfill_titles.sql`.
- SQL must be idempotent (`IF NOT EXISTS`, safe `DO $$` guards, etc.).
- You may edit files directly, but keep them safe for repeated execution.

Notes:
- Keep repeatable/bootstrap SQL in `startup_sql/pre` and `startup_sql/post`.
- Keep repeatable schema/data SQL in `migrations/`.
