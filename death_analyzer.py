"""
Death Analyzer — Learn from every death.

Flow:
  1. server.js captures death event → stores pre-death snapshot
  2. Agent detects death via /state or /death_log endpoint
  3. LLM analyzes the snapshot and generates a "lesson"
  4. Lesson is stored and injected into every future prompt

Architecture:
  DeathAnalyzer
    ├── DeathSnapshot     (state right before death)
    ├── DeathLesson       (what went wrong + how to avoid)
    └── LessonMemory      (persistent lessons injected into prompt)
"""

import time
import json
import requests
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DeathSnapshot:
    """State captured at or near time of death."""
    timestamp: float
    position: dict                    # {x, y, z}
    health_before: float              # last known health before death
    hunger_before: float
    time_of_day: str                  # "day" or "night"
    inventory: list[dict]             # [{name, count}, ...]
    nearby_entities: list[dict]       # [{type, distance}, ...]
    nearby_blocks: list[str]
    recent_actions: list[str]         # last 5 agent actions
    active_goal: str                  # what goal was active
    death_message: str                # Minecraft death message if available
    
    def summary(self) -> str:
        inv = ", ".join(f"{i['name']}x{i['count']}" for i in self.inventory[:10]) or "empty"
        mobs = ", ".join(f"{e['type']}({e['distance']}m)" for e in self.nearby_entities[:5]) or "none"
        actions = " → ".join(self.recent_actions[-5:]) or "none"
        
        return (
            f"=== DEATH SNAPSHOT ===\n"
            f"Time: {self.time_of_day} | Position: ({self.position.get('x','?')}, {self.position.get('y','?')}, {self.position.get('z','?')})\n"
            f"Health before death: {self.health_before}/20 | Hunger: {self.hunger_before}/20\n"
            f"Death message: {self.death_message}\n"
            f"Nearby mobs: {mobs}\n"
            f"Inventory: {inv}\n"
            f"Recent actions: {actions}\n"
            f"Active goal: {self.active_goal}\n"
            f"======================"
        )


@dataclass
class DeathLesson:
    """A lesson learned from a death."""
    id: int
    timestamp: float
    cause: str                        # "killed by zombie at night without weapons"
    lesson: str                       # "always craft sword before nightfall"
    prevention: str                   # specific actionable rule
    severity: str = "medium"          # low, medium, high
    times_relevant: int = 0           # how often this lesson was triggered

    def to_prompt_line(self) -> str:
        return f"- [{self.severity.upper()}] {self.lesson} (cause: {self.cause})"


