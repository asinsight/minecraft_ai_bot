"""
Minecraft Bot Tools — LangChain tools that call the Mineflayer REST API.

18 tools across 5 categories:
  Perception (5): get_world_state, get_inventory, get_nearby, find_block, get_recipe
  Movement (5):   move_to, move_to_player, follow_player, explore, stop_moving
  Resource (3):   mine_block, place_block, attack_entity
  Survival (4):   eat_food, equip_item, craft_item, sleep_in_bed
  Social (1):     send_chat
"""

import os
import requests
from langchain.tools import tool
from dotenv import load_dotenv

load_dotenv()

BOT_API = os.getenv("BOT_API_URL", "http://localhost:3001")


# ============================================
# PERCEPTION TOOLS
# ============================================

@tool
def get_world_state() -> str:
    """Get the bot's current state including position, health, hunger, time of day, inventory, nearby blocks and entities.
    Use this to understand the current situation before deciding what to do."""
    try:
        r = requests.get(f"{BOT_API}/state", timeout=10)
        data = r.json()
        inv = ", ".join(f"{i['name']} x{i['count']}" for i in data.get("inventory", [])) or "empty"
        entities = ", ".join(f"{e['type']}({e['distance']}m)" for e in data.get("nearbyEntities", [])[:10]) or "none"
        blocks = ", ".join(data.get("nearbyBlocks", [])[:15]) or "none"
        pos = data.get("position", {})
        chat = data.get("recentChat", [])
        chat_str = " | ".join(f"{c['username']}: {c['message']}" for c in chat[-5:]) if chat else "no recent chat"

        # Environment info
        env = data.get('environment', 'surface')
        env_icons = {
            'surface': '🌍 Surface',
            'indoors': '🏠 Indoors',
            'underground': '⛏️ Underground (cave/mine)',
            'deep_underground': '🕳️ Deep Underground (deepslate)',
        }
        env_str = env_icons.get(env, env)
        if data.get('isDark'):
            env_str += ' ⚠️ DARK (mobs can spawn!)'
        if not data.get('canSeeSky') and data.get('roofHeight'):
            env_str += f' (roof {data["roofHeight"]} blocks up)'

        return (
            f"Position: x={pos.get('x')}, y={pos.get('y')}, z={pos.get('z')}\n"
            f"Health: {data.get('health', '?')}/20, Hunger: {data.get('food', '?')}/20\n"
            f"Time: {data.get('time', '?')} (tick {data.get('timeOfDay', '?')})\n"
            f"Environment: {env_str}\n"
            f"Weather: {'raining' if data.get('isRaining') else 'clear'}\n"
            f"Inventory: {inv}\n"
            f"Nearby blocks: {blocks}\n"
            f"Nearby entities: {entities}\n"
            f"Recent chat: {chat_str}"
        )
    except Exception as e:
        return f"Error getting state: {e}"


@tool
def get_inventory() -> str:
    """Get detailed inventory contents. Shows item name and count for each slot."""
    try:
        r = requests.get(f"{BOT_API}/inventory", timeout=10)
        items = r.json().get("items", [])
        if not items:
            return "Inventory is empty."
        return "Inventory:\n" + "\n".join(f"  {i['name']} x{i['count']}" for i in items)
    except Exception as e:
        return f"Error: {e}"


@tool
def get_nearby(range: int = 16) -> str:
    """Get nearby blocks (with counts) and entities within range.

    Args:
        range: Search radius in blocks (default 16, max 64)
    """
    try:
        r = requests.get(f"{BOT_API}/nearby", params={"range": min(range, 64)}, timeout=10)
        data = r.json()
        blocks = data.get("blocks", {})
        entities = data.get("entities", [])
        block_str = ", ".join(f"{name}({count})" for name, count in sorted(blocks.items(), key=lambda x: -x[1])[:20])
        entity_str = ", ".join(f"{e['type']}({e['distance']}m)" for e in entities[:10])
        return f"Blocks: {block_str or 'none'}\nEntities: {entity_str or 'none'}"
    except Exception as e:
        return f"Error: {e}"


