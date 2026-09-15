from __future__ import annotations

import pytest

from app.services.modules.custom_modules import (
    build_custom_module_permission_levels,
    normalize_custom_module_actions,
)


def _levels(
    actions: list[dict],
    *,
    permissions: dict | None = None,
    existing: dict | None = None,
) -> dict[str, str]:
    return build_custom_module_permission_levels(
        module_name="interactive_brokers_accounts",
        actions=normalize_custom_module_actions(actions),
        permissions=permissions,
        existing=existing,
    )


def test_custom_action_permission_default_seeds_missing_permission():
    levels = _levels(
        [
            {
                "id": "place_order",
                "label": "Place Order",
                "permission_default": "approval",
            }
        ]
    )

    assert levels["place_order"] == "approval"


def test_existing_permission_row_wins_over_action_permission_default():
    levels = _levels(
        [
            {
                "id": "place_order",
                "label": "Place Order",
                "permission_default": "approval",
            }
        ],
        existing={"place_order": "allow"},
    )

    assert levels["place_order"] == "allow"


def test_explicit_permission_payload_wins_over_action_permission_default():
    levels = _levels(
        [
            {
                "id": "place_order",
                "label": "Place Order",
                "permission_default": "approval",
            }
        ],
        permissions={"place_order": "deny"},
    )

    assert levels["place_order"] == "deny"


def test_invalid_action_permission_default_uses_current_fallback():
    levels = _levels(
        [
            {
                "id": "place_order",
                "label": "Place Order",
                "permission_default": "sometimes",
            }
        ]
    )

    assert levels["place_order"] == "allow"


def test_ibkr_place_order_permission_default_seeds_approval():
    levels = _levels(
        [
            {
                "id": "preview_order",
                "label": "Preview Order",
                "permission_default": "allow",
            },
            {
                "id": "place_order",
                "label": "Place Order",
                "permission_default": "approval",
            },
        ]
    )

    assert levels["preview_order"] == "allow"
    assert levels["place_order"] == "approval"


@pytest.mark.parametrize("command", ["create_record", "update_record", "delete_record"])
def test_singular_record_permission_aliases_are_rejected(command):
    with pytest.raises(ValueError, match="Unknown permission command"):
        _levels([], permissions={command: "deny"})
