"""
Chain Library — Hardcoded action chains for Minecraft tasks.

Each chain is a list of steps. Each step has:
  - tool: API tool name
  - args: arguments for the tool
  - type: "craft" | "gather" | "search" | "place" | "action"
  - skip_if: {item_name: count} — skip if inventory has enough
  - skip_if_nearby: block_name — skip if block found nearby
  - search_strategy: list of fallback actions when "search" type fails

Step types:
  - craft:   deterministic — needs materials, always succeeds
  - gather:  deterministic-ish — mine nearby blocks
  - search:  non-deterministic — might not find target, needs fallback
  - place:   deterministic — place from inventory
  - action:  misc (eat, equip, attack, etc)
"""

# ============================================
# DROP MAPPING (block mined → item dropped)
# ============================================
DROP_MAP = {
    "stone": "cobblestone",
    "iron_ore": "raw_iron",
    "deepslate_iron_ore": "raw_iron",
    "coal_ore": "coal",
    "deepslate_coal_ore": "coal",
    "copper_ore": "raw_copper",
    "gold_ore": "raw_gold",
    "diamond_ore": "diamond",
    "deepslate_diamond_ore": "diamond",
    "lapis_ore": "lapis_lazuli",
    "redstone_ore": "redstone",
    "emerald_ore": "emerald",
    "nether_gold_ore": "gold_nugget",
    "nether_quartz_ore": "quartz",
    "oak_log": "oak_log",
    "spruce_log": "spruce_log",
    "birch_log": "birch_log",
    "dirt": "dirt",
    "gravel": "gravel",
    "sand": "sand",
}

# ============================================
# SEARCH STRATEGIES
# ============================================
# When a "search" step fails (target not found nearby), try these in order.
# Format: (action_type, args)
#   "find_block"   — scan nearby
#   "check_memory" — look for saved locations with this resource
#   "dig_down"     — staircase mine to target Y level
#   "dig_tunnel"   — strip mine in a direction
#   "explore"      — walk to random location and retry

