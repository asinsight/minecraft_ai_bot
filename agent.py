"""
Minecraft AI Agent v3 — with Multi-Step Goal Planning

Changes from v2:
  - GoalPlanner injects current goal/step context into every LLM prompt
  - LLM can set_goal, complete_step, fail_step, skip_step, cancel_goal
  - Agent prompt includes goal state so LLM knows what to work on
  - Survival interrupts: if health/hunger critical, auto-override with survival goal
"""

import time
import os
import json
import requests
from typing import Optional
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tools import ALL_TOOLS
from goal_tools import GOAL_TOOLS, planner
from death_tools import DEATH_TOOLS, analyzer
from memory_tools import MEMORY_TOOLS, memory
from grand_goal_tools import GRAND_GOAL_TOOLS, grand_manager

load_dotenv()

# ============================================
# CONFIG
# ============================================
LOCAL_LLM_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "http://192.168.86.250:12000")
LOCAL_LLM_API_KEY = os.getenv("LOCAL_LLM_API_KEY", "your-jwt-token")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "glm-4.7-flash:latest")
BOT_API = os.getenv("BOT_API_URL", "http://localhost:3001")
TICK_INTERVAL = int(os.getenv("TICK_INTERVAL", "5"))
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "10"))

# ============================================
# LLM SETUP
# ============================================
action_llm = ChatOpenAI(
    base_url=f"{LOCAL_LLM_BASE_URL}/api",
    api_key=LOCAL_LLM_API_KEY,
    model=LOCAL_LLM_MODEL,
    temperature=0.7,
    max_tokens=500,
)

# ============================================
# COMBINE ALL TOOLS
# ============================================
ALL_AGENT_TOOLS = ALL_TOOLS + GOAL_TOOLS + DEATH_TOOLS + MEMORY_TOOLS + GRAND_GOAL_TOOLS

# ============================================
# SYSTEM PROMPT (with goal context placeholder)
# ============================================
SYSTEM_PROMPT = """You are an autonomous AI bot playing Minecraft survival mode.
You have tools to perceive the world and take actions, plus a GOAL SYSTEM for multi-step plans.

═══════════════════════════════════════
GOAL SYSTEM RULES:
═══════════════════════════════════════
1. You have a GRAND GOAL — the ultimate objective (e.g., defeat Ender Dragon).
2. Tasks have DEPENDENCIES — some unlock after others are done.
3. You see ALL available tasks. YOU DECIDE which to work on based on your situation.
4. There is NO fixed order. Prioritize based on:
   - Survival first (health, food, shelter) if in danger
   - Efficiency (what resources are nearby right now?)
   - Opportunity (found diamonds early? Mine them if you can!)
   - Preparation (night coming? Build shelter instead of mining)
5. Use set_goal to start a task, complete steps, then complete_grand_task when done.
6. You can SWITCH tasks mid-way if the situation changes (e.g., attacked → stop mining, fight/flee)
7. After dying, reassess: do you need better gear? More food? A shelter first?
8. NEVER blindly repeat what failed. Adapt your approach.

═══════════════════════════════════════
SURVIVAL OVERRIDES (highest priority):
═══════════════════════════════════════
- Health < 5: IMMEDIATELY eat food or flee. Don't continue the goal.
- Hunger < 5: Find and eat food before continuing.
- Night + hostile mobs nearby: Fight or find shelter.
- Player request in chat: Help the player (but can resume goal after).

═══════════════════════════════════════
GRAND GOAL (BIG PICTURE):
═══════════════════════════════════════
{grand_goal_context}

═══════════════════════════════════════
CURRENT TASK STEPS:
═══════════════════════════════════════
{goal_context}

═══════════════════════════════════════
LESSONS FROM PAST DEATHS:
═══════════════════════════════════════
{death_lessons}

═══════════════════════════════════════
REMEMBERED LOCATIONS:
═══════════════════════════════════════
{location_memory}

═══════════════════════════════════════
TIPS:
═══════════════════════════════════════
- Call get_world_state first to know your situation.
- For crafting chains: logs → planks → sticks → tools. Use get_recipe to check.
- For iron/gold: mine ore → smelt_item("raw_iron") in furnace → iron_ingot → craft tools. You NEED fuel (coal or charcoal).
- To get charcoal without coal: smelt_item("oak_log") → charcoal (use planks as fuel).
- Don't repeat failed actions — try alternatives.
- Use send_chat to tell players what you're doing.
- SHELTER OPTIONS: build_shelter needs 20+ blocks. dig_shelter needs NOTHING — just digs underground. Use dig_shelter when you're desperate (night, no blocks).
- MINING: Use dig_down to reach ore levels (y<16 for diamonds, y<48 for iron). Use dig_tunnel for strip mining at ore level. Both auto-stop on lava.
- ALWAYS save_location when you place a crafting table, chest, furnace, or build a shelter.
- ALWAYS save_location when you find valuable resources (diamonds, iron vein, village).
- Use find_nearest_location to go back to your crafting table or shelter instead of making new ones.
- Before crafting, check if you already have a crafting table saved — go there instead of making a new one.
"""

