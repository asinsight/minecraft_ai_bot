"""
Goal Planner — Multi-step goal decomposition and tracking for Minecraft AI bot.

The planner breaks high-level goals (e.g., "make diamond pickaxe") into
ordered sub-steps, tracks completion, and feeds the current step to the
LLM each tick so it knows exactly what to do next.

Architecture:
  GoalPlanner
    ├── GoalLibrary       (predefined goal → steps mappings)
    ├── ActiveGoal        (current goal state with step tracking)
    └── DynamicPlanner    (LLM-generated plans for unknown goals)
"""

import time
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ============================================
# ENUMS & DATA CLASSES
# ============================================

class GoalPriority(Enum):
    IDLE = 0
    AUTONOMOUS = 1
    PLAYER = 2
    DIRECT = 3       # immediate commands: "come here", "stop"
    SURVIVAL = 4     # auto-triggered: health critical, starving


class StepStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class GoalStep:
    """A single step within a multi-step goal."""
    id: int
    description: str                         # human-readable: "Mine 3 oak logs"
    tool_hint: str                           # suggested tool: "mine_block"
    tool_args_hint: dict = field(default_factory=dict)  # suggested args
    check_condition: str = ""                # how to verify completion
    status: StepStatus = StepStatus.PENDING
    result: str = ""                         # tool output after execution
    attempts: int = 0
    max_attempts: int = 3


@dataclass
class ActiveGoal:
    """A goal currently being pursued by the bot."""
    name: str                                # e.g., "make_wooden_pickaxe"
    description: str                         # e.g., "Craft a wooden pickaxe for mining stone"
    priority: GoalPriority
    steps: list[GoalStep] = field(default_factory=list)
    current_step_idx: int = 0
    created_at: float = field(default_factory=time.time)
    ttl: float = 300.0                       # 5 minutes default
    source: str = "autonomous"               # "autonomous", "player", "survival"

    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.ttl

    @property
    def is_complete(self) -> bool:
        return all(s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED) for s in self.steps)

    @property
    def current_step(self) -> Optional[GoalStep]:
        if self.current_step_idx < len(self.steps):
            return self.steps[self.current_step_idx]
        return None

    @property
    def progress_str(self) -> str:
        done = sum(1 for s in self.steps if s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED))
        return f"[{done}/{len(self.steps)}]"

    def advance(self):
        """Move to the next pending step."""
        self.current_step_idx += 1
        while self.current_step_idx < len(self.steps):
            if self.steps[self.current_step_idx].status == StepStatus.PENDING:
                return
            self.current_step_idx += 1

    def to_prompt_context(self) -> str:
        """Generate context string to inject into the LLM prompt."""
        lines = [
            f"🎯 ACTIVE GOAL: {self.description}",
            f"   Progress: {self.progress_str}",
            f"   Priority: {self.priority.name} | Source: {self.source}",
            "",
            "   Steps:"
        ]
        for step in self.steps:
            icon = {
                StepStatus.COMPLETED: "✅",
                StepStatus.FAILED: "❌",
                StepStatus.SKIPPED: "⏭️",
                StepStatus.IN_PROGRESS: "▶️",
                StepStatus.PENDING: "⬜",
            }[step.status]
            lines.append(f"   {icon} Step {step.id}: {step.description}")
            if step.status == StepStatus.FAILED and step.result:
                lines.append(f"      Last error: {step.result}")

        current = self.current_step
        if current:
            lines.append("")
            lines.append(f"👉 CURRENT STEP: {current.description}")
            lines.append(f"   Suggested tool: {current.tool_hint}({json.dumps(current.tool_args_hint)})")
            if current.check_condition:
                lines.append(f"   Success condition: {current.check_condition}")
            lines.append(f"   Attempts: {current.attempts}/{current.max_attempts}")

        return "\n".join(lines)


# ============================================
# GOAL LIBRARY — Predefined multi-step plans
# ============================================

