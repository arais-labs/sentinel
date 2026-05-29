from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _script_dir(ini_name: str, script_location: str) -> ScriptDirectory:
    config = Config(str(BACKEND_ROOT / ini_name))
    config.set_main_option("script_location", str(BACKEND_ROOT / script_location))
    return ScriptDirectory.from_config(config)


def test_alembic_histories_are_linear_from_v1_baseline() -> None:
    for ini_name, script_location, baseline in (
        ("alembic.manager.ini", "db/alembic/manager", "0000_manager_v1"),
        ("alembic.instance.ini", "db/alembic/instance", "0000_instance_v1"),
    ):
        script_dir = _script_dir(ini_name, script_location)
        assert len(script_dir.get_heads()) == 1
        assert script_dir.get_bases() == [baseline]


def test_backup_verified_head_matches_instance_head() -> None:
    # Dead-man's-switch: any new instance migration moves the head, reddening
    # this test until a commit re-affirms backup compatibility (and raises
    # MIN_RESTORABLE_VERSION if the change breaks older backups).
    from app.services.backup.engine import VERIFIED_INSTANCE_ALEMBIC_HEAD

    script_dir = _script_dir("alembic.instance.ini", "db/alembic/instance")
    assert script_dir.get_heads() == [VERIFIED_INSTANCE_ALEMBIC_HEAD], (
        "Instance migration head changed. A schema change can break backup "
        "restore: review compatibility, raise MIN_RESTORABLE_VERSION if older "
        "backups no longer restore, then re-pin VERIFIED_INSTANCE_ALEMBIC_HEAD."
    )


def test_alembic_templates_do_not_generate_downgrades() -> None:
    for template in (
        BACKEND_ROOT / "db/alembic/manager/script.py.mako",
        BACKEND_ROOT / "db/alembic/instance/script.py.mako",
    ):
        content = template.read_text(encoding="utf-8")
        assert "${downgrades" not in content
        assert (
            'raise RuntimeError("Downgrade is not supported for Sentinel database migrations.")'
            in content
        )


def test_instance_baseline_preserves_unique_system_memory_key_index() -> None:
    baseline = (
        BACKEND_ROOT / "db/alembic/instance/versions/0000_instance_v1_baseline.py"
    ).read_text(encoding="utf-8")
    assert '"uq_memories_system_key"' in baseline
    assert "unique=True" in baseline
    assert 'postgresql_where=sa.text("is_system")' in baseline
