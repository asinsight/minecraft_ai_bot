"""
Grand Goal System v6 — Links tasks to action chains.

Each task maps to a chain_name in chain_library.py.
System auto-checks inventory each tick to complete tasks.
No LLM needed for task tracking — it's all data-driven.
"""

import time
import json
import os
import requests
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


BOT_API = os.getenv("BOT_API_URL", "http://localhost:3001")


class TaskStatus(Enum):
    BLOCKED = "blocked"
    AVAILABLE = "available"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


@dataclass
class Task:
    id: str
    description: str
    chain_name: str                                   # maps to chain_library.py
    requires: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.AVAILABLE
    optional: bool = False
    phase: str = ""
    completion_items: dict = field(default_factory=dict)    # {"iron_pickaxe": 1}
    completion_blocks_placed: list[str] = field(default_factory=list)


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

    @property
    def is_complete(self) -> bool:
        required = [t for t in self.tasks if not t.optional]
        return all(t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED) for t in required)

    @property
    def overall_progress(self) -> str:
        total = len(self.tasks)
        done = sum(1 for t in self.tasks if t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED))
        pct = int(done / total * 100) if total > 0 else 0
        return f"{done}/{total} ({pct}%)"

    def refresh_availability(self):
        completed_ids = {t.id for t in self.tasks if t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)}
        for task in self.tasks:
            if task.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED, TaskStatus.IN_PROGRESS):
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
# INVENTORY HELPERS
# ============================================

def get_inventory_counts() -> dict[str, int]:
    try:
        r = requests.get(f"{BOT_API}/inventory", timeout=5)
        items = r.json().get("items", [])
        counts = {}
        for item in items:
            name = item["name"]
            counts[name] = counts.get(name, 0) + item["count"]
        return counts
    except:
        return {}


def check_block_nearby(block_name: str) -> bool:
    try:
        r = requests.get(f"{BOT_API}/find_block", params={"type": block_name, "range": 32}, timeout=5)
        data = r.json()
        return data.get("success", False) or "Found" in data.get("message", "")
    except:
        return False


def check_task_completion(task: Task, inventory: dict[str, int]) -> bool:
    if task.completion_items:
        for item_name, required_count in task.completion_items.items():
            if inventory.get(item_name, 0) < required_count:
                return False
    if task.completion_blocks_placed:
        for block_name in task.completion_blocks_placed:
            if not check_block_nearby(block_name):
                return False
    if task.completion_items or task.completion_blocks_placed:
        return True
    return False


# ============================================
# GOAL DEFINITIONS
# ============================================

