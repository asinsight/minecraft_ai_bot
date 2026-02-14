"""
Grand Goal System — High-level game objectives with flexible task ordering.

KEY DESIGN: Phases are just labels for organization. ALL tasks are visible
to the LLM at all times. Tasks have 'requires' — a list of other task IDs
that must be completed first. The LLM freely chooses which available task
to work on based on the current situation (health, gear, time, threats).

Example:
  "mine_diamonds" requires ["make_iron_pickaxe"]
  → Can't do until iron pickaxe is done
  → But "find_food" or "build_shelter" can be done at any time (no requires)
"""

import time
import json
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TaskStatus(Enum):
    BLOCKED = "blocked"
    AVAILABLE = "available"
    COMPLETED = "completed"
    SKIPPED = "skipped"


@dataclass
class Task:
    id: str
    description: str
    goal_name: str
    requires: list[str] = field(default_factory=list)
    check_hint: str = ""
    status: TaskStatus = TaskStatus.AVAILABLE
    optional: bool = False
    phase: str = ""


@dataclass
class Phase:
    id: str
    name: str
    description: str


@dataclass
class GrandGoal:
    name: str
    description: str
    tasks: list[Task] = field(default_factory=list)
    phases: list[Phase] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    completed_at: float = 0

    @property
    def is_complete(self) -> bool:
        required = [t for t in self.tasks if not t.optional]
        return all(t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED) for t in required)

    @property
    def overall_progress(self) -> str:
        total = len(self.tasks)
        done = sum(1 for t in self.tasks if t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED))
        pct = int(done / total * 100) if total > 0 else 0
        return f"Tasks {done}/{total} ({pct}%)"

    def refresh_availability(self):
        completed_ids = {t.id for t in self.tasks if t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)}
        for task in self.tasks:
            if task.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED):
                continue
            if all(req in completed_ids for req in task.requires):
                task.status = TaskStatus.AVAILABLE
            else:
                task.status = TaskStatus.BLOCKED

    def get_available_tasks(self) -> list[Task]:
        self.refresh_availability()
        return [t for t in self.tasks if t.status == TaskStatus.AVAILABLE]

    def get_tasks_by_phase(self, phase_id: str) -> list[Task]:
        return [t for t in self.tasks if t.phase == phase_id]


# ============================================
# GRAND GOAL DEFINITIONS
# ============================================