# ============================================
# AGENT CREATION (recreated each tick with fresh goal context)
# ============================================
def create_agent_executor():
    """Create a fresh agent executor with all context injected."""
    grand_goal_context = grand_manager.get_prompt_context()
    goal_context = planner.get_prompt_context()
    death_lessons = analyzer.get_lessons_prompt()
    location_memory = memory.get_prompt_context()

    # Build system prompt with context injected
    system_msg = SYSTEM_PROMPT.replace("{grand_goal_context}", grand_goal_context) \
                              .replace("{goal_context}", goal_context) \
                              .replace("{death_lessons}", death_lessons) \
                              .replace("{location_memory}", location_memory)

    # Escape any remaining curly braces so ChatPromptTemplate doesn't treat them as variables
    system_msg = system_msg.replace("{", "{{").replace("}", "}}")

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_msg),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])

    agent = create_tool_calling_agent(action_llm, ALL_AGENT_TOOLS, prompt)

    return AgentExecutor(
        agent=agent,
        tools=ALL_AGENT_TOOLS,
        verbose=True,
        max_iterations=MAX_ITERATIONS,
        handle_parsing_errors=True,
        return_intermediate_steps=True,
    )


# ============================================
# SURVIVAL CHECK
# ============================================
def check_survival_override() -> Optional[str]:
    """Check if survival conditions require interrupting the current goal."""
    try:
        # Basic health/hunger check
        r = requests.get(f"{BOT_API}/state", timeout=5)
        state = r.json()
        health = state.get("health", 20)
        food = state.get("food", 20)

        if health < 5:
            return "⚠️ CRITICAL: Health is very low! Eat food immediately or flee from danger."
        if food < 5:
            return "⚠️ WARNING: Very hungry! Find and eat food before doing anything else."

        # Threat assessment
        t = requests.get(f"{BOT_API}/threat_assessment", timeout=5)
        threat = t.json()
        rec = threat.get("recommendation", "safe")

        if rec == "flee":
            return (
                f"🚨 FLEE NOW! {threat['reason']}\n"
                f"Run away from threats using explore (move in opposite direction). "
                f"Go to nearest shelter if you have one saved (find_nearest_location('shelter')). "
                f"If no shelter saved, use dig_shelter to quickly hide underground!"
            )
        if rec == "avoid":
            return (
                f"⚠️ AVOID COMBAT: {threat['reason']}\n"
                f"Don't engage enemies. Move away carefully or gear up first."
            )

        return None
    except Exception:
        return None


# ============================================
# MAIN LOOP
# ============================================