def create_ender_dragon_goal() -> GrandGoal:
    phases = [
        Phase("survival", "Phase 1: Basic Survival", "Tools, food, shelter"),
        Phase("iron", "Phase 2: Iron Age", "Iron gear"),
        Phase("diamond", "Phase 3: Diamond Age", "Diamond gear"),
        Phase("nether", "Phase 4: The Nether", "Blaze rods"),
        Phase("ender", "Phase 5: Eyes of Ender", "Enderpearls + eyes"),
        Phase("end", "Phase 6: The End", "Defeat the dragon"),
    ]
    tasks = [
        # Phase 1
        Task("get_wood", "Gather wood and make planks", "get_wood",
             phase="survival", completion_items={"oak_planks": 12}),
        Task("make_crafting_table", "Make and place crafting table", "make_crafting_table",
             requires=["get_wood"], phase="survival",
             completion_blocks_placed=["crafting_table"]),
        Task("make_wooden_pickaxe", "Craft wooden pickaxe", "make_wooden_pickaxe",
             requires=["make_crafting_table"], phase="survival",
             completion_items={"wooden_pickaxe": 1}),
        Task("make_stone_pickaxe", "Craft stone pickaxe", "make_stone_pickaxe",
             requires=["make_wooden_pickaxe"], phase="survival",
             completion_items={"stone_pickaxe": 1}),
        Task("find_food", "Hunt animals for food", "find_food",
             phase="survival"),
        Task("build_shelter", "Build a shelter", "build_shelter",
             phase="survival"),
        # Phase 2
        Task("make_iron_pickaxe", "Craft iron pickaxe", "make_iron_pickaxe",
             requires=["make_stone_pickaxe"], phase="iron",
             completion_items={"iron_pickaxe": 1}),
        Task("make_iron_sword", "Craft iron sword", "make_iron_sword",
             requires=["make_stone_pickaxe"], phase="iron",
             completion_items={"iron_sword": 1}),
        Task("make_iron_armor", "Craft iron chestplate", "make_iron_armor",
             requires=["make_stone_pickaxe"], phase="iron",
             completion_items={"iron_chestplate": 1}, optional=True),
        Task("make_shield", "Craft a shield", "make_shield",
             requires=["make_iron_sword"], phase="iron",
             completion_items={"shield": 1}, optional=True),
        Task("make_bucket", "Craft a bucket", "make_bucket",
             requires=["make_stone_pickaxe"], phase="iron",
             completion_items={"bucket": 1}),
        # Phase 3
        Task("mine_diamonds", "Mine 5+ diamonds", "mine_diamonds",
             requires=["make_iron_pickaxe"], phase="diamond",
             completion_items={"diamond": 5}),
        Task("make_diamond_pickaxe", "Craft diamond pickaxe", "make_diamond_pickaxe",
             requires=["mine_diamonds"], phase="diamond",
             completion_items={"diamond_pickaxe": 1}),
        Task("make_diamond_sword", "Craft diamond sword", "make_diamond_sword",
             requires=["mine_diamonds"], phase="diamond",
             completion_items={"diamond_sword": 1}),
        # Phase 4-6 (less automatable — LLM will handle more)
        Task("get_obsidian", "Mine 10 obsidian", "mine_diamonds",  # reuse dig chain
             requires=["make_diamond_pickaxe", "make_bucket"], phase="nether",
             completion_items={"obsidian": 10}),
        Task("build_portal", "Build nether portal", "",
             requires=["get_obsidian"], phase="nether"),
        Task("find_fortress", "Find nether fortress", "",
             requires=["build_portal"], phase="nether"),
        Task("kill_blazes", "Kill blazes for 7 rods", "",
             requires=["find_fortress", "make_diamond_sword"], phase="nether",
             completion_items={"blaze_rod": 7}),
        Task("craft_blaze_powder", "Craft blaze powder", "",
             requires=["kill_blazes"], phase="ender",
             completion_items={"blaze_powder": 7}),
        Task("kill_endermen", "Kill endermen for 12 pearls", "",
             requires=["make_diamond_sword"], phase="ender",
             completion_items={"ender_pearl": 12}),
        Task("craft_eyes", "Craft 12 eyes of ender", "",
             requires=["craft_blaze_powder", "kill_endermen"], phase="ender",
             completion_items={"ender_eye": 12}),
        Task("find_stronghold", "Find stronghold", "",
             requires=["craft_eyes"], phase="end"),
        Task("activate_portal", "Activate end portal", "",
             requires=["find_stronghold"], phase="end"),
        Task("defeat_dragon", "Defeat the Ender Dragon!", "",
             requires=["activate_portal"], phase="end"),
    ]
    return GrandGoal("defeat_ender_dragon", "Defeat the Ender Dragon!", tasks, phases)


def create_full_iron_goal() -> GrandGoal:
    phases = [
        Phase("basic", "Phase 1: Basics", "Wood and stone"),
        Phase("iron", "Phase 2: Iron", "Full iron gear"),
    ]
    tasks = [
        Task("get_wood", "Gather wood", "get_wood",
             phase="basic", completion_items={"oak_planks": 12}),
        Task("make_crafting_table", "Make crafting table", "make_crafting_table",
             requires=["get_wood"], phase="basic",
             completion_blocks_placed=["crafting_table"]),
        Task("make_wooden_pickaxe", "Wooden pickaxe", "make_wooden_pickaxe",
             requires=["make_crafting_table"], phase="basic",
             completion_items={"wooden_pickaxe": 1}),
        Task("make_stone_pickaxe", "Stone pickaxe", "make_stone_pickaxe",
             requires=["make_wooden_pickaxe"], phase="basic",
             completion_items={"stone_pickaxe": 1}),
        Task("make_iron_pickaxe", "Iron pickaxe", "make_iron_pickaxe",
             requires=["make_stone_pickaxe"], phase="iron",
             completion_items={"iron_pickaxe": 1}),
        Task("make_iron_sword", "Iron sword", "make_iron_sword",
             requires=["make_stone_pickaxe"], phase="iron",
             completion_items={"iron_sword": 1}),
        Task("make_iron_armor", "Iron chestplate", "make_iron_armor",
             requires=["make_stone_pickaxe"], phase="iron",
             completion_items={"iron_chestplate": 1}),
        Task("make_shield", "Shield", "make_shield",
             requires=["make_iron_sword"], phase="iron",
             completion_items={"shield": 1}),
    ]
    return GrandGoal("full_iron_gear", "Get full iron gear", tasks, phases)