GOAL_LIBRARY: dict[str, dict] = {
    # ── BASIC PROGRESSION ──
    "get_wood": {
        "description": "Gather wood logs and make planks",
        "ttl": 120,
        "steps": [
            {"description": "Mine 5 oak logs", "tool_hint": "mine_block", "tool_args_hint": {"block_type": "oak_log", "count": 5}, "check_condition": "inventory has oak_log >= 5"},
            {"description": "Craft oak planks from logs", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "oak_planks"}, "check_condition": "inventory has oak_planks >= 4"},
        ]
    },
    "make_crafting_table": {
        "description": "Make a crafting table",
        "ttl": 180,
        "steps": [
            {"description": "Mine 1 oak log", "tool_hint": "mine_block", "tool_args_hint": {"block_type": "oak_log", "count": 1}, "check_condition": "inventory has oak_log >= 1"},
            {"description": "Craft oak planks", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "oak_planks"}, "check_condition": "inventory has oak_planks >= 4"},
            {"description": "Craft crafting table", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "crafting_table"}, "check_condition": "inventory has crafting_table >= 1"},
            {"description": "Place crafting table on ground", "tool_hint": "place_block", "tool_args_hint": {"block_name": "crafting_table"}, "check_condition": "crafting_table placed nearby"},
        ]
    },
    "make_wooden_pickaxe": {
        "description": "Craft a wooden pickaxe to mine stone",
        "ttl": 300,
        "steps": [
            {"description": "Mine 4 oak logs", "tool_hint": "mine_block", "tool_args_hint": {"block_type": "oak_log", "count": 4}, "check_condition": "inventory has oak_log >= 4"},
            {"description": "Craft oak planks (need 7+)", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "oak_planks"}, "check_condition": "inventory has oak_planks >= 7"},
            {"description": "Craft sticks (need 2+)", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "stick"}, "check_condition": "inventory has stick >= 2"},
            {"description": "Craft crafting table if not placed", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "crafting_table"}, "check_condition": "crafting_table available"},
            {"description": "Place crafting table", "tool_hint": "place_block", "tool_args_hint": {"block_name": "crafting_table"}, "check_condition": "crafting_table placed"},
            {"description": "Craft wooden pickaxe", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "wooden_pickaxe"}, "check_condition": "inventory has wooden_pickaxe >= 1"},
            {"description": "Equip the wooden pickaxe", "tool_hint": "equip_item", "tool_args_hint": {"item_name": "wooden_pickaxe"}, "check_condition": "wooden_pickaxe equipped"},
        ]
    },
    "make_stone_pickaxe": {
        "description": "Craft a stone pickaxe for mining iron",
        "ttl": 360,
        "steps": [
            {"description": "Ensure wooden pickaxe is equipped", "tool_hint": "equip_item", "tool_args_hint": {"item_name": "wooden_pickaxe"}, "check_condition": "wooden_pickaxe equipped"},
            {"description": "Mine 3 cobblestone", "tool_hint": "mine_block", "tool_args_hint": {"block_type": "stone", "count": 3}, "check_condition": "inventory has cobblestone >= 3"},
            {"description": "Craft sticks if needed", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "stick"}, "check_condition": "inventory has stick >= 2"},
            {"description": "Craft stone pickaxe at crafting table", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "stone_pickaxe"}, "check_condition": "inventory has stone_pickaxe >= 1"},
            {"description": "Equip stone pickaxe", "tool_hint": "equip_item", "tool_args_hint": {"item_name": "stone_pickaxe"}, "check_condition": "stone_pickaxe equipped"},
        ]
    },
    "make_iron_pickaxe": {
        "description": "Craft an iron pickaxe for mining diamonds",
        "ttl": 600,
        "steps": [
            {"description": "Ensure stone pickaxe is equipped", "tool_hint": "equip_item", "tool_args_hint": {"item_name": "stone_pickaxe"}, "check_condition": "stone_pickaxe equipped"},
            {"description": "Mine 3 iron ore", "tool_hint": "mine_block", "tool_args_hint": {"block_type": "iron_ore", "count": 3}, "check_condition": "inventory has raw_iron >= 3"},
            {"description": "Mine 8 cobblestone for furnace", "tool_hint": "mine_block", "tool_args_hint": {"block_type": "stone", "count": 8}, "check_condition": "inventory has cobblestone >= 8"},
            {"description": "Craft furnace", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "furnace"}, "check_condition": "inventory has furnace >= 1"},
            {"description": "Place furnace", "tool_hint": "place_block", "tool_args_hint": {"block_name": "furnace"}, "check_condition": "furnace placed"},
            {"description": "Mine coal for fuel", "tool_hint": "mine_block", "tool_args_hint": {"block_type": "coal_ore", "count": 3}, "check_condition": "inventory has coal >= 3"},
            {"description": "Smelt raw iron in furnace to get iron ingots", "tool_hint": "smelt_item", "tool_args_hint": {"item_name": "raw_iron", "count": 3}, "check_condition": "inventory has iron_ingot >= 3"},
            {"description": "Craft sticks if needed", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "stick"}, "check_condition": "inventory has stick >= 2"},
            {"description": "Craft iron pickaxe", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "iron_pickaxe"}, "check_condition": "inventory has iron_pickaxe >= 1"},
            {"description": "Equip iron pickaxe", "tool_hint": "equip_item", "tool_args_hint": {"item_name": "iron_pickaxe"}, "check_condition": "iron_pickaxe equipped"},
        ]
    },

    # ── SURVIVAL ──
    "find_food": {
        "description": "Hunt animals for food and eat",
        "ttl": 180,
        "steps": [
            {"description": "Look for animals nearby (cow, pig, chicken, sheep)", "tool_hint": "get_nearby", "tool_args_hint": {}, "check_condition": "see animals nearby"},
            {"description": "Kill an animal (attack until it dies)", "tool_hint": "attack_entity", "tool_args_hint": {"entity_type": "cow"}, "check_condition": "raw food dropped"},
            {"description": "Pick up dropped items (walk to them)", "tool_hint": "get_inventory", "tool_args_hint": {}, "check_condition": "food in inventory"},
            {"description": "Eat the food", "tool_hint": "eat_food", "tool_args_hint": {}, "check_condition": "hunger restored"},
        ]
    },
    "build_shelter": {
        "description": "Build an enclosed shelter to survive the night (mobs can't enter)",
        "ttl": 300,
        "steps": [
            {"description": "Mine 25 cobblestone or dirt for building", "tool_hint": "mine_block", "tool_args_hint": {"block_type": "stone", "count": 25}, "check_condition": "inventory has 20+ building blocks"},
            {"description": "Build enclosed shelter (walls + roof)", "tool_hint": "build_shelter", "tool_args_hint": {}, "check_condition": "shelter built around bot"},
        ]
    },

    # ── FULL PROGRESSION CHAIN ──
    "full_tool_progression": {
        "description": "Complete tool progression: wood → stone → iron pickaxe",
        "ttl": 900,
        "steps": [
            {"description": "Mine 8 oak logs", "tool_hint": "mine_block", "tool_args_hint": {"block_type": "oak_log", "count": 8}, "check_condition": "inventory has oak_log >= 8"},
            {"description": "Craft oak planks", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "oak_planks"}, "check_condition": "inventory has oak_planks >= 12"},
            {"description": "Craft sticks", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "stick"}, "check_condition": "inventory has stick >= 8"},
            {"description": "Craft crafting table", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "crafting_table"}, "check_condition": "inventory has crafting_table >= 1"},
            {"description": "Place crafting table", "tool_hint": "place_block", "tool_args_hint": {"block_name": "crafting_table"}, "check_condition": "crafting_table placed"},
            {"description": "Craft wooden pickaxe", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "wooden_pickaxe"}, "check_condition": "inventory has wooden_pickaxe >= 1"},
            {"description": "Equip wooden pickaxe", "tool_hint": "equip_item", "tool_args_hint": {"item_name": "wooden_pickaxe"}, "check_condition": "equipped"},
            {"description": "Mine 3 stone → cobblestone", "tool_hint": "mine_block", "tool_args_hint": {"block_type": "stone", "count": 3}, "check_condition": "inventory has cobblestone >= 3"},
            {"description": "Craft stone pickaxe", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "stone_pickaxe"}, "check_condition": "inventory has stone_pickaxe >= 1"},
            {"description": "Equip stone pickaxe", "tool_hint": "equip_item", "tool_args_hint": {"item_name": "stone_pickaxe"}, "check_condition": "equipped"},
            {"description": "Mine 3 iron ore", "tool_hint": "mine_block", "tool_args_hint": {"block_type": "iron_ore", "count": 3}, "check_condition": "inventory has raw_iron >= 3"},
            {"description": "Mine coal for fuel", "tool_hint": "mine_block", "tool_args_hint": {"block_type": "coal_ore", "count": 3}, "check_condition": "inventory has coal >= 3"},
            {"description": "Mine 8 cobblestone for furnace (if not enough)", "tool_hint": "mine_block", "tool_args_hint": {"block_type": "stone", "count": 8}, "check_condition": "inventory has cobblestone >= 8"},
            {"description": "Craft furnace", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "furnace"}, "check_condition": "inventory has furnace >= 1"},
            {"description": "Place furnace", "tool_hint": "place_block", "tool_args_hint": {"block_name": "furnace"}, "check_condition": "furnace placed"},
            {"description": "Smelt raw iron into iron ingots", "tool_hint": "smelt_item", "tool_args_hint": {"item_name": "raw_iron", "count": 3}, "check_condition": "inventory has iron_ingot >= 3"},
            {"description": "Craft iron pickaxe", "tool_hint": "craft_item", "tool_args_hint": {"item_name": "iron_pickaxe"}, "check_condition": "inventory has iron_pickaxe >= 1"},
            {"description": "Equip iron pickaxe", "tool_hint": "equip_item", "tool_args_hint": {"item_name": "iron_pickaxe"}, "check_condition": "equipped"},
        ]
    },
}


