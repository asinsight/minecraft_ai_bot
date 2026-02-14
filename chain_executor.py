"""
Chain Executor — The 3-layer execution engine.

Layer 0 (Instinct):  No thinking. HP low → eat. Night → shelter. Instant.
Layer 1 (Chain):     No LLM. Execute hardcoded action chains step by step.
Layer 2 (Planning):  LLM call. Choose next objective, handle novel failures.

Flow per tick:
  1. Layer 0 check → if triggered, execute and return
  2. Layer 1 check → if chain active, execute next step and return
  3. Layer 2 → if no chain, call LLM to decide what to do next
"""

import os
import json
import time
import requests
from typing import Optional
from dataclasses import dataclass, field

from chain_library import (
    get_chain, get_search_strategy, list_available_chains,
    DROP_MAP, SEARCH_STRATEGIES
)
from experience_memory import ExperienceMemory
from grand_goal import GrandGoalManager, get_inventory_counts, check_block_nearby


BOT_API = os.getenv("BOT_API_URL", "http://localhost:3001")


# ============================================
# DATA CLASSES
# ============================================

@dataclass
class ChainState:
    """Tracks the currently running action chain."""
    chain_name: str                         # e.g., "make_iron_pickaxe"
    steps: list[dict] = field(default_factory=list)
    current_idx: int = 0
    search_retry_idx: int = 0              # which search strategy step we're on
    retry_count: int = 0                    # retries for current step
    max_retries: int = 2
    started_at: float = field(default_factory=time.time)
    timeout: float = 300.0                  # 5 min timeout per chain

    @property
    def is_done(self) -> bool:
        return self.current_idx >= len(self.steps)

    @property
    def is_timed_out(self) -> bool:
        return time.time() - self.started_at > self.timeout

    @property
    def current_step(self) -> Optional[dict]:
        if self.current_idx < len(self.steps):
            return self.steps[self.current_idx]
        return None

    def advance(self):
        self.current_idx += 1
        self.search_retry_idx = 0
        self.retry_count = 0

    def reset_search(self):
        self.search_retry_idx = 0
        self.retry_count = 0


@dataclass
class TickResult:
    """Result of one tick's execution."""
    layer: int                  # 0, 1, or 2
    action: str                 # what was done
    result: str                 # API response
    success: bool
    needs_llm: bool = False     # escalate to layer 2?
    llm_context: str = ""       # context for LLM if escalated


# ============================================
# API CALLER (direct, no LLM)
# ============================================