def create_ender_dragon_goal() -> GrandGoal:
    phases = [
        Phase("survival", "Phase 1: Basic Survival", "Tools, food, shelter"),
        Phase("iron", "Phase 2: Iron Age", "Iron gear for combat and mining"),
        Phase("diamond", "Phase 3: Diamond Age", "Diamond gear for deep mining"),
        Phase("nether", "Phase 4: The Nether", "Blaze rods from Nether fortress"),
        Phase("ender", "Phase 5: Eyes of Ender", "Enderpearls and eyes of ender"),
        Phase("end", "Phase 6: The End", "Find stronghold, defeat the dragon"),
    ]
    tasks = [
        # Phase 1 — no/minimal requirements
        Task("get_wood", "Gather wood and make planks", "get_wood",
             phase="survival"),
        Task("make_crafting_table", "Make and place a crafting table", "make_crafting_table",
             requires=["get_wood"], phase="survival"),
        Task("make_wooden_pickaxe", "Craft a wooden pickaxe", "make_wooden_pickaxe",
             requires=["make_crafting_table"], phase="survival"),
        Task("make_stone_pickaxe", "Craft a stone pickaxe", "make_stone_pickaxe",
             requires=["make_wooden_pickaxe"], phase="survival"),
        Task("find_food", "Hunt animals for food", "find_food",
             phase="survival"),
        Task("build_shelter", "Build an enclosed shelter", "build_shelter",
             phase="survival"),

        # Phase 2
        Task("make_iron_pickaxe", "Craft an iron pickaxe", "make_iron_pickaxe",
             requires=["make_stone_pickaxe"], phase="iron"),
        Task("make_iron_sword", "Craft an iron sword", "",
             requires=["make_stone_pickaxe"], check_hint="inventory has iron_sword", phase="iron"),
        Task("make_iron_armor", "Craft iron armor (chestplate)", "",
             requires=["make_stone_pickaxe"], check_hint="inventory has iron_chestplate",
             optional=True, phase="iron"),
        Task("make_shield", "Craft a shield", "",
             requires=["make_iron_sword"], check_hint="inventory has shield",
             optional=True, phase="iron"),
        Task("make_bucket", "Craft a bucket (for nether portal)", "",
             requires=["make_stone_pickaxe"], check_hint="inventory has bucket", phase="iron"),

        # Phase 3
        Task("mine_diamonds", "Mine 5+ diamonds (go deep, y<16)", "",
             requires=["make_iron_pickaxe"], check_hint="inventory has diamond >= 5", phase="diamond"),
        Task("make_diamond_pickaxe", "Craft a diamond pickaxe", "",
             requires=["mine_diamonds"], check_hint="inventory has diamond_pickaxe", phase="diamond"),
        Task("make_diamond_sword", "Craft a diamond sword", "",
             requires=["mine_diamonds"], check_hint="inventory has diamond_sword", phase="diamond"),
        Task("enchanting_setup", "Set up enchanting table", "",
             requires=["mine_diamonds"], check_hint="enchanting_table placed",
             optional=True, phase="diamond"),

        # Phase 4
        Task("get_obsidian", "Mine 10 obsidian (diamond pick + water on lava)", "",
             requires=["make_diamond_pickaxe", "make_bucket"],
             check_hint="inventory has obsidian >= 10", phase="nether"),
        Task("build_portal", "Build and light a Nether portal", "",
             requires=["get_obsidian"], check_hint="nether portal activated", phase="nether"),
        Task("find_fortress", "Find a Nether fortress", "",
             requires=["build_portal"], check_hint="at nether fortress", phase="nether"),
        Task("kill_blazes", "Kill blazes for 7+ blaze rods", "",
             requires=["find_fortress", "make_diamond_sword"],
             check_hint="inventory has blaze_rod >= 7", phase="nether"),

        # Phase 5
        Task("craft_blaze_powder", "Craft blaze powder from rods", "",
             requires=["kill_blazes"], check_hint="inventory has blaze_powder >= 7", phase="ender"),
        Task("kill_endermen", "Kill endermen for 12+ ender pearls", "",
             requires=["make_diamond_sword"], check_hint="inventory has ender_pearl >= 12", phase="ender"),
        Task("craft_eyes", "Craft 12 eyes of ender", "",
             requires=["craft_blaze_powder", "kill_endermen"],
             check_hint="inventory has ender_eye >= 12", phase="ender"),

        # Phase 6
        Task("find_stronghold", "Use eyes of ender to find stronghold", "",
             requires=["craft_eyes"], check_hint="at stronghold", phase="end"),
        Task("activate_portal", "Place eyes in end portal frame", "",
             requires=["find_stronghold"], check_hint="end portal activated", phase="end"),
        Task("prepare_for_fight", "Stock up: food, blocks, gear", "",
             requires=["activate_portal"], check_hint="ready for dragon fight", phase="end"),
        Task("defeat_dragon", "Enter The End and defeat the Ender Dragon!", "",
             requires=["prepare_for_fight"], check_hint="ender dragon defeated", phase="end"),
    ]
    return GrandGoal(name="defeat_ender_dragon",
                     description="Defeat the Ender Dragon and beat Minecraft!",
                     tasks=tasks, phases=phases)


def create_full_iron_goal() -> GrandGoal:
    phases = [
        Phase("basic", "Phase 1: Basics", "Wood and stone tools"),
        Phase("iron", "Phase 2: Iron", "Full iron gear"),
    ]
    tasks = [
        Task("get_wood", "Gather wood", "get_wood", phase="basic"),
        Task("make_crafting_table", "Make crafting table", "make_crafting_table",
             requires=["get_wood"], phase="basic"),
        Task("make_wooden_pickaxe", "Wooden pickaxe", "make_wooden_pickaxe",
             requires=["make_crafting_table"], phase="basic"),
        Task("make_stone_pickaxe", "Stone pickaxe", "make_stone_pickaxe",
             requires=["make_wooden_pickaxe"], phase="basic"),
        Task("make_iron_pickaxe", "Iron pickaxe", "make_iron_pickaxe",
             requires=["make_stone_pickaxe"], phase="iron"),
        Task("make_iron_sword", "Iron sword", "",
             requires=["make_stone_pickaxe"], check_hint="inventory has iron_sword", phase="iron"),
        Task("make_iron_armor", "Iron chestplate", "",
             requires=["make_stone_pickaxe"], check_hint="inventory has iron_chestplate", phase="iron"),
        Task("make_shield", "Shield", "",
             requires=["make_iron_sword"], check_hint="inventory has shield", phase="iron"),
    ]
    return GrandGoal(name="full_iron_gear", description="Get full iron gear",
                     tasks=tasks, phases=phases)