SEARCH_STRATEGIES = {
    "oak_log": [
        ("find_block", {"block_type": "oak_log", "max_distance": 64}),
        ("find_block", {"block_type": "birch_log", "max_distance": 64}),
        ("find_block", {"block_type": "spruce_log", "max_distance": 64}),
        ("explore", {"distance": 30}),
        ("explore", {"distance": 50}),
        ("explore", {"distance": 80}),
    ],
    "birch_log": [
        ("find_block", {"block_type": "birch_log", "max_distance": 64}),
        ("find_block", {"block_type": "oak_log", "max_distance": 64}),
        ("explore", {"distance": 30}),
        ("explore", {"distance": 50}),
    ],
    "spruce_log": [
        ("find_block", {"block_type": "spruce_log", "max_distance": 64}),
        ("find_block", {"block_type": "oak_log", "max_distance": 64}),
        ("explore", {"distance": 30}),
        ("explore", {"distance": 50}),
    ],
    "stone": [
        ("find_block", {"block_type": "stone", "max_distance": 32}),
        ("dig_down", {"depth": 5}),
        ("dig_tunnel", {"direction": "north", "length": 10}),
    ],
    "iron_ore": [
        ("find_block", {"block_type": "iron_ore", "max_distance": 64}),
        ("check_memory", {"category": "resource", "keyword": "iron"}),
        ("dig_down", {"target_y": 32}),
        ("dig_tunnel", {"direction": "north", "length": 20}),
        ("dig_tunnel", {"direction": "east", "length": 20}),
        ("dig_tunnel", {"direction": "south", "length": 20}),
        ("dig_tunnel", {"direction": "west", "length": 20}),
        ("explore", {"distance": 40}),
        ("dig_down", {"target_y": 16}),
        ("dig_tunnel", {"direction": "north", "length": 25}),
        ("dig_tunnel", {"direction": "east", "length": 25}),
    ],
    "coal_ore": [
        ("find_block", {"block_type": "coal_ore", "max_distance": 64}),
        ("check_memory", {"category": "resource", "keyword": "coal"}),
        ("dig_down", {"target_y": 48}),
        ("dig_tunnel", {"direction": "west", "length": 15}),
        ("dig_tunnel", {"direction": "north", "length": 15}),
        ("explore", {"distance": 30}),
        ("dig_down", {"target_y": 40}),
        ("dig_tunnel", {"direction": "east", "length": 20}),
        ("dig_tunnel", {"direction": "south", "length": 20}),
    ],
    "diamond_ore": [
        ("find_block", {"block_type": "diamond_ore", "max_distance": 64}),
        ("find_block", {"block_type": "deepslate_diamond_ore", "max_distance": 64}),
        ("check_memory", {"category": "resource", "keyword": "diamond"}),
        ("dig_down", {"target_y": -58}),
        ("dig_tunnel", {"direction": "north", "length": 30}),
        ("dig_tunnel", {"direction": "east", "length": 30}),
        ("dig_tunnel", {"direction": "south", "length": 30}),
        ("dig_tunnel", {"direction": "west", "length": 30}),
        ("explore", {"distance": 40}),
        ("dig_down", {"target_y": -58}),
        ("dig_tunnel", {"direction": "north", "length": 40}),
        ("dig_tunnel", {"direction": "east", "length": 40}),
    ],
    "gold_ore": [
        ("find_block", {"block_type": "gold_ore", "max_distance": 64}),
        ("check_memory", {"category": "resource", "keyword": "gold"}),
        ("dig_down", {"target_y": 16}),
        ("dig_tunnel", {"direction": "north", "length": 20}),
        ("dig_tunnel", {"direction": "east", "length": 20}),
        ("explore", {"distance": 30}),
        ("dig_down", {"target_y": -10}),
        ("dig_tunnel", {"direction": "south", "length": 25}),
    ],
    # Animals — wander around looking more persistently
    "cow": [
        ("explore", {"distance": 30}),
        ("explore", {"distance": 50}),
        ("explore", {"distance": 80}),
        ("explore", {"distance": 60}),
        ("explore", {"distance": 100}),
    ],
    "pig": [
        ("explore", {"distance": 30}),
        ("explore", {"distance": 50}),
        ("explore", {"distance": 80}),
        ("explore", {"distance": 60}),
    ],
    "chicken": [
        ("explore", {"distance": 30}),
        ("explore", {"distance": 50}),
        ("explore", {"distance": 80}),
    ],
    "sheep": [
        ("explore", {"distance": 30}),
        ("explore", {"distance": 50}),
        ("explore", {"distance": 80}),
    ],
}

# Default search strategy for unknown targets
DEFAULT_SEARCH_STRATEGY = [
    ("explore", {"distance": 30}),
    ("explore", {"distance": 60}),
]


# ============================================
# CHAIN LIBRARY
# ============================================

