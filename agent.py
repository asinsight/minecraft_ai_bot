"""
Minecraft AI Agent v4 — Grand Goal Driven

- Grand Goal dependency graph tells what tasks are available
- Each tick: survival check → build input message → LLM executes with tools
- No GoalPlanner — LLM acts directly toward the current task
- Server enforces tool requirements (pickaxe for stone, etc.)
- Death lessons persist across restarts
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
from death_tools import DEATH_TOOLS, analyzer
from memory_tools import MEMORY_TOOLS, memory
from grand_goal_tools import GRAND_GOAL_TOOLS, grand_manager

load_dotenv()

# ============================================
# CONFIG
# ============================================
LOCAL_LLM_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "http://192.168.86.250:12000")
LOCAL_LLM_API_KEY = os.getenv("LOCAL_LLM_API_KEY", "your-jwt-token")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "qwen3:30b-a3b")
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
# ALL TOOLS (no GoalPlanner tools)
# ============================================
ALL_AGENT_TOOLS = ALL_TOOLS + DEATH_TOOLS + MEMORY_TOOLS + GRAND_GOAL_TOOLS

# ============================================
# SYSTEM PROMPT
# ============================================
SYSTEM_PROMPT = """You are a Minecraft survival bot. Your ONLY job is to complete the CURRENT TASK below.

STRICT RULES:
1. EVERY tick, work toward the CURRENT TASK. Do NOT do unrelated things.
2. If you need materials for the task, gather them. That counts as working toward it.
3. When a task is DONE, call complete_grand_task("task_id") to mark it complete.
4. Survival emergencies override everything: eat if hungry, flee if dying.
5. Call ONE tool at a time. Wait for the result.
6. You can CHANGE your grand goal anytime with set_grand_goal if:
   - Current goal is too hard for your gear level
   - You completed the current goal and want a new challenge
   - You want to try something different
   Available goals: defeat_ender_dragon, full_iron_gear, cozy_base

CRAFTING CHAIN:
  punch oak_log → craft oak_planks (4) → craft sticks (4) → craft crafting_table
  → place crafting_table → craft wooden_pickaxe → mine cobblestone (3)
  → craft stone_pickaxe → mine iron_ore (needs stone_pickaxe+)
  → craft furnace (8 cobblestone) → smelt raw_iron (needs fuel: coal or planks)
  → craft iron_pickaxe (3 iron_ingot + 2 sticks)

RULES:
- Stone/ore needs pickaxe. No pickaxe = craft one FIRST.
- Iron ore needs stone_pickaxe or better.
- Diamond ore needs iron_pickaxe or better.
- dig_shelter needs NO materials — use when desperate.
- NIGHT on surface = death. Go underground or use dig_shelter.
- save_location when you place crafting_table, furnace, chest, or shelter.
- find_nearest_location before crafting — reuse existing tables.

{grand_goal_context}

{death_lessons}

