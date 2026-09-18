"""Workspace names are worker-owned; client references are keyed by UUID."""

from alembic import op
from sqlalchemy import inspect

revision = "0007_workspace_name_cache"
down_revision = "0006_approval_session_cascade"
branch_labels = None
depends_on = None


def rebuild(*, unique):
    connection = op.get_bind()
    constraints = inspect(connection).get_unique_constraints("workspaces")
    existing = next((item for item in constraints if item["column_names"] == ["name"]), None)
    if bool(existing) == unique:
        return  # Safe if SQLite committed the rebuild before Alembic recorded it.
    # SQLite rebuilds parent tables to alter constraints. Disable FK actions
    # outside a transaction, then rebuild atomically; otherwise DROP TABLE can
    # null conversation bindings or cascade-delete dependent rows.
    with op.get_context().autocommit_block():
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            with op.batch_alter_table(
                "workspaces", naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}
            ) as batch:
                if unique:
                    batch.create_unique_constraint("uq_workspaces_name", ["name"])
                else:
                    batch.drop_constraint(existing["name"] or "uq_workspaces_name", type_="unique")
            if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("Workspace migration failed foreign-key verification")
            connection.exec_driver_sql("COMMIT")
        except BaseException:
            connection.exec_driver_sql("ROLLBACK")
            raise
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def upgrade():
    rebuild(unique=False)


def downgrade():
    # Duplicate names deliberately block downgrade; never rename or delete data.
    rebuild(unique=True)