def call_tool(tool_name: str, args: dict, timeout: int = 60) -> dict:
    """Call a bot API tool directly. Returns {success, message}."""
    try:
        # Scale timeout for operations that take longer with higher counts
        if tool_name == "mine_block":
            count = int(args.get("count", 1))
            timeout = max(60, count * 8)  # ~8s per block (pathfind + dig + collect)
        elif tool_name in ("dig_down", "dig_tunnel", "dig_shelter", "build_shelter"):
            timeout = 120

        endpoint_map = {
            "mine_block": ("POST", "/action/mine", {"block_type": args.get("block_type"), "count": args.get("count", 1)}),
            "craft_item": ("POST", "/action/craft", {"item_name": args.get("item_name"), "count": args.get("count", 1)}),
            "smelt_item": ("POST", "/action/smelt", {"item_name": args.get("item_name"), "count": args.get("count", 1)}),
            "place_block": ("POST", "/action/place", {"block_name": args.get("block_name")}),
            "equip_item": ("POST", "/action/equip", {"item_name": args.get("item_name"), "destination": args.get("destination", "hand")}),
            "eat_food": ("POST", "/action/eat", {}),
            "attack_entity": ("POST", "/action/attack", {"entity_type": args.get("entity_type", "")}),
            "dig_shelter": ("POST", "/action/dig_shelter", {}),
            "dig_down": ("POST", "/action/dig_down", {"depth": args.get("depth", 10), "target_y": args.get("target_y", 0)}),
            "dig_tunnel": ("POST", "/action/dig_tunnel", {"direction": args.get("direction", "north"), "length": args.get("length", 10)}),
            "build_shelter": ("POST", "/action/build_shelter", {}),
            "explore": ("POST", "/action/explore", {"distance": args.get("distance", 20)}),
            "move_to": ("POST", "/action/move", {"x": args.get("x"), "y": args.get("y"), "z": args.get("z")}),
            "find_block": ("GET", "/find_block", {"type": args.get("block_type"), "range": args.get("max_distance", 64)}),
            "sleep_in_bed": ("POST", "/action/sleep", {}),
            "send_chat": ("POST", "/action/chat", {"message": args.get("message", "")}),
            "stop_moving": ("POST", "/action/stop", {}),
        }

        if tool_name not in endpoint_map:
            return {"success": False, "message": f"Unknown tool: {tool_name}"}

        method, path, body = endpoint_map[tool_name]
        url = f"{BOT_API}{path}"

        if method == "GET":
            r = requests.get(url, params=body, timeout=timeout)
        else:
            r = requests.post(url, json=body, timeout=timeout)

        result = r.json()

        # Auto-save location for important placed blocks (crafting_table, furnace, etc.)
        if tool_name == "place_block" and result.get("success"):
            try:
                from memory_tools import memory
                state = requests.get(f"{BOT_API}/state", timeout=5).json()
                pos = state.get("position", {})
                block_name = args.get("block_name", "")
                auto_msg = memory.auto_save_placed(
                    block_name, float(pos["x"]), float(pos["y"]), float(pos["z"])
                )
                if auto_msg:
                    print(f"   📍 {auto_msg}")
            except Exception:
                pass

        # Auto-save shelter location
        if tool_name in ("build_shelter", "dig_shelter") and result.get("success"):
            try:
                from memory_tools import memory
                state = requests.get(f"{BOT_API}/state", timeout=5).json()
                pos = state.get("position", {})
                existing = [n for n in memory.waypoints if n.startswith("shelter")]
                name = f"shelter_{len(existing) + 1}" if existing else "shelter"
                desc = "Enclosed shelter" if tool_name == "build_shelter" else "Emergency underground shelter"
                memory.save_location(name, "shelter", float(pos["x"]), float(pos["y"]), float(pos["z"]), desc)
                print(f"   📍 Saved shelter as '{name}'")
            except Exception:
                pass

        return result
    except Exception as e:
        return {"success": False, "message": f"API error: {e}"}


def get_bot_state() -> dict:
    """Get current bot state."""
    try:
        r = requests.get(f"{BOT_API}/state", timeout=5)
        return r.json()
    except:
        return {}


def get_threat_assessment() -> dict:
    """Get threat assessment."""
    try:
        r = requests.get(f"{BOT_API}/threat_assessment", timeout=5)
        return r.json()
    except:
        return {"recommendation": "safe", "threats": {"count": 0}}


# ============================================
# LAYER 0: INSTINCT (no thinking)
# ============================================

_last_shelter_time = 0  # module-level cooldown tracker

