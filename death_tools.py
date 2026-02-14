"""
Death Tools — LangChain tools for death analysis and lesson management.

The LLM uses these to:
  - Check if it recently died
  - Analyze death cause
  - Store lessons learned
  - Review past lessons
"""

from langchain.tools import tool
from death_analyzer import DeathAnalyzer

# Shared analyzer instance — imported by agent.py
analyzer = DeathAnalyzer()


@tool
def check_death() -> str:
    """Check if the bot has recently died. Returns death details if a new death is detected.
    Call this at the start of each tick to check for deaths."""
    snapshot = analyzer.check_for_death()
    if snapshot:
        return (
            f"☠️ YOU DIED! Here's what happened:\n"
            f"{snapshot.summary()}\n\n"
            f"Now analyze this death and call learn_from_death with the cause and lesson."
        )
    return "No recent death detected."


@tool
def learn_from_death(cause: str, lesson: str, severity: str = "medium") -> str:
    """Store a lesson learned from a death. This lesson will be remembered
    and shown to you in every future tick to prevent the same mistake.

    Args:
        cause: What caused the death (e.g., "attacked by zombies at night without weapons")
        lesson: Actionable rule to follow (e.g., "always craft sword before nightfall")
        severity: How critical — 'low', 'medium', or 'high'
    """
    if severity not in ("low", "medium", "high"):
        severity = "medium"
    return analyzer.add_lesson_manual(cause, lesson, severity)


@tool
def get_lessons() -> str:
    """Review all lessons learned from past deaths.
    Use this to remember what mistakes to avoid."""
    return analyzer.get_lessons_prompt()


@tool
def get_death_stats() -> str:
    """Get death statistics — total deaths, lessons learned, recent death causes."""
    stats = analyzer.get_death_stats()
    if stats["total_deaths"] == 0:
        return "No deaths yet! Keep it up."

    lines = [
        f"Total deaths: {stats['total_deaths']}",
        f"Lessons learned: {stats['lessons_learned']} ({stats['high_severity']} high severity)",
        "",
        "Recent deaths:"
    ]
    for d in stats["recent_deaths"]:
        lines.append(f"  - {d['message']} ({d['time_of_day']})")

    return "\n".join(lines)


# All death tools for registration
DEATH_TOOLS = [
    check_death,
    learn_from_death,
    get_lessons,
    get_death_stats,
]