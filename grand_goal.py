"""
Grand Goal System v7 — File-based goal library + LLM dynamic goal creation.

Goals are stored in goal_library.json (not hardcoded).
LLM can create new goals dynamically, which are saved for future reuse.
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
# GOAL LIBRARY — File-based goal storage
# ============================================

class GoalLibrary:
    """Manages goal_library.json — stores, retrieves, and searches goal templates."""

    FILE = "goal_library.json"

    VALID_CHAINS = {
        "get_wood", "mine_stone", "make_crafting_table", "make_wooden_pickaxe",
        "make_stone_pickaxe", "make_iron_pickaxe", "make_iron_sword",
        "make_iron_armor", "make_shield", "make_bucket", "mine_diamonds",
        "make_diamond_pickaxe", "make_diamond_sword", "find_food",
        "build_shelter", "place_furnace", "place_chest",
    }

    def __init__(self):
        self.goals: dict[str, dict] = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.FILE):
                with open(self.FILE, "r") as f:
                    self.goals = json.load(f)
                print(f"📚 Loaded {len(self.goals)} goals from library")
            else:
                self._seed_builtin_goals()
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️ goal_library.json corrupted ({e}), re-seeding...")
            self._seed_builtin_goals()

    def _save(self):
        try:
            with open(self.FILE, "w") as f:
                json.dump(self.goals, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ Failed to save goal library: {e}")

    def _seed_builtin_goals(self):
        """Create goal_library.json with the 3 built-in goals."""
        self.goals = {
            "defeat_ender_dragon": {
                "name": "defeat_ender_dragon",
                "description": "Defeat the Ender Dragon!",
                "source": "builtin",
                "phases": [
                    {"id": "survival", "name": "Phase 1: Basic Survival", "description": "Tools, food, shelter"},
                    {"id": "iron", "name": "Phase 2: Iron Age", "description": "Iron gear"},
                    {"id": "diamond", "name": "Phase 3: Diamond Age", "description": "Diamond gear"},
                    {"id": "nether", "name": "Phase 4: The Nether", "description": "Blaze rods"},
                    {"id": "ender", "name": "Phase 5: Eyes of Ender", "description": "Enderpearls + eyes"},
                    {"id": "end", "name": "Phase 6: The End", "description": "Defeat the dragon"},
                ],
                "tasks": [
                    {"id": "get_wood", "description": "Gather wood and make planks", "chain_name": "get_wood",
                     "requires": [], "phase": "survival", "optional": False,
                     "completion_items": {"oak_planks": 12}, "completion_blocks_placed": []},
                    {"id": "make_crafting_table", "description": "Make and place crafting table", "chain_name": "make_crafting_table",
                     "requires": ["get_wood"], "phase": "survival", "optional": False,
                     "completion_items": {}, "completion_blocks_placed": ["crafting_table"]},
                    {"id": "make_wooden_pickaxe", "description": "Craft wooden pickaxe", "chain_name": "make_wooden_pickaxe",
                     "requires": ["make_crafting_table"], "phase": "survival", "optional": False,
                     "completion_items": {"wooden_pickaxe": 1}, "completion_blocks_placed": []},
                    {"id": "make_stone_pickaxe", "description": "Craft stone pickaxe", "chain_name": "make_stone_pickaxe",
                     "requires": ["make_wooden_pickaxe"], "phase": "survival", "optional": False,
                     "completion_items": {"stone_pickaxe": 1}, "completion_blocks_placed": []},
                    {"id": "find_food", "description": "Hunt animals for food", "chain_name": "find_food",
                     "requires": [], "phase": "survival", "optional": False,
                     "completion_items": {}, "completion_blocks_placed": []},
                    {"id": "build_shelter", "description": "Build a shelter", "chain_name": "build_shelter",
                     "requires": [], "phase": "survival", "optional": False,
                     "completion_items": {}, "completion_blocks_placed": []},
                    {"id": "make_iron_pickaxe", "description": "Craft iron pickaxe", "chain_name": "make_iron_pickaxe",
                     "requires": ["make_stone_pickaxe"], "phase": "iron", "optional": False,
                     "completion_items": {"iron_pickaxe": 1}, "completion_blocks_placed": []},
                    {"id": "make_iron_sword", "description": "Craft iron sword", "chain_name": "make_iron_sword",
                     "requires": ["make_stone_pickaxe"], "phase": "iron", "optional": False,
                     "completion_items": {"iron_sword": 1}, "completion_blocks_placed": []},
                    {"id": "make_iron_armor", "description": "Craft iron chestplate", "chain_name": "make_iron_armor",
                     "requires": ["make_stone_pickaxe"], "phase": "iron", "optional": True,
                     "completion_items": {"iron_chestplate": 1}, "completion_blocks_placed": []},
                    {"id": "make_shield", "description": "Craft a shield", "chain_name": "make_shield",
                     "requires": ["make_iron_sword"], "phase": "iron", "optional": True,
                     "completion_items": {"shield": 1}, "completion_blocks_placed": []},
                    {"id": "make_bucket", "description": "Craft a bucket", "chain_name": "make_bucket",
                     "requires": ["make_stone_pickaxe"], "phase": "iron", "optional": False,
                     "completion_items": {"bucket": 1}, "completion_blocks_placed": []},
                    {"id": "mine_diamonds", "description": "Mine 5+ diamonds", "chain_name": "mine_diamonds",
                     "requires": ["make_iron_pickaxe"], "phase": "diamond", "optional": False,
                     "completion_items": {"diamond": 5}, "completion_blocks_placed": []},
                    {"id": "make_diamond_pickaxe", "description": "Craft diamond pickaxe", "chain_name": "make_diamond_pickaxe",
                     "requires": ["mine_diamonds"], "phase": "diamond", "optional": False,
                     "completion_items": {"diamond_pickaxe": 1}, "completion_blocks_placed": []},
                    {"id": "make_diamond_sword", "description": "Craft diamond sword", "chain_name": "make_diamond_sword",
                     "requires": ["mine_diamonds"], "phase": "diamond", "optional": False,
                     "completion_items": {"diamond_sword": 1}, "completion_blocks_placed": []},
                    {"id": "get_obsidian", "description": "Mine 10 obsidian", "chain_name": "mine_diamonds",
                     "requires": ["make_diamond_pickaxe", "make_bucket"], "phase": "nether", "optional": False,
                     "completion_items": {"obsidian": 10}, "completion_blocks_placed": []},
                    {"id": "build_portal", "description": "Build nether portal", "chain_name": "",
                     "requires": ["get_obsidian"], "phase": "nether", "optional": False,
                     "completion_items": {}, "completion_blocks_placed": []},
                    {"id": "find_fortress", "description": "Find nether fortress", "chain_name": "",
                     "requires": ["build_portal"], "phase": "nether", "optional": False,
                     "completion_items": {}, "completion_blocks_placed": []},
                    {"id": "kill_blazes", "description": "Kill blazes for 7 rods", "chain_name": "",
                     "requires": ["find_fortress", "make_diamond_sword"], "phase": "nether", "optional": False,
                     "completion_items": {"blaze_rod": 7}, "completion_blocks_placed": []},
                    {"id": "craft_blaze_powder", "description": "Craft blaze powder", "chain_name": "",
                     "requires": ["kill_blazes"], "phase": "ender", "optional": False,
                     "completion_items": {"blaze_powder": 7}, "completion_blocks_placed": []},
                    {"id": "kill_endermen", "description": "Kill endermen for 12 pearls", "chain_name": "",
                     "requires": ["make_diamond_sword"], "phase": "ender", "optional": False,
                     "completion_items": {"ender_pearl": 12}, "completion_blocks_placed": []},
                    {"id": "craft_eyes", "description": "Craft 12 eyes of ender", "chain_name": "",
                     "requires": ["craft_blaze_powder", "kill_endermen"], "phase": "ender", "optional": False,
                     "completion_items": {"ender_eye": 12}, "completion_blocks_placed": []},
                    {"id": "find_stronghold", "description": "Find stronghold", "chain_name": "",
                     "requires": ["craft_eyes"], "phase": "end", "optional": False,
                     "completion_items": {}, "completion_blocks_placed": []},
                    {"id": "activate_portal", "description": "Activate end portal", "chain_name": "",
                     "requires": ["find_stronghold"], "phase": "end", "optional": False,
                     "completion_items": {}, "completion_blocks_placed": []},
                    {"id": "defeat_dragon", "description": "Defeat the Ender Dragon!", "chain_name": "",
                     "requires": ["activate_portal"], "phase": "end", "optional": False,
                     "completion_items": {}, "completion_blocks_placed": []},
                ],
            },
            "full_iron_gear": {
                "name": "full_iron_gear",
                "description": "Get full iron gear",
                "source": "builtin",
                "phases": [
                    {"id": "basic", "name": "Phase 1: Basics", "description": "Wood and stone"},
                    {"id": "iron", "name": "Phase 2: Iron", "description": "Full iron gear"},
                ],
                "tasks": [
                    {"id": "get_wood", "description": "Gather wood", "chain_name": "get_wood",
                     "requires": [], "phase": "basic", "optional": False,
                     "completion_items": {"oak_planks": 12}, "completion_blocks_placed": []},
                    {"id": "make_crafting_table", "description": "Make crafting table", "chain_name": "make_crafting_table",
                     "requires": ["get_wood"], "phase": "basic", "optional": False,
                     "completion_items": {}, "completion_blocks_placed": ["crafting_table"]},
                    {"id": "make_wooden_pickaxe", "description": "Wooden pickaxe", "chain_name": "make_wooden_pickaxe",
                     "requires": ["make_crafting_table"], "phase": "basic", "optional": False,
                     "completion_items": {"wooden_pickaxe": 1}, "completion_blocks_placed": []},
                    {"id": "make_stone_pickaxe", "description": "Stone pickaxe", "chain_name": "make_stone_pickaxe",
                     "requires": ["make_wooden_pickaxe"], "phase": "basic", "optional": False,
                     "completion_items": {"stone_pickaxe": 1}, "completion_blocks_placed": []},
                    {"id": "make_iron_pickaxe", "description": "Iron pickaxe", "chain_name": "make_iron_pickaxe",
                     "requires": ["make_stone_pickaxe"], "phase": "iron", "optional": False,
                     "completion_items": {"iron_pickaxe": 1}, "completion_blocks_placed": []},
                    {"id": "make_iron_sword", "description": "Iron sword", "chain_name": "make_iron_sword",
                     "requires": ["make_stone_pickaxe"], "phase": "iron", "optional": False,
                     "completion_items": {"iron_sword": 1}, "completion_blocks_placed": []},
                    {"id": "make_iron_armor", "description": "Iron chestplate", "chain_name": "make_iron_armor",
                     "requires": ["make_stone_pickaxe"], "phase": "iron", "optional": False,
                     "completion_items": {"iron_chestplate": 1}, "completion_blocks_placed": []},
                    {"id": "make_shield", "description": "Shield", "chain_name": "make_shield",
                     "requires": ["make_iron_sword"], "phase": "iron", "optional": False,
                     "completion_items": {"shield": 1}, "completion_blocks_placed": []},
                ],
            },
            "cozy_base": {
                "name": "cozy_base",
                "description": "Build a cozy base",
                "source": "builtin",
                "phases": [
                    {"id": "gather", "name": "Phase 1: Gather", "description": "Collect materials"},
                    {"id": "build", "name": "Phase 2: Build", "description": "Build base"},
                ],
                "tasks": [
                    {"id": "get_wood", "description": "Get lots of wood", "chain_name": "get_wood",
                     "requires": [], "phase": "gather", "optional": False,
                     "completion_items": {"oak_planks": 32}, "completion_blocks_placed": []},
                    {"id": "get_stone", "description": "Mine 64+ cobblestone", "chain_name": "mine_stone",
                     "requires": [], "phase": "gather", "optional": False,
                     "completion_items": {"cobblestone": 64}, "completion_blocks_placed": []},
                    {"id": "make_crafting_table", "description": "Place crafting table", "chain_name": "make_crafting_table",
                     "requires": ["get_wood"], "phase": "build", "optional": False,
                     "completion_items": {}, "completion_blocks_placed": ["crafting_table"]},
                    {"id": "build_main_shelter", "description": "Build shelter", "chain_name": "build_shelter",
                     "requires": ["get_stone"], "phase": "build", "optional": False,
                     "completion_items": {}, "completion_blocks_placed": ["oak_door"]},
                    {"id": "place_furnace", "description": "Place furnace", "chain_name": "place_furnace",
                     "requires": ["get_stone"], "phase": "build", "optional": False,
                     "completion_items": {}, "completion_blocks_placed": ["furnace"]},
                    {"id": "place_chests", "description": "Place chests", "chain_name": "place_chest",
                     "requires": ["get_wood"], "phase": "build", "optional": False,
                     "completion_items": {}, "completion_blocks_placed": ["chest"]},
                ],
            },
        }
        self._save()
        print(f"📚 Seeded goal library with {len(self.goals)} built-in goals")

    # ── Goal Retrieval ──

    def get_goal(self, name: str) -> Optional[GrandGoal]:
        data = self.goals.get(name)
        if not data:
            return None
        return self._dict_to_grand_goal(data)

    def _dict_to_grand_goal(self, data: dict) -> GrandGoal:
        phases = [Phase(id=p["id"], name=p["name"], description=p["description"])
                  for p in data.get("phases", [])]
        tasks = []
        for td in data.get("tasks", []):
            tasks.append(Task(
                id=td["id"],
                description=td["description"],
                chain_name=td.get("chain_name", ""),
                requires=td.get("requires", []),
                phase=td.get("phase", ""),
                optional=td.get("optional", False),
                completion_items=td.get("completion_items", {}),
                completion_blocks_placed=td.get("completion_blocks_placed", []),
            ))
        return GrandGoal(
            name=data["name"],
            description=data["description"],
            tasks=tasks,
            phases=phases,
        )

    def list_goals(self) -> list[dict]:
        return [
            {
                "name": name,
                "description": data["description"],
                "source": data.get("source", "unknown"),
                "task_count": len(data.get("tasks", [])),
            }
            for name, data in self.goals.items()
        ]

    # ── Goal Creation ──

    def save_goal(self, name: str, description: str, phases: list[dict],
                  tasks: list[dict], source: str = "llm_created") -> str:
        errors = self._validate_goal(name, tasks)
        if errors:
            return f"Validation failed: {'; '.join(errors)}"
        self.goals[name] = {
            "name": name,
            "description": description,
            "source": source,
            "phases": phases,
            "tasks": tasks,
        }
        self._save()
        return f"Goal '{name}' saved ({len(tasks)} tasks)"

    # ── Similarity Search ──

    def find_similar(self, description: str) -> list[str]:
        desc_words = set(description.lower().split())
        if not desc_words:
            return []
        scores = []
        for name, data in self.goals.items():
            goal_words = set(data["description"].lower().split())
            goal_words.update(name.lower().replace("_", " ").split())
            overlap = len(desc_words & goal_words)
            if overlap > 0:
                score = overlap / max(len(desc_words), len(goal_words))
                scores.append((name, score))
        scores.sort(key=lambda x: -x[1])
        return [name for name, score in scores if score > 0.2]

    # ── Validation ──

    def _validate_goal(self, name: str, tasks: list[dict]) -> list[str]:
        errors = []
        if not name or not isinstance(name, str):
            errors.append("Goal name must be a non-empty string")
        if not tasks:
            errors.append("Goal must have at least one task")
            return errors

        all_task_ids = {t.get("id", "") for t in tasks}
        seen_ids = set()

        for i, t in enumerate(tasks):
            tid = t.get("id", "")
            if not tid:
                errors.append(f"Task {i} missing 'id'")
                continue
            if tid in seen_ids:
                errors.append(f"Duplicate task id: '{tid}'")
            seen_ids.add(tid)

            chain = t.get("chain_name", "")
            if chain and chain not in self.VALID_CHAINS:
                errors.append(
                    f"Task '{tid}' has invalid chain_name '{chain}'. "
                    f"Valid: {', '.join(sorted(self.VALID_CHAINS))}"
                )

            for req in t.get("requires", []):
                if req not in all_task_ids:
                    errors.append(f"Task '{tid}' requires unknown task '{req}'")

        if not errors:
            errors.extend(self._check_circular_deps(tasks))
        return errors

    def _check_circular_deps(self, tasks: list[dict]) -> list[str]:
        task_map = {t["id"]: t.get("requires", []) for t in tasks}
        visited = set()
        in_stack = set()

        def dfs(tid):
            if tid in in_stack:
                return [f"Circular dependency involving '{tid}'"]
            if tid in visited:
                return []
            in_stack.add(tid)
            visited.add(tid)
            for dep in task_map.get(tid, []):
                errs = dfs(dep)
                if errs:
                    return errs
            in_stack.discard(tid)
            return []

        for tid in task_map:
            errs = dfs(tid)
            if errs:
                return errs
        return []


# ============================================
# GRAND GOAL MANAGER
# ============================================

class GrandGoalManager:
    SAVE_FILE = "grand_goal_state.json"

    MAX_SKIP_RETRIES = 2  # retry skipped tasks up to 2 more times

    def __init__(self):
        self.goal_library = GoalLibrary()
        self.active_goal: Optional[GrandGoal] = None
        self.completed_goals: list[str] = []
        self.current_task_id: Optional[str] = None
        self.task_fail_count: dict[str, int] = {}  # task_id → consecutive fail count
        self.skip_retry_count: dict[str, int] = {}  # task_id → how many times retried after skip
        self.user_requested: bool = False  # True when user explicitly requested this goal
        self._load()

    def _save(self):
        try:
            data = {
                "completed_goals": self.completed_goals,
                "user_requested": self.user_requested,
            }
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
            self.user_requested = data.get("user_requested", False)
            if "active_goal" in data:
                gd = data["active_goal"]
                name = gd["name"]
                goal = self.goal_library.get_goal(name)
                if goal:
                    self.active_goal = goal
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
                else:
                    print(f"⚠️ Goal '{name}' not found in library, ignoring saved state")
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
                self.user_requested = False  # Reset priority after goal completion
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

        # ── Fix orphaned IN_PROGRESS tasks ──
        # Task is IN_PROGRESS but current_task_id points elsewhere → reset to AVAILABLE
        for task in self.active_goal.tasks:
            if task.status == TaskStatus.IN_PROGRESS and task.id != self.current_task_id:
                task.status = TaskStatus.AVAILABLE
                print(f"   🔧 Reset orphaned task '{task.id}' from IN_PROGRESS → AVAILABLE")

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
                    self.user_requested = False
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

    def set_grand_goal(self, goal_name: str, user_requested: bool = False) -> str:
        goal = self.goal_library.get_goal(goal_name)
        if not goal:
            available = [g["name"] for g in self.goal_library.list_goals()]
            return f"Unknown goal '{goal_name}'. Available: {', '.join(available)}"
        self.active_goal = goal
        self.user_requested = user_requested
        self.current_task_id = None
        self.task_fail_count = {}
        self.skip_retry_count = {}
        self.auto_check_progress()
        if self.active_goal:
            self.active_goal.refresh_availability()
        self._save()
        if self.active_goal:
            available = self.active_goal.get_available_tasks()
            return (
                f"🏆 GRAND GOAL: {self.active_goal.description}\n"
                f"   {len(self.active_goal.tasks)} tasks. Next: {', '.join(t.id for t in available[:5])}"
            )
        return f"🏆 Goal '{goal_name}' already complete!"

    def create_grand_goal(self, name: str, description: str, phases: list[dict],
                          tasks: list[dict], user_requested: bool = False,
                          save_to_library: bool = True) -> str:
        """Create a new goal from LLM-generated data."""
        # Validate
        errors = self.goal_library._validate_goal(name, tasks)
        if errors:
            return f"Invalid goal: {'; '.join(errors)}"

        # Save to library
        if save_to_library:
            source = "user_requested" if user_requested else "llm_created"
            save_result = self.goal_library.save_goal(name, description, phases, tasks, source)
            print(f"   📚 {save_result}")

        # Build and set active
        self.active_goal = self.goal_library._dict_to_grand_goal({
            "name": name, "description": description,
            "phases": phases, "tasks": tasks,
        })
        self.user_requested = user_requested
        self.current_task_id = None
        self.task_fail_count = {}
        self.skip_retry_count = {}
        self.auto_check_progress()
        if self.active_goal:
            self.active_goal.refresh_availability()
        self._save()
        if self.active_goal:
            available = self.active_goal.get_available_tasks()
            return (
                f"🏆 NEW GRAND GOAL: {description}\n"
                f"   {len(self.active_goal.tasks)} tasks. Next: {', '.join(t.id for t in available[:5])}"
            )
        return f"🏆 Goal '{name}' already complete!"

    # ── Status ──

    def get_prompt_context(self) -> str:
        if not self.active_goal:
            goals = self.goal_library.list_goals()
            goal_list = ", ".join(f"{g['name']}({g['task_count']} tasks)" for g in goals)
            return f"🏆 NO GRAND GOAL. Saved goals: {goal_list}"
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
        # Show tasks without phase
        no_phase_tasks = [t for t in goal.tasks if not t.phase]
        if no_phase_tasks:
            for task in no_phase_tasks:
                icon = {"completed": "✅", "skipped": "⏭️", "available": "⬜",
                        "blocked": "🔒", "in_progress": "▶️"}[task.status.value]
                current = " ← NOW" if task.id == self.current_task_id else ""
                lines.append(f"  {icon} {task.description}{current}")
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