def check_instinct(state: dict, threat: dict) -> Optional[TickResult]:
    """Check for immediate survival needs. Returns action if triggered."""
    global _last_shelter_time

    health = state.get("health", 20)
    food = state.get("food", 20)
    is_safe_outside = state.get("isSafeOutside", True)
    environment = state.get("environment", "surface")
    can_see_sky = state.get("canSeeSky", True)
    time_phase = state.get("time", "day")
    inventory = state.get("inventory", [])
    position = state.get("position", {})
    bot_y = float(position.get("y", 64))

    is_sheltered = (
        environment in ("indoors", "underground", "deep_underground")
        or not can_see_sky
        or bot_y < 55  # underground if below surface level
    )
    has_food = any(i["name"] in (
        "cooked_beef", "cooked_porkchop", "cooked_chicken", "cooked_mutton",
        "bread", "apple", "golden_apple", "baked_potato", "sweet_berries",
        "cooked_salmon", "cooked_cod"
    ) for i in inventory)

    rec = threat.get("recommendation", "safe")
    threat_details = threat.get("threats", {}).get("details", [])
    threat_count = threat.get("threats", {}).get("count", 0)

    # Shelter cooldown — don't spam dig_shelter every tick
    shelter_cooldown = 60  # seconds
    shelter_on_cooldown = (time.time() - _last_shelter_time) < shelter_cooldown

    # ── Critical health ──
    if health < 5:
        if has_food:
            result = call_tool("eat_food", {})
            return TickResult(0, "eat_food()", result.get("message", ""), result.get("success", False))
        elif not shelter_on_cooldown:
            _last_shelter_time = time.time()
            result = call_tool("dig_shelter", {})
            return TickResult(0, "dig_shelter() [no food, critical HP]", result.get("message", ""), result.get("success", False))

    # ── Creeper very close ──
    for td in threat_details:
        if td.get("type") == "creeper" and td.get("distance", 99) < 5:
            if not shelter_on_cooldown:
                _last_shelter_time = time.time()
                result = call_tool("dig_shelter", {})
                return TickResult(0, "dig_shelter() [creeper close!]", result.get("message", ""), result.get("success", False))

    # ── Warden ──
    for td in threat_details:
        if td.get("type") == "warden":
            if not shelter_on_cooldown:
                _last_shelter_time = time.time()
                result = call_tool("dig_shelter", {})
                return TickResult(0, "dig_shelter() [warden!]", result.get("message", ""), result.get("success", False))

    # ── Flee recommendation ──
    if rec == "flee" and not is_sheltered and not shelter_on_cooldown:
        _last_shelter_time = time.time()
        result = call_tool("dig_shelter", {})
        return TickResult(0, "dig_shelter() [flee!]", result.get("message", ""), result.get("success", False))

    # ── Night on surface ──
    if not is_safe_outside and not is_sheltered and not shelter_on_cooldown:
        _last_shelter_time = time.time()
        result = call_tool("dig_shelter", {})
        return TickResult(0, "dig_shelter() [night]", result.get("message", ""), result.get("success", False))

    # ── Dusk warning ──
    if time_phase == "dusk" and not is_sheltered and not shelter_on_cooldown:
        _last_shelter_time = time.time()
        result = call_tool("dig_shelter", {})
        return TickResult(0, "dig_shelter() [dusk]", result.get("message", ""), result.get("success", False))

    # ── Very hungry ──
    if food < 5 and has_food:
        result = call_tool("eat_food", {})
        return TickResult(0, "eat_food() [hungry]", result.get("message", ""), result.get("success", False))

    # ── Mob inside shelter ──
    if is_sheltered and threat_count > 0:
        closest = min((td["distance"] for td in threat_details), default=99)
        if closest <= 5:
            has_weapon = any(i["name"].endswith(("_sword", "_axe")) for i in inventory)
            if has_weapon:
                mob_type = threat_details[0].get("type", "")
                result = call_tool("attack_entity", {"entity_type": mob_type})
                return TickResult(0, f"attack_entity({mob_type}) [mob in shelter]",
                                result.get("message", ""), result.get("success", False))

    return None  # No instinct triggered


# ============================================
# LAYER 1: CHAIN EXECUTION (no LLM)
# ============================================

