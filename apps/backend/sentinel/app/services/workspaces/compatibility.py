"""Typed compatibility failures from either local or SSH runtime transport.

Protocol negotiation belongs to the runtime connection, not individual features.
Keep upgrade metadata intact through API error handling and workspace switching.
"""


class RuntimeCompatibilityError(RuntimeError):
    def __init__(self, code, message, *, machine_id=None, installed=None, required=None):
        super().__init__(message)
        self.code = code
        self.details = {
            "machine_id": str(machine_id) if machine_id else None,
            "installed": installed,
            "required": required,
        }

    @classmethod
    def check(cls, response, *, machine_id=None):
        if response.get("code") in {"runtime_update_required", "app_update_required"}:
            versions = response.get("details") or {}
            raise cls(
                response["code"],
                response["error"],
                machine_id=machine_id,
                installed=versions.get("installed"),
                required=versions.get("required"),
            )