@tool
def find_block(block_type: str, max_distance: int = 64) -> str:
    """Find the nearest block of a specific type and its coordinates.

    Args:
        block_type: Block name to search for (e.g., 'oak_log', 'iron_ore', 'crafting_table')
        max_distance: Maximum search distance (default 64)
    """
    try:
        r = requests.get(f"{BOT_API}/find_block", params={"type": block_type, "range": max_distance}, timeout=10)
        return r.json().get("message", "No result")
    except Exception as e:
        return f"Error: {e}"


@tool
def assess_threat() -> str:
    """Evaluate whether to fight, avoid, or flee from nearby threats.
    Considers: your weapon, armor, health, food supply vs enemy count and types.
    Returns a recommendation: safe, fight, fight_careful, avoid, or flee.
    Call this whenever you detect hostile mobs nearby or before entering combat."""
    try:
        r = requests.get(f"{BOT_API}/threat_assessment", timeout=10)
        data = r.json()
        rec = data['recommendation'].upper()
        reason = data['reason']
        combat = data['combat_readiness']
        threats = data['threats']

        lines = [
            f"⚔️ THREAT ASSESSMENT: {rec}",
            f"   Reason: {reason}",
            f"",
            f"   Your power: weapon={combat['weapon']}(power:{combat['weapon_power']}), "
            f"armor={combat['armor_points']}, shield={'yes' if combat['shield'] else 'no'}",
            f"   Health: {combat['health']}/20, Food items: {combat['food_items']}",
            f"",
            f"   Threats: {threats['count']} hostile(s), total danger: {threats['total_danger']}",
        ]
        if threats['details']:
            for t in threats['details']:
                lines.append(f"     - {t['type']} at {t['distance']}m (danger: {t['danger']})")
        if threats['is_night']:
            lines.append(f"   ⚠️ It's NIGHT — more mobs will spawn!")

        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


@tool
def get_recipe(item_name: str) -> str:
    """Look up the crafting recipe for an item. Shows ingredients needed, whether a crafting table is required, and what's missing from inventory.

    Args:
        item_name: Item to look up (e.g., 'torch', 'wooden_pickaxe', 'furnace')
    """
    try:
        r = requests.post(f"{BOT_API}/action/recipe", json={"item_name": item_name}, timeout=10)
        return r.json().get("message", "No result")
    except Exception as e:
        return f"Error: {e}"


@tool
def search_item(keyword: str) -> str:
    """Search for the correct Minecraft item or block name by keyword.
    Use this when you're not sure of the exact item ID.
    Returns matching item/block names that you can use with other tools.

    Args:
        keyword: Search keyword (e.g., 'pickaxe', 'oak', 'iron', 'sword', 'bed')
    """
    try:
        r = requests.get(f"{BOT_API}/search_item", params={"q": keyword}, timeout=10)
        data = r.json()
        if data.get("total", 0) == 0:
            return f"No items/blocks matching '{keyword}'. Try a different keyword."
        results = data.get("results", [])
        lines = [f"Found {data['total']} results for '{keyword}':"]
        for item in results:
            lines.append(f"  {item['name']} ({item['displayName']}) [{item['type']}]")
        lines.append("\nUse the 'name' field when calling tools like mine_block or craft_item.")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


# ============================================
# MOVEMENT TOOLS
# ============================================

@tool
def move_to(x: float, y: float, z: float) -> str:
    """Move the bot to specific coordinates using pathfinding.

    Args:
        x: X coordinate
        y: Y coordinate
        z: Z coordinate
    """
    try:
        r = requests.post(f"{BOT_API}/action/move", json={"x": x, "y": y, "z": z}, timeout=130)
        return r.json().get("message", "No result")
    except Exception as e:
        return f"Error: {e}"


@tool
def move_to_player(player_name: str = "") -> str:
    """Move to a player's position.

    Args:
        player_name: Name of the player. Leave empty for the nearest player.
    """
    try:
        r = requests.post(f"{BOT_API}/action/move_to_player", json={"player_name": player_name}, timeout=30)
        return r.json().get("message", "No result")
    except Exception as e:
        return f"Error: {e}"


