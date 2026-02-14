"""
Grand Goal Tools — LangChain tools for managing the big-picture game objective.
"""

from langchain.tools import tool
from grand_goal import GrandGoalManager

# Shared instance
grand_manager = GrandGoalManager()


@tool
def set_grand_goal(goal_name: str) -> str:
    """Set the ultimate game objective. Everything you do should work toward this.

    Available grand goals:
    - defeat_ender_dragon: The classic Minecraft endgame (6 phases, ~30 tasks)
    - full_iron_gear: Get complete iron tools and armor (2 phases)
    - cozy_base: Build a base with all crafting stations (2 phases)

    Args:
        goal_name: Name of the grand goal
    """
    return grand_manager.set_grand_goal(goal_name)


@tool
def complete_grand_task(task_id: str) -> str:
    """Mark a grand goal task as completed. Call this when you've finished
    a major milestone (e.g., after crafting a stone pickaxe, mark 'make_stone_pickaxe' done).
    This advances the phase and unlocks new tasks.

    Args:
        task_id: ID of the completed task (e.g., 'make_wooden_pickaxe', 'mine_diamonds')
    """
    return grand_manager.complete_task(task_id)


@tool
def skip_grand_task(task_id: str) -> str:
    """Skip a grand goal task (only for optional tasks or tasks already fulfilled).

    Args:
        task_id: ID of the task to skip
    """
    return grand_manager.skip_task(task_id)


@tool
def get_grand_goal_status() -> str:
    """Check overall progress toward the grand goal.
    Shows all phases, tasks, and what to do next."""
    return grand_manager.get_prompt_context()


GRAND_GOAL_TOOLS = [
    set_grand_goal,
    complete_grand_task,
    skip_grand_task,
    get_grand_goal_status,
]