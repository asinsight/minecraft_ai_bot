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
        self._load()

    def _save(self):
        try:
            data = {
                "search_successes": self.search_successes,
                "error_solutions": self.error_solutions,
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
                count = len(self.search_successes) + len(self.error_solutions)
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
        if not lines:
            return ""
        return "\n".join(lines)

    def clear(self):
        """Clear all experience memory."""
        self.search_successes = {}
        self.error_solutions = {}
        self._save()
