"""
Custom Chain Library - LLM-created action chains that persist across restarts.

Chains are stored in custom_chains.json alongside the default CHAIN_LIBRARY
from chain_library.py. Default chains are never overridden.
"""

import json
import os
import time
import copy
from typing import Optional

CUSTOM_CHAINS_FILE = "custom_chains.json"

# Valid tool names (must match call_tool endpoint_map in chain_executor.py)
VALID_TOOL_NAMES = {
    "mine_block", "craft_item", "smelt_item", "place_block", "equip_item",
    "eat_food", "attack_entity", "dig_shelter", "escape_water", "flee",
    "dig_down", "dig_tunnel", "branch_mine", "build_shelter", "explore",
    "move_to", "find_block", "sleep_in_bed", "send_chat", "stop_moving",
    "shield_block", "store_items", "retrieve_items", "open_chest",
    "use_bucket", "collect_drops", "scan_caves",
}

VALID_STEP_TYPES = {"craft", "gather", "search", "place", "action"}


class CustomChainLibrary:
    def __init__(self):
        self.chains: dict[str, dict] = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(CUSTOM_CHAINS_FILE):
                with open(CUSTOM_CHAINS_FILE, "r", encoding="utf-8") as f:
                    self.chains = json.load(f)
                if self.chains:
                    print(f"Custom chains loaded: {len(self.chains)} chains")
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: {CUSTOM_CHAINS_FILE} corrupted ({e}), starting empty")
            self.chains = {}

    def _save(self):
        try:
            with open(CUSTOM_CHAINS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.chains, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Warning: Failed to save custom chains: {e}")

    def get_chain(self, name: str) -> Optional[list[dict]]:
        """Get a deep copy of a custom chain's steps."""
        entry = self.chains.get(name)
        if entry is None:
            return None
        return copy.deepcopy(entry.get("steps", []))

    def list_chains(self) -> list[dict]:
        """List all custom chains with metadata."""
        return [
            {
                "name": name,
                "description": data.get("description", ""),
                "source": data.get("source", "unknown"),
                "step_count": len(data.get("steps", [])),
                "success_count": data.get("success_count", 0),
                "fail_count": data.get("fail_count", 0),
            }
            for name, data in self.chains.items()
        ]

    def list_chain_names(self) -> list[str]:
        """List just the chain names."""
        return list(self.chains.keys())

    def save_chain(self, name: str, description: str, steps: list[dict],
                   source: str = "llm_created") -> str:
        """Validate and save a custom chain. Returns success/error message."""
        errors = self._validate_chain(name, steps)
        if errors:
            return f"Validation failed: {'; '.join(errors)}"

        self.chains[name] = {
            "name": name,
            "description": description,
            "source": source,
            "created_at": time.time(),
            "success_count": 0,
            "fail_count": 0,
            "steps": steps,
        }
        self._save()
        return f"Custom chain '{name}' saved ({len(steps)} steps)"

    def delete_chain(self, name: str) -> str:
        """Delete a custom chain."""
        if name not in self.chains:
            return f"Chain '{name}' not found in custom chains."
        del self.chains[name]
        self._save()
        return f"Deleted custom chain '{name}'"

    def record_success(self, name: str):
        """Increment success counter for a chain."""
        if name in self.chains:
            self.chains[name]["success_count"] = self.chains[name].get("success_count", 0) + 1
            self._save()

    def record_failure(self, name: str):
        """Increment failure counter for a chain."""
        if name in self.chains:
            self.chains[name]["fail_count"] = self.chains[name].get("fail_count", 0) + 1
            self._save()

    def _validate_chain(self, name: str, steps: list[dict]) -> list[str]:
        """Validate a chain definition. Returns list of errors (empty = valid)."""
        from chain_library import CHAIN_LIBRARY
        errors = []

        # Name checks
        if not name or not isinstance(name, str):
            errors.append("Chain name must be a non-empty string")
        elif not name.replace("_", "").isalnum():
            errors.append(f"Chain name '{name}' must be snake_case (letters, digits, underscores)")
        elif name in CHAIN_LIBRARY:
            errors.append(f"Chain name '{name}' conflicts with a default chain. Choose a different name.")

        # Steps checks
        if not steps or not isinstance(steps, list):
            errors.append("Chain must have at least one step")
            return errors

        if len(steps) > 30:
            errors.append(f"Chain has {len(steps)} steps, maximum is 30")

        # Per-step syntax validation
        has_gather_step = False
        has_craft_step = False
        craft_without_skip_if = []

        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                errors.append(f"Step {i} must be a dict")
                continue
            tool = step.get("tool", "")
            if not tool:
                errors.append(f"Step {i} missing 'tool'")
            elif tool not in VALID_TOOL_NAMES:
                errors.append(f"Step {i} has invalid tool '{tool}'. Valid: {', '.join(sorted(VALID_TOOL_NAMES))}")

            if "args" not in step or not isinstance(step.get("args"), dict):
                errors.append(f"Step {i} missing 'args' dict")

            step_type = step.get("type", "action")
            if step_type not in VALID_STEP_TYPES:
                errors.append(f"Step {i} has invalid type '{step_type}'. Valid: {', '.join(VALID_STEP_TYPES)}")

            if step_type == "search" and not step.get("search_target"):
                errors.append(f"Step {i} is type 'search' but missing 'search_target'")

            # Track gather vs craft for semantic validation
            if tool in ("mine_block", "find_block") or step_type == "search":
                has_gather_step = True
            if tool == "craft_item":
                has_craft_step = True
                if "skip_if" not in step:
                    craft_without_skip_if.append(i)

        # Semantic validation: craft chains MUST include resource gathering
        if has_craft_step and not has_gather_step:
            errors.append(
                "Chain has craft_item steps but NO resource gathering steps (mine_block/find_block with type 'search'). "
                "A working chain must gather raw materials BEFORE crafting. "
                "Pattern: mine_block(raw_material, type='search', search_target='...') → craft_item(result). "
                "Example: mine_block(oak_log) → craft_item(oak_planks) → craft_item(stick) → craft_item(wooden_sword)"
            )

        # Semantic validation: craft steps should have skip_if
        if craft_without_skip_if:
            step_nums = ", ".join(str(i) for i in craft_without_skip_if)
            errors.append(
                f"Craft steps [{step_nums}] are missing 'skip_if'. "
                "Every craft_item step MUST have skip_if to avoid re-crafting when items already exist. "
                "Example: {{\"tool\": \"craft_item\", \"args\": {{\"item_name\": \"oak_planks\"}}, "
                "\"type\": \"craft\", \"skip_if\": {{\"oak_planks\": 4}}}}"
            )

        # Semantic validation: search steps should have skip_if
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            if step.get("type") == "search" and "skip_if" not in step:
                errors.append(
                    f"Step {i} (type 'search') is missing 'skip_if'. "
                    "Search steps should have skip_if to skip gathering when you already have enough materials."
                )

        return errors
