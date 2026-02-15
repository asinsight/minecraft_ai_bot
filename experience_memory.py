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

    def record_search_success(self, target: str, method: str,
                               location: Optional[dict] = None):
        """Record that a search target was found using a specific method.

        Args:
            target: What was being searched for (e.g., "iron_ore")
            method: How it was found (e.g., "dig_down:32", "dig_tunnel:north:20")
            location: Where it was found {x, y, z}
        """
        key = target
        existing = self.search_successes.get(key, {})
        self.search_successes[key] = {
            "method": method,
            "location": location,
            "success_count": existing.get("success_count", 0) + 1,
            "last_used": time.time(),
        }
        self._save()
        print(f"🧠 Remembered: {target} found via {method}")

    def get_search_hint(self, target: str) -> Optional[dict]:
        """Get a past successful search method for a target.

        Returns:
            dict with "method" and optionally "location", or None
        """
        entry = self.search_successes.get(target)
        if entry:
            return entry
        return None

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
            lines.append(f"Past success finding {target}: {s['method']} (worked {s['success_count']}x)")
            if s.get("location"):
                loc = s["location"]
                lines.append(f"  Last found at: ({loc.get('x','?')}, {loc.get('y','?')}, {loc.get('z','?')})")

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
