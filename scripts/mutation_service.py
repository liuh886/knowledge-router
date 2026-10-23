"""Application commit boundary: source mutation followed by projection activation."""

from __future__ import annotations

from typing import Any, Callable

from capture_broker import CapturePlan, MutationResult, commit_capture_plan
from registry import VaultConfig, VaultRegistry
import vault_service


def commit_and_refresh(
    registry: VaultRegistry,
    config: VaultConfig,
    plan: CapturePlan | str,
    *,
    on_applied: Callable[[MutationResult], None] | None = None,
) -> dict[str, Any]:
    """A pending index is an applied operation, never an invitation to capture again."""
    mutation = commit_capture_plan(config.root_path, registry.vault_state_dir(config), plan)
    if on_applied is not None:
        on_applied(mutation)
    try:
        index = vault_service.sync_vault(registry, registry.resolve(config.id))
    except (OSError, ValueError, RuntimeError) as exc:
        return {
            "status": "applied_pending_index",
            "mutation": mutation.to_dict(),
            "sync": None,
            "plan_id": mutation.plan_id,
            "index_error": str(exc),
            "message": "Content was committed; indexing is pending. Retry the frozen plan with "
            f"capture --commit --plan-id {mutation.plan_id} --vault {config.id}",
        }
    return {
        "status": "success",
        "mutation": mutation.to_dict(),
        "sync": index,
        "plan_id": mutation.plan_id,
    }