@tool
def follow_player(player_name: str = "") -> str:
    """Continuously follow a player, staying close to them.

    Args:
        player_name: Name of the player to follow. Leave empty for nearest.
    """
    try:
        r = requests.post(f"{BOT_API}/action/follow", json={"player_name": player_name}, timeout=10)
        return r.json().get("message", "No result")
    except Exception as e:
        return f"Error: {e}"


@tool
def explore(distance: int = 20) -> str:
    """Explore by moving to a random nearby location.

    Args:
        distance: How far to explore (default 20 blocks)
    """
    try:
        r = requests.post(f"{BOT_API}/action/explore", json={"distance": distance}, timeout=30)
        return r.json().get("message", "No result")
    except Exception as e:
        return f"Error: {e}"


@tool
def stop_moving() -> str:
    """Stop all current movement and pathfinding."""
    try:
        r = requests.post(f"{BOT_API}/action/stop", timeout=10)
        return r.json().get("message", "No result")
    except Exception as e:
        return f"Error: {e}"


# ============================================
# RESOURCE / COMBAT TOOLS
# ============================================

@tool
def mine_block(block_type: str, count: int = 1) -> str:
    """Mine/dig blocks of a specific type. The bot will walk to the nearest one and mine it.

    Args:
        block_type: Type of block to mine (e.g., 'oak_log', 'stone', 'iron_ore', 'diamond_ore', 'coal_ore')
        count: Number of blocks to mine (default 1)
    """
    try:
        r = requests.post(f"{BOT_API}/action/mine", json={"block_type": block_type, "count": count}, timeout=60)
        return r.json().get("message", "No result")
    except Exception as e:
        return f"Error: {e}"


@tool
def place_block(block_name: str, x: float = 0, y: float = 0, z: float = 0) -> str:
    """Place a block from inventory at specific coordinates. If no coordinates given, places near the bot.
    Important blocks (crafting_table, chest, furnace, bed) are auto-saved to location memory.

    Args:
        block_name: Name of the block to place (must be in inventory)
        x: X coordinate (0 = auto near bot)
        y: Y coordinate (0 = auto near bot)
        z: Z coordinate (0 = auto near bot)
    """
    try:
        body = {"block_name": block_name}
        if x != 0 or y != 0 or z != 0:
            body.update({"x": x, "y": y, "z": z})
        r = requests.post(f"{BOT_API}/action/place", json=body, timeout=15)
        result = r.json().get("message", "No result")

        # Auto-save important placed blocks
        if "Placed" in result:
            try:
                from memory_tools import memory
                state = requests.get(f"{BOT_API}/state", timeout=5).json()
                pos = state.get("position", {})
                auto_msg = memory.auto_save_placed(
                    block_name, float(pos["x"]), float(pos["y"]), float(pos["z"])
                )
                if auto_msg:
                    result += f" | 📍 {auto_msg}"
            except:
                pass

        return result
    except Exception as e:
        return f"Error: {e}"


@tool
def attack_entity(entity_type: str = "") -> str:
    """Attack the nearest entity and keep hitting until it dies.
    Auto-equips the best weapon. Works on both hostile mobs and animals.
    After killing, nearby item drops are auto-collected.

    Args:
        entity_type: Target type (e.g., 'zombie', 'cow', 'pig', 'chicken', 'sheep'). Leave empty for nearest.
    """
    try:
        r = requests.post(f"{BOT_API}/action/attack", json={"entity_type": entity_type}, timeout=30)
        return r.json().get("message", "No result")
    except Exception as e:
        return f"Error: {e}"


# ============================================
# SURVIVAL TOOLS
# ============================================

@tool
def eat_food() -> str:
    """Eat food from inventory to restore hunger. Automatically picks the best food available."""
    try:
        r = requests.post(f"{BOT_API}/action/eat", timeout=15)
        return r.json().get("message", "No result")
    except Exception as e:
        return f"Error: {e}"


@tool
def equip_item(item_name: str, destination: str = "hand") -> str:
    """Equip an item from inventory.

    Args:
        item_name: Name of the item to equip (e.g., 'diamond_sword', 'iron_pickaxe')
        destination: Where to equip — 'hand', 'head', 'torso', 'legs', 'feet', 'off-hand'
    """
    try:
        r = requests.post(f"{BOT_API}/action/equip", json={"item_name": item_name, "destination": destination}, timeout=10)
        return r.json().get("message", "No result")
    except Exception as e:
        return f"Error: {e}"


