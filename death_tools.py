"""
Death Tools — LangChain tools for death analysis and lesson management.
"""

from langchain.tools import tool
from death_analyzer import DeathAnalyzer

analyzer = DeathAnalyzer()


@tool
def check_death() -> str:
    """Check if the bot has recently died. Returns death details if new death detected."""
    snapshot = analyzer.check_for_death()
    if snapshot:
        return f"YOU DIED!\n{snapshot.summary()}\nCall learn_from_death with cause and lesson."
    return "No recent death."


@tool
def learn_from_death(cause: str, lesson: str, severity: str = "medium") -> str:
    """Store a lesson from death. Remembered forever.
    Args:
        cause: What killed you
        lesson: Rule to follow next time
        severity: low, medium, or high
    """
    if severity not in ("low", "medium", "high"):
        severity = "medium"
    return analyzer.add_lesson_manual(cause, lesson, severity)


@tool
def get_lessons() -> str:
    """Review all death lessons."""
    return analyzer.get_lessons_prompt()


@tool
def get_death_stats() -> str:
    """Get death statistics."""
    stats = analyzer.get_death_stats()
    if stats["total_deaths"] == 0:
        return "No deaths yet!"
    lines = [
        f"Deaths: {stats['total_deaths']}, Lessons: {stats['lessons_learned']}",
    ]
    for d in stats["recent_deaths"]:
        lines.append(f"  - {d['message']} ({d['time_of_day']})")
    return "\n".join(lines)


DEATH_TOOLS = [check_death, learn_from_death, get_lessons, get_death_stats]
