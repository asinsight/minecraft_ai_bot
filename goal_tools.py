"""
Goal Tools — LangChain tools that let the LLM manage multi-step goals.

These tools wrap the GoalPlanner so the LLM can:
  - Start predefined goals (e.g., "make_wooden_pickaxe")
  - Complete / fail / skip steps
  - Check current goal status
  - Cancel goals

The planner instance is shared across all tools via module-level variable.
"""

from langchain.tools import tool
from goal_planner import GoalPlanner, GoalPriority

# Shared planner instance — imported by agent.py
planner = GoalPlanner()


@tool
def set_goal(goal_name: str) -> str:
    """Start a predefined multi-step goal. The bot will follow the steps one by one.

    Available goals:
    - get_wood: Gather wood logs and make planks
    - make_crafting_table: Make and place a crafting table
    - make_wooden_pickaxe: Full chain to craft wooden pickaxe
    - make_stone_pickaxe: Craft stone pickaxe (needs wooden pickaxe first)
    - make_iron_pickaxe: Full chain to craft iron pickaxe
    - find_food: Find animals, kill, eat
    - build_shelter: Build a simple shelter
    - full_tool_progression: Complete wood → stone → iron progression

    Args:
        goal_name: Name of the goal from the list above
    """
    return planner.set_goal_from_library(goal_name, GoalPriority.AUTONOMOUS, "autonomous")


@tool
def complete_step(result: str = "") -> str:
    """Mark the current goal step as completed and move to the next step.
    Call this AFTER you have successfully executed the current step's action.

    Args:
        result: Brief description of what was accomplished (e.g., "Mined 5 oak logs")
    """
    return planner.complete_current_step(result)


@tool
def fail_step(error: str = "") -> str:
    """Mark the current goal step as failed. The step will be retried up to 3 times.
    Call this when the action for the current step did NOT succeed.

    Args:
        error: What went wrong (e.g., "No oak logs found nearby")
    """
    return planner.fail_current_step(error)


@tool
def skip_step(reason: str = "") -> str:
    """Skip the current goal step because it's not needed.
    Use when the step is already done (e.g., already have planks in inventory).

    Args:
        reason: Why skipping (e.g., "Already have 10 oak_planks in inventory")
    """
    return planner.skip_current_step(reason)


@tool
def cancel_goal(reason: str = "") -> str:
    """Cancel the current goal entirely. Use when the goal is no longer relevant
    or cannot be completed.

    Args:
        reason: Why cancelling (e.g., "Player asked me to do something else")
    """
    return planner.cancel_goal(reason)


@tool
def get_goal_status() -> str:
    """Check the current goal status including which step we're on.
    Use this to understand what you should be doing right now."""
    status = planner.get_status()
    if not status["has_goal"]:
        return "No active goal. Use set_goal to start one, or decide what to do freely."
    return (
        f"Goal: {status['goal_name']} — {status['description']}\n"
        f"Progress: {status['progress']}\n"
        f"Current step: {status['current_step']}\n"
        f"Priority: {status['priority']} | Source: {status['source']}\n"
        f"Elapsed: {status['elapsed']}s / TTL: {status['ttl']}s"
    )


@tool
def list_available_goals() -> str:
    """List all predefined goals you can set. Use this to see what multi-step
    plans are available."""
    return planner.get_available_goals()


# All goal tools for registration
GOAL_TOOLS = [
    set_goal,
    complete_step,
    fail_step,
    skip_step,
    cancel_goal,
    get_goal_status,
    list_available_goals,
]