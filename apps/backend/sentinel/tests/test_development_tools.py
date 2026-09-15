"""Keep the API picker, desktop installer, and rendered names in agreement."""

import json
from pathlib import Path
from uuid import uuid4

from app.routers.workspaces import WorkspaceCreate, WorkspaceUpdate
from app.services.runtime.development_tools import STACKS, TOOLS, TOOL_IDS

ROOT = Path(__file__).resolve().parents[4]


def exported_object(file, declaration):
    text = (ROOT / file).read_text().split(declaration, 1)[1].split("=", 1)[1].lstrip()
    return json.JSONDecoder().raw_decode(text)[0]


def test_every_selectable_tool_has_an_installer_and_display_name():
    packages = exported_object(
        "apps/desktop/sentinel/src/main/workspace/workspaceTools.ts", "export const toolPackages:"
    )
    names = exported_object(
        "apps/frontend/sentinel/src/components/runtime/workspaceTools.ts", "export const toolNames:"
    )
    assert set(packages) == TOOL_IDS == set(names)
    assert names == {tool["id"]: tool["name"] for tool in TOOLS}
    assert all(set(stack["tools"]) <= TOOL_IDS for stack in STACKS)
    assert len(TOOL_IDS) == len(TOOLS)


def test_full_catalog_can_be_created_and_added_to_existing_workspaces():
    selected = sorted(TOOL_IDS)
    new = WorkspaceCreate(
        name="Full stack", machine_id=uuid4(), directory="/project", development_tools=selected
    )
    edit = WorkspaceUpdate(name="Full stack", development_tools=selected)
    assert new.development_tools == edit.development_tools == selected


def test_brand_assets_exist_for_stacks_and_branded_tools():
    logos = ROOT / "apps/frontend/sentinel/src/assets/tool-logos"
    for identifier in TOOL_IDS - {"ninja"}:
        assert "<svg" in (logos / f"{identifier}.svg").read_text(), identifier
    for stack in STACKS:
        assert (logos / f"{stack['id']}.svg").exists()