# ============================================
# GOAL PLANNER
# ============================================

class GoalPlanner:
    """
    Manages the bot's current goal, tracks step progress,
    and generates context for the LLM prompt.
    """

    def __init__(self):
        self.active_goal: Optional[ActiveGoal] = None
        self.goal_history: list[dict] = []   # completed/failed goals log
        self.max_history = 20

    # ── Goal Creation ──

    def set_goal_from_library(self, goal_name: str, priority: GoalPriority = GoalPriority.AUTONOMOUS, source: str = "autonomous") -> str:
        """Set a goal from the predefined library."""
        if goal_name not in GOAL_LIBRARY:
            available = ", ".join(GOAL_LIBRARY.keys())
            return f"Unknown goal '{goal_name}'. Available: {available}"

        # Don't override higher priority
        if self.active_goal and not self.active_goal.is_expired:
            if self.active_goal.priority.value > priority.value:
                return f"Cannot override {self.active_goal.priority.name} goal with {priority.name}"

        template = GOAL_LIBRARY[goal_name]
        steps = [
            GoalStep(
                id=i + 1,
                description=s["description"],
                tool_hint=s["tool_hint"],
                tool_args_hint=s.get("tool_args_hint", {}),
                check_condition=s.get("check_condition", ""),
            )
            for i, s in enumerate(template["steps"])
        ]

        self.active_goal = ActiveGoal(
            name=goal_name,
            description=template["description"],
            priority=priority,
            steps=steps,
            ttl=template.get("ttl", 300),
            source=source,
        )
        self.active_goal.steps[0].status = StepStatus.IN_PROGRESS

        return f"Goal set: {template['description']} ({len(steps)} steps)"

    def set_custom_goal(self, name: str, description: str, steps_data: list[dict],
                        priority: GoalPriority = GoalPriority.PLAYER, source: str = "player",
                        ttl: float = 300) -> str:
        """Set a custom goal with LLM-generated steps."""
        if self.active_goal and not self.active_goal.is_expired:
            if self.active_goal.priority.value > priority.value:
                return f"Cannot override {self.active_goal.priority.name} goal"

        steps = [
            GoalStep(
                id=i + 1,
                description=s.get("description", f"Step {i+1}"),
                tool_hint=s.get("tool_hint", ""),
                tool_args_hint=s.get("tool_args_hint", {}),
                check_condition=s.get("check_condition", ""),
            )
            for i, s in enumerate(steps_data)
        ]

        self.active_goal = ActiveGoal(
            name=name,
            description=description,
            priority=priority,
            steps=steps,
            ttl=ttl,
            source=source,
        )
        if steps:
            self.active_goal.steps[0].status = StepStatus.IN_PROGRESS

        return f"Custom goal set: {description} ({len(steps)} steps)"

    # ── Step Management ──

    def complete_current_step(self, result: str = "") -> str:
        """Mark the current step as completed and advance."""
        if not self.active_goal or not self.active_goal.current_step:
            return "No active step to complete"

        step = self.active_goal.current_step
        step.status = StepStatus.COMPLETED
        step.result = result

        self.active_goal.advance()

        if self.active_goal.is_complete:
            goal_name = self.active_goal.name
            self._archive_goal("completed")
            return f"🎉 Goal '{goal_name}' completed!"

        next_step = self.active_goal.current_step
        if next_step:
            next_step.status = StepStatus.IN_PROGRESS
            return f"Step {step.id} done. Next: Step {next_step.id} — {next_step.description}"

        return f"Step {step.id} done."

    def fail_current_step(self, error: str = "") -> str:
        """Mark current step as failed. Retry if attempts remain."""
        if not self.active_goal or not self.active_goal.current_step:
            return "No active step to fail"

        step = self.active_goal.current_step
        step.attempts += 1
        step.result = error

        if step.attempts >= step.max_attempts:
            step.status = StepStatus.FAILED
            self._archive_goal("failed")
            return f"Step {step.id} failed after {step.max_attempts} attempts: {error}. Goal abandoned."

        return f"Step {step.id} failed (attempt {step.attempts}/{step.max_attempts}): {error}. Retrying..."

    def skip_current_step(self, reason: str = "") -> str:
        """Skip the current step (e.g., already have the items)."""
        if not self.active_goal or not self.active_goal.current_step:
            return "No active step to skip"

        step = self.active_goal.current_step
        step.status = StepStatus.SKIPPED
        step.result = reason

        self.active_goal.advance()

        next_step = self.active_goal.current_step
        if next_step:
            next_step.status = StepStatus.IN_PROGRESS
            return f"Skipped step {step.id}. Next: {next_step.description}"

        if self.active_goal.is_complete:
            goal_name = self.active_goal.name
            self._archive_goal("completed")
            return f"🎉 Goal '{goal_name}' completed (some steps skipped)!"

        return f"Skipped step {step.id}."

    def cancel_goal(self, reason: str = "") -> str:
        """Cancel the current goal."""
        if not self.active_goal:
            return "No active goal to cancel"

        name = self.active_goal.name
        self._archive_goal("cancelled")
        return f"Goal '{name}' cancelled. {reason}"

    # ── Query ──

    def get_prompt_context(self) -> str:
        """Get goal context to inject into the LLM system prompt."""
        if not self.active_goal:
            return "🎯 NO ACTIVE GOAL — Decide what to do autonomously."

        if self.active_goal.is_expired:
            name = self.active_goal.name
            self._archive_goal("expired")
            return f"🎯 Previous goal '{name}' expired. Decide a new goal."

        return self.active_goal.to_prompt_context()

    def get_available_goals(self) -> str:
        """List available predefined goals."""
        lines = ["Available goals:"]
        for name, data in GOAL_LIBRARY.items():
            step_count = len(data["steps"])
            lines.append(f"  • {name}: {data['description']} ({step_count} steps)")
        return "\n".join(lines)

    def get_status(self) -> dict:
        """Get current planner status as a dict."""
        if not self.active_goal:
            return {"has_goal": False}

        goal = self.active_goal
        return {
            "has_goal": True,
            "goal_name": goal.name,
            "description": goal.description,
            "priority": goal.priority.name,
            "source": goal.source,
            "progress": goal.progress_str,
            "current_step": goal.current_step.description if goal.current_step else "All done",
            "is_expired": goal.is_expired,
            "elapsed": round(time.time() - goal.created_at, 1),
            "ttl": goal.ttl,
        }

    # ── Internal ──

    def _archive_goal(self, outcome: str):
        """Archive the current goal and clear it."""
        if self.active_goal:
            self.goal_history.append({
                "name": self.active_goal.name,
                "outcome": outcome,
                "steps_completed": sum(1 for s in self.active_goal.steps if s.status == StepStatus.COMPLETED),
                "total_steps": len(self.active_goal.steps),
                "elapsed": round(time.time() - self.active_goal.created_at, 1),
                "time": time.strftime("%H:%M:%S"),
            })
            if len(self.goal_history) > self.max_history:
                self.goal_history.pop(0)
            self.active_goal = None