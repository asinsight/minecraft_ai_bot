"""
Experience Memory — Remembers what worked and what didn't.

Two types of memory:
  1. Search success: "Found iron_ore at y=32 by digging down"
     → Next time iron_ore is needed, try dig_down(32) first
  2. Error solutions: "craft_item failed: no crafting table nearby"
     → Next time, find_nearest_location(crafting) first, or place one

Persists to experience.json across restarts.
"""

import json
import os
import time
from typing import Optional


EXPERIENCE_FILE = "experience.json"


class ExperienceMemory:
    def __init__(self):
        self.search_successes: dict = {}   # target → {method, location, count, last}
        self.error_solutions: dict = {}     # "tool:error_keyword" → {chain, count, last}
        self.combat_encounters: list = []   # [{mob, position, time_of_day, outcome, damage_taken, ...}]
        self._load()

    def _save(self):
        try:
            data = {
                "search_successes": self.search_successes,
                "error_solutions": self.error_solutions,
                "combat_encounters": self.combat_encounters[-30:],  # keep last 30
            }
            with open(EXPERIENCE_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"⚠️ Failed to save experience: {e}")

    def _load(self):
        try:
            if os.path.exists(EXPERIENCE_FILE):
                with open(EXPERIENCE_FILE, "r") as f:
                    data = json.load(f)
                self.search_successes = data.get("search_successes", {})
                self.error_solutions = data.get("error_solutions", {})
                self.combat_encounters = data.get("combat_encounters", [])
                count = len(self.search_successes) + len(self.error_solutions) + len(self.combat_encounters)
                if count > 0:
                    print(f"🧠 Loaded {count} experience memories")
        except Exception as e:
            print(f"⚠️ Failed to load experience: {e}")

    # ── Search Success ──

    MAX_LOCATIONS_PER_RESOURCE = 5

    def record_search_success(self, target: str, method: str,
                               location: Optional[dict] = None):
        """Record that a search target was found using a specific method.

        Stores multiple locations per resource (up to MAX_LOCATIONS_PER_RESOURCE).
        Oldest locations are evicted when the limit is reached.

        Args:
            target: What was being searched for (e.g., "iron_ore")
            method: How it was found (e.g., "dig_down:32", "dig_tunnel:north:20")
            location: Where it was found {x, y, z} — should always be provided
        """
        existing = self.search_successes.get(target, {})
        locations = existing.get("locations", [])

        # Migrate old single-location format
        if not locations and existing.get("location"):
            old_loc = existing["location"]
            if old_loc:
                locations.append({**old_loc, "method": existing.get("method", "unknown"),
                                  "found_at": existing.get("last_used", time.time())})

        # Add new location if provided and not too close to existing ones
        if location:
            too_close = False
            for loc in locations:
                dx = float(location.get("x", 0)) - float(loc.get("x", 0))
                dy = float(location.get("y", 0)) - float(loc.get("y", 0))
                dz = float(location.get("z", 0)) - float(loc.get("z", 0))
                if (dx**2 + dy**2 + dz**2) ** 0.5 < 16:  # within 16 blocks = same area
                    too_close = True
                    loc["method"] = method  # update method for this spot
                    loc["found_at"] = time.time()
                    break
            if not too_close:
                locations.append({
                    "x": float(location.get("x", 0)),
                    "y": float(location.get("y", 0)),
                    "z": float(location.get("z", 0)),
                    "method": method,
                    "found_at": time.time(),
                })
                # Evict oldest if over limit
                if len(locations) > self.MAX_LOCATIONS_PER_RESOURCE:
                    locations.sort(key=lambda l: l.get("found_at", 0))
                    locations = locations[-self.MAX_LOCATIONS_PER_RESOURCE:]

        self.search_successes[target] = {
            "method": method,  # most recent method (for backward compat)
            "locations": locations,
            "success_count": existing.get("success_count", 0) + 1,
            "last_used": time.time(),
        }
        self._save()
        loc_str = f" at ({location['x']:.0f}, {location['y']:.0f}, {location['z']:.0f})" if location else ""
        print(f"🧠 Remembered: {target} found via {method}{loc_str} ({len(locations)} known locations)")

    def remove_location(self, target: str, location: dict):
        """Remove a depleted location from memory.

        Called when scouting a remembered location finds no resources there.
        Removes any stored location within 16 blocks of the given coordinates.
        """
        entry = self.search_successes.get(target)
        if not entry:
            return
        locations = entry.get("locations", [])
        if not locations:
            return

        lx = float(location.get("x", 0))
        ly = float(location.get("y", 0))
        lz = float(location.get("z", 0))

        new_locations = []
        removed = False
        for loc in locations:
            dx = lx - float(loc.get("x", 0))
            dy = ly - float(loc.get("y", 0))
            dz = lz - float(loc.get("z", 0))
            if (dx**2 + dy**2 + dz**2) ** 0.5 < 16:
                removed = True  # skip this one
            else:
                new_locations.append(loc)

        if removed:
            if new_locations:
                entry["locations"] = new_locations
            else:
                # No locations left — remove the entire entry
                del self.search_successes[target]
            self._save()
            print(f"🧠 Forgot depleted location for {target} at ({lx:.0f}, {ly:.0f}, {lz:.0f})")

    def get_search_hint(self, target: str, bot_position: Optional[dict] = None) -> Optional[dict]:
        """Get a past successful search location for a target.

        If bot_position is provided, returns the nearest known location.
        Otherwise returns the most recently found location.

        Returns:
            dict with "method", "location", "distance" or None
        """
        entry = self.search_successes.get(target)
        if not entry:
            return None

        locations = entry.get("locations", [])
        if not locations:
            # Backward compat: old format had single "location" field
            if entry.get("location"):
                return {"method": entry["method"], "location": entry["location"]}
            return {"method": entry["method"], "location": None}

        if bot_position:
            # Return nearest location
            bx = float(bot_position.get("x", 0))
            by = float(bot_position.get("y", 0))
            bz = float(bot_position.get("z", 0))
            best = None
            best_dist = float("inf")
            for loc in locations:
                dx = bx - float(loc.get("x", 0))
                dy = by - float(loc.get("y", 0))
                dz = bz - float(loc.get("z", 0))
                dist = (dx**2 + dy**2 + dz**2) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    best = loc
            if best:
                return {
                    "method": best.get("method", entry["method"]),
                    "location": {"x": best["x"], "y": best["y"], "z": best["z"]},
                    "distance": round(best_dist, 1),
                }

        # No bot position — return most recent
        most_recent = max(locations, key=lambda l: l.get("found_at", 0))
        return {
            "method": most_recent.get("method", entry["method"]),
            "location": {"x": most_recent["x"], "y": most_recent["y"], "z": most_recent["z"]},
        }

    # ── Error Solutions ──

    def record_error_solution(self, tool_name: str, error_keyword: str,
                               solution_chain: list[dict]):
        """Record a solution chain for a specific error pattern.

        Args:
            tool_name: Tool that failed (e.g., "craft_item")
            error_keyword: Key phrase from error (e.g., "no crafting table")
            solution_chain: Steps that fixed the problem
        """
        key = f"{tool_name}:{error_keyword}"
        existing = self.error_solutions.get(key, {})
        self.error_solutions[key] = {
            "chain": solution_chain,
            "success_count": existing.get("success_count", 0) + 1,
            "last_used": time.time(),
        }
        self._save()
        print(f"🧠 Remembered solution for: {key}")

    def get_error_solution(self, tool_name: str, error_msg: str) -> Optional[list[dict]]:
        """Look up a known solution for an error.

        Searches stored error patterns against the error message.
        Returns solution chain if found, None otherwise.
        """
        error_lower = error_msg.lower()
        for key, entry in self.error_solutions.items():
            stored_tool, stored_keyword = key.split(":", 1)
            if stored_tool == tool_name and stored_keyword.lower() in error_lower:
                return entry.get("chain")
        return None

    # ── Combat Encounters ──

    def record_combat(self, mob_type: str, outcome: str, position: Optional[dict] = None,
                       damage_taken: float = 0, time_of_day: str = "day",
                       weapon_used: str = "fist", had_armor: bool = False):
        """Record a combat encounter for future reference.

        Args:
            mob_type: What was fought (e.g., "zombie", "skeleton")
            outcome: "won", "fled", "died"
            position: Where the encounter happened {x, y, z}
            damage_taken: Total HP lost during encounter
            time_of_day: "day" or "night"
            weapon_used: Weapon name or "fist"
            had_armor: Whether bot had armor equipped
        """
        self.combat_encounters.append({
            "mob": mob_type,
            "outcome": outcome,
            "position": position,
            "damage_taken": damage_taken,
            "time_of_day": time_of_day,
            "weapon": weapon_used,
            "had_armor": had_armor,
            "timestamp": time.time(),
        })
        # Keep only last 30
        if len(self.combat_encounters) > 30:
            self.combat_encounters = self.combat_encounters[-30:]
        self._save()
        print(f"🧠 Combat memory: {outcome} vs {mob_type} (dmg={damage_taken:.0f}, weapon={weapon_used})")

    def get_combat_summary(self) -> str:
        """Get a summary of recent combat for LLM context."""
        if not self.combat_encounters:
            return ""

        # Aggregate by mob type
        mob_stats: dict = {}
        for enc in self.combat_encounters:
            mob = enc.get("mob", "unknown")
            if mob not in mob_stats:
                mob_stats[mob] = {"won": 0, "fled": 0, "died": 0, "total_dmg": 0}
            outcome = enc.get("outcome", "unknown")
            if outcome in mob_stats[mob]:
                mob_stats[mob][outcome] += 1
            mob_stats[mob]["total_dmg"] += enc.get("damage_taken", 0)

        lines = ["COMBAT HISTORY:"]
        for mob, stats in mob_stats.items():
            total = stats["won"] + stats["fled"] + stats["died"]
            avg_dmg = stats["total_dmg"] / total if total > 0 else 0
            lines.append(f"  {mob}: {stats['won']}W/{stats['fled']}F/{stats['died']}D "
                        f"(avg dmg taken: {avg_dmg:.0f})")

        # Last 3 encounters for recency
        recent = self.combat_encounters[-3:]
        lines.append("  Recent:")
        for enc in recent:
            lines.append(f"    {enc.get('outcome','?')} vs {enc.get('mob','?')} "
                        f"(weapon={enc.get('weapon','?')}, dmg={enc.get('damage_taken',0):.0f})")

        return "\n".join(lines)

    def get_dangerous_area(self, position: dict, radius: float = 30) -> Optional[str]:
        """Check if a position is near a known dangerous area."""
        if not position:
            return None

        px = float(position.get("x", 0))
        pz = float(position.get("z", 0))

        deaths_nearby = 0
        mob_types = set()
        for enc in self.combat_encounters:
            if enc.get("outcome") != "died":
                continue
            epos = enc.get("position")
            if not epos:
                continue
            ex = float(epos.get("x", 0))
            ez = float(epos.get("z", 0))
            dist = ((px - ex) ** 2 + (pz - ez) ** 2) ** 0.5
            if dist < radius:
                deaths_nearby += 1
                mob_types.add(enc.get("mob", "unknown"))

        if deaths_nearby >= 2:
            return f"DANGER ZONE: Died {deaths_nearby}x nearby (mobs: {', '.join(mob_types)})"
        return None

    # ── Prompt Context (for LLM when escalated) ──

    def get_context_for_llm(self, target: str = "") -> str:
        """Generate compact context about past experiences for LLM."""
        lines = []
        if target and target in self.search_successes:
            s = self.search_successes[target]
            locations = s.get("locations", [])
            lines.append(f"Past success finding {target}: {s['method']} (worked {s['success_count']}x, {len(locations)} known locations)")
            for loc in locations[-3:]:  # show last 3
                lines.append(f"  Found at: ({loc.get('x','?'):.0f}, {loc.get('y','?'):.0f}, {loc.get('z','?'):.0f}) via {loc.get('method','?')}")

        combat_summary = self.get_combat_summary()
        if combat_summary:
            lines.append(combat_summary)

        if not lines:
            return ""
        return "\n".join(lines)

    def clear(self):
        """Clear all experience memory."""
        self.search_successes = {}
        self.error_solutions = {}
        self.combat_encounters = []
        self._save()
