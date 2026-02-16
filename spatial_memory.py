"""
Spatial Memory — Remember important locations in the Minecraft world.

Stores named waypoints like:
  - "shelter" → (100, 64, -50)
  - "crafting_table" → (105, 64, -48)
  - "chest_iron" → (110, 64, -45)
  - "diamond_cave" → (80, 12, -60)

Features:
  - Save/update/delete named locations
  - Find nearest saved location of a type
  - Auto-save: server can auto-register placed blocks (crafting table, chest, furnace, bed)
  - Distance calculation from current bot position
  - Prompt injection: LLM sees known locations every tick
"""

import time
import json
import math
import os
import requests
from dataclasses import dataclass, field


@dataclass
class Waypoint:
    """A saved location in the world."""
    name: str                       # unique key: "shelter_1", "crafting_table", "diamond_cave"
    category: str                   # "shelter", "crafting", "storage", "resource", "poi", "custom"
    x: float
    y: float
    z: float
    description: str = ""           # "Oak shelter with door facing north"
    created_at: float = field(default_factory=time.time)
    last_visited: float = 0

    def distance_to(self, px: float, py: float, pz: float) -> float:
        return math.sqrt((self.x - px)**2 + (self.y - py)**2 + (self.z - pz)**2)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "x": self.x, "y": self.y, "z": self.z,
            "description": self.description,
        }


