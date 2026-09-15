"""Initial Sentinel manager schema for fresh installations."""

from alembic import op

revision = "0001_manager_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE instances (
	id CHAR(32) NOT NULL,
	name VARCHAR(80) NOT NULL,
	database_name VARCHAR(80) NOT NULL,
	display_name VARCHAR(120),
	appearance JSON DEFAULT '{}' NOT NULL,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name),
	UNIQUE (database_name)
)""")
    op.execute("""CREATE TABLE machines (
	id CHAR(32) NOT NULL,
	name VARCHAR(120) NOT NULL,
	provider VARCHAR(32) NOT NULL,
	status VARCHAR(32) NOT NULL,
	host VARCHAR(255),
	port INTEGER,
	username VARCHAR(120),
	auth_type VARCHAR(24),
	encrypted_secret TEXT,
	profile VARCHAR(120),
	last_job_id CHAR(32),
	last_job_status VARCHAR(32),
	provider_config JSON DEFAULT '{}' NOT NULL,
	provider_state JSON DEFAULT '{}' NOT NULL,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
)""")
    op.execute("""CREATE TABLE manager_settings (
	"key" VARCHAR(100) NOT NULL,
	value TEXT NOT NULL,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY ("key")
)""")


def downgrade():
    raise RuntimeError("Downgrade is not supported for Sentinel database migrations.")