class DeathAnalyzer:
    """
    Tracks deaths, analyzes causes, and maintains a lesson memory
    that gets injected into the agent's prompt.
    """

    LESSONS_FILE = "death_lessons.json"

    def __init__(self, bot_api: str = None, max_lessons: int = 10):
        self.bot_api = bot_api or os.getenv("BOT_API_URL", "http://localhost:3001")
        self.death_log: list[DeathSnapshot] = []
        self.lessons: list[DeathLesson] = []
        self.max_lessons = max_lessons
        self.next_lesson_id = 1
        self.recent_actions: list[str] = []     # rolling buffer of recent actions
        self.max_recent_actions = 20
        self._last_known_state: dict = {}       # cache last state for snapshot
        self._death_count = 0
        self._load_lessons()

    # ── Lesson Persistence ──

    def _save_lessons(self):
        """Save lessons to file for persistence across restarts."""
        data = {
            "death_count": self._death_count,
            "next_id": self.next_lesson_id,
            "lessons": [
                {
                    "id": l.id,
                    "timestamp": l.timestamp,
                    "cause": l.cause,
                    "lesson": l.lesson,
                    "prevention": l.prevention,
                    "severity": l.severity,
                    "times_relevant": l.times_relevant,
                }
                for l in self.lessons
            ]
        }
        try:
            with open(self.LESSONS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"⚠️ Failed to save death lessons: {e}")

    def _load_lessons(self):
        """Load lessons from file on startup."""
        try:
            if os.path.exists(self.LESSONS_FILE):
                with open(self.LESSONS_FILE, "r") as f:
                    data = json.load(f)
                self._death_count = data.get("death_count", 0)
                self.next_lesson_id = data.get("next_id", 1)
                for ld in data.get("lessons", []):
                    self.lessons.append(DeathLesson(
                        id=ld["id"],
                        timestamp=ld["timestamp"],
                        cause=ld["cause"],
                        lesson=ld["lesson"],
                        prevention=ld.get("prevention", ld["lesson"]),
                        severity=ld.get("severity", "medium"),
                        times_relevant=ld.get("times_relevant", 0),
                    ))
                if self.lessons:
                    print(f"📚 Loaded {len(self.lessons)} death lessons ({self._death_count} total deaths)")
        except Exception as e:
            print(f"⚠️ Failed to load death lessons: {e}")

    # ── State Tracking (call every tick) ──

    def record_action(self, action: str):
        """Record an action the agent took. Called after each tool execution."""
        self.recent_actions.append(f"[{time.strftime('%H:%M:%S')}] {action}")
        if len(self.recent_actions) > self.max_recent_actions:
            self.recent_actions.pop(0)

    def update_state_cache(self, state: dict):
        """Cache the latest world state for death snapshots."""
        self._last_known_state = state

    # ── Death Detection ──

    def check_for_death(self) -> Optional[DeathSnapshot]:
        """Check if the bot has died since last check. Returns snapshot if death detected."""
        try:
            r = requests.get(f"{self.bot_api}/death_log", timeout=5)
            data = r.json()
            
            deaths = data.get("deaths", [])
            if not deaths:
                return None
            
            latest = deaths[-1]
            death_time = latest.get("timestamp", 0)
            
            # Only process new deaths
            if death_time <= getattr(self, '_last_death_time', 0):
                return None
            self._last_death_time = death_time
            self._death_count += 1
            self._save_lessons()

            # Build snapshot from cached state + death info
            state = self._last_known_state
            snapshot = DeathSnapshot(
                timestamp=death_time,
                position=state.get("position", {"x": "?", "y": "?", "z": "?"}),
                health_before=latest.get("health_before", state.get("health", 0)),
                hunger_before=latest.get("hunger_before", state.get("food", 0)),
                time_of_day=state.get("time", "unknown"),
                inventory=state.get("inventory", []),
                nearby_entities=state.get("nearbyEntities", []),
                nearby_blocks=state.get("nearbyBlocks", []),
                recent_actions=list(self.recent_actions[-10:]),
                active_goal=latest.get("active_goal", "none"),
                death_message=latest.get("message", "unknown cause"),
            )
            
            self.death_log.append(snapshot)
            print(f"\n💀 Death #{self._death_count} detected!")
            print(snapshot.summary())
            
            return snapshot

        except Exception as e:
            # Fallback: check health == 0 from state
            try:
                r = requests.get(f"{self.bot_api}/state", timeout=5)
                state = r.json()
                if state.get("health", 20) <= 0:
                    snapshot = DeathSnapshot(
                        timestamp=time.time(),
                        position=state.get("position", {}),
                        health_before=0,
                        hunger_before=state.get("food", 0),
                        time_of_day=state.get("time", "unknown"),
                        inventory=state.get("inventory", []),
                        nearby_entities=state.get("nearbyEntities", []),
                        nearby_blocks=state.get("nearbyBlocks", []),
                        recent_actions=list(self.recent_actions[-10:]),
                        active_goal="unknown",
                        death_message="unknown",
                    )
                    self.death_log.append(snapshot)
                    self._death_count += 1
                    self._save_lessons()
                    return snapshot
            except:
                pass
            return None

    # ── Analysis ──

    def generate_analysis_prompt(self, snapshot: DeathSnapshot) -> str:
        """Generate a prompt for the LLM to analyze the death."""
        existing = self.get_lessons_prompt()
        
        return (
            f"You just DIED in Minecraft. Analyze what went wrong and how to prevent it.\n\n"
            f"{snapshot.summary()}\n\n"
            f"Existing lessons you already know:\n{existing}\n\n"
            f"Respond in this EXACT format (3 lines only):\n"
            f"CAUSE: <one sentence describing what killed you>\n"
            f"LESSON: <one actionable rule to follow in the future>\n"
            f"SEVERITY: <low/medium/high>\n\n"
            f"Examples:\n"
            f"CAUSE: Attacked by zombie and skeleton at night without armor or weapons\n"
            f"LESSON: Always craft a sword before nightfall; avoid open areas at night without gear\n"
            f"SEVERITY: high\n\n"
            f"CAUSE: Fell into a deep cave while exploring\n"
            f"LESSON: Move carefully near edges; always carry blocks for bridging gaps\n"
            f"SEVERITY: medium"
        )

    def add_lesson_from_llm_response(self, llm_response: str, snapshot: DeathSnapshot) -> str:
        """Parse LLM analysis response and store as a lesson."""
        lines = llm_response.strip().split("\n")
        
        cause = "unknown"
        lesson = "be more careful"
        severity = "medium"
        
        for line in lines:
            line = line.strip()
            if line.upper().startswith("CAUSE:"):
                cause = line[6:].strip()
            elif line.upper().startswith("LESSON:"):
                lesson = line[7:].strip()
            elif line.upper().startswith("SEVERITY:"):
                sev = line[9:].strip().lower()
                if sev in ("low", "medium", "high"):
                    severity = sev

        # Check for duplicate lessons
        for existing in self.lessons:
            if self._similar(existing.cause, cause) or self._similar(existing.lesson, lesson):
                existing.times_relevant += 1
                if severity == "high":
                    existing.severity = "high"
                self._save_lessons()
                return f"Similar lesson already exists (updated relevance): {existing.lesson}"

        new_lesson = DeathLesson(
            id=self.next_lesson_id,
            timestamp=time.time(),
            cause=cause,
            lesson=lesson,
            prevention=lesson,
            severity=severity,
        )
        self.next_lesson_id += 1
        self.lessons.append(new_lesson)

        # Trim old low-severity lessons if over limit
        if len(self.lessons) > self.max_lessons:
            self.lessons.sort(key=lambda l: (l.severity != "high", l.severity != "medium", -l.times_relevant))
            self.lessons = self.lessons[:self.max_lessons]

        self._save_lessons()
        return f"New lesson learned: [{severity.upper()}] {lesson}"

    def add_lesson_manual(self, cause: str, lesson: str, severity: str = "medium") -> str:
        """Manually add a lesson (e.g., from tool call)."""
        new_lesson = DeathLesson(
            id=self.next_lesson_id,
            timestamp=time.time(),
            cause=cause,
            lesson=lesson,
            prevention=lesson,
            severity=severity,
        )
        self.next_lesson_id += 1
        self.lessons.append(new_lesson)
        self._save_lessons()
        return f"Lesson added: [{severity.upper()}] {lesson}"

    # ── Prompt Generation ──

    def get_lessons_prompt(self) -> str:
        """Generate lessons context for injection into the agent prompt."""
        if not self.lessons:
            return "No lessons yet — you haven't died."

        lines = [
            f"⚠️ LESSONS FROM {self._death_count} PAST DEATH(S):",
            f"   (Follow these rules to survive longer!)",
        ]
        
        # High severity first
        for lesson in sorted(self.lessons, key=lambda l: {"high": 0, "medium": 1, "low": 2}.get(l.severity, 3)):
            lines.append(f"   {lesson.to_prompt_line()}")

        return "\n".join(lines)

    def get_death_stats(self) -> dict:
        """Get death statistics."""
        return {
            "total_deaths": self._death_count,
            "lessons_learned": len(self.lessons),
            "high_severity": sum(1 for l in self.lessons if l.severity == "high"),
            "recent_deaths": [
                {
                    "message": d.death_message,
                    "time_of_day": d.time_of_day,
                    "timestamp": d.timestamp,
                }
                for d in self.death_log[-5:]
            ],
        }

    # ── Internal ──

    @staticmethod
    def _similar(a: str, b: str, threshold: float = 0.5) -> bool:
        """Simple word overlap similarity check."""
        words_a = set(a.lower().split())
        words_b = set(b.lower().split())
        if not words_a or not words_b:
            return False
        overlap = len(words_a & words_b)
        return overlap / min(len(words_a), len(words_b)) > threshold