def create_cozy_base_goal() -> GrandGoal:
    phases = [
        Phase("gather", "Phase 1: Gather", "Collect materials"),
        Phase("build", "Phase 2: Build", "Build and furnish base"),
    ]
    tasks = [
        Task("get_wood", "Get lots of wood", "get_wood", phase="gather"),
        Task("get_stone", "Mine 64+ cobblestone", "",
             check_hint="inventory has cobblestone >= 64", phase="gather"),
        Task("get_iron", "Mine 10+ iron", "",
             requires=["get_wood"], check_hint="inventory has raw_iron >= 10", phase="gather"),
        Task("build_shelter", "Build main shelter", "build_shelter",
             requires=["get_stone"], phase="build"),
        Task("place_crafting", "Place crafting table", "",
             requires=["get_wood"], check_hint="crafting_table placed", phase="build"),
        Task("place_furnace", "Place furnace", "",
             requires=["get_stone"], check_hint="furnace placed", phase="build"),
        Task("place_chests", "Place storage chests", "",
             requires=["get_wood"], check_hint="chest placed", phase="build"),
        Task("place_bed", "Craft and place bed", "",
             check_hint="bed placed", optional=True, phase="build"),
    ]
    return GrandGoal(name="cozy_base", description="Build a cozy base",
                     tasks=tasks, phases=phases)


GRAND_GOAL_REGISTRY = {
    "defeat_ender_dragon": create_ender_dragon_goal,
    "full_iron_gear": create_full_iron_goal,
    "cozy_base": create_cozy_base_goal,
}


# ============================================
# GRAND GOAL MANAGER
# ============================================