def run_autonomous_loop():
    print("=" * 60)
    print("🤖 Minecraft AI Agent v3 — Goal Planner Edition")
    print(f"🧠 Action LLM: {LOCAL_LLM_MODEL} @ {LOCAL_LLM_BASE_URL}")
    print(f"⏱️  Tick interval: {TICK_INTERVAL}s")
    print("=" * 60)

    tick = 0

    while True:
        tick += 1
        print(f"\n{'=' * 50}")
        print(f"🔄 Tick #{tick}")

        # Show goal status
        status = planner.get_status()
        if status["has_goal"]:
            print(f"🎯 Goal: {status['goal_name']} {status['progress']}")
            print(f"   Current: {status['current_step']}")
        else:
            print("🎯 No active goal")
        print(f"{'=' * 50}")

        try:
            # Check for death first
            death_snapshot = analyzer.check_for_death()

            # Cache latest state for death snapshots
            try:
                state_r = requests.get(f"{BOT_API}/state", timeout=5)
                analyzer.update_state_cache(state_r.json())
            except:
                pass

            # Check survival override
            survival_msg = check_survival_override()
            if survival_msg:
                input_msg = f"{survival_msg}\nDeal with this survival emergency first, then resume your goal."
            elif death_snapshot:
                # Reset current GoalPlanner task on death — situation has changed
                old_goal = None
                if planner.active_goal:
                    old_goal = planner.active_goal.name
                    planner.cancel_goal("Died — need to reassess")

                death_context = (
                    f"☠️ YOU JUST DIED! Analyze what happened and learn from it.\n"
                    f"{death_snapshot.summary()}\n\n"
                    f"INSTRUCTIONS:\n"
                    f"1. Call learn_from_death with the cause, lesson, and severity.\n"
                    f"2. Think carefully before resuming:\n"
                )

                if old_goal:
                    death_context += (
                        f"   - You were working on task '{old_goal}' — it has been cancelled.\n"
                        f"   - Should you retry it? Or do you need to prepare better first?\n"
                        f"   - Consider: Do you need better gear? Food? Shelter? A different approach?\n"
                    )

                death_context += (
                    f"3. Check get_grand_goal_status to see overall progress.\n"
                    f"4. Set a new goal with set_goal that addresses WHY you died.\n"
                    f"   For example: if you died from mobs → make a sword or shelter first.\n"
                    f"   If you died from hunger → find food first.\n"
                    f"   If you died from falling → be more careful with movement.\n"
                    f"5. You may also skip or reorder grand goal tasks if the current path is too dangerous."
                )

                input_msg = death_context
            elif status["has_goal"]:
                step = planner.active_goal.current_step if planner.active_goal else None
                if step:
                    import json as _json
                    args_str = _json.dumps(step.tool_args_hint)
                    input_msg = (
                        f"You have an active goal. Execute the current step:\n"
                        f"Step {step.id}: {step.description}\n"
                        f"Suggested tool: {step.tool_hint}({args_str})\n"
                        f"After executing, call complete_step if successful or fail_step if not.\n"
                        f"If this step is already done (check inventory), call skip_step."
                    )
                else:
                    input_msg = "Your goal seems complete. Check status and decide what's next."
            else:
                # No active GoalPlanner task — LLM decides what to do
                grand_status = grand_manager.get_status()
                if not grand_status.get("has_grand_goal"):
                    input_msg = (
                        "You have no grand goal set. Choose one:\n"
                        "- set_grand_goal('defeat_ender_dragon') — The ultimate challenge\n"
                        "- set_grand_goal('full_iron_gear') — Shorter: get full iron equipment\n"
                        "- set_grand_goal('cozy_base') — Build a nice base\n"
                        "Pick one and begin!"
                    )
                else:
                    available = grand_status.get("available_tasks", [])
                    input_msg = (
                        "You have no active task right now. Look at the GRAND GOAL section in your context.\n"
                        f"Available tasks you can start: {', '.join(available) if available else 'check get_grand_goal_status'}\n"
                        "Choose the BEST task based on your current situation:\n"
                        "- What resources are nearby? What do you already have?\n"
                        "- Is it night? Are there threats? Do you have food?\n"
                        "- Check your death lessons — avoid repeating mistakes.\n"
                        "- Use set_goal to start the task you chose, or act freely if none fit."
                    )

            # Create fresh executor with updated goal context
            tick_start = time.time()
            executor = create_agent_executor()
            result = executor.invoke({"input": input_msg})
            elapsed = time.time() - tick_start

            print(f"\n✅ Agent output: {result.get('output', 'No output')}")
            print(f"⏱️  Tick took {elapsed:.1f}s")

            # Record actions for death analysis
            steps = result.get("intermediate_steps", [])
            for step in steps:
                if len(step) >= 2:
                    action = step[0]
                    tool_name = getattr(action, 'tool', 'unknown')
                    tool_input = getattr(action, 'tool_input', '')
                    analyzer.record_action(f"{tool_name}({json.dumps(tool_input) if isinstance(tool_input, dict) else tool_input})")

        except KeyboardInterrupt:
            print("\n👋 Shutting down agent...")
            break
        except Exception as e:
            print(f"\n❌ Agent error: {e}")
            import traceback
            traceback.print_exc()

        print(f"\n⏳ Waiting {TICK_INTERVAL}s...")
        time.sleep(TICK_INTERVAL)


if __name__ == "__main__":
    run_autonomous_loop()