@tool
def craft_item(item_name: str) -> str:
    """Craft an item using materials in inventory. Automatically uses crafting table if nearby.

    Args:
        item_name: Name of the item to craft (e.g., 'crafting_table', 'wooden_pickaxe', 'stick', 'oak_planks')
    """
    try:
        r = requests.post(f"{BOT_API}/action/craft", json={"item_name": item_name}, timeout=15)
        return r.json().get("message", "No result")
    except Exception as e:
        return f"Error: {e}"


@tool
def smelt_item(item_name: str, count: int = 1) -> str:
    """Smelt items in a furnace. If no furnace nearby, will try to craft and place one (needs 8 cobblestone).
    Requires fuel (coal, charcoal, planks, or logs) in inventory.

    Common smelting recipes:
      raw_iron → iron_ingot
      raw_gold → gold_ingot
      raw_copper → copper_ingot
      cobblestone → stone
      sand → glass
      oak_log → charcoal (useful as fuel!)
      raw_beef → cooked_beef
      raw_porkchop → cooked_porkchop
      raw_chicken → cooked_chicken

    Args:
        item_name: Raw item to smelt (e.g., 'raw_iron', 'raw_gold', 'sand', 'raw_beef')
        count: How many to smelt (default 1)
    """
    try:
        r = requests.post(f"{BOT_API}/action/smelt", json={"item_name": item_name, "count": count}, timeout=180)
        return r.json().get("message", "No result")
    except Exception as e:
        return f"Error: {e}"


@tool
def dig_shelter() -> str:
    """EMERGENCY shelter: dig a 3x3x3 underground room and seal the entrance.
    No building materials needed — just dig into the ground!
    Use this when night is coming and you have NO blocks to build with.
    Much faster than build_shelter but less comfortable."""
    try:
        r = requests.post(f"{BOT_API}/action/dig_shelter", timeout=60)
        result = r.json().get("message", "No result")
        # Auto-save location
        if "emergency" in result.lower() or "shelter" in result.lower():
            try:
                from memory_tools import memory
                state = requests.get(f"{BOT_API}/state", timeout=5).json()
                pos = state.get("position", {})
                existing = [n for n in memory.waypoints if n.startswith("shelter")]
                name = f"shelter_{len(existing) + 1}" if existing else "shelter"
                memory.save_location(name, "shelter", float(pos["x"]), float(pos["y"]), float(pos["z"]), "Emergency underground shelter")
                result += f" | 📍 Saved as '{name}'"
            except:
                pass
        return result
    except Exception as e:
        return f"Error: {e}"


@tool
def dig_down(depth: int = 10, target_y: int = 0) -> str:
    """Mine downward in a staircase pattern. Good for reaching ore levels.
    Auto-stops if lava is detected below.

    Y level guide:
      y < 16: Diamond ore level
      y 16-48: Iron and gold ore common
      y 48+: Above most ore levels

    Args:
        depth: How many blocks to descend (default 10)
        target_y: Target Y level to reach (0 = use depth instead)
    """
    try:
        body = {"depth": depth}
        if target_y > 0:
            body["target_y"] = target_y
        r = requests.post(f"{BOT_API}/action/dig_down", json=body, timeout=120)
        return r.json().get("message", "No result")
    except Exception as e:
        return f"Error: {e}"


@tool
def dig_tunnel(direction: str = "north", length: int = 10) -> str:
    """Dig a horizontal 1x2 tunnel in a direction. Great for strip mining or exploring.
    Reports any ores found while digging. Auto-stops if lava detected.

    Args:
        direction: 'north', 'south', 'east', or 'west'
        length: How many blocks long (default 10)
    """
    try:
        r = requests.post(f"{BOT_API}/action/dig_tunnel", json={"direction": direction, "length": length}, timeout=120)
        return r.json().get("message", "No result")
    except Exception as e:
        return f"Error: {e}"