CHAIN_LIBRARY = {
    # ═══════════════════════════════════════════
    # PHASE 1: BASIC SURVIVAL
    # ═══════════════════════════════════════════

    "get_wood": [
        {"tool": "mine_block", "args": {"block_type": "oak_log", "count": 6},
         "type": "search", "skip_if": {"oak_log": 6, "oak_planks": 20},
         "search_target": "oak_log"},
        {"tool": "craft_item", "args": {"item_name": "oak_planks"},
         "type": "craft", "skip_if": {"oak_planks": 16}},
        {"tool": "craft_item", "args": {"item_name": "oak_planks"},
         "type": "craft", "skip_if": {"oak_planks": 16}},
        {"tool": "craft_item", "args": {"item_name": "stick"},
         "type": "craft", "skip_if": {"stick": 8}},
    ],

    "make_crafting_table": [
        {"tool": "craft_item", "args": {"item_name": "crafting_table"},
         "type": "craft", "skip_if": {"crafting_table": 1},
         "skip_if_nearby": "crafting_table"},
        {"tool": "place_block", "args": {"block_name": "crafting_table"},
         "type": "place", "skip_if_nearby": "crafting_table"},
    ],

    "make_wooden_pickaxe": [
        # Ensure we have planks + sticks
        {"tool": "mine_block", "args": {"block_type": "oak_log", "count": 2},
         "type": "search", "skip_if": {"oak_planks": 3, "wooden_pickaxe": 1},
         "search_target": "oak_log"},
        {"tool": "craft_item", "args": {"item_name": "oak_planks"},
         "type": "craft", "skip_if": {"oak_planks": 3, "wooden_pickaxe": 1}},
        {"tool": "craft_item", "args": {"item_name": "stick"},
         "type": "craft", "skip_if": {"stick": 2, "wooden_pickaxe": 1}},
        {"tool": "craft_item", "args": {"item_name": "wooden_pickaxe"},
         "type": "craft", "skip_if": {"wooden_pickaxe": 1}},
        {"tool": "equip_item", "args": {"item_name": "wooden_pickaxe"},
         "type": "action"},
    ],

    "make_stone_pickaxe": [
        {"tool": "mine_block", "args": {"block_type": "stone", "count": 3},
         "type": "search", "skip_if": {"cobblestone": 3, "stone_pickaxe": 1},
         "search_target": "stone"},
        {"tool": "craft_item", "args": {"item_name": "stick"},
         "type": "craft", "skip_if": {"stick": 2, "stone_pickaxe": 1}},
        {"tool": "craft_item", "args": {"item_name": "stone_pickaxe"},
         "type": "craft", "skip_if": {"stone_pickaxe": 1}},
        {"tool": "equip_item", "args": {"item_name": "stone_pickaxe"},
         "type": "action"},
    ],

    "find_food": [
        {"tool": "attack_entity", "args": {"entity_type": "cow"},
         "type": "search", "search_target": "cow"},
        {"tool": "attack_entity", "args": {"entity_type": "pig"},
         "type": "search", "search_target": "pig"},
        {"tool": "eat_food", "args": {},
         "type": "action"},
    ],

    "mine_stone": [
        {"tool": "mine_block", "args": {"block_type": "stone", "count": 32},
         "type": "search", "skip_if": {"cobblestone": 64},
         "search_target": "stone"},
        {"tool": "mine_block", "args": {"block_type": "stone", "count": 32},
         "type": "search", "skip_if": {"cobblestone": 64},
         "search_target": "stone"},
    ],

    "place_furnace": [
        {"tool": "mine_block", "args": {"block_type": "stone", "count": 8},
         "type": "search", "skip_if": {"cobblestone": 8, "furnace": 1},
         "search_target": "stone"},
        {"tool": "craft_item", "args": {"item_name": "furnace"},
         "type": "craft", "skip_if": {"furnace": 1}, "skip_if_nearby": "furnace"},
        {"tool": "place_block", "args": {"block_name": "furnace"},
         "type": "place", "skip_if_nearby": "furnace"},
    ],

    "place_chest": [
        {"tool": "mine_block", "args": {"block_type": "oak_log", "count": 2},
         "type": "search", "skip_if": {"oak_planks": 8},
         "search_target": "oak_log"},
        {"tool": "craft_item", "args": {"item_name": "oak_planks"},
         "type": "craft", "skip_if": {"oak_planks": 8}},
        {"tool": "craft_item", "args": {"item_name": "chest"},
         "type": "craft", "skip_if": {"chest": 1}, "skip_if_nearby": "chest"},
        {"tool": "place_block", "args": {"block_name": "chest"},
         "type": "place", "skip_if_nearby": "chest"},
    ],

    "build_shelter": [
        {"tool": "mine_block", "args": {"block_type": "oak_log", "count": 2},
         "type": "search", "skip_if": {"oak_planks": 6, "oak_door": 1},
         "search_target": "oak_log"},
        {"tool": "craft_item", "args": {"item_name": "oak_planks"},
         "type": "craft", "skip_if": {"oak_planks": 6, "oak_door": 1}},
        {"tool": "craft_item", "args": {"item_name": "oak_door"},
         "type": "craft", "skip_if": {"oak_door": 1}},
        {"tool": "mine_block", "args": {"block_type": "stone", "count": 30},
         "type": "search", "skip_if": {"cobblestone": 25},
         "search_target": "stone"},
        {"tool": "build_shelter", "args": {},
         "type": "action"},
    ],

    # ═══════════════════════════════════════════
    # PHASE 2: IRON AGE
    # ═══════════════════════════════════════════

    "make_iron_pickaxe": [
        # Mine iron ore (search type — might need to dig down)
        {"tool": "mine_block", "args": {"block_type": "iron_ore", "count": 3},
         "type": "search", "skip_if": {"raw_iron": 3, "iron_ingot": 3, "iron_pickaxe": 1},
         "search_target": "iron_ore"},
        # Mine coal for fuel
        {"tool": "mine_block", "args": {"block_type": "coal_ore", "count": 3},
         "type": "search", "skip_if": {"coal": 3, "charcoal": 3, "iron_pickaxe": 1},
         "search_target": "coal_ore"},
        # Furnace
        {"tool": "mine_block", "args": {"block_type": "stone", "count": 8},
         "type": "search", "skip_if": {"cobblestone": 8, "furnace": 1, "iron_pickaxe": 1},
         "search_target": "stone"},
        {"tool": "craft_item", "args": {"item_name": "furnace"},
         "type": "craft", "skip_if": {"furnace": 1}, "skip_if_nearby": "furnace"},
        {"tool": "place_block", "args": {"block_name": "furnace"},
         "type": "place", "skip_if_nearby": "furnace"},
        # Smelt
        {"tool": "smelt_item", "args": {"item_name": "raw_iron", "count": 3},
         "type": "craft", "skip_if": {"iron_ingot": 3, "iron_pickaxe": 1}},
        # Craft
        {"tool": "craft_item", "args": {"item_name": "stick"},
         "type": "craft", "skip_if": {"stick": 2, "iron_pickaxe": 1}},
        {"tool": "craft_item", "args": {"item_name": "iron_pickaxe"},
         "type": "craft", "skip_if": {"iron_pickaxe": 1}},
        {"tool": "equip_item", "args": {"item_name": "iron_pickaxe"},
         "type": "action"},
    ],

    "make_iron_sword": [
        {"tool": "mine_block", "args": {"block_type": "iron_ore", "count": 2},
         "type": "search", "skip_if": {"raw_iron": 2, "iron_ingot": 2, "iron_sword": 1},
         "search_target": "iron_ore"},
        {"tool": "mine_block", "args": {"block_type": "coal_ore", "count": 2},
         "type": "search", "skip_if": {"coal": 2, "charcoal": 2, "iron_sword": 1},
         "search_target": "coal_ore"},
        {"tool": "smelt_item", "args": {"item_name": "raw_iron", "count": 2},
         "type": "craft", "skip_if": {"iron_ingot": 2, "iron_sword": 1}},
        {"tool": "craft_item", "args": {"item_name": "stick"},
         "type": "craft", "skip_if": {"stick": 1, "iron_sword": 1}},
        {"tool": "craft_item", "args": {"item_name": "iron_sword"},
         "type": "craft", "skip_if": {"iron_sword": 1}},
        {"tool": "equip_item", "args": {"item_name": "iron_sword"},
         "type": "action"},
    ],

    "make_iron_armor": [
        {"tool": "mine_block", "args": {"block_type": "iron_ore", "count": 8},
         "type": "search", "skip_if": {"raw_iron": 8, "iron_ingot": 8, "iron_chestplate": 1},
         "search_target": "iron_ore"},
        {"tool": "mine_block", "args": {"block_type": "coal_ore", "count": 8},
         "type": "search", "skip_if": {"coal": 8, "iron_chestplate": 1},
         "search_target": "coal_ore"},
        {"tool": "smelt_item", "args": {"item_name": "raw_iron", "count": 8},
         "type": "craft", "skip_if": {"iron_ingot": 8, "iron_chestplate": 1}},
        {"tool": "craft_item", "args": {"item_name": "iron_chestplate"},
         "type": "craft", "skip_if": {"iron_chestplate": 1}},
        {"tool": "equip_item", "args": {"item_name": "iron_chestplate", "destination": "torso"},
         "type": "action"},
    ],

    "make_shield": [
        {"tool": "craft_item", "args": {"item_name": "shield"},
         "type": "craft", "skip_if": {"shield": 1}},
        {"tool": "equip_item", "args": {"item_name": "shield", "destination": "off-hand"},
         "type": "action"},
    ],

    "make_bucket": [
        {"tool": "mine_block", "args": {"block_type": "iron_ore", "count": 3},
         "type": "search", "skip_if": {"raw_iron": 3, "iron_ingot": 3, "bucket": 1},
         "search_target": "iron_ore"},
        {"tool": "smelt_item", "args": {"item_name": "raw_iron", "count": 3},
         "type": "craft", "skip_if": {"iron_ingot": 3, "bucket": 1}},
        {"tool": "craft_item", "args": {"item_name": "bucket"},
         "type": "craft", "skip_if": {"bucket": 1}},
    ],

    # ═══════════════════════════════════════════
    # PHASE 3: DIAMOND AGE
    # ═══════════════════════════════════════════

    "mine_diamonds": [
        {"tool": "dig_down", "args": {"target_y": -58},
         "type": "action"},
        {"tool": "dig_tunnel", "args": {"direction": "north", "length": 30},
         "type": "action"},
        {"tool": "mine_block", "args": {"block_type": "diamond_ore", "count": 5},
         "type": "search", "skip_if": {"diamond": 5},
         "search_target": "diamond_ore"},
    ],

    "make_diamond_pickaxe": [
        {"tool": "craft_item", "args": {"item_name": "stick"},
         "type": "craft", "skip_if": {"stick": 2, "diamond_pickaxe": 1}},
        {"tool": "craft_item", "args": {"item_name": "diamond_pickaxe"},
         "type": "craft", "skip_if": {"diamond_pickaxe": 1}},
        {"tool": "equip_item", "args": {"item_name": "diamond_pickaxe"},
         "type": "action"},
    ],

    "make_diamond_sword": [
        {"tool": "craft_item", "args": {"item_name": "stick"},
         "type": "craft", "skip_if": {"stick": 1, "diamond_sword": 1}},
        {"tool": "craft_item", "args": {"item_name": "diamond_sword"},
         "type": "craft", "skip_if": {"diamond_sword": 1}},
        {"tool": "equip_item", "args": {"item_name": "diamond_sword"},
         "type": "action"},
    ],

    # ═══════════════════════════════════════════
    # EMERGENCY / INSTINCT CHAINS
    # ═══════════════════════════════════════════

    "emergency_eat": [
        {"tool": "eat_food", "args": {}, "type": "action"},
    ],

    "emergency_shelter": [
        {"tool": "dig_shelter", "args": {}, "type": "action"},
    ],

    "emergency_flee": [
        {"tool": "dig_shelter", "args": {}, "type": "action"},
    ],
}


def get_chain(chain_name: str) -> list[dict]:
    """Get a copy of a chain from the library."""
    chain = CHAIN_LIBRARY.get(chain_name)
    if chain is None:
        return []
    # Deep copy so we don't mutate the library
    import copy
    return copy.deepcopy(chain)


def get_search_strategy(target: str) -> list[tuple]:
    """Get search strategy for a specific resource target."""
    return SEARCH_STRATEGIES.get(target, DEFAULT_SEARCH_STRATEGY)


def list_available_chains() -> list[str]:
    """List all chain names available in the library."""
    return list(CHAIN_LIBRARY.keys())