class GrandGoalManager:
    SAVE_FILE = "grand_goal_state.json"

    def __init__(self):
        self.active_goal: Optional[GrandGoal] = None
        self.completed_goals: list[str] = []
        self._load()

    def _save(self):
        try:
            data = {"completed_goals": self.completed_goals}
            if self.active_goal:
                tasks_data = [{"id": t.id, "status": t.status.value} for t in self.active_goal.tasks]
                data["active_goal"] = {
                    "name": self.active_goal.name,
                    "started_at": self.active_goal.started_at,
                    "tasks": tasks_data,
                }
            with open(self.SAVE_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"⚠️ Failed to save grand goal: {e}")

    def _load(self):
        try:
            if not os.path.exists(self.SAVE_FILE):
                return
            with open(self.SAVE_FILE, "r") as f:
                data = json.load(f)
            self.completed_goals = data.get("completed_goals", [])
            if "active_goal" in data:
                goal_data = data["active_goal"]
                goal_name = goal_data["name"]
                if goal_name in GRAND_GOAL_REGISTRY:
                    self.active_goal = GRAND_GOAL_REGISTRY[goal_name]()
                    self.active_goal.started_at = goal_data.get("started_at", time.time())
                    status_map = {t["id"]: t["status"] for t in goal_data.get("tasks", [])}
                    for task in self.active_goal.tasks:
                        if task.id in status_map:
                            task.status = TaskStatus(status_map[task.id])
                    self.active_goal.refresh_availability()
                    print(f"🏆 Loaded grand goal: {self.active_goal.description} ({self.active_goal.overall_progress})")
        except Exception as e:
            print(f"⚠️ Failed to load grand goal: {e}")

    def set_grand_goal(self, goal_name: str) -> str:
        if goal_name not in GRAND_GOAL_REGISTRY:
            available = ", ".join(GRAND_GOAL_REGISTRY.keys())
            return f"Unknown grand goal '{goal_name}'. Available: {available}"
        self.active_goal = GRAND_GOAL_REGISTRY[goal_name]()
        self.active_goal.refresh_availability()
        self._save()
        available = self.active_goal.get_available_tasks()
        task_list = ", ".join(t.id for t in available[:6])
        return (
            f"🏆 GRAND GOAL SET: {self.active_goal.description}\n"
            f"   {len(self.active_goal.tasks)} total tasks.\n"
            f"   Available now: {task_list}\n"
            f"   Pick whichever task makes sense for your current situation!"
        )

    def complete_task(self, task_id: str) -> str:
        if not self.active_goal:
            return "No grand goal set."
        for task in self.active_goal.tasks:
            if task.id == task_id:
                task.status = TaskStatus.COMPLETED
                self.active_goal.refresh_availability()
                if self.active_goal.is_complete:
                    self.active_goal.completed_at = time.time()
                    elapsed = self.active_goal.completed_at - self.active_goal.started_at
                    self.completed_goals.append(self.active_goal.name)
                    name = self.active_goal.description
                    self.active_goal = None
                    self._save()
                    return f"🏆🎉 GRAND GOAL ACHIEVED: {name}! Time: {elapsed/60:.1f} minutes!"
                newly_available = self.active_goal.get_available_tasks()
                self._save()
                msg = f"✅ Task '{task_id}' completed! {self.active_goal.overall_progress}"
                if newly_available:
                    msg += f"\n   🔓 Available: {', '.join(t.id for t in newly_available[:5])}"
                return msg
        return f"Task '{task_id}' not found."

    def skip_task(self, task_id: str) -> str:
        if not self.active_goal:
            return "No grand goal set."
        for task in self.active_goal.tasks:
            if task.id == task_id:
                task.status = TaskStatus.SKIPPED
                self.active_goal.refresh_availability()
                self._save()
                return f"⏭️ Skipped '{task_id}'. {self.active_goal.overall_progress}"
        return f"Task '{task_id}' not found."

    def get_prompt_context(self) -> str:
        if not self.active_goal:
            return (
                "🏆 NO GRAND GOAL SET.\n"
                "   Available: defeat_ender_dragon, full_iron_gear, cozy_base\n"
                "   Use set_grand_goal to pick one!"
            )
        goal = self.active_goal
        goal.refresh_availability()
        lines = [
            f"🏆 GRAND GOAL: {goal.description}",
            f"   Progress: {goal.overall_progress}",
            "",
        ]
        for phase in goal.phases:
            phase_tasks = goal.get_tasks_by_phase(phase.id)
            done = sum(1 for t in phase_tasks if t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED))
            lines.append(f"   📋 {phase.name} [{done}/{len(phase_tasks)}]")
            for task in phase_tasks:
                icon = {"completed": "✅", "skipped": "⏭️", "available": "⬜", "blocked": "🔒"}[task.status.value]
                opt = " (optional)" if task.optional else ""
                req_str = ""
                if task.status == TaskStatus.BLOCKED and task.requires:
                    completed_ids = {t.id for t in goal.tasks if t.status == TaskStatus.COMPLETED}
                    missing = [r for r in task.requires if r not in completed_ids]
                    if missing:
                        req_str = f" [needs: {', '.join(missing)}]"
                lines.append(f"      {icon} {task.description}{opt}{req_str}")

        available = goal.get_available_tasks()
        if available:
            lines.append("")
            lines.append(f"👉 YOU CAN WORK ON ({len(available)} available):")
            for task in available:
                hint = f" → set_goal(\"{task.goal_name}\")" if task.goal_name else ""
                lines.append(f"   - {task.id}: {task.description}{hint}")
            lines.append("")
            lines.append("   Choose based on: health, hunger, time, nearby resources, threats.")
            lines.append("   You are FREE to do tasks in any order. Prioritize survival if needed.")
        return "\n".join(lines)

    def get_status(self) -> dict:
        if not self.active_goal:
            return {"has_grand_goal": False}
        return {
            "has_grand_goal": True,
            "name": self.active_goal.name,
            "description": self.active_goal.description,
            "progress": self.active_goal.overall_progress,
            "available_tasks": [t.id for t in self.active_goal.get_available_tasks()],
        }