{location_memory}
"""

# ============================================
# AGENT CREATION
# ============================================
def create_agent_executor():
    grand_goal_context = grand_manager.get_prompt_context()
    death_lessons = analyzer.get_lessons_prompt()
    location_memory = memory.get_prompt_context()

    system_msg = SYSTEM_PROMPT.replace("{grand_goal_context}", grand_goal_context) \
                              .replace("{death_lessons}", death_lessons) \
                              .replace("{location_memory}", location_memory)

    # Escape curly braces for ChatPromptTemplate
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
    """Check if survival conditions require interrupting the current task."""
    try:
        r = requests.get(f"{BOT_API}/state", timeout=5)
        state = r.json()
        health = state.get("health", 20)
        food = state.get("food", 20)
        time_phase = state.get("time", "day")
        is_safe_outside = state.get("isSafeOutside", True)
        environment = state.get("environment", "surface")
        can_see_sky = state.get("canSeeSky", True)
        inventory = state.get("inventory", [])

        is_sheltered = environment in ("indoors", "underground", "deep_underground")

        has_weapon = any(i["name"].endswith(("_sword", "_axe")) for i in inventory)
        has_food = any(i["name"] in ("cooked_beef", "cooked_porkchop", "cooked_chicken",
            "cooked_mutton", "bread", "apple", "golden_apple", "cooked_salmon",
            "cooked_cod", "baked_potato", "sweet_berries") for i in inventory)
        inv_empty = len(inventory) == 0

        if health < 5:
            if has_food:
                return "EMERGENCY: Health very low! Call eat_food RIGHT NOW."
            return "EMERGENCY: Health very low, NO food! Use dig_shelter to hide. Do NOT fight."

        if food < 5:
            return "WARNING: Very hungry! Find and eat food first."

        # Threats
        t = requests.get(f"{BOT_API}/threat_assessment", timeout=5)
        threat = t.json()
        rec = threat.get("recommendation", "safe")
        threats = threat.get("threats", {})
        threat_count = threats.get("count", 0)
        threat_details = threats.get("details", [])

        has_phantoms = any(td.get("type") == "phantom" for td in threat_details)
        if has_phantoms and is_sheltered:
            is_sheltered = False

        if is_sheltered and threat_count > 0:
            closest_dist = min((td["distance"] for td in threat_details), default=99)
            if closest_dist > 10:
                return None
            elif closest_dist > 5:
                return "Mobs nearby but you're sheltered. STAY INSIDE."
            else:
                if has_weapon:
                    return "Mob inside shelter! Fight with attack_entity."
                return "Mob inside shelter, no weapon! dig_down(depth=3) to escape!"

        if rec == "flee" and not is_sheltered:
            return "FLEE! Use dig_shelter to hide underground NOW."
        if rec == "avoid" and not is_sheltered:
            return "Danger nearby. Move away or dig_shelter."

        if not is_safe_outside and not is_sheltered and can_see_sky:
            return "NIGHT on surface! Use dig_shelter immediately."

        if time_phase == "dusk" and not is_sheltered and can_see_sky:
            return "DUSK — night coming! dig_shelter or head to shelter."

        return None
    except Exception:
        return None


# ============================================
# BUILD TICK INPUT
# ============================================
def check_player_chat() -> Optional[str]:
    """Check for unread player chat messages. Returns message if any."""
    try:
        r = requests.get(f"{BOT_API}/chat/unread", timeout=5)
        data = r.json()
        if data.get("count", 0) > 0:
            messages = data["messages"]
            chat_lines = []
            for m in messages:
                chat_lines.append(f"{m['username']}: {m['message']}")
            return "\n".join(chat_lines)
        return None
    except Exception:
        return None


def build_input_message(survival_msg, death_snapshot):
    """Build the input message for this tick. Priority: chat > survival > death > task."""

    # HIGHEST PRIORITY: Player chat
    player_chat = check_player_chat()
    if player_chat:
        return (
            f"PLAYER MESSAGE (respond to this FIRST):\n"
            f"{player_chat}\n\n"
            f"Respond to the player using send_chat. Follow their instructions.\n"
            f"If they asked you to do something, do it. If they asked a question, answer it.\n"
            f"After responding, resume your current task."
        )

    if survival_msg:
        return f"{survival_msg}\nHandle this FIRST, then resume your task."

    if death_snapshot:
        return (
            f"YOU JUST DIED!\n{death_snapshot.summary()}\n\n"
            f"1. Call learn_from_death with cause, lesson, severity.\n"
            f"2. Then resume working — pick a task from get_grand_goal_status.\n"
            f"3. Adapt: died from mobs? Make weapon first. Hunger? Find food."
        )

    grand_status = grand_manager.get_status()

    if not grand_status.get("has_grand_goal"):
        return "No grand goal set. Call set_grand_goal('defeat_ender_dragon') NOW."

    available = grand_status.get("available_tasks", [])
    if not available:
        # Check if goal is complete
        if grand_status.get("completed_count", 0) >= grand_status.get("total_count", 1):
            return (
                "GRAND GOAL COMPLETE! Great job!\n"
                "Choose a harder goal with set_grand_goal:\n"
                "- full_iron_gear (if you haven't done it)\n"
                "- defeat_ender_dragon (the ultimate challenge)\n"
                "Pick one and call set_grand_goal NOW."
            )
        return "All tasks blocked. Call get_grand_goal_status to check what's needed."

    # Pick first available task
    task_id = available[0]
    task_info = None
    for t in grand_status.get("all_tasks", []):
        if t.get("id") == task_id:
            task_info = t
            break

    task_name = task_info.get("name", task_id) if task_info else task_id

    return (
        f"CURRENT TASK: {task_name} (id: {task_id})\n\n"
        f"Work on this NOW. Get what you need and do it.\n"
        f"When DONE, call complete_grand_task('{task_id}').\n"
        f"Do NOT do unrelated things."
    )


# ============================================
# MAIN LOOP
# ============================================
def run_autonomous_loop():
    print("=" * 60)
    print("🤖 Minecraft AI Agent v4 — Grand Goal Driven")
    print(f"🧠 LLM: {LOCAL_LLM_MODEL} @ {LOCAL_LLM_BASE_URL}")
    print(f"⏱️  Tick: {TICK_INTERVAL}s | Max iterations: {MAX_ITERATIONS}")
    print("=" * 60)

    tick = 0

    while True:
        tick += 1
        print(f"\n{'=' * 50}")
        print(f"🔄 Tick #{tick}")

        grand_status = grand_manager.get_status()
        if grand_status.get("has_grand_goal"):
            available = grand_status.get("available_tasks", [])
            completed = grand_status.get("completed_count", 0)
            total = grand_status.get("total_count", 0)
            print(f"🏆 {grand_status.get('goal_name', '?')} ({completed}/{total})")
            print(f"📋 Available: {', '.join(available[:5])}")
        else:
            print("🎯 No grand goal")
        print(f"{'=' * 50}")

        try:
            death_snapshot = analyzer.check_for_death()

            try:
                state_r = requests.get(f"{BOT_API}/state", timeout=5)
                analyzer.update_state_cache(state_r.json())
            except:
                pass

            survival_msg = check_survival_override()
            input_msg = build_input_message(survival_msg, death_snapshot)
            print(f"📨 {input_msg[:150]}")

            tick_start = time.time()
            executor = create_agent_executor()
            result = executor.invoke({"input": input_msg})
            elapsed = time.time() - tick_start

            print(f"\n✅ {result.get('output', 'No output')[:200]}")
            print(f"⏱️  {elapsed:.1f}s")

            steps = result.get("intermediate_steps", [])
            for step in steps:
                if len(step) >= 2:
                    action = step[0]
                    tool_name = getattr(action, 'tool', 'unknown')
                    tool_input = getattr(action, 'tool_input', '')
                    analyzer.record_action(
                        f"{tool_name}({json.dumps(tool_input) if isinstance(tool_input, dict) else tool_input})"
                    )

        except KeyboardInterrupt:
            print("\n👋 Shutting down...")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()

        print(f"\n⏳ {TICK_INTERVAL}s...")
        time.sleep(TICK_INTERVAL)


if __name__ == "__main__":
    run_autonomous_loop()