@tool
def build_shelter() -> str:
    """Build a simple enclosed shelter around the bot's current position.
    Builds 5x3x5 walls + roof using blocks from inventory (cobblestone, dirt, planks, etc).
    Needs at least 20 building blocks. Leaves a door opening on one side.
    Mobs cannot enter a fully enclosed shelter. Location is auto-saved to memory."""
    try:
        r = requests.post(f"{BOT_API}/action/build_shelter", timeout=60)
        result = r.json().get("message", "No result")

        # Auto-save shelter location
        if "Built shelter" in result:
            try:
                from memory_tools import memory
                state = requests.get(f"{BOT_API}/state", timeout=5).json()
                pos = state.get("position", {})
                existing = [n for n in memory.waypoints if n.startswith("shelter")]
                name = f"shelter_{len(existing) + 1}" if existing else "shelter"
                memory.save_location(name, "shelter", float(pos["x"]), float(pos["y"]), float(pos["z"]), "Enclosed shelter")
                result += f" | 📍 Saved as '{name}'"
            except:
                pass

        return result
    except Exception as e:
        return f"Error: {e}"


@tool
def sleep_in_bed() -> str:
    """Find a nearby bed and sleep in it. Only works at night."""
    try:
        r = requests.post(f"{BOT_API}/action/sleep", timeout=15)
        return r.json().get("message", "No result")
    except Exception as e:
        return f"Error: {e}"


# ============================================
# COMMUNICATION TOOLS
# ============================================

@tool
def send_chat(message: str) -> str:
    """Send a chat message in Minecraft game chat. Use this to communicate with players.

    Args:
        message: The message to send (keep it short, max 256 chars)
    """
    try:
        r = requests.post(f"{BOT_API}/action/chat", json={"message": message[:256]}, timeout=10)
        return r.json().get("message", "No result")
    except Exception as e:
        return f"Error: {e}"


# ============================================
# ALL TOOLS LIST (imported by agent.py)
# ============================================
@tool
def scan_structure(name: str, radius: int = 5) -> str:
    """Scan and save the block structure around you. Use this to remember a shelter,
    base, or any build so you can rebuild it later.

    Args:
        name: Name for this structure (e.g., 'my_shelter', 'main_base')
        radius: Scan radius in blocks (default 5, max 10)
    """
    r = requests.post(f"{BOT_API}/action/scan_structure",
                      json={"name": name, "radius": min(radius, 10)}, timeout=30)
    return r.json().get("message", r.text)


@tool
def list_structures() -> str:
    """List all saved structures that can be rebuilt."""
    r = requests.get(f"{BOT_API}/action/list_structures", timeout=10)
    data = r.json()
    if not data.get("structures"):
        return "No saved structures."
    lines = []
    for s in data["structures"]:
        c = s["center"]
        lines.append(f"  {s['name']}: {s['block_count']} blocks at ({c['x']}, {c['y']}, {c['z']}) radius={s['radius']}")
    return "Saved structures:\n" + "\n".join(lines)


@tool
def rebuild_structure(name: str, offset_x: int = 0, offset_y: int = 0, offset_z: int = 0) -> str:
    """Rebuild a previously scanned structure. Needs the right blocks in inventory.

    Args:
        name: Name of the saved structure to rebuild
        offset_x: X offset from original position (0 = same spot)
        offset_y: Y offset from original position (0 = same spot)
        offset_z: Z offset from original position (0 = same spot)
    """
    r = requests.post(f"{BOT_API}/action/rebuild_structure",
                      json={"name": name, "offset_x": offset_x, "offset_y": offset_y, "offset_z": offset_z},
                      timeout=120)
    return r.json().get("message", r.text)


ALL_TOOLS = [
    # Perception
    get_world_state,
    get_inventory,
    get_nearby,
    find_block,
    assess_threat,
    get_recipe,
    search_item,
    # Movement
    move_to,
    move_to_player,
    follow_player,
    explore,
    stop_moving,
    # Resource / Combat
    mine_block,
    place_block,
    attack_entity,
    # Survival
    eat_food,
    equip_item,
    craft_item,
    smelt_item,
    dig_shelter,
    build_shelter,
    sleep_in_bed,
    # Mining
    dig_down,
    dig_tunnel,
    # Structures
    scan_structure,
    list_structures,
    rebuild_structure,
    # Communication
    send_chat,
]