class SpatialMemory:
    """
    Manages a collection of named waypoints.
    Persists to a JSON file so locations survive restarts.
    """

    SAVE_FILE = "waypoints.json"

    def __init__(self, bot_api: str = None):
        self.bot_api = bot_api or os.getenv("BOT_API_URL", "http://localhost:3001")
        self.waypoints: dict[str, Waypoint] = {}
        self._load()

    # ── Save / Load persistence ──

    def _save(self):
        data = {name: wp.to_dict() for name, wp in self.waypoints.items()}
        try:
            with open(self.SAVE_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"⚠️ Failed to save waypoints: {e}")

    def _load(self):
        try:
            if os.path.exists(self.SAVE_FILE):
                with open(self.SAVE_FILE, "r") as f:
                    data = json.load(f)
                for name, d in data.items():
                    self.waypoints[name] = Waypoint(
                        name=d["name"],
                        category=d["category"],
                        x=d["x"], y=d["y"], z=d["z"],
                        description=d.get("description", ""),
                    )
                print(f"📍 Loaded {len(self.waypoints)} saved waypoints")
        except Exception as e:
            print(f"⚠️ Failed to load waypoints: {e}")

    # ── Core Operations ──

    def save_location(self, name: str, category: str, x: float, y: float, z: float,
                      description: str = "") -> str:
        """Save or update a named location."""
        name = name.lower().replace(" ", "_")
        category = category.lower()

        if name in self.waypoints:
            old = self.waypoints[name]
            self.waypoints[name] = Waypoint(
                name=name, category=category,
                x=x, y=y, z=z, description=description,
                created_at=old.created_at,
            )
            self._save()
            return f"Updated '{name}' → ({x:.0f}, {y:.0f}, {z:.0f})"
        else:
            self.waypoints[name] = Waypoint(
                name=name, category=category,
                x=x, y=y, z=z, description=description,
            )
            self._save()
            return f"Saved '{name}' [{category}] at ({x:.0f}, {y:.0f}, {z:.0f})"

    def delete_location(self, name: str) -> str:
        """Delete a saved location."""
        name = name.lower().replace(" ", "_")
        if name in self.waypoints:
            del self.waypoints[name]
            self._save()
            return f"Deleted '{name}'"
        return f"No location named '{name}'"

    def get_location(self, name: str) -> str:
        """Get a specific saved location."""
        name = name.lower().replace(" ", "_")
        wp = self.waypoints.get(name)
        if not wp:
            return f"No location named '{name}'. Use list_locations to see all saved places."
        return f"'{wp.name}' [{wp.category}] at ({wp.x:.0f}, {wp.y:.0f}, {wp.z:.0f}) — {wp.description}"

    def list_locations(self, category: str = "") -> str:
        """List all saved locations, optionally filtered by category."""
        wps = list(self.waypoints.values())
        if category:
            wps = [wp for wp in wps if wp.category == category.lower()]

        if not wps:
            return f"No saved locations{' in category: ' + category if category else ''}."

        lines = [f"📍 Saved locations ({len(wps)}):"]
        # Group by category
        categories = {}
        for wp in wps:
            categories.setdefault(wp.category, []).append(wp)

        for cat, items in sorted(categories.items()):
            lines.append(f"\n  [{cat.upper()}]")
            for wp in items:
                lines.append(f"    {wp.name}: ({wp.x:.0f}, {wp.y:.0f}, {wp.z:.0f}) {wp.description}")

        return "\n".join(lines)

    def find_nearest(self, category: str = "", bot_pos: tuple = None) -> str:
        """Find the nearest saved location, optionally by category."""
        if not bot_pos:
            try:
                r = requests.get(f"{self.bot_api}/state", timeout=5)
                pos = r.json().get("position", {})
                bot_pos = (float(pos["x"]), float(pos["y"]), float(pos["z"]))
            except:
                return "Cannot determine bot position."

        wps = list(self.waypoints.values())
        if category:
            wps = [wp for wp in wps if wp.category == category.lower()]

        if not wps:
            return f"No saved locations{' in category: ' + category if category else ''}."

        nearest = min(wps, key=lambda wp: wp.distance_to(*bot_pos))
        dist = nearest.distance_to(*bot_pos)
        return (
            f"Nearest{' ' + category if category else ''}: '{nearest.name}' at "
            f"({nearest.x:.0f}, {nearest.y:.0f}, {nearest.z:.0f}) — {dist:.0f} blocks away. "
            f"{nearest.description}"
        )

    # ── Cave Management (max 10) ──

    MAX_CAVES = 10

    def save_cave(self, x: float, y: float, z: float, size: int = 0) -> str:
        """Save a cave location. Skips if too close to existing cave. Keeps max MAX_CAVES."""
        # Skip if a cave is already saved within 32 blocks
        for name, wp in self.waypoints.items():
            if wp.category == "cave" and wp.distance_to(x, y, z) < 32:
                return f"Cave already known near ({x:.0f}, {y:.0f}, {z:.0f})"

        # Evict oldest if at capacity
        cave_names = sorted(
            [n for n, wp in self.waypoints.items() if wp.category == "cave"],
            key=lambda n: self.waypoints[n].created_at,
        )
        while len(cave_names) >= self.MAX_CAVES:
            oldest = cave_names.pop(0)
            del self.waypoints[oldest]

        # Generate name
        existing_nums = [0]
        for n in self.waypoints:
            if n.startswith("cave_") and n.split("_")[-1].isdigit():
                existing_nums.append(int(n.split("_")[-1]))
        name = f"cave_{max(existing_nums) + 1}"

        desc = f"size={size}" if size else ""
        result = self.save_location(name, "cave", x, y, z, desc)
        print(f"   📍 Saved cave '{name}' at ({x:.0f}, {y:.0f}, {z:.0f})")
        return result

    def get_caves_sorted(self, bot_pos: tuple) -> list[dict]:
        """Get all saved caves sorted by distance from bot. Returns list of {name, x, y, z, dist}."""
        caves = []
        for name, wp in self.waypoints.items():
            if wp.category == "cave":
                dist = wp.distance_to(*bot_pos)
                caves.append({"name": name, "x": wp.x, "y": wp.y, "z": wp.z, "dist": dist})
        caves.sort(key=lambda c: c["dist"])
        return caves

    # ── Shelter Management (max 3) ──

    MAX_SHELTERS = 3

    def save_shelter(self, x: float, y: float, z: float, description: str = "Shelter") -> str:
        """Save a shelter location, keeping only the most recent MAX_SHELTERS."""
        # Find all existing shelter waypoints
        shelter_names = sorted(
            [n for n, wp in self.waypoints.items() if wp.category == "shelter"],
            key=lambda n: self.waypoints[n].created_at,
        )

        # Delete oldest shelters if at capacity
        while len(shelter_names) >= self.MAX_SHELTERS:
            oldest = shelter_names.pop(0)
            del self.waypoints[oldest]
            print(f"   🗑️ Removed old shelter '{oldest}'")

        # Generate name
        existing_nums = []
        for n in self.waypoints:
            if n.startswith("shelter"):
                parts = n.split("_")
                if len(parts) == 2 and parts[1].isdigit():
                    existing_nums.append(int(parts[1]))
                elif n == "shelter":
                    existing_nums.append(0)
        next_num = max(existing_nums, default=0) + 1
        name = f"shelter_{next_num}"

        result = self.save_location(name, "shelter", x, y, z, description)
        print(f"   📍 Saved shelter as '{name}'")
        return result

    # ── Auto-save from placed blocks ──

    def auto_save_placed(self, block_name: str, x: float, y: float, z: float) -> str:
        """Auto-save when important blocks are placed."""
        auto_categories = {
            "crafting_table": "crafting",
            "chest": "storage",
            "trapped_chest": "storage",
            "barrel": "storage",
            "furnace": "crafting",
            "blast_furnace": "crafting",
            "smoker": "crafting",
            "anvil": "crafting",
            "enchanting_table": "crafting",
            "brewing_stand": "crafting",
            "smithing_table": "crafting",
            "bed": "shelter",
        }

        # Also catch bed variants
        if "bed" in block_name:
            category = "shelter"
        else:
            category = auto_categories.get(block_name)

        if not category:
            return ""

        # Generate unique name
        existing = [n for n in self.waypoints if n.startswith(block_name)]
        name = f"{block_name}_{len(existing) + 1}" if existing else block_name

        return self.save_location(name, category, x, y, z, f"Auto-saved {block_name}")

    # ── Prompt Context ──

    def get_prompt_context(self, bot_pos: tuple = None) -> str:
        """Generate location memory for LLM prompt injection."""
        if not self.waypoints:
            return "📍 No saved locations yet. Use save_location to remember important places."

        lines = [f"📍 KNOWN LOCATIONS ({len(self.waypoints)}):"]

        # Get bot position for distance
        if not bot_pos:
            try:
                r = requests.get(f"{self.bot_api}/state", timeout=3)
                pos = r.json().get("position", {})
                bot_pos = (float(pos["x"]), float(pos["y"]), float(pos["z"]))
            except:
                bot_pos = None

        categories = {}
        for wp in self.waypoints.values():
            categories.setdefault(wp.category, []).append(wp)

        for cat, items in sorted(categories.items()):
            lines.append(f"  [{cat.upper()}]")
            for wp in sorted(items, key=lambda w: w.distance_to(*bot_pos) if bot_pos else 0):
                dist = f" ({wp.distance_to(*bot_pos):.0f}m)" if bot_pos else ""
                desc = f" — {wp.description}" if wp.description else ""
                lines.append(f"    {wp.name}: ({wp.x:.0f}, {wp.y:.0f}, {wp.z:.0f}){dist}{desc}")

        return "\n".join(lines)