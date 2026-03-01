Startup SQL files executed by `init_db()` in deterministic order.

- `pre/*.sql` runs before `Base.metadata.create_all`
- `post/*.sql` runs after `Base.metadata.create_all`

Guidelines:
- Keep each file to a single SQL statement.
- Use ordered numeric prefixes (`001_`, `010_`, etc.).
- Make statements idempotent (`IF NOT EXISTS`) where possible.