def create_cozy_base_goal() -> GrandGoal:
    phases = [
        Phase("gather", "Phase 1: Gather", "Collect materials"),
        Phase("build", "Phase 2: Build", "Build base"),
    ]
    tasks = [
        Task("get_wood", "Get lots of wood", "get_wood",
             phase="gather", completion_items={"oak_planks": 32}),
        Task("get_stone", "Mine 64+ cobblestone", "mine_stone",
             phase="gather", completion_items={"cobblestone": 64}),
        Task("make_crafting_table", "Place crafting table", "make_crafting_table",
             requires=["get_wood"], phase="build",
             completion_blocks_placed=["crafting_table"]),
        Task("build_main_shelter", "Build shelter", "build_shelter",
             requires=["get_stone"], phase="build",
             completion_blocks_placed=["oak_door"]),
        Task("place_furnace", "Place furnace", "place_furnace",
             requires=["get_stone"], phase="build",
             completion_blocks_placed=["furnace"]),
        Task("place_chests", "Place chests", "place_chest",
             requires=["get_wood"], phase="build",
             completion_blocks_placed=["chest"]),
    ]
    return GrandGoal("cozy_base", "Build a cozy base", tasks, phases)


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

    MAX_SKIP_RETRIES = 2  # retry skipped tasks up to 2 more times

    def __init__(self):
        self.active_goal: Optional[GrandGoal] = None
        self.completed_goals: list[str] = []
        self.current_task_id: Optional[str] = None
        self.task_fail_count: dict[str, int] = {}  # task_id → consecutive fail count
        self.skip_retry_count: dict[str, int] = {}  # task_id → how many times retried after skip
        self._load()

    def _save(self):
        try:
            data = {"completed_goals": self.completed_goals}
            if self.active_goal:
                data["active_goal"] = {
                    "name": self.active_goal.name,
                    "started_at": self.active_goal.started_at,
                    "tasks": [{"id": t.id, "status": t.status.value} for t in self.active_goal.tasks],
                    "current_task_id": self.current_task_id,
                    "task_fail_count": self.task_fail_count,
                    "skip_retry_count": self.skip_retry_count,
                }
            with open(self.SAVE_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"⚠️ Save error: {e}")

    def _load(self):
        try:
            if not os.path.exists(self.SAVE_FILE):
                return
            with open(self.SAVE_FILE, "r") as f:
                data = json.load(f)
            self.completed_goals = data.get("completed_goals", [])
            if "active_goal" in data:
                gd = data["active_goal"]
                name = gd["name"]
                if name in GRAND_GOAL_REGISTRY:
                    self.active_goal = GRAND_GOAL_REGISTRY[name]()
                    self.active_goal.started_at = gd.get("started_at", time.time())
                    status_map = {t["id"]: t["status"] for t in gd.get("tasks", [])}
                    for task in self.active_goal.tasks:
                        if task.id in status_map:
                            task.status = TaskStatus(status_map[task.id])
                    self.active_goal.refresh_availability()
                    self.current_task_id = gd.get("current_task_id")
                    self.task_fail_count = gd.get("task_fail_count", {})
                    self.skip_retry_count = gd.get("skip_retry_count", {})
                    print(f"🏆 Loaded: {self.active_goal.description} ({self.active_goal.overall_progress})")
        except Exception as e:
            print(f"⚠️ Load error: {e}")

    # ── Auto Progress ──

    def auto_check_progress(self) -> list[str]:
        """Check inventory against all tasks. Auto-complete where possible."""
        if not self.active_goal:
            return []
        messages = []
        inventory = get_inventory_counts()
        for task in self.active_goal.tasks:
            if task.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED):
                continue
            if check_task_completion(task, inventory):
                task.status = TaskStatus.COMPLETED
                messages.append(f"✅ AUTO: {task.id} — {task.description}")
                self.task_fail_count.pop(task.id, None)  # reset fail count
                if self.current_task_id == task.id:
                    self.current_task_id = None
        if messages:
            self.active_goal.refresh_availability()
            self._save()
            if self.active_goal.is_complete:
                elapsed = time.time() - self.active_goal.started_at
                self.completed_goals.append(self.active_goal.name)
                desc = self.active_goal.description
                self.active_goal = None
                self.current_task_id = None
                self._save()
                messages.append(f"🏆🎉 GRAND GOAL ACHIEVED: {desc}! ({elapsed/60:.1f}min)")
        return messages

    # ── Task Selection ──

    def get_current_task(self) -> Optional[Task]:
        if not self.active_goal or not self.current_task_id:
            return None
        for t in self.active_goal.tasks:
            if t.id == self.current_task_id:
                if t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED):
                    self.current_task_id = None
                    return None
                return t
        return None

    def pick_next_task(self) -> Optional[Task]:
        """Pick next available task. If none, retry skipped tasks."""
        if not self.active_goal:
            return None

        available = self.active_goal.get_available_tasks()

        # Normal case: pick an available task with low fail count
        if available:
            for task in available:
                fail_count = self.task_fail_count.get(task.id, 0)
                if fail_count < 3:
                    task.status = TaskStatus.IN_PROGRESS
                    self.current_task_id = task.id
                    self._save()
                    return task
            # All available tasks have failed 3+ times → return first anyway
            task = available[0]
            task.status = TaskStatus.IN_PROGRESS
            self.current_task_id = task.id
            self._save()
            return task

        # No available tasks → retry skipped tasks that haven't exceeded retry limit
        skipped = [t for t in self.active_goal.tasks if t.status == TaskStatus.SKIPPED]
        retryable = [t for t in skipped
                     if self.skip_retry_count.get(t.id, 0) < self.MAX_SKIP_RETRIES]
        if retryable:
            task = retryable[0]
            retry_num = self.skip_retry_count.get(task.id, 0) + 1
            self.skip_retry_count[task.id] = retry_num
            # Reset fail count so it gets 5 more chain attempts
            self.task_fail_count[task.id] = 0
            task.status = TaskStatus.IN_PROGRESS
            self.current_task_id = task.id
            self._save()
            print(f"   🔄 Retrying skipped task '{task.id}' (retry {retry_num}/{self.MAX_SKIP_RETRIES})")
            return task

        return None

    def record_task_failure(self, task_id: str):
        """Record a task failure for smart selection."""
        self.task_fail_count[task_id] = self.task_fail_count.get(task_id, 0) + 1
        self._save()

    def complete_task(self, task_id: str) -> str:
        if not self.active_goal:
            return "No grand goal."
        for task in self.active_goal.tasks:
            if task.id == task_id:
                task.status = TaskStatus.COMPLETED
                if self.current_task_id == task_id:
                    self.current_task_id = None
                self.task_fail_count.pop(task_id, None)
                self.active_goal.refresh_availability()
                self._save()
                if self.active_goal.is_complete:
                    elapsed = time.time() - self.active_goal.started_at
                    self.completed_goals.append(self.active_goal.name)
                    desc = self.active_goal.description
                    self.active_goal = None
                    self._save()
                    return f"🏆 GRAND GOAL ACHIEVED: {desc}! ({elapsed/60:.1f}min)"
                return f"✅ '{task_id}' done! {self.active_goal.overall_progress}"
        return f"Task '{task_id}' not found."

    def skip_task(self, task_id: str) -> str:
        if not self.active_goal:
            return "No grand goal."
        for task in self.active_goal.tasks:
            if task.id == task_id:
                task.status = TaskStatus.SKIPPED
                if self.current_task_id == task_id:
                    self.current_task_id = None
                self.active_goal.refresh_availability()
                self._save()
                return f"⏭️ Skipped '{task_id}'."
        return f"Not found."

    # ── Goal Management ──

    def set_grand_goal(self, goal_name: str) -> str:
        if goal_name not in GRAND_GOAL_REGISTRY:
            return f"Unknown. Available: {', '.join(GRAND_GOAL_REGISTRY.keys())}"
        self.active_goal = GRAND_GOAL_REGISTRY[goal_name]()
        self.current_task_id = None
        self.task_fail_count = {}
        self.skip_retry_count = {}
        self.auto_check_progress()
        self.active_goal.refresh_availability()
        self._save()
        available = self.active_goal.get_available_tasks()
        return (
            f"🏆 GRAND GOAL: {self.active_goal.description}\n"
            f"   {len(self.active_goal.tasks)} tasks. Next: {', '.join(t.id for t in available[:5])}"
        )

    # ── Status ──

    def get_prompt_context(self) -> str:
        if not self.active_goal:
            return "🏆 NO GRAND GOAL. Available: defeat_ender_dragon, full_iron_gear, cozy_base"
        goal = self.active_goal
        goal.refresh_availability()
        lines = [f"🏆 GRAND GOAL: {goal.description} ({goal.overall_progress})"]
        for phase in goal.phases:
            phase_tasks = goal.get_tasks_by_phase(phase.id)
            done = sum(1 for t in phase_tasks if t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED))
            lines.append(f"  📋 {phase.name} [{done}/{len(phase_tasks)}]")
            for task in phase_tasks:
                icon = {"completed": "✅", "skipped": "⏭️", "available": "⬜",
                        "blocked": "🔒", "in_progress": "▶️"}[task.status.value]
                current = " ← NOW" if task.id == self.current_task_id else ""
                lines.append(f"    {icon} {task.description}{current}")
        return "\n".join(lines)

    def get_status(self) -> dict:
        if not self.active_goal:
            return {"has_grand_goal": False}
        return {
            "has_grand_goal": True,
            "goal_name": self.active_goal.name,
            "progress": self.active_goal.overall_progress,
            "current_task_id": self.current_task_id,
            "available_tasks": [t.id for t in self.active_goal.get_available_tasks()],
        }