class ChainExecutor:
    """Executes action chains step by step without LLM."""

    def __init__(self, experience: ExperienceMemory, goal_manager: GrandGoalManager):
        self.experience = experience
        self.goal_manager = goal_manager
        self.active_chain: Optional[ChainState] = None
        self._in_search_mode = False  # currently executing a search strategy

    def has_active_chain(self) -> bool:
        return self.active_chain is not None and not self.active_chain.is_done

    def start_chain(self, chain_name: str, completion_items: dict = None) -> str:
        """Start a new action chain.

        Args:
            chain_name: Name of chain in chain_library
            completion_items: Target items from grand goal task (e.g. {"oak_planks": 32}).
                If provided, chain steps won't skip unless these targets are met.
        """
        steps = get_chain(chain_name)
        if not steps:
            return f"Unknown chain: {chain_name}"

        # If task needs more items than chain's skip thresholds,
        # raise skip thresholds to match task requirements
        if completion_items:
            for step in steps:
                skip_if = step.get("skip_if", {})
                if skip_if:
                    for item_name, skip_count in list(skip_if.items()):
                        if item_name in completion_items:
                            needed = completion_items[item_name]
                            if needed > skip_count:
                                skip_if[item_name] = needed

        self.active_chain = ChainState(
            chain_name=chain_name,
            steps=steps,
            timeout=600 if "diamond" in chain_name else 300,
        )
        self._in_search_mode = False
        return f"▶️ Started chain: {chain_name} ({len(steps)} steps)"

    def cancel_chain(self, reason: str = ""):
        """Cancel the current chain."""
        name = self.active_chain.chain_name if self.active_chain else "none"
        self.active_chain = None
        self._in_search_mode = False
        print(f"   ⏹️ Chain '{name}' cancelled: {reason}")

    def execute_tick(self) -> TickResult:
        """Execute one step of the active chain. Called by main loop."""
        if not self.active_chain:
            return TickResult(1, "no_chain", "No active chain", False, needs_llm=True)

        chain = self.active_chain

        # Timeout check
        if chain.is_timed_out:
            name = chain.chain_name
            self.cancel_chain("timeout")
            return TickResult(1, "timeout", f"Chain {name} timed out", False,
                            needs_llm=True, llm_context=f"Chain '{name}' timed out after 5 minutes.")

        # All steps done
        if chain.is_done:
            name = chain.chain_name
            self.active_chain = None
            return TickResult(1, "chain_complete", f"Chain '{name}' completed!", True)

        step = chain.current_step
        inventory = get_inventory_counts()

        # ── Skip check ──
        if self._should_skip(step, inventory):
            print(f"   ⏭️ Skip: {step['tool']}({step['args']}) — already have items")
            chain.advance()
            # Try next step immediately (recursive, but bounded by chain length)
            if not chain.is_done:
                return self.execute_tick()
            name = chain.chain_name
            self.active_chain = None
            return TickResult(1, "chain_complete", f"Chain '{name}' completed (some steps skipped)!", True)

        # ── Execute step ──
        tool_name = step["tool"]
        tool_args = step["args"]
        step_type = step.get("type", "action")

        # ── Prerequisite tool check (Layer 1 logic) ──
        # Before mining, ensure we have the right pickaxe
        prereq_result = self._check_and_inject_prerequisites(step, inventory, chain)
        if prereq_result:
            return prereq_result

        print(f"   🔧 [{step_type}] {tool_name}({json.dumps(tool_args)})")

        # Auto-equip best tool before mining
        if tool_name == "mine_block":
            self._auto_equip_for_mining(tool_args.get("block_type", ""), inventory)

        result = call_tool(tool_name, tool_args)
        success = result.get("success", False)
        message = result.get("message", "")

        if success:
            print(f"   ✅ {message[:100]}")

            # Record search success for experience memory
            if step_type == "search":
                target = step.get("search_target", tool_args.get("block_type", ""))
                if target:
                    self.experience.record_search_success(target, f"direct_nearby")

            chain.advance()
            return TickResult(1, f"{tool_name}({tool_args})", message, True)

        # ── Handle failure ──
        print(f"   ❌ {message[:100]}")

        if step_type == "search":
            return self._handle_search_failure(step, message, inventory)
        else:
            return self._handle_step_failure(step, message, inventory)

    # ── Tool requirement rules (game knowledge) ──
    # block_type → (minimum_tool, chain_to_make_it)
    TOOL_REQUIREMENTS = {
        # Stone-tier blocks: need wooden_pickaxe+
        "stone": ("wooden_pickaxe", "make_wooden_pickaxe"),
        "cobblestone": ("wooden_pickaxe", "make_wooden_pickaxe"),
        "coal_ore": ("wooden_pickaxe", "make_wooden_pickaxe"),
        "deepslate_coal_ore": ("wooden_pickaxe", "make_wooden_pickaxe"),
        # Iron-tier blocks: need stone_pickaxe+
        "iron_ore": ("stone_pickaxe", "make_stone_pickaxe"),
        "deepslate_iron_ore": ("stone_pickaxe", "make_stone_pickaxe"),
        "copper_ore": ("stone_pickaxe", "make_stone_pickaxe"),
        "lapis_ore": ("stone_pickaxe", "make_stone_pickaxe"),
        # Diamond/gold-tier: need iron_pickaxe+
        "diamond_ore": ("iron_pickaxe", "make_iron_pickaxe"),
        "deepslate_diamond_ore": ("iron_pickaxe", "make_iron_pickaxe"),
        "gold_ore": ("iron_pickaxe", "make_iron_pickaxe"),
        "deepslate_gold_ore": ("iron_pickaxe", "make_iron_pickaxe"),
        "emerald_ore": ("iron_pickaxe", "make_iron_pickaxe"),
        "redstone_ore": ("iron_pickaxe", "make_iron_pickaxe"),
        # Obsidian: need diamond_pickaxe
        "obsidian": ("diamond_pickaxe", "make_diamond_pickaxe"),
    }

    # Tool tier order (higher index = better)
    TOOL_TIERS = [
        "wooden_pickaxe", "stone_pickaxe", "iron_pickaxe", "diamond_pickaxe"
    ]

    def _has_tool_or_better(self, required_tool: str, inventory: dict) -> bool:
        """Check if inventory has the required tool or a better one."""
        try:
            required_idx = self.TOOL_TIERS.index(required_tool)
        except ValueError:
            return False
        for tier_idx in range(required_idx, len(self.TOOL_TIERS)):
            if inventory.get(self.TOOL_TIERS[tier_idx], 0) > 0:
                return True
        return False

    def _check_and_inject_prerequisites(self, step: dict, inventory: dict,
                                         chain: ChainState) -> Optional[TickResult]:
        """Check if current step needs a tool we don't have.
        If so, inject the prerequisite chain steps before the current step.
        Returns a TickResult if prerequisites were injected, None otherwise.
        """
        tool_name = step.get("tool", "")
        if tool_name != "mine_block":
            return None

        block_type = step.get("args", {}).get("block_type", "")
        req = self.TOOL_REQUIREMENTS.get(block_type)
        if not req:
            return None  # No special tool needed (dirt, sand, wood, etc.)

        required_tool, prereq_chain_name = req
        if self._has_tool_or_better(required_tool, inventory):
            return None  # Already have the right tool

        # Need to make the tool first!
        print(f"   🔧 Need {required_tool} to mine {block_type} — injecting {prereq_chain_name}")

        prereq_steps = get_chain(prereq_chain_name)
        if not prereq_steps:
            return None

        # Insert prerequisite steps before current step
        chain.steps = (
            chain.steps[:chain.current_idx] +
            prereq_steps +
            chain.steps[chain.current_idx:]
        )
        # Don't advance — next tick will execute the first prereq step
        return TickResult(1, f"inject_prereq:{prereq_chain_name}",
                         f"Need {required_tool} for {block_type}. Injected {prereq_chain_name} ({len(prereq_steps)} steps).",
                         True)

    def _auto_equip_for_mining(self, block_type: str, inventory: dict):
        """Equip the best available pickaxe before mining."""
        # Find best pickaxe in inventory
        for tool in reversed(self.TOOL_TIERS):
            if inventory.get(tool, 0) > 0:
                call_tool("equip_item", {"item_name": tool})
                return
        # No pickaxe — try equip any axe or shovel
        for tool_type in ["_axe", "_shovel"]:
            for prefix in ["diamond", "iron", "stone", "wooden"]:
                name = f"{prefix}{tool_type}"
                if inventory.get(name, 0) > 0:
                    call_tool("equip_item", {"item_name": name})
                    return

    def _should_skip(self, step: dict, inventory: dict) -> bool:
        """Check if step should be skipped based on inventory."""
        # skip_if: any ONE of the items at required count → skip
        skip_if = step.get("skip_if", {})
        if skip_if:
            for item_name, count in skip_if.items():
                have = inventory.get(item_name, 0)
                if have >= count:
                    return True
            # Debug: show why NOT skipped
            print(f"   📋 Skip check: need {skip_if}, have {{{', '.join(f'{k}:{inventory.get(k,0)}' for k in skip_if)}}}")

        # skip_if_nearby: block exists nearby → skip
        skip_nearby = step.get("skip_if_nearby")
        if skip_nearby:
            if check_block_nearby(skip_nearby):
                return True

        return False

    def _handle_search_failure(self, step: dict, error_msg: str,
                                inventory: dict) -> TickResult:
        """Handle failure of a search-type step. Tries search strategy."""
        chain = self.active_chain
        target = step.get("search_target", step["args"].get("block_type", ""))

        # Check experience memory first
        if chain.search_retry_idx == 0:
            hint = self.experience.get_search_hint(target)
            if hint and hint.get("location"):
                loc = hint["location"]
                print(f"   🧠 Experience: {target} was found at ({loc.get('x')}, {loc.get('y')}, {loc.get('z')})")
                # Move there and retry
                call_tool("move_to", {"x": loc["x"], "y": loc["y"], "z": loc["z"]})
                result = call_tool(step["tool"], step["args"])
                if result.get("success"):
                    print(f"   ✅ Found via memory! {result.get('message', '')[:80]}")
                    self.experience.record_search_success(target, "memory_location", loc)
                    chain.advance()
                    return TickResult(1, f"search:{target} via memory", result.get("message", ""), True)

        # Get search strategy
        strategies = get_search_strategy(target)
        if chain.search_retry_idx >= len(strategies):
            # All strategies exhausted → escalate to LLM
            chain.advance()  # Skip this step, let LLM figure it out
            return TickResult(1, f"search:{target} exhausted", error_msg, False,
                            needs_llm=True,
                            llm_context=f"Cannot find {target}. Tried {len(strategies)} strategies. "
                                       f"Current inventory: {json.dumps(dict(list(inventory.items())[:15]))}. "
                                       f"What should I do?")

        # Execute next search strategy
        strategy = strategies[chain.search_retry_idx]
        action_type, action_args = strategy

        print(f"   🔍 Search strategy [{chain.search_retry_idx+1}/{len(strategies)}]: {action_type}({action_args})")

        if action_type == "check_memory":
            # Check spatial memory for saved locations
            try:
                from memory_tools import memory
                nearest = memory.find_nearest(action_args.get("category", "resource"))
                if "No saved locations" not in nearest and "Cannot" not in nearest:
                    print(f"   📍 Memory: {nearest[:80]}")
                    # Parse coordinates and move there — simplified
            except:
                pass
            chain.search_retry_idx += 1
            return TickResult(1, f"check_memory({action_args})", "Checked memory", True)

        # Execute the search action
        result = call_tool(action_type, action_args)
        search_msg = result.get("message", "")
        chain.search_retry_idx += 1

        # After search action (dig_down, dig_tunnel, explore), retry the original step
        if result.get("success"):
            # Check if target is now findable
            find_result = call_tool("find_block", {"block_type": target, "max_distance": 32})
            if find_result.get("success"):
                # Found it! Now do the original step
                original_result = call_tool(step["tool"], step["args"])
                if original_result.get("success"):
                    # Record success
                    state = get_bot_state()
                    pos = state.get("position", {})
                    location = {"x": float(pos.get("x", 0)), "y": float(pos.get("y", 0)), "z": float(pos.get("z", 0))}
                    self.experience.record_search_success(target, f"{action_type}:{json.dumps(action_args)}", location)
                    chain.advance()
                    return TickResult(1, f"{step['tool']} (after {action_type})",
                                    original_result.get("message", ""), True)

        # Search action done but target not found yet → next tick will try next strategy
        return TickResult(1, f"search:{action_type}({action_args})", search_msg, True)

    def _handle_step_failure(self, step: dict, error_msg: str,
                              inventory: dict) -> TickResult:
        """Handle failure of a non-search step."""
        chain = self.active_chain
        error_lower = error_msg.lower()
        chain.retry_count += 1

        # Check experience for known solution FIRST (before escalation)
        solution = self.experience.get_error_solution(step["tool"], error_msg)
        if solution:
            print(f"   🧠 Known solution for this error, inserting {len(solution)} steps")
            chain.steps = chain.steps[:chain.current_idx] + solution + chain.steps[chain.current_idx:]
            chain.retry_count = 0  # reset — new steps injected
            return TickResult(1, f"apply_solution", "Applying known solution from experience", True)

        # ═══ UNIVERSAL ESCALATION RULE ═══
        # Any step failing 3+ times → stop trying, ask LLM
        if chain.retry_count > 3:
            chain.advance()  # Skip this step
            return TickResult(1, f"escalate:{step['tool']}", error_msg, False,
                            needs_llm=True,
                            llm_context=f"Step failed {chain.retry_count} times, giving up.\n"
                                       f"Step: {step['tool']}({step['args']})\n"
                                       f"Error: {error_msg}\n"
                                       f"Inventory: {json.dumps(dict(list(inventory.items())[:15]))}\n"
                                       f"What should I do differently?")

        # ── "No crafting table nearby" → resolve, then RETRY same step ──
        if "crafting table" in error_lower or "crafting_table" in error_lower:
            fixed = self._ensure_crafting_table(inventory)
            if fixed:
                return TickResult(1, "auto_fix: crafting_table", fixed, True)

        # ── "No furnace nearby" → resolve, then RETRY same step ──
        if "furnace" in error_lower and ("nearby" in error_lower or "no furnace" in error_lower):
            fixed = self._ensure_furnace(inventory)
            if fixed:
                return TickResult(1, "auto_fix: furnace", fixed, True)

        # ── place_block: "no suitable position" → mine adjacent block to create space ──
        if step["tool"] == "place_block" and "no suitable position" in error_lower:
            mine_result = call_tool("mine_block", {"block_type": "stone", "count": 1})
            if not mine_result.get("success"):
                mine_result = call_tool("mine_block", {"block_type": "dirt", "count": 1})
            if mine_result.get("success"):
                print(f"   🔧 Cleared space for block placement")
                chain.retry_count = 0  # reset — space cleared, retry should work
                return TickResult(1, "auto_fix: clear_space", "Mined block to create placement space", True)

        # ── move_to: path blocked → server already tried mining, escalate to LLM ──
        if step["tool"] == "move_to" and ("blocked" in error_lower or "timed out" in error_lower):
            chain.advance()  # skip this step
            return TickResult(1, f"escalate:{step['tool']}", error_msg, False,
                            needs_llm=True,
                            llm_context=f"Movement failed — path blocked even after mining obstacle.\n"
                                       f"Step: {step['tool']}({step['args']})\n"
                                       f"Error: {error_msg}\n"
                                       f"Bot needs a new route or different approach.\n"
                                       f"Inventory: {json.dumps(dict(list(inventory.items())[:15]))}")

        # ── Missing materials for crafting → let universal rule handle it ──
        # (retry_count already incremented above, escalation at 3)

        # ── Need a pickaxe → inject prerequisite chain ──
        if "pickaxe" in error_lower and ("craft" in error_lower or "need" in error_lower):
            for tool_name, chain_name in [
                ("wooden_pickaxe", "make_wooden_pickaxe"),
                ("stone_pickaxe", "make_stone_pickaxe"),
                ("iron_pickaxe", "make_iron_pickaxe"),
            ]:
                if tool_name in error_lower or not self._has_tool_or_better("wooden_pickaxe", inventory):
                    prereq_steps = get_chain(chain_name)
                    if prereq_steps:
                        print(f"   🔧 Injecting {chain_name} to fix missing tool")
                        chain.steps = chain.steps[:chain.current_idx] + prereq_steps + chain.steps[chain.current_idx:]
                        chain.retry_count = 0  # reset — new steps injected
                        return TickResult(1, f"inject_prereq:{chain_name}", f"Need tool, injecting {chain_name}", True)
                    break

        # ── Generic: wait for next tick to retry (universal rule catches at 3) ──
        return TickResult(1, f"retry:{step['tool']}", f"Retry {chain.retry_count}/3: {error_msg}", False)

    def _ensure_crafting_table(self, inventory: dict) -> Optional[str]:
        """Make sure a crafting table is placed nearby. Returns status message or None."""
        # 1. One is already nearby → just move to it
        find_result = call_tool("find_block", {"block_type": "crafting_table", "max_distance": 32})
        if find_result.get("success"):
            return "Found crafting_table nearby"

        # 2. Have one in inventory → try to place
        if inventory.get("crafting_table", 0) > 0:
            place_result = call_tool("place_block", {"block_name": "crafting_table"})
            if place_result.get("success"):
                return "Placed crafting_table from inventory"
            # Place failed — try digging a spot first, then place
            print(f"   ⚠️ place_block failed: {place_result.get('message', '')[:80]}")
            # Dig the block in front of bot to make space, then place on it
            call_tool("mine_block", {"block_type": "dirt", "count": 1})
            place_result = call_tool("place_block", {"block_name": "crafting_table"})
            if place_result.get("success"):
                return "Placed crafting_table (after clearing space)"
            # Still failing — just continue, server craft may find it anyway
            print(f"   ⚠️ Still can't place crafting_table, continuing anyway")

        # 3. Have planks → craft + place
        if inventory.get("oak_planks", 0) >= 4:
            craft_result = call_tool("craft_item", {"item_name": "crafting_table"})
            if craft_result.get("success"):
                place_result = call_tool("place_block", {"block_name": "crafting_table"})
                if place_result.get("success"):
                    return "Crafted + placed crafting_table from planks"
                return "Crafted crafting_table (place may have failed)"

        # 4. Have logs → planks → craft + place
        log_types = ["oak_log", "spruce_log", "birch_log", "jungle_log", "acacia_log", "dark_oak_log"]
        for log in log_types:
            if inventory.get(log, 0) >= 1:
                call_tool("craft_item", {"item_name": "oak_planks"})
                call_tool("craft_item", {"item_name": "crafting_table"})
                call_result = call_tool("place_block", {"block_name": "crafting_table"})
                return f"Converted {log} → planks → crafting_table"

        # 5. Nothing available → mine wood first
        print(f"   🔧 No logs or planks — mining wood for crafting table")
        mine_result = call_tool("mine_block", {"block_type": "oak_log", "count": 1})
        if mine_result.get("success"):
            call_tool("craft_item", {"item_name": "oak_planks"})
            call_tool("craft_item", {"item_name": "crafting_table"})
            call_tool("place_block", {"block_name": "crafting_table"})
            return "Mined wood → planks → crafting_table"

        return None  # Truly can't fix this

    def _ensure_furnace(self, inventory: dict) -> Optional[str]:
        """Make sure a furnace is placed nearby. Returns status message or None."""
        if inventory.get("furnace", 0) > 0:
            call_tool("place_block", {"block_name": "furnace"})
            return "Placed furnace from inventory"

        find_result = call_tool("find_block", {"block_type": "furnace", "max_distance": 32})
        if find_result.get("success"):
            return "Found furnace nearby"

        if inventory.get("cobblestone", 0) >= 8:
            # Need crafting table for furnace
            ct_nearby = call_tool("find_block", {"block_type": "crafting_table", "max_distance": 32})
            if not ct_nearby.get("success"):
                self._ensure_crafting_table(inventory)
            call_tool("craft_item", {"item_name": "furnace"})
            call_tool("place_block", {"block_name": "furnace"})
            return "Crafted + placed furnace"

        return None

    def get_status_str(self) -> str:
        """Get human-readable status of current chain."""
        if not self.active_chain:
            return "No active chain"
        c = self.active_chain
        step = c.current_step
        step_desc = f"{step['tool']}({step['args']})" if step else "done"
        return f"Chain: {c.chain_name} [{c.current_idx+1}/{len(c.steps)}] → {step_desc}"