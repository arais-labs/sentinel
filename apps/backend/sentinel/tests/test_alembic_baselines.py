from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _current_head(ini_name: str, script_location: str) -> str:
    config = Config(str(BACKEND_ROOT / ini_name))
    config.set_main_option("script_location", str(BACKEND_ROOT / script_location))
    return ScriptDirectory.from_config(config).get_current_head()


def test_alembic_baselines_have_single_v1_heads() -> None:
    assert _current_head("alembic.manager.ini", "db/alembic/manager") == "0000_manager_v1"
    assert _current_head("alembic.instance.ini", "db/alembic/instance") == "0000_instance_v1"


def test_alembic_templates_do_not_generate_downgrades() -> None:
    for template in (
        BACKEND_ROOT / "db/alembic/manager/script.py.mako",
        BACKEND_ROOT / "db/alembic/instance/script.py.mako",
    ):
        content = template.read_text(encoding="utf-8")
        assert "${downgrades" not in content
        assert 'raise RuntimeError("Downgrade is not supported for Sentinel database migrations.")' in content


def test_instance_baseline_preserves_unique_system_memory_key_index() -> None:
    baseline = (BACKEND_ROOT / "db/alembic/instance/versions/0000_instance_v1_baseline.py").read_text(
        encoding="utf-8"
    )
    assert '"uq_memories_system_key"' in baseline
    assert "unique=True" in baseline
    assert 'postgresql_where=sa.text("is_system")' in baseline
