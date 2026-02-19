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


def abort_bot_action():
    """Tell the Node.js server to cancel any long-running action."""
    try:
        requests.post(f"{BOT_API}/abort", timeout=3)
    except Exception:
        pass


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
            timeout = max(60, count * 25)  # ~25s per block (deepslate: 12s dig + 5s pathfind + 3s collect + margin)
        elif tool_name == "dig_down":
            target_y = args.get("target_y")
            depth = int(args.get("depth", 10))
            if target_y is not None:
                # Estimate depth: assume starting from ~Y=80 (conservative)
                estimated_depth = max(depth, 80 - int(target_y))
            else:
                estimated_depth = depth
            timeout = max(120, estimated_depth * 4)  # ~4s per Y level
        elif tool_name in ("dig_tunnel", "dig_shelter", "build_shelter", "escape_water", "flee"):
            timeout = 120
        elif tool_name == "branch_mine":
            timeout = 300
        elif tool_name in ("store_items", "retrieve_items", "open_chest", "collect_drops"):
            timeout = 30
        elif tool_name == "shield_block":
            timeout = 10

        endpoint_map = {
            "mine_block": ("POST", "/action/mine", {"block_type": args.get("block_type"), "count": args.get("count", 1)}),
            "craft_item": ("POST", "/action/craft", {"item_name": args.get("item_name"), "count": args.get("count", 1)}),
            "smelt_item": ("POST", "/action/smelt", {"item_name": args.get("item_name"), "count": args.get("count", 1)}),
            "place_block": ("POST", "/action/place", {"block_name": args.get("block_name")}),
            "equip_item": ("POST", "/action/equip", {"item_name": args.get("item_name"), "destination": args.get("destination", "hand")}),
            "eat_food": ("POST", "/action/eat", {}),
            "attack_entity": ("POST", "/action/attack", {"entity_type": args.get("entity_type", "")}),
            "dig_shelter": ("POST", "/action/dig_shelter", {}),
            "escape_water": ("POST", "/action/escape_water", {}),
            "flee": ("POST", "/action/flee", {"distance": args.get("distance", 30)}),
            "dig_down": ("POST", "/action/dig_down", {"depth": args.get("depth", 10), "target_y": args.get("target_y", 0)}),
            "dig_tunnel": ("POST", "/action/dig_tunnel", {"direction": args.get("direction", "north"), "length": args.get("length", 10)}),
            "branch_mine": ("POST", "/action/branch_mine", {
                "direction": args.get("direction", "north"),
                "main_length": args.get("main_length", 20),
                "branch_length": args.get("branch_length", 5),
                "branch_spacing": args.get("branch_spacing", 3),
            }),
            "build_shelter": ("POST", "/action/build_shelter", {}),
            "explore": ("POST", "/action/explore", {"distance": args.get("distance", 20)}),
            "move_to": ("POST", "/action/move", {"x": args.get("x"), "y": args.get("y"), "z": args.get("z")}),
            "find_block": ("GET", "/find_block", {"type": args.get("block_type"), "range": args.get("max_distance", 64)}),
            "sleep_in_bed": ("POST", "/action/sleep", {}),
            "send_chat": ("POST", "/action/chat", {"message": args.get("message", "")}),
            "stop_moving": ("POST", "/action/stop", {}),
            # New: shield, chest, bucket, drops, caves
            "shield_block": ("POST", "/action/shield_block", {"duration": args.get("duration", 2000)}),
            "store_items": ("POST", "/action/store_items", {}),
            "retrieve_items": ("POST", "/action/retrieve_items", {"item_name": args.get("item_name"), "count": args.get("count", 1)}),
            "open_chest": ("POST", "/action/open_chest", {}),
            "use_bucket": ("POST", "/action/use_bucket", {"action": args.get("action"), "x": args.get("x"), "y": args.get("y"), "z": args.get("z")}),
            "collect_drops": ("POST", "/action/collect_drops", {}),
            "scan_caves": ("GET", "/scan_caves", {"radius": args.get("radius", 16)}),
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

        # Auto-save shelter location (keep max 3 shelters)
        if tool_name in ("build_shelter", "dig_shelter") and result.get("success"):
            try:
                from memory_tools import memory
                state = requests.get(f"{BOT_API}/state", timeout=5).json()
                pos = state.get("position", {})
                desc = "Enclosed shelter" if tool_name == "build_shelter" else "Emergency underground shelter"
                memory.save_shelter(float(pos["x"]), float(pos["y"]), float(pos["z"]), desc)
            except Exception:
                pass

        return result
    except Exception as e:
        err_msg = str(e).lower()
        if "timed out" in err_msg or "timeout" in err_msg:
            print(f"   [abort] Timeout on {tool_name}, sending abort to server")
            abort_bot_action()
            time.sleep(1.5)  # let server finish cleanup
            return {"success": False, "message": f"Timeout: {tool_name} took too long, aborted server action"}
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


def get_combat_status() -> dict:
    """Get real-time combat status (attack detection)."""
    try:
        r = requests.get(f"{BOT_API}/combat_status", timeout=3)
        return r.json()
    except:
        return {"isUnderAttack": False}


# ============================================
# LAYER 0: INSTINCT (no thinking)
# ============================================

_previous_health = 20.0  # Track health between ticks for delta detection
_stuck_positions = []    # list of (x, y, z, time) for stuck detection
_stuck_cooldown = 0      # timestamp: don't re-trigger stuck within 30s


def _check_stuck(state: dict) -> bool:
    """Track position; return True if bot hasn't moved for 3+ ticks (~9+ seconds)."""
    global _stuck_positions, _stuck_cooldown
    pos = state.get("position", {})
    x = float(pos.get("x", 0))
    y = float(pos.get("y", 0))
    z = float(pos.get("z", 0))
    now = time.time()
    _stuck_positions.append((x, y, z, now))
    if len(_stuck_positions) > 5:
        _stuck_positions = _stuck_positions[-5:]
    if len(_stuck_positions) >= 3 and now > _stuck_cooldown:
        first = _stuck_positions[-3]
        dist = ((x - first[0])**2 + (y - first[1])**2 + (z - first[2])**2) ** 0.5
        if dist < 1.0 and (now - first[3]) > 8:
            _stuck_cooldown = now + 30  # don't retrigger for 30s
            _stuck_positions.clear()
            return True
    return False


def _get_surrounding_blocks() -> Optional[dict]:
    """Get blocks immediately surrounding the bot (4 directions + above/below)."""
    try:
        r = requests.get(f"{BOT_API}/surrounding_blocks", timeout=3)
        return r.json()
    except Exception:
        return None


def _try_unstick() -> Optional[TickResult]:
    """Smart unstick: scan surroundings, find or create an opening, move through it.
    Returns TickResult if action taken, None if couldn't do anything."""
    surr = _get_surrounding_blocks()
    if not surr:
        # API failed — fallback to old dig_down
        result = call_tool("dig_down", {"depth": 2, "target_y": 0, "emergency": True})
        return TickResult(1, "unstick [dig_down fallback]",
                         result.get("message", ""), result.get("success", False))

    bot_pos = surr.get("position", {})
    directions = ["north", "south", "east", "west"]

    # Phase 1: Try to move toward an already-open direction
    for d in directions:
        info = surr.get(d, {})
        if info.get("open"):
            target_x = info.get("x", bot_pos.get("x", 0))
            target_z = info.get("z", bot_pos.get("z", 0))
            target_y = bot_pos.get("y", 64)
            print(f"   🔓 Open direction: {d}, moving there")
            result = call_tool("move_to", {"x": target_x, "y": target_y, "z": target_z})
            if result.get("success"):
                return TickResult(1, f"unstick [move {d}]",
                                 f"Moved {d} through open gap", True)

    # Phase 2: All 4 directions blocked — mine the easiest block to create an opening
    # Prefer directions where head is already open (only feet blocked)
    best_dir = None
    for d in directions:
        info = surr.get(d, {})
        head = info.get("head", {})
        if head.get("passable"):
            best_dir = d
            break

    # If no head-open direction, just pick the first
    if not best_dir:
        best_dir = directions[0]

    info = surr[best_dir]
    # Mine the feet-level block in that direction
    feet_block = info.get("feet", {}).get("name", "air")
    head_block = info.get("head", {}).get("name", "air")
    blocks_to_mine = []
    if not info.get("feet", {}).get("passable"):
        blocks_to_mine.append(feet_block)
    if not info.get("head", {}).get("passable"):
        blocks_to_mine.append(head_block)

    if blocks_to_mine:
        block_name = blocks_to_mine[0]
        print(f"   ⛏️ All directions blocked! Mining {block_name} ({best_dir}) to create opening")
        result = call_tool("mine_block", {"block_type": block_name, "count": 1})
        if result.get("success"):
            # Now move into the cleared space
            target_x = info.get("x", bot_pos.get("x", 0))
            target_z = info.get("z", bot_pos.get("z", 0))
            target_y = bot_pos.get("y", 64)
            call_tool("move_to", {"x": target_x, "y": target_y, "z": target_z})
            return TickResult(1, f"unstick [mine {block_name} + move {best_dir}]",
                             f"Mined {block_name} to {best_dir} and moved through", True)

    # Phase 3: Can't mine sideways — dig down as last resort
    print(f"   ⬇️ Can't clear sideways, digging down")
    result = call_tool("dig_down", {"depth": 2, "target_y": 0, "emergency": True})
    return TickResult(1, "unstick [dig_down last resort]",
                     result.get("message", ""), result.get("success", False))


def _get_pending_drops() -> int:
    """Check how many entity drops are pending collection."""
    try:
        r = requests.get(f"{BOT_API}/pending_drops", timeout=3)
        return r.json().get("count", 0)
    except Exception:
        return 0

def _equip_best_weapon(inventory: list) -> Optional[str]:
    """Equip best available weapon. Returns weapon name or None."""
    inv_dict = {i["name"]: i["count"] for i in inventory}
    sword_tiers = ["wooden_sword", "stone_sword", "iron_sword", "diamond_sword"]
    for sword in reversed(sword_tiers):
        if inv_dict.get(sword, 0) > 0:
            call_tool("equip_item", {"item_name": sword})
            return sword
    return None


RANGED_MOBS = {"skeleton", "stray", "pillager", "drowned", "blaze", "ghast", "shulker"}


def _try_shield_block(inventory: list, attacker_type: str = "") -> bool:
    """If bot has shield and attacker is ranged, do a quick shield block.
    Returns True if shield was activated."""
    has_shield = any(i["name"] == "shield" for i in inventory)
    if not has_shield:
        return False
    # Block if ranged attacker, or if taking damage (brief block before counter-attack)
    if attacker_type in RANGED_MOBS or attacker_type == "":
        print(f"   🛡️ Shield block! (attacker: {attacker_type or 'unknown'})")
        call_tool("shield_block", {"duration": 1500})
        return True
    return False


def check_instinct(state: dict, threat: dict) -> Optional[TickResult]:
    """Check for immediate survival needs. Returns action if triggered."""
    global _previous_health

    health = state.get("health", 20)
    food = state.get("food", 20)
    is_safe_outside = state.get("isSafeOutside", True)
    environment = state.get("environment", "surface")
    can_see_sky = state.get("canSeeSky", True)
    time_phase = state.get("time", "day")
    inventory = state.get("inventory", [])
    position = state.get("position", {})
    bot_y = float(position.get("y", 64))

    # Combat state from /state
    combat = state.get("combat", {})
    is_under_attack = combat.get("isUnderAttack", False)
    last_attacker = combat.get("lastAttacker", None)
    time_since_hit = combat.get("timeSinceHit", None)

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
    has_weapon = any(i["name"].endswith(("_sword", "_axe")) for i in inventory)

    rec = threat.get("recommendation", "safe")
    threat_details = threat.get("threats", {}).get("details", [])
    threat_count = threat.get("threats", {}).get("count", 0)

    # Health delta detection (between ticks)
    health_delta = _previous_health - health
    _previous_health = health

    # ── Critical health ──
    if health < 5:
        if has_food:
            result = call_tool("eat_food", {})
            return TickResult(0, "eat_food()", result.get("message", ""), result.get("success", False))
        elif is_under_attack:
            # Critical HP + under attack = flee immediately (don't wait for shelter cooldown)
            print(f"   🏃 Critical HP + under attack → flee!")
            result = call_tool("flee", {})
            return TickResult(0, "flee() [critical HP + under attack]", result.get("message", ""), result.get("success", False))
        else:
            # No food, not under attack — dig down to safety
            target_y = min(int(bot_y) - 10, 50)
            target_y = max(target_y, 10)
            if int(bot_y) > target_y:
                print(f"   💀 Critical HP, no food → digging down to Y={target_y}")
                result = call_tool("dig_down", {"target_y": target_y, "emergency": True})
                return TickResult(0, f"dig_down(target_y={target_y}) [critical HP, no food]",
                                result.get("message", ""), result.get("success", False))

    # ── Drowning / Water escape ──
    is_in_water = state.get("isInWater", False)
    oxygen_level = state.get("oxygenLevel", 20)

    if is_in_water and oxygen_level <= 12:
        has_turtle_helmet = any(i["name"] == "turtle_helmet" for i in inventory)
        if has_turtle_helmet:
            call_tool("equip_item", {"item_name": "turtle_helmet", "destination": "head"})
        oxygen_threshold = 5 if has_turtle_helmet else 12
        if oxygen_level <= oxygen_threshold:
            label = "drowning!" if oxygen_level <= 5 else "low oxygen"
            print(f"   🌊 Water escape triggered: oxygen={oxygen_level}, inWater={is_in_water}")
            result = call_tool("escape_water", {})
            return TickResult(0, f"escape_water() [{label}]",
                            result.get("message", ""), result.get("success", False))

    # ── Sudden health drop (being attacked without knowing) ──
    if health_delta >= 4 and threat_count > 0:
        # Lost 4+ HP in one tick = definitely under attack
        attacker_type = last_attacker.get("type", "unknown") if last_attacker else "unknown"
        print(f"   ⚔️ Sudden HP drop: -{health_delta:.0f} HP! Attacker: {attacker_type}")
        if rec in ("flee", "avoid") or not has_weapon or health < 10:
            # Outmatched or low HP — shield block briefly then flee
            _try_shield_block(inventory, attacker_type)
            result = call_tool("flee", {})
            return TickResult(0, f"flee() [sudden damage -{health_delta:.0f}HP from {attacker_type}]",
                            result.get("message", ""), result.get("success", False))
        else:
            # We can fight — shield block if ranged, then engage
            _try_shield_block(inventory, attacker_type)
            _equip_best_weapon(inventory)
            result = call_tool("attack_entity", {"entity_type": attacker_type})
            return TickResult(0, f"attack_entity({attacker_type}) [counter-attack, -{health_delta:.0f}HP]",
                            result.get("message", ""), result.get("success", False))

    # ── Actively being attacked (combat state from server) ──
    if is_under_attack and time_since_hit is not None and time_since_hit <= 3:
        attacker_type = last_attacker.get("type", "unknown") if last_attacker else "unknown"
        attacker_dist = last_attacker.get("distance", 99) if last_attacker else 99
        print(f"   ⚔️ Under attack by {attacker_type} ({attacker_dist}m away)! rec={rec}")

        if rec == "flee":
            _try_shield_block(inventory, attacker_type)
            result = call_tool("flee", {})
            return TickResult(0, f"flee() [under attack by {attacker_type}, flee rec]",
                            result.get("message", ""), result.get("success", False))
        elif rec == "avoid" or not has_weapon:
            _try_shield_block(inventory, attacker_type)
            result = call_tool("flee", {})
            return TickResult(0, f"flee() [under attack by {attacker_type}, no weapon/outmatched]",
                            result.get("message", ""), result.get("success", False))
        elif rec in ("fight", "fight_careful"):
            # Shield block if ranged attacker, then fight
            if attacker_type in RANGED_MOBS:
                _try_shield_block(inventory, attacker_type)
            if rec == "fight_careful" and health < 12 and has_food:
                call_tool("eat_food", {})
            _equip_best_weapon(inventory)
            result = call_tool("attack_entity", {"entity_type": attacker_type})
            return TickResult(0, f"attack_entity({attacker_type}) [{rec}, under attack]",
                            result.get("message", ""), result.get("success", False))

    # ── Creeper very close ──
    for td in threat_details:
        if td.get("type") == "creeper" and td.get("distance", 99) < 5:
            # Creepers: always flee (don't dig shelter, too slow)
            print(f"   💥 Creeper at {td.get('distance')}m! Fleeing!")
            result = call_tool("flee", {})
            return TickResult(0, "flee() [creeper close!]", result.get("message", ""), result.get("success", False))

    # ── Warden ──
    for td in threat_details:
        if td.get("type") == "warden":
            result = call_tool("flee", {})
            return TickResult(0, "flee() [warden!]", result.get("message", ""), result.get("success", False))

    # ── Flee recommendation (not yet under attack but dangerous) ──
    if rec == "flee":
        # Try flee first, dig down as fallback
        result = call_tool("flee", {})
        if result.get("success"):
            return TickResult(0, "flee() [threat assessment: flee]", result.get("message", ""), True)
        # Flee failed — dig down to escape
        target_y = min(int(bot_y) - 10, 50)
        target_y = max(target_y, 10)
        if int(bot_y) > target_y:
            print(f"   🏃 Flee failed, digging down to Y={target_y}")
            result = call_tool("dig_down", {"target_y": target_y, "emergency": True})
            return TickResult(0, f"dig_down(target_y={target_y}) [flee failed, dig fallback]",
                            result.get("message", ""), result.get("success", False))

    # ── Fight recommendation (proactive engagement) ──
    if rec in ("fight", "fight_careful") and threat_count > 0:
        closest_hostile = min((td for td in threat_details), key=lambda t: t.get("distance", 99), default=None)
        if closest_hostile and closest_hostile.get("distance", 99) <= 8:
            mob_type = closest_hostile.get("type", "")
            # Don't proactively fight creepers or warden (handled above)
            if mob_type not in ("creeper", "warden"):
                if rec == "fight_careful" and health < 12 and has_food:
                    call_tool("eat_food", {})
                _equip_best_weapon(inventory)
                print(f"   ⚔️ Proactive combat: {mob_type} at {closest_hostile.get('distance')}m (rec={rec})")
                result = call_tool("attack_entity", {"entity_type": mob_type})
                return TickResult(0, f"attack_entity({mob_type}) [proactive {rec}]",
                                result.get("message", ""), result.get("success", False))

    # ── Avoid recommendation (outmatched, disengage) ──
    if rec == "avoid" and threat_count > 0:
        closest_hostile = min((td for td in threat_details), key=lambda t: t.get("distance", 99), default=None)
        if closest_hostile and closest_hostile.get("distance", 99) <= 6:
            # Threat too close while outmatched — flee
            mob_type = closest_hostile.get("type", "")
            print(f"   🏃 Avoid: {mob_type} at {closest_hostile.get('distance')}m, outmatched!")
            result = call_tool("flee", {})
            return TickResult(0, f"flee() [avoid {mob_type}, outmatched]",
                            result.get("message", ""), result.get("success", False))

    # ── Night on surface → dig down to safety ──
    if not is_safe_outside and not is_sheltered:
        # Already underground (Y < 55) = safe, skip
        # On surface at night = dig down to Y~50 where is_sheltered kicks in
        target_y = min(int(bot_y) - 10, 50)  # at least 10 blocks down, or to Y=50
        target_y = max(target_y, 10)  # don't dig below Y=10 (bedrock danger)
        depth = int(bot_y) - target_y
        if depth > 0:
            print(f"   🌙 Night on surface (Y={bot_y:.0f}), digging down to Y={target_y}")
            result = call_tool("dig_down", {"target_y": target_y, "emergency": True})
            return TickResult(0, f"dig_down(target_y={target_y}) [night evasion]",
                            result.get("message", ""), result.get("success", False))

    # ── Dusk warning → dig down early ──
    if time_phase == "dusk" and not is_sheltered:
        target_y = min(int(bot_y) - 10, 50)
        target_y = max(target_y, 10)
        depth = int(bot_y) - target_y
        if depth > 0:
            print(f"   🌅 Dusk approaching (Y={bot_y:.0f}), digging down to Y={target_y}")
            result = call_tool("dig_down", {"target_y": target_y, "emergency": True})
            return TickResult(0, f"dig_down(target_y={target_y}) [dusk evasion]",
                            result.get("message", ""), result.get("success", False))

    # ── Very hungry ──
    if food < 5 and has_food:
        result = call_tool("eat_food", {})
        return TickResult(0, "eat_food() [hungry]", result.get("message", ""), result.get("success", False))

    # ── Mob inside shelter ──
    if is_sheltered and threat_count > 0:
        closest = min((td["distance"] for td in threat_details), default=99)
        if closest <= 5:
            if has_weapon:
                mob_type = threat_details[0].get("type", "")
                _equip_best_weapon(inventory)
                result = call_tool("attack_entity", {"entity_type": mob_type})
                return TickResult(0, f"attack_entity({mob_type}) [mob in shelter]",
                                result.get("message", ""), result.get("success", False))

    # ── Inventory almost full ──
    empty_slots = state.get("emptySlots", 36)
    if empty_slots <= 3:
        nearby_blocks = state.get("nearbyBlocks", [])
        chest_nearby = any(b in ("chest", "trapped_chest", "barrel") for b in nearby_blocks)
        if chest_nearby:
            print(f"   📦 Inventory nearly full ({empty_slots} slots), storing in nearby chest")
            result = call_tool("store_items", {})
            return TickResult(0, "store_items() [inventory full]",
                            result.get("message", ""), result.get("success", False))
        else:
            print(f"   ⚠️ Inventory nearly full ({empty_slots} slots), no chest nearby")

    # ── Nearby drops to collect (lowest priority) ──
    if rec == "safe" and threat_count == 0:
        drop_count = _get_pending_drops()
        if drop_count > 0:
            print(f"   📥 {drop_count} drops nearby, collecting...")
            result = call_tool("collect_drops", {})
            return TickResult(0, f"collect_drops() [{drop_count} drops]",
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
        self._explored_caves: set = set()  # chunk-level keys of visited caves

    def _estimate_chain_timeout(self, chain_name: str, steps: list[dict]) -> float:
        """Estimate timeout based on chain complexity."""
        base = 120  # 2 min base
        for step in steps:
            tool = step.get("tool", "")
            if tool == "mine_block":
                count = int(step.get("args", {}).get("count", 1))
                base += count * 10  # 10s per block (includes search time)
            elif tool == "smelt_item":
                count = int(step.get("args", {}).get("count", 1))
                base += count * 12  # smelting is slow
            elif tool in ("dig_down", "dig_tunnel", "build_shelter"):
                base += 120
            elif tool in ("craft_item", "place_block", "equip_item"):
                base += 15
            else:
                base += 30
        return max(300, min(base, 900))  # clamp: 5 min ~ 15 min

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
            all_chains = list_available_chains()
            return (f"Unknown chain: '{chain_name}'. "
                    f"'{chain_name}' is NOT a chain name — it might be an action name. "
                    f"Valid chains: {', '.join(all_chains)}")

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
            timeout=self._estimate_chain_timeout(chain_name, steps),
        )
        self._in_search_mode = False
        self._explored_caves = set()  # reset cave tracking for new chain

        # Auto-equip best gear at chain start
        inv = get_inventory_counts()
        self._auto_equip_best_gear(inv)

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
            # Track custom chain failure
            from chain_library import _get_custom_lib
            custom_lib = _get_custom_lib()
            if name in custom_lib.chains:
                custom_lib.record_failure(name)
            elapsed = int(time.time() - chain.started_at)
            return TickResult(1, "timeout", f"Chain {name} timed out", False,
                            needs_llm=True, llm_context=f"Chain '{name}' timed out after {elapsed}s (limit: {int(chain.timeout)}s).")

        # All steps done
        if chain.is_done:
            name = chain.chain_name
            self.active_chain = None
            # Track custom chain success
            from chain_library import _get_custom_lib
            custom_lib = _get_custom_lib()
            if name in custom_lib.chains:
                custom_lib.record_success(name)
            # Auto-equip best gear after chain completion
            inv = get_inventory_counts()
            self._auto_equip_best_gear(inv)
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

        # ── Environmental awareness (Layer 1) ──
        mid_chain_state = get_bot_state()

        # Water: escape if drowning
        if mid_chain_state.get("isInWater") and mid_chain_state.get("oxygenLevel", 20) < 10:
            print(f"   🌊 Underwater during chain (oxygen={mid_chain_state.get('oxygenLevel')}), escaping first...")
            result = call_tool("escape_water", {})
            return TickResult(1, "escape_water() [mid-chain]",
                            result.get("message", ""), result.get("success", False))

        # Combat: if being attacked during chain, let instinct handle it next tick
        # (escalate to LLM if ongoing combat persists)
        mid_combat = mid_chain_state.get("combat", {})
        if mid_combat.get("isUnderAttack") and mid_combat.get("timeSinceHit", 99) <= 2:
            attacker = mid_combat.get("lastAttacker", {})
            attacker_type = attacker.get("type", "unknown") if attacker else "unknown"
            print(f"   ⚔️ Under attack during chain by {attacker_type}! Pausing chain for combat response.")
            # Don't execute the step — return and let check_instinct handle it next tick
            return TickResult(1, f"combat_interrupt [attacked by {attacker_type}]",
                            f"Chain paused: under attack by {attacker_type}. Instinct will handle combat next tick.",
                            False)

        # ── Stuck detection (only for movement/explore actions, NOT mining) ──
        tool_check = step.get("tool", "")
        if tool_check in ("move_to", "explore", "find_block") and _check_stuck(mid_chain_state):
            print(f"   🔄 Stuck detected during {tool_check}! Scanning surroundings...")
            chain.retry_count += 1
            unstick_result = _try_unstick()
            if unstick_result:
                return unstick_result

        # ── Execute step ──
        tool_name = step["tool"]
        tool_args = step["args"]
        step_type = step.get("type", "action")

        # ── Prerequisite tool check (Layer 1 logic) ──
        # Before mining, ensure we have the right pickaxe
        prereq_result = self._check_and_inject_prerequisites(step, inventory, chain)
        if prereq_result:
            return prereq_result

        # Adjust mine count for search steps (only mine what's still needed)
        effective_args = tool_args
        if step_type == "search" and tool_name == "mine_block":
            target = step.get("search_target", tool_args.get("block_type", ""))
            drop = DROP_MAP.get(target, target)
            have = inventory.get(drop, 0)
            need = step.get("skip_if", {}).get(drop, int(tool_args.get("count", 1)))
            remaining = max(1, need - have)
            if remaining < int(tool_args.get("count", 1)):
                effective_args = dict(tool_args)
                effective_args["count"] = remaining

        print(f"   🔧 [{step_type}] {tool_name}({json.dumps(effective_args)})")

        # Auto-equip best tool before mining
        if tool_name == "mine_block":
            self._auto_equip_for_mining(effective_args.get("block_type", ""), inventory)

        # Auto-equip best weapon before combat
        if tool_name == "attack_entity":
            for sword in reversed(self.SWORD_TIERS):
                if inventory.get(sword, 0) > 0:
                    call_tool("equip_item", {"item_name": sword})
                    break

        result = call_tool(tool_name, effective_args)
        success = result.get("success", False)
        message = result.get("message", "")

        if success:
            print(f"   ✅ {message[:100]}")

            # Record search success for experience memory
            if step_type == "search":
                target = step.get("search_target", tool_args.get("block_type", ""))
                if target:
                    state = get_bot_state()
                    pos = state.get("position", {})
                    location = {"x": float(pos.get("x", 0)), "y": float(pos.get("y", 0)),
                               "z": float(pos.get("z", 0))}
                    self.experience.record_search_success(target, "direct_nearby", location)

                # ── Count check: do we have enough? ──
                new_inv = get_inventory_counts()
                if not self._should_skip(step, new_inv):
                    # Not enough — keep searching for more
                    drop = DROP_MAP.get(target, target)
                    have = new_inv.get(drop, 0)
                    need = step.get("skip_if", {}).get(drop, int(tool_args.get("count", 1)))
                    have_before = inventory.get(drop, 0)

                    if have <= have_before and tool_name == "mine_block":
                        # Items may still be on the ground — wait and re-check
                        # Tree blocks drop items from height → need longer wait
                        is_tree = target in ("oak_log", "birch_log", "spruce_log",
                                             "jungle_log", "acacia_log", "dark_oak_log",
                                             "mangrove_log", "cherry_log")
                        wait_time = 3.0 if is_tree else 1.5
                        max_retries = 3 if is_tree else 1

                        for retry_i in range(max_retries):
                            time.sleep(wait_time if retry_i == 0 else 1.5)
                            retry_inv = get_inventory_counts()
                            have = retry_inv.get(drop, 0)
                            if have > have_before:
                                break

                        if have > have_before:
                            # Items picked up after delay (common with trees/falling items)
                            print(f"   📊 Have {have}/{need} {drop} (delayed pickup)")
                            if self._should_skip(step, retry_inv):
                                chain.advance()
                                return TickResult(1, f"{tool_name}({tool_args})", message, True)
                            chain.search_retry_idx = 0
                            return TickResult(1, f"partial_mine:{target}",
                                            f"Mined some {target} but need more {drop} ({have}/{need})", True)

                        # Genuinely stale — treat as search failure to trigger explore/dig strategies
                        chain.search_retry_idx += 1
                        print(f"   ⚠️ Mined {target} but {drop} didn't increase ({have_before}→{have})! Triggering search strategies (idx={chain.search_retry_idx})")
                        stale_msg = f"Mining {target} not producing {drop} ({have}/{need}). Area depleted."
                        return self._handle_search_failure(step, stale_msg, inventory)

                    print(f"   📊 Have {have}/{need} {drop} — searching for more")
                    chain.search_retry_idx = 0  # reset search to try nearby first
                    return TickResult(1, f"partial_mine:{target}",
                                    f"Mined some {target} but need more {drop} ({have}/{need})", True)

            # Pick up crafting table after use — carry it instead of leaving behind
            if tool_name == "craft_item" and "at crafting table" in message:
                next_idx = chain.current_idx + 1
                next_step = chain.steps[next_idx] if next_idx < len(chain.steps) else None
                if not next_step or next_step.get("tool") != "craft_item":
                    pickup = call_tool("mine_block", {"block_type": "crafting_table", "count": 1})
                    if pickup.get("success"):
                        print(f"   📦 Picked up crafting_table to carry")

            # Pick up furnace after smelting — carry it instead of leaving behind
            if tool_name == "smelt_item" and success:
                next_idx = chain.current_idx + 1
                next_step = chain.steps[next_idx] if next_idx < len(chain.steps) else None
                if not next_step or next_step.get("tool") != "smelt_item":
                    pickup = call_tool("mine_block", {"block_type": "furnace", "count": 1})
                    if pickup.get("success"):
                        print(f"   📦 Picked up furnace to carry")

            chain.advance()
            return TickResult(1, f"{tool_name}({tool_args})", message, True)

        # ── Handle failure ──
        print(f"   ❌ {message[:100]}")

        if step_type == "search":
            # Check if we made progress despite failure (partial mine before timeout/abort)
            if tool_name == "mine_block":
                new_inv = get_inventory_counts()
                target = step.get("search_target", tool_args.get("block_type", ""))
                drop = DROP_MAP.get(target, target)
                have_now = new_inv.get(drop, 0)
                had_before = inventory.get(drop, 0)

                if have_now > had_before:
                    # We DID mine some ore — don't explore elsewhere, stay and retry
                    need = step.get("skip_if", {}).get(drop, int(tool_args.get("count", 1)))
                    print(f"   📊 Partial progress despite error: {had_before}→{have_now}/{need} {drop} — retrying here")
                    if self._should_skip(step, new_inv):
                        chain.advance()
                        return TickResult(1, f"{tool_name}({tool_args})", message, True)
                    chain.search_retry_idx = 0  # reset search — ore IS here
                    return TickResult(1, f"partial_mine:{target}",
                                    f"Mined some {target} ({have_now}/{need}), retrying nearby", True)

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

    # Equipment tier order (higher index = better)
    SWORD_TIERS = ["wooden_sword", "stone_sword", "iron_sword", "diamond_sword"]
    HELMET_TIERS = ["leather_helmet", "chainmail_helmet", "iron_helmet", "diamond_helmet"]
    CHESTPLATE_TIERS = ["leather_chestplate", "chainmail_chestplate", "iron_chestplate", "diamond_chestplate"]
    LEGGINGS_TIERS = ["leather_leggings", "chainmail_leggings", "iron_leggings", "diamond_leggings"]
    BOOTS_TIERS = ["leather_boots", "chainmail_boots", "iron_boots", "diamond_boots"]

    # Slot → tier list mapping
    GEAR_SLOTS = {
        "head": HELMET_TIERS,
        "torso": CHESTPLATE_TIERS,
        "legs": LEGGINGS_TIERS,
        "feet": BOOTS_TIERS,
    }

    def _auto_equip_best_gear(self, inventory: dict):
        """Equip the best available gear in all slots (armor, weapon, shield)."""
        equipped_any = False

        # ── Best sword in hand ──
        for sword in reversed(self.SWORD_TIERS):
            if inventory.get(sword, 0) > 0:
                call_tool("equip_item", {"item_name": sword})
                print(f"   ⚔️ Auto-equipped {sword}")
                equipped_any = True
                break

        # ── Best armor in each slot ──
        for slot, tiers in self.GEAR_SLOTS.items():
            for armor in reversed(tiers):
                if inventory.get(armor, 0) > 0:
                    call_tool("equip_item", {"item_name": armor, "destination": slot})
                    print(f"   🛡️ Auto-equipped {armor} → {slot}")
                    equipped_any = True
                    break

        # ── Shield in off-hand ──
        if inventory.get("shield", 0) > 0:
            call_tool("equip_item", {"item_name": "shield", "destination": "off-hand"})
            print(f"   🛡️ Auto-equipped shield → off-hand")
            equipped_any = True

        if equipped_any:
            print(f"   ✅ Best gear equipped")

    def _get_tool_durability(self, tool_name: str) -> int:
        """Get durability percent of a tool from /state. Returns 100 if unknown."""
        try:
            state = get_bot_state()
            for item in state.get("inventory", []):
                if item.get("name") == tool_name and item.get("durability"):
                    return item["durability"].get("percent", 100)
        except Exception:
            pass
        return 100

    def _auto_equip_for_mining(self, block_type: str, inventory: dict):
        """Equip the best available pickaxe before mining.
        Skips tools with <10% durability to avoid breaking them."""
        # Find best pickaxe in inventory (skip nearly broken ones)
        for tool in reversed(self.TOOL_TIERS):
            if inventory.get(tool, 0) > 0:
                durability = self._get_tool_durability(tool)
                if durability < 10:
                    print(f"   ⚠️ {tool} almost broken ({durability}%), skipping")
                    continue
                result = call_tool("equip_item", {"item_name": tool})
                if result.get("success"):
                    print(f"   ⛏️ Auto-equipped {tool} for mining {block_type}")
                else:
                    print(f"   ⚠️ Failed to equip {tool}: {result.get('message', '')}")
                return
        # No pickaxe with enough durability — try equip any axe or shovel
        for tool_type in ["_axe", "_shovel"]:
            for prefix in ["diamond", "iron", "stone", "wooden"]:
                name = f"{prefix}{tool_type}"
                if inventory.get(name, 0) > 0:
                    result = call_tool("equip_item", {"item_name": name})
                    if result.get("success"):
                        print(f"   ⛏️ Auto-equipped {name} (fallback) for {block_type}")
                    else:
                        print(f"   ⚠️ Failed to equip {name}: {result.get('message', '')}")
                    return
        # No tool found at all
        pickaxes = [k for k in inventory if "pickaxe" in k]
        print(f"   ⚠️ No mining tool for {block_type}! Pickaxes in inv: {pickaxes}")

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

    # Persistent search: extra dynamic attempts after static strategies
    MAX_PERSISTENT_SEARCH = 10

    # Ore types that need underground search (optimal Y level)
    ORE_SEARCH_Y = {
        "iron_ore": 16, "deepslate_iron_ore": 0,
        "coal_ore": 48, "deepslate_coal_ore": 0,
        "diamond_ore": -58, "deepslate_diamond_ore": -58,
        "gold_ore": -16, "deepslate_gold_ore": -16,
        "copper_ore": 48, "lapis_ore": 0,
        "redstone_ore": -32, "emerald_ore": 16,
    }

    def _scan_for_caves(self) -> Optional[dict]:
        """Check for nearby caves via /scan_caves. Returns best cave or None."""
        try:
            r = requests.get(f"{BOT_API}/scan_caves", params={"radius": 32}, timeout=5)
            data = r.json()
            caves = data.get("caves", [])
            if caves:
                return caves[0]  # largest/closest cave
        except Exception:
            pass
        return None

    def _get_ore_search_action(self, target: str, persistent_idx: int) -> tuple:
        """Get the next search action for underground ore.
        Cave-first strategy: scan for caves before every action.
        Branch mining / dig_down may expose new caves → re-scan finds them.
        Remembered caves from spatial_memory are revisited when no new cave is found.

        Phase 2 search order (ore):
          Every step: 1) scan new caves  2) check remembered caves  3) fallback strategy
          0: Y-check → dig_down or branch_mine north
          1-3: branch_mine east/south/west
          4: explore(50) → move to new area
          5: dig_down to optimal Y (fresh location)
          6-7: branch_mine north/east (longer)
          8: explore(100) → last resort before LLM
        """
        from memory_tools import memory as spatial_mem

        target_y = self.ORE_SEARCH_Y.get(target, 16)
        directions = ["north", "east", "south", "west"]
        state = get_bot_state()
        pos = state.get("position", {})
        current_y = float(pos.get("y", 64))
        bot_pos = (float(pos.get("x", 0)), current_y, float(pos.get("z", 0)))

        # ── Step 1: Scan for NEW caves (branch_mine may have exposed new ones) ──
        cave = self._scan_for_caves()
        if cave and cave.get("size", 0) >= 5:
            center = cave["center"]
            cave_key = (int(center["x"]) // 16, int(center["y"]) // 16, int(center["z"]) // 16)
            if cave_key not in self._explored_caves:
                self._explored_caves.add(cave_key)
                # Save to persistent memory for future searches
                spatial_mem.save_cave(center["x"], center["y"], center["z"], cave.get("size", 0))
                print(f"   🕳️ New cave detected! size={cave['size']}, dist={cave['distance']}m — exploring cave first")
                return ("move_to", {"x": center["x"], "y": center["y"], "z": center["z"]})

        # ── Step 2: Check remembered caves from spatial_memory ──
        known_caves = spatial_mem.get_caves_sorted(bot_pos)
        for kc in known_caves:
            cave_key = (int(kc["x"]) // 16, int(kc["y"]) // 16, int(kc["z"]) // 16)
            if cave_key not in self._explored_caves and kc["dist"] < 200:
                self._explored_caves.add(cave_key)
                print(f"   🕳️ Remembered cave '{kc['name']}' at ({kc['x']:.0f}, {kc['y']:.0f}, {kc['z']:.0f}) — "
                      f"{kc['dist']:.0f}m away, revisiting")
                return ("move_to", {"x": kc["x"], "y": kc["y"], "z": kc["z"]})

        # ── Step 3: No caves available — fallback mining/exploration ──
        y_close = abs(current_y - target_y) < 15

        if persistent_idx == 0:
            if y_close:
                print(f"   📍 Already near optimal Y (current={int(current_y)}, target={target_y}) — branch mining")
                return ("branch_mine", {"direction": "north", "main_length": 15,
                                        "branch_length": 4, "branch_spacing": 3})
            else:
                print(f"   📍 Need to reach Y={target_y} (current={int(current_y)}) — digging down")
                return ("dig_down", {"target_y": target_y})

        elif persistent_idx <= 3:
            direction = directions[persistent_idx % 4]
            return ("branch_mine", {"direction": direction, "main_length": 15,
                                    "branch_length": 4, "branch_spacing": 3})

        elif persistent_idx == 4:
            return ("explore", {"distance": 50})

        elif persistent_idx == 5:
            return ("dig_down", {"target_y": target_y})

        elif persistent_idx <= 7:
            direction = directions[(persistent_idx - 6) % 4]
            return ("branch_mine", {"direction": direction, "main_length": 25,
                                    "branch_length": 5, "branch_spacing": 3})

        else:
            return ("explore", {"distance": 100})

    def _handle_search_failure(self, step: dict, error_msg: str,
                                inventory: dict) -> TickResult:
        """Handle failure of a search-type step. Tries search strategy,
        then persistent exploration, then LLM escalation."""
        chain = self.active_chain
        target = step.get("search_target", step["args"].get("block_type", ""))

        # Check experience memory first — scout nearest known location
        MAX_MEMORY_DISTANCE = 150  # blocks — farther than this, better to search locally
        if chain.search_retry_idx == 0:
            bot_state = get_bot_state()
            bot_pos = bot_state.get("position", {})
            hint = self.experience.get_search_hint(target, bot_position=bot_pos)
            if hint and hint.get("location"):
                loc = hint["location"]
                dist = hint.get("distance", 0)
                dist_str = f", {dist}m away" if dist else ""

                # Skip if too far — local search is more efficient
                if dist and dist > MAX_MEMORY_DISTANCE:
                    print(f"   🧠 Experience: {target} was found at ({loc.get('x'):.0f}, {loc.get('y'):.0f}, {loc.get('z'):.0f}){dist_str} — too far, searching locally instead")
                else:
                    print(f"   🧠 Experience: {target} was found at ({loc.get('x'):.0f}, {loc.get('y'):.0f}, {loc.get('z'):.0f}){dist_str} — scouting area")
                    call_tool("move_to", {"x": loc["x"], "y": loc["y"], "z": loc["z"]})

                    # Scout first: find_block to verify resources still exist in this area
                    scout_result = call_tool("find_block", {"block_type": target, "max_distance": 32})
                    if not scout_result.get("success"):
                        # Resource depleted at this location — remove from memory and fall through
                        print(f"   🧠 No {target} found at remembered location — memory outdated, removing")
                        self.experience.remove_location(target, loc)
                    else:
                        # Resource confirmed — now mine it
                        result = call_tool(step["tool"], step["args"])
                        if result.get("success"):
                            print(f"   ✅ Found via memory! {result.get('message', '')[:80]}")
                            self.experience.record_search_success(target, "memory_location", loc)
                            # Count check before advancing
                            new_inv = get_inventory_counts()
                            if self._should_skip(step, new_inv):
                                chain.advance()
                                return TickResult(1, f"search:{target} via memory", result.get("message", ""), True)
                            else:
                                drop = DROP_MAP.get(target, target)
                                have = new_inv.get(drop, 0)
                                need = step.get("skip_if", {}).get(drop, int(step["args"].get("count", 1)))
                                print(f"   📊 Have {have}/{need} {drop} — searching for more")
                                return TickResult(1, f"partial_mine:{target} via memory",
                                                f"Mined some via memory but need more ({have}/{need})", True)

        # ── Phase 1: Static search strategies ──
        strategies = get_search_strategy(target)
        if chain.search_retry_idx < len(strategies):
            strategy = strategies[chain.search_retry_idx]
            action_type, action_args = strategy

            print(f"   🔍 Search [{chain.search_retry_idx+1}/{len(strategies)}]: {action_type}({action_args})")

            if action_type == "check_memory":
                try:
                    from memory_tools import memory
                    nearest = memory.find_nearest(action_args.get("category", "resource"))
                    if "No saved locations" not in nearest and "Cannot" not in nearest:
                        print(f"   📍 Memory: {nearest[:80]}")
                except:
                    pass
                chain.search_retry_idx += 1
                return TickResult(1, f"check_memory({action_args})", "Checked memory", True)

            result = call_tool(action_type, action_args)
            search_msg = result.get("message", "")
            chain.search_retry_idx += 1

            # If find_block returned coordinates, move to the found location first
            if action_type == "find_block" and result.get("success"):
                block_data = result.get("block", {})
                pos = block_data.get("position")
                if pos:
                    print(f"   📍 Moving to found {target} at ({pos.get('x')}, {pos.get('y')}, {pos.get('z')})")
                    call_tool("move_to", {"x": pos["x"], "y": pos["y"], "z": pos["z"]})
                else:
                    # Fallback: parse from message "Found X at (x, y, z)"
                    import re
                    match = re.search(r'at \((-?\d+),\s*(-?\d+),\s*(-?\d+)\)', search_msg)
                    if match:
                        fx, fy, fz = int(match.group(1)), int(match.group(2)), int(match.group(3))
                        print(f"   📍 Moving to found {target} at ({fx}, {fy}, {fz})")
                        call_tool("move_to", {"x": fx, "y": fy, "z": fz})

            # Try find+mine after search action (now closer to target)
            found = self._try_find_and_mine(step, target)
            if found:
                return found

            return TickResult(1, f"search:{action_type}({action_args})", search_msg, True)

        # ── Phase 2: Smart persistent search ──
        # Ore search: Y-aware dig + branch_mine + rescan after each move
        # Surface search: explore + rescan
        persistent_idx = chain.search_retry_idx - len(strategies)
        if persistent_idx < self.MAX_PERSISTENT_SEARCH:
            chain.search_retry_idx += 1
            is_ore = target in self.ORE_SEARCH_Y

            if is_ore:
                action = self._get_ore_search_action(target, persistent_idx)
            else:
                # Surface resource: explore + rescan
                distance = 30 + (persistent_idx * 20)  # 30, 50, 70, 90...
                action = ("explore", {"distance": min(distance, 120)})

            action_type, action_args = action
            print(f"   🔍 Persistent search [{persistent_idx+1}/{self.MAX_PERSISTENT_SEARCH}]: "
                  f"{action_type}({action_args})")

            result = call_tool(action_type, action_args)

            # Check for wild chests after exploring new areas (dungeons/ruins)
            self._try_loot_nearby_chests()

            # Always rescan after moving/digging (new chunks loaded)
            found = self._try_find_and_mine(step, target)
            if found:
                return found

            return TickResult(1, f"persist:{action_type}({action_args})",
                            result.get("message", ""), True)

        # ── Phase 3: All search exhausted → cancel chain, escalate to LLM ──
        total_attempts = len(strategies) + self.MAX_PERSISTENT_SEARCH
        chain_name = chain.chain_name
        self.cancel_chain(f"search_exhausted:{target}")
        return TickResult(1, f"search:{target} exhausted", error_msg, False,
                        needs_llm=True,
                        llm_context=f"Cannot find {target} after {total_attempts} search attempts "
                                   f"(static strategies + persistent exploration).\n"
                                   f"Tried: dig_down, dig_tunnel (all directions), explore (various distances).\n"
                                   f"Current inventory: {json.dumps(dict(list(inventory.items())[:15]))}.\n"
                                   f"The chain for this task has failed. Analyze what went wrong and "
                                   f"try a DIFFERENT approach or chain. Maybe gather prerequisites first, "
                                   f"or explore a completely new area.")

    def _try_find_and_mine(self, step: dict, target: str) -> Optional[TickResult]:
        """After a search action, check if the target is now findable and mine it.
        Only advances the chain if target count is met."""
        find_result = call_tool("find_block", {"block_type": target, "max_distance": 64})
        if find_result.get("success"):
            # Calculate remaining count needed
            tool_args = step["args"]
            drop = DROP_MAP.get(target, target)
            inv = get_inventory_counts()
            have = inv.get(drop, 0)
            need = step.get("skip_if", {}).get(drop, int(tool_args.get("count", 1)))
            remaining = max(1, need - have)

            mine_args = dict(tool_args)
            mine_args["count"] = remaining
            original_result = call_tool(step["tool"], mine_args)
            if original_result.get("success"):
                state = get_bot_state()
                pos = state.get("position", {})
                location = {"x": float(pos.get("x", 0)), "y": float(pos.get("y", 0)),
                           "z": float(pos.get("z", 0))}
                self.experience.record_search_success(target, "persistent_search", location)

                # Count check — advance only if enough
                new_inv = get_inventory_counts()
                if self._should_skip(step, new_inv):
                    self.active_chain.advance()
                    return TickResult(1, f"{step['tool']} (found after search)",
                                    original_result.get("message", ""), True)
                else:
                    # Mined some but not enough — keep searching
                    have_now = new_inv.get(drop, 0)
                    print(f"   📊 Have {have_now}/{need} {drop} — searching for more")
                    return TickResult(1, f"partial_mine:{target} (after search)",
                                    f"Mined some but need more {drop} ({have_now}/{need})", True)
        return None

    # Valuable items worth looting from dungeon/ruin chests
    LOOT_VALUABLE = {
        "diamond", "emerald", "gold_ingot", "iron_ingot", "raw_gold", "raw_iron",
        "lapis_lazuli", "redstone", "coal", "enchanted_book", "name_tag", "saddle",
        "golden_apple", "enchanted_golden_apple", "music_disc", "heart_of_the_sea",
        "diamond_sword", "diamond_pickaxe", "diamond_axe", "diamond_shovel",
        "diamond_helmet", "diamond_chestplate", "diamond_leggings", "diamond_boots",
        "iron_sword", "iron_pickaxe", "iron_axe", "iron_shovel",
        "iron_helmet", "iron_chestplate", "iron_leggings", "iron_boots",
        "golden_carrot", "bread", "cooked_beef", "cooked_porkchop",
        "bucket", "water_bucket", "shield", "bow", "crossbow", "arrow",
    }

    def _try_loot_nearby_chests(self) -> bool:
        """Check for wild chests nearby (dungeons/ruins) and loot valuables.
        Returns True if any chest was looted."""
        from memory_tools import memory as spatial_mem

        looted_any = False
        for block_type in ("chest", "trapped_chest"):
            find_result = call_tool("find_block", {"block_type": block_type, "max_distance": 16})
            if not find_result.get("success"):
                continue

            # Parse position from result
            msg = find_result.get("message", "")
            import re
            match = re.search(r'at \((-?\d+),\s*(-?\d+),\s*(-?\d+)\)', msg)
            if not match:
                continue
            cx, cy, cz = int(match.group(1)), int(match.group(2)), int(match.group(3))

            # Check if this is a bot-placed chest (in spatial_memory) → skip
            is_own = False
            for name, wp in spatial_mem.waypoints.items():
                if wp.category in ("storage", "looted_chest") and wp.distance_to(cx, cy, cz) < 5:
                    is_own = True
                    break
            if is_own:
                continue

            # Wild chest found! Move to it and loot
            print(f"   🎁 Wild {block_type} at ({cx}, {cy}, {cz}) — looting!")
            call_tool("move_to", {"x": cx, "y": cy, "z": cz})

            # Open chest to see contents
            chest_result = call_tool("open_chest", {})
            if not chest_result.get("success"):
                print(f"   ⚠️ Can't open chest: {chest_result.get('message', '')[:60]}")
                # Remember so we don't keep trying
                spatial_mem.save_location(f"looted_{cx}_{cz}", "looted_chest", cx, cy, cz)
                continue

            items = chest_result.get("items", [])
            if not items:
                print(f"   📦 Chest is empty")
                spatial_mem.save_location(f"looted_{cx}_{cz}", "looted_chest", cx, cy, cz)
                continue

            # Check inventory space
            state = get_bot_state()
            empty_slots = state.get("emptySlots", 0)
            if empty_slots <= 3:
                print(f"   📦 Inventory too full to loot ({empty_slots} slots)")
                continue

            # Loot valuable items
            looted_items = []
            for item in items:
                name = item.get("name", "")
                count = item.get("count", 1)
                if name in self.LOOT_VALUABLE:
                    result = call_tool("retrieve_items", {"item_name": name, "count": count})
                    if result.get("success"):
                        looted_items.append(f"{name}×{count}")

            if looted_items:
                print(f"   🎁 Looted: {', '.join(looted_items)}")
                looted_any = True
            else:
                print(f"   📦 No valuables in chest ({len(items)} junk items)")

            # Remember this chest as looted
            spatial_mem.save_location(f"looted_{cx}_{cz}", "looted_chest", cx, cy, cz)

        return looted_any

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

        # ── "missing materials" for craft_item → detect prerequisite and inject IMMEDIATELY ──
        # (before universal escalation — don't waste 3 retries on a problem that can't be retried)
        if step["tool"] == "craft_item" and "missing materials" in error_lower:
            item_name = step["args"].get("item_name", "")
            # Detect diamond items → need mine_diamonds first
            if "diamond" in item_name and inventory.get("diamond", 0) < 2:
                prereq_steps = get_chain("mine_diamonds")
                if prereq_steps:
                    print(f"   🔧 Missing diamonds for {item_name}! Injecting mine_diamonds prerequisite")
                    chain.steps = chain.steps[:chain.current_idx] + prereq_steps + chain.steps[chain.current_idx:]
                    chain.retry_count = 0
                    return TickResult(1, "inject_prereq:mine_diamonds",
                                    f"Need diamonds for {item_name}, injecting mine_diamonds", True)
            # Detect iron items → need iron mining first
            if "iron" in item_name and inventory.get("iron_ingot", 0) < 1:
                for prereq_name in ["make_iron_pickaxe"]:
                    prereq_steps = get_chain(prereq_name)
                    if prereq_steps:
                        print(f"   🔧 Missing iron for {item_name}! Injecting {prereq_name} prerequisite")
                        chain.steps = chain.steps[:chain.current_idx] + prereq_steps + chain.steps[chain.current_idx:]
                        chain.retry_count = 0
                        return TickResult(1, f"inject_prereq:{prereq_name}",
                                        f"Need iron for {item_name}, injecting {prereq_name}", True)
            # Other missing materials → escalate immediately with clear message (no retries)
            chain_name = chain.chain_name
            self.cancel_chain(f"missing_materials:{item_name}")
            return TickResult(1, f"escalate:missing_materials", error_msg, False,
                            needs_llm=True,
                            llm_context=f"Chain '{chain_name}' FAILED: missing crafting materials.\n"
                                       f"Tried to craft: {item_name}\n"
                                       f"Error: {error_msg}\n"
                                       f"Current inventory: {json.dumps(dict(list(inventory.items())[:20]))}\n"
                                       f"ACTION REQUIRED: You MUST mine the raw materials first.\n"
                                       f"Use choose_next_chain with the correct MINING chain (e.g., mine_diamonds, get_wood, mine_stone).\n"
                                       f"Do NOT choose a crafting chain — the bot does not have the materials yet.")

        # ═══ UNIVERSAL ESCALATION RULE ═══
        # Any step failing 3+ times → CANCEL entire chain, ask LLM
        # (Don't advance/skip — that causes chains to "complete" with nothing done)
        if chain.retry_count > 3:
            chain_name = chain.chain_name
            failed_step = f"{step['tool']}({step['args']})"
            step_idx = chain.current_idx
            total_steps = len(chain.steps)
            self.cancel_chain(f"step_failed:{step['tool']}")
            return TickResult(1, f"escalate:{step['tool']}", error_msg, False,
                            needs_llm=True,
                            llm_context=f"Chain '{chain_name}' CANCELLED because step {step_idx+1}/{total_steps} kept failing.\n"
                                       f"Failed step: {failed_step}\n"
                                       f"Error: {error_msg}\n"
                                       f"Inventory: {json.dumps(dict(list(inventory.items())[:20]))}\n"
                                       f"You MUST choose the correct prerequisite chain first.\n"
                                       f"Example: to craft diamond gear, first ensure you have enough diamonds (mine_diamonds), iron tools (make_iron_pickaxe), etc.\n"
                                       f"Do NOT restart the same chain without fixing the missing prerequisites.")

        # ── "No crafting table nearby" → resolve, then RETRY same step ──
        # (exclude "At crafting table but missing materials" — that's handled above)
        if ("crafting table" in error_lower or "crafting_table" in error_lower) and "missing materials" not in error_lower:
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
            chain_name = chain.chain_name
            self.cancel_chain(f"move_blocked:{step['tool']}")
            return TickResult(1, f"escalate:{step['tool']}", error_msg, False,
                            needs_llm=True,
                            llm_context=f"Chain '{chain_name}' CANCELLED — movement blocked even after mining obstacle.\n"
                                       f"Step: {step['tool']}({step['args']})\n"
                                       f"Error: {error_msg}\n"
                                       f"Bot needs a new route or different approach.\n"
                                       f"Inventory: {json.dumps(dict(list(inventory.items())[:15]))}")

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
            # Dig adjacent block to create space (stone underground, dirt on surface)
            mine_result = call_tool("mine_block", {"block_type": "stone", "count": 1})
            if not mine_result.get("success"):
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
            place_result = call_tool("place_block", {"block_name": "furnace"})
            if place_result.get("success"):
                return "Placed furnace from inventory"
            # Place failed — clear space and retry
            print(f"   ⚠️ furnace place failed: {place_result.get('message', '')[:80]}")
            mine_result = call_tool("mine_block", {"block_type": "stone", "count": 1})
            if not mine_result.get("success"):
                call_tool("mine_block", {"block_type": "dirt", "count": 1})
            place_result = call_tool("place_block", {"block_name": "furnace"})
            if place_result.get("success"):
                return "Placed furnace (after clearing space)"

        find_result = call_tool("find_block", {"block_type": "furnace", "max_distance": 32})
        if find_result.get("success"):
            return "Found furnace nearby"

        if inventory.get("cobblestone", 0) >= 8:
            # Need crafting table for furnace
            ct_nearby = call_tool("find_block", {"block_type": "crafting_table", "max_distance": 32})
            if not ct_nearby.get("success"):
                self._ensure_crafting_table(inventory)
            call_tool("craft_item", {"item_name": "furnace"})
            place_result = call_tool("place_block", {"block_name": "furnace"})
            if not place_result.get("success"):
                # Clear space and retry
                mine_result = call_tool("mine_block", {"block_type": "stone", "count": 1})
                if not mine_result.get("success"):
                    call_tool("mine_block", {"block_type": "dirt", "count": 1})
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