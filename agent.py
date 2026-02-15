"""
Minecraft AI Agent v6 — Chain of Action Architecture.

3-Layer Execution:
  Layer 0 (Instinct):  Immediate survival. No LLM. ~0ms.
  Layer 1 (Chain):     Execute hardcoded chains. No LLM. ~1-2s per step.
  Layer 2 (Planning):  LLM decides next objective / handles novel failures. ~5-15s.

LLM is only called when:
  - Chain completes → "What next?"
  - Chain fails with no known solution → "How to fix this?"
  - Death → "What went wrong? What now?"
  - Player chat → "How to respond?"
  - No grand goal → "What should we do?"

Everything else runs automatically.
"""

import time
import os
import sys
import json
import requests

# Fix Windows console encoding for emoji output
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from typing import Optional
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage

from chain_executor import ChainExecutor, check_instinct, get_bot_state, get_threat_assessment, TickResult
from chain_library import get_chain, list_available_chains
from experience_memory import ExperienceMemory
from grand_goal import GrandGoalManager, TaskStatus
from death_analyzer import DeathAnalyzer
from spatial_memory import SpatialMemory
from tools import ALL_TOOLS
from death_tools import DEATH_TOOLS
from memory_tools import MEMORY_TOOLS

load_dotenv()

# ============================================
# CONFIG
# ============================================
LOCAL_LLM_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "http://192.168.86.250:12000")
LOCAL_LLM_API_KEY = os.getenv("LOCAL_LLM_API_KEY", "your-jwt-token")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "qwen3:30b-a3b")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
BOT_API = os.getenv("BOT_API_URL", "http://localhost:3001")
TICK_INTERVAL = float(os.getenv("TICK_INTERVAL", "3"))  # faster now — chains are quick
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "5"))

# ============================================
# SHARED INSTANCES
# ============================================
goal_manager = GrandGoalManager()
experience = ExperienceMemory()
death_analyzer = DeathAnalyzer()
spatial_memory = SpatialMemory()
chain_executor = ChainExecutor(experience, goal_manager)
_consecutive_blocked = 0  # counter for consecutive "All tasks blocked" ticks
_pending_goal_request: Optional[str] = None  # Chat Agent → Planning Agent bridge

# ============================================
# LLM (only used in Layer 2)
# ============================================
llm = ChatOpenAI(
    base_url=f"{LOCAL_LLM_BASE_URL}/api",
    api_key=LOCAL_LLM_API_KEY,
    model=LOCAL_LLM_MODEL,
    temperature=0.3,
    max_tokens=2000,
)

# LangChain tools — only for LLM layer 2
# Grand goal tools are inline here since LLM needs them
from langchain.tools import tool as lc_tool

@lc_tool
def set_grand_goal(goal_name: str) -> str:
    """Set a grand goal from saved goals. Use list_saved_goals() to see available goals.
    If no matching goal exists, use create_custom_grand_goal() instead."""
    global _pending_goal_request
    is_user = _pending_goal_request is not None
    result = goal_manager.set_grand_goal(goal_name, user_requested=is_user)
    if is_user:
        _pending_goal_request = None
    return result

@lc_tool
def complete_grand_task(task_id: str) -> str:
    """Manually mark a task complete (for tasks without auto-check)."""
    return goal_manager.complete_task(task_id)

@lc_tool
def skip_grand_task(task_id: str) -> str:
    """Skip a task."""
    return goal_manager.skip_task(task_id)

@lc_tool
def get_grand_goal_status() -> str:
    """Check overall goal progress."""
    return goal_manager.get_prompt_context()

@lc_tool
def choose_next_chain(chain_name: str) -> str:
    """Start an action chain. The chain will execute automatically without LLM.
    Available chains: get_wood, mine_stone, make_crafting_table, make_wooden_pickaxe,
    make_stone_pickaxe, make_iron_pickaxe, make_iron_sword, make_iron_armor,
    make_shield, make_bucket, mine_diamonds, make_diamond_pickaxe,
    make_diamond_sword, find_food, build_shelter, place_furnace, place_chest"""
    result = chain_executor.start_chain(chain_name)
    return result

@lc_tool
def list_saved_goals() -> str:
    """List all saved goals in the goal library.
    Check this before creating a new goal — a similar one might exist."""
    goals = goal_manager.goal_library.list_goals()
    if not goals:
        return "No saved goals."
    lines = ["Saved goals:"]
    for g in goals:
        lines.append(f"  - {g['name']}: {g['description']} [{g['source']}, {g['task_count']} tasks]")
    return "\n".join(lines)

@lc_tool
def find_similar_goals(description: str) -> str:
    """Search saved goals for ones similar to a description.
    Use this BEFORE creating a new goal to avoid duplicates.

    Args:
        description: What the goal is about (e.g., 'build a house', 'get diamond armor')
    """
    matches = goal_manager.goal_library.find_similar(description)
    if matches:
        details = []
        for name in matches:
            data = goal_manager.goal_library.goals.get(name, {})
            details.append(f"  - {name}: {data.get('description', '?')} ({len(data.get('tasks', []))} tasks)")
        return f"Similar goals found:\n" + "\n".join(details)
    return "No similar goals found. Create a new one with create_custom_grand_goal()."

@lc_tool
def create_custom_grand_goal(name: str, description: str, phases_json: str, tasks_json: str) -> str:
    """Create a brand new grand goal with custom tasks and save it to the library.

    IMPORTANT: tasks must use ONLY these chain_name values (or "" for LLM-handled tasks):
      get_wood, mine_stone, make_crafting_table, make_wooden_pickaxe,
      make_stone_pickaxe, make_iron_pickaxe, make_iron_sword, make_iron_armor,
      make_shield, make_bucket, mine_diamonds, make_diamond_pickaxe,
      make_diamond_sword, find_food, build_shelter, place_furnace, place_chest

    Args:
        name: Unique goal name (snake_case, e.g., 'diamond_armor_set')
        description: Human-readable description
        phases_json: JSON array of phases, e.g. [{"id":"p1","name":"Phase 1","description":"Basics"}]
        tasks_json: JSON array of tasks, each with:
            id (str), description (str), chain_name (str), requires (list[str]),
            phase (str), optional (bool), completion_items (dict), completion_blocks_placed (list[str])
    """
    try:
        phases = json.loads(phases_json)
        tasks = json.loads(tasks_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    global _pending_goal_request
    is_user = _pending_goal_request is not None

    result = goal_manager.create_grand_goal(
        name=name, description=description,
        phases=phases, tasks=tasks,
        user_requested=is_user,
        save_to_library=True,
    )

    if is_user:
        _pending_goal_request = None
    return result

@lc_tool
def request_custom_goal(description: str) -> str:
    """Request a custom goal based on a player's chat message.
    The planning agent will design tasks for this goal next tick.

    Args:
        description: What the player wants (e.g., 'build a big house', 'get full diamond gear')
    """
    global _pending_goal_request
    _pending_goal_request = description
    return f"Goal request queued: '{description}'. Planning will start next tick."

LLM_TOOLS = (
    ALL_TOOLS + DEATH_TOOLS + MEMORY_TOOLS +
    [set_grand_goal, complete_grand_task, skip_grand_task,
     get_grand_goal_status, choose_next_chain,
     list_saved_goals, find_similar_goals, create_custom_grand_goal]
)

# ============================================
# LLM AGENT (created once, reused)
# ============================================

SYSTEM_PROMPT = """You are a Minecraft AI bot's planning brain. You are NOT called every tick — only when a decision is needed.

YOUR JOB: Decide what to do next, then call choose_next_chain() to start an action chain.
The chain will execute automatically without you. You'll be called again when it finishes.

WHEN YOU'RE CALLED:
1. "Chain completed" → Pick the next chain based on the grand goal progress
2. "Chain failed" → Analyze the problem, try a different approach
3. "You died" → Call learn_from_death, then pick next chain
4. "Player says..." → Respond with send_chat, then resume
5. "No grand goal" → Pick from saved goals or create a new one
6. "Player requested a custom goal" → Check similar goals first, then set or create

GOAL MANAGEMENT:
- list_saved_goals() → see all saved goals in the library
- find_similar_goals(description) → search for matching goals before creating new ones
- set_grand_goal(name) → activate a saved goal
- create_custom_grand_goal(name, description, phases_json, tasks_json) → create a NEW goal

WHEN CREATING A NEW GOAL:
1. FIRST call find_similar_goals() to check if a similar goal already exists
2. If found → use set_grand_goal(name) with the matching goal
3. If not → call create_custom_grand_goal() to design tasks
4. Tasks MUST use valid chain_name values (listed below) or "" for LLM-handled tasks
5. Tasks with "requires" must reference other task IDs in the same goal
6. Set completion_items for auto-completion (inventory check) when possible

IMPORTANT RULES:
- ALWAYS call choose_next_chain() with one of the available chains
- Match the chain to the current grand goal's next available task
- The chain name should match the task's chain_name in the goal progress
- If a chain failed, ANALYZE why it failed and try a DIFFERENT approach
- Keep responses SHORT — you're a planner, not a narrator

AVAILABLE CHAINS (for choose_next_chain AND task chain_name):
  Gathering: get_wood, mine_stone
  Basic Tools: make_crafting_table, make_wooden_pickaxe, make_stone_pickaxe
  Iron: make_iron_pickaxe, make_iron_sword, make_iron_armor, make_shield, make_bucket
  Diamond: mine_diamonds, make_diamond_pickaxe, make_diamond_sword
  Building: build_shelter, place_furnace, place_chest
  Survival: find_food

CRAFTING ORDER: wood → crafting_table → wooden_pickaxe → stone_pickaxe → iron_pickaxe → diamond_pickaxe
Each requires the previous. Don't skip steps."""

def create_llm_agent() -> AgentExecutor:
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    agent = create_tool_calling_agent(llm, LLM_TOOLS, prompt)
    return AgentExecutor(
        agent=agent,
        tools=LLM_TOOLS,
        verbose=True,
        max_iterations=MAX_ITERATIONS,
        handle_parsing_errors=True,
        return_intermediate_steps=True,
    )

llm_agent = create_llm_agent()
chat_history: list = []
MAX_HISTORY = 8  # keep last N turns


def trim_history():
    global chat_history
    if len(chat_history) > MAX_HISTORY * 2:
        chat_history = chat_history[-(MAX_HISTORY * 2):]


# ============================================
# LAYER 2: LLM PLANNING
# ============================================

def call_llm_planner(reason: str, context: str = "") -> str:
    """Call the LLM to make a decision. Returns LLM output."""
    global chat_history

    # Build input
    parts = [f"REASON: {reason}"]
    
    goal_ctx = goal_manager.get_prompt_context()
    parts.append(f"\n{goal_ctx}")

    death_ctx = death_analyzer.get_lessons_prompt()
    if "LESSONS" in death_ctx:
        parts.append(f"\n{death_ctx}")

    loc_ctx = spatial_memory.get_prompt_context()
    if "KNOWN LOCATIONS" in loc_ctx:
        parts.append(f"\n{loc_ctx}")

    if context:
        parts.append(f"\nDETAILS: {context}")

    # Current bot state (HP, food, position, time)
    try:
        state_r = requests.get(f"{BOT_API}/state", timeout=5).json()
        pos = state_r.get("position", {})
        hp = state_r.get("health", "?")
        food = state_r.get("food", "?")
        time_of_day = state_r.get("timeOfDay", 0)
        day_phase = "night" if 13000 <= time_of_day <= 23000 else "day"
        parts.append(f"\nBOT STATE: HP={hp}/20, Food={food}/20, "
                     f"Pos=({pos.get('x','?'):.0f}, {pos.get('y','?'):.0f}, {pos.get('z','?'):.0f}), "
                     f"Time={day_phase} ({time_of_day})")
    except:
        pass

    # Current inventory summary
    try:
        r = requests.get(f"{BOT_API}/inventory", timeout=5)
        items = r.json().get("items", [])
        inv_str = ", ".join(f"{i['name']}x{i['count']}" for i in items[:20]) or "empty"
        parts.append(f"\nINVENTORY: {inv_str}")
    except:
        pass

    # Threat & combat context
    try:
        threat_r = requests.get(f"{BOT_API}/threat_assessment", timeout=3).json()
        t_rec = threat_r.get("recommendation", "safe")
        t_reason = threat_r.get("reason", "")
        t_count = threat_r.get("threats", {}).get("count", 0)
        t_details = threat_r.get("threats", {}).get("details", [])
        if t_count > 0:
            mobs_str = ", ".join(f"{t['type']}({t['distance']}m)" for t in t_details[:5])
            parts.append(f"\nTHREATS: {t_rec} — {t_reason}")
            parts.append(f"  Nearby hostiles: {mobs_str}")
            readiness = threat_r.get("combat_readiness", {})
            parts.append(f"  Combat: weapon={readiness.get('weapon','none')}, "
                        f"armor={readiness.get('armor_points',0)}, "
                        f"power={readiness.get('player_power',0)}")

        combat_r = requests.get(f"{BOT_API}/combat_status", timeout=3).json()
        if combat_r.get("isUnderAttack"):
            attacker = combat_r.get("lastAttacker", {})
            parts.append(f"\n⚠️ UNDER ATTACK by {attacker.get('type','unknown')} "
                        f"({attacker.get('distance','?')}m)! "
                        f"Recent damage: {combat_r.get('healthDelta', 0)} HP")
    except:
        pass

    parts.append("\nDecide what to do. Call choose_next_chain() to start a chain, "
                 "or use other tools if needed.")

    input_msg = "\n".join(parts)
    print(f"\n🧠 LLM CALL: {reason}")
    print(f"   Input: {input_msg[:200]}...")

    try:
        tick_start = time.time()
        result = llm_agent.invoke({
            "input": input_msg,
            "chat_history": chat_history,
        })
        elapsed = time.time() - tick_start

        output = result.get("output", "No output")
        print(f"   🧠 LLM ({elapsed:.1f}s): {output[:200]}")

        # Update history
        chat_history.append(HumanMessage(content=input_msg[:500]))
        chat_history.append(AIMessage(content=output[:500]))
        trim_history()

        # Record actions for death analyzer + save solutions to experience
        api_tools = {"mine_block", "craft_item", "smelt_item", "place_block",
                     "equip_item", "eat_food", "move_to", "explore",
                     "dig_down", "dig_tunnel", "build_shelter", "dig_shelter",
                     "attack_entity", "find_block"}
        solution_steps = []
        for step in result.get("intermediate_steps", []):
            if len(step) >= 2:
                action = step[0]
                tool_name = getattr(action, 'tool', 'unknown')
                death_analyzer.record_action(f"LLM:{tool_name}")
                # Capture API tool calls as potential solution chain
                if tool_name in api_tools:
                    tool_input = getattr(action, 'tool_input', {})
                    if isinstance(tool_input, str):
                        try:
                            tool_input = json.loads(tool_input)
                        except:
                            tool_input = {}
                    solution_steps.append({"tool": tool_name, "args": tool_input, "type": "action"})

        # Save solution to experience if escalated from a chain failure
        if solution_steps and "failed" in reason.lower():
            # Extract error keyword from context
            error_keyword = ""
            for line in context.split("\n"):
                if line.startswith("Error:"):
                    error_keyword = line[6:].strip()[:60].lower()
                    break
                elif "no suitable position" in line.lower():
                    error_keyword = "no suitable position"
                    break
            step_tool = ""
            for line in context.split("\n"):
                if line.startswith("Step:"):
                    # e.g. "Step: place_block({'block_name': 'furnace'})"
                    step_tool = line.split("(")[0].replace("Step:", "").strip()
                    break
            if error_keyword and step_tool:
                experience.record_error_solution(step_tool, error_keyword, solution_steps)
                print(f"   🧠 Saved LLM solution: {step_tool}:{error_keyword} → {len(solution_steps)} steps")

        return output
    except Exception as e:
        print(f"   ❌ LLM error: {e}")
        return f"LLM error: {e}"


# ============================================
# CLAUDE CHAT AGENT (player conversations)
# ============================================

CHAT_SYSTEM_PROMPT = """You are PenguinBot, a friendly Minecraft AI bot. A player is talking to you in-game.

YOUR PERSONALITY:
- Friendly, helpful, and concise
- You speak naturally in the same language the player uses
- You know what you're currently doing and can explain it

YOUR CAPABILITIES:
- You can respond to the player via send_chat()
- You can change what you're doing based on player requests:
  - set_grand_goal() to switch to a saved goal
  - request_custom_goal(description) to request a custom goal
    Use this when the player asks for something not in saved goals:
    "build me a castle", "get full diamond gear", "make a farm", etc.
    The planning agent will design tasks next tick.
  - choose_next_chain() to start a specific action
  - skip_grand_task() to skip tasks
  - complete_grand_task() to mark tasks done
- You see your current state, inventory, and goal progress

RULES:
- ALWAYS call send_chat() to respond to the player
- Keep chat messages SHORT (under 100 chars, Minecraft chat is small)
- If the player asks you to do something specific, change your goal/chain accordingly
- If the player is just chatting, respond friendly and continue what you were doing
- You can send multiple chat messages if needed (split long responses)"""

# Claude chat tools: chat + goal/chain management
CHAT_TOOLS = [
    set_grand_goal, complete_grand_task, skip_grand_task,
    get_grand_goal_status, choose_next_chain,
    request_custom_goal,
]
# Add send_chat from ALL_TOOLS
for t in ALL_TOOLS:
    if hasattr(t, 'name') and t.name == 'send_chat':
        CHAT_TOOLS.append(t)
        break

claude_chat_agent = None
claude_chat_history: list = []

def _get_claude_chat_agent() -> Optional[AgentExecutor]:
    """Lazy-init Claude chat agent (only when needed)."""
    global claude_chat_agent
    if claude_chat_agent is not None:
        return claude_chat_agent
    if not ANTHROPIC_API_KEY:
        print("   ⚠️ ANTHROPIC_API_KEY not set, falling back to local LLM for chat")
        return None
    try:
        claude_llm = ChatAnthropic(
            model="claude-sonnet-4-20250514",
            api_key=ANTHROPIC_API_KEY,
            temperature=0.5,
            max_tokens=300,
        )
        prompt = ChatPromptTemplate.from_messages([
            ("system", CHAT_SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])
        agent = create_tool_calling_agent(claude_llm, CHAT_TOOLS, prompt)
        claude_chat_agent = AgentExecutor(
            agent=agent,
            tools=CHAT_TOOLS,
            verbose=True,
            max_iterations=3,
            handle_parsing_errors=True,
            return_intermediate_steps=True,
        )
        print("   🤖 Claude chat agent initialized")
        return claude_chat_agent
    except Exception as e:
        print(f"   ❌ Claude init failed: {e}, falling back to local LLM")
        return None


def call_claude_chat(player_message: str) -> str:
    """Handle player chat with Claude API. Falls back to local LLM if unavailable."""
    global claude_chat_history

    agent = _get_claude_chat_agent()
    if not agent:
        # Fallback to local LLM
        return call_llm_planner("Player message",
                                f"CHAT: {player_message}\nRespond with send_chat then resume task.")

    # Build context
    parts = [f"PLAYER MESSAGE: {player_message}"]

    goal_ctx = goal_manager.get_prompt_context()
    parts.append(f"\n{goal_ctx}")

    chain_status = chain_executor.get_status_str()
    parts.append(f"\nCURRENT ACTION: {chain_status}")

    try:
        state_r = requests.get(f"{BOT_API}/state", timeout=5).json()
        pos = state_r.get("position", {})
        hp = state_r.get("health", "?")
        food = state_r.get("food", "?")
        parts.append(f"\nBOT STATE: HP={hp}/20, Food={food}/20, "
                     f"Pos=({pos.get('x','?'):.0f}, {pos.get('y','?'):.0f}, {pos.get('z','?'):.0f})")
    except:
        pass

    try:
        r = requests.get(f"{BOT_API}/inventory", timeout=5)
        items = r.json().get("items", [])
        inv_str = ", ".join(f"{i['name']}x{i['count']}" for i in items[:15]) or "empty"
        parts.append(f"\nINVENTORY: {inv_str}")
    except:
        pass

    input_msg = "\n".join(parts)
    print(f"\n💬 CLAUDE CHAT: {player_message}")

    try:
        tick_start = time.time()
        result = agent.invoke({
            "input": input_msg,
            "chat_history": claude_chat_history[-6:],  # last 3 turns
        })
        elapsed = time.time() - tick_start

        output = result.get("output", "No output")
        print(f"   💬 Claude ({elapsed:.1f}s): {output[:200]}")

        # Update chat history
        claude_chat_history.append(HumanMessage(content=input_msg[:500]))
        claude_chat_history.append(AIMessage(content=output[:500]))
        if len(claude_chat_history) > 12:
            claude_chat_history = claude_chat_history[-12:]

        return output
    except Exception as e:
        print(f"   ❌ Claude error: {e}")
        # Fallback to local LLM
        return call_llm_planner("Player message",
                                f"CHAT: {player_message}\nRespond with send_chat then resume task.")


# ============================================
# PLAYER CHAT CHECK
# ============================================

def check_player_chat() -> Optional[str]:
    try:
        r = requests.get(f"{BOT_API}/chat/unread", timeout=5)
        data = r.json()
        if data.get("count", 0) > 0:
            bot_name = os.getenv("BOT_USERNAME", "PenguinBot").lower()
            lines = []
            for m in data["messages"]:
                username = m.get("username", "").strip()
                message = m.get("message", "").strip()
                # Skip bot's own messages, empty usernames, and system messages
                if not username or username.lower() == bot_name:
                    continue
                if not message:
                    continue
                # Skip common server system messages
                if any(kw in message.lower() for kw in [
                    "joined the game", "left the game", "survival mode",
                    "creative mode", "game mode", "set the time",
                    "server", "issued server command"
                ]):
                    continue
                lines.append(f"{username}: {message}")
            if lines:
                return "\n".join(lines)
    except:
        pass
    return None


# ============================================
# DEATH CHECK
# ============================================

def check_death() -> bool:
    """Returns True if bot just died."""
    snapshot = death_analyzer.check_for_death()
    if snapshot:
        print(f"\n💀 DEATH DETECTED!")
        print(snapshot.summary())
        chain_executor.cancel_chain("died")
        goal_manager.current_task_id = None
        # LLM analyzes death
        call_llm_planner(
            "YOU JUST DIED",
            f"Death: {snapshot.death_message}\n{snapshot.summary()}\n"
            f"Call learn_from_death with the cause and lesson, "
            f"then choose_next_chain to resume."
        )
        return True
    return False


# ============================================
# MAIN TICK LOGIC
# ============================================

def tick_once(tick_num: int):
    """Execute one tick of the bot."""

    # Cache state
    state = get_bot_state()
    if not state:
        print("   ⚠️ Cannot reach bot API")
        return
    death_analyzer.update_state_cache(state)
    threat = get_threat_assessment()

    # ── Auto-progress check (inventory scan) ──
    progress_msgs = goal_manager.auto_check_progress()
    for msg in progress_msgs:
        print(f"   {msg}")

    # If current task was auto-completed, clear chain
    current_task = goal_manager.get_current_task()
    if chain_executor.has_active_chain() and not current_task:
        # Task got auto-completed while chain was running
        chain_executor.cancel_chain("task auto-completed")

    # ── Layer 0: Instinct ──
    instinct_result = check_instinct(state, threat)
    if instinct_result:
        print(f"   ⚡ L0 INSTINCT: {instinct_result.action}")
        print(f"      → {instinct_result.result[:100]}")
        death_analyzer.record_action(f"instinct:{instinct_result.action}")

        # Record combat encounters to experience memory
        action_str = instinct_result.action
        if "attack_entity" in action_str or "flee()" in action_str:
            combat_info = state.get("combat", {})
            attacker = combat_info.get("lastAttacker", {})
            mob_type = attacker.get("type", "unknown") if attacker else "unknown"
            # Extract mob type from action string if not in combat state
            if mob_type == "unknown" and "attack_entity(" in action_str:
                try:
                    mob_type = action_str.split("attack_entity(")[1].split(")")[0]
                except (IndexError, ValueError):
                    pass
            outcome = "won" if instinct_result.success and "attack" in action_str else "fled"
            hp_delta = combat_info.get("healthDelta", 0)
            weapon = threat.get("combat_readiness", {}).get("weapon", "fist")
            armor = threat.get("combat_readiness", {}).get("armor_points", 0) > 0
            time_of_day = state.get("time", "day")
            experience.record_combat(
                mob_type=mob_type, outcome=outcome,
                position=state.get("position"), damage_taken=hp_delta,
                time_of_day=time_of_day, weapon_used=weapon, had_armor=armor
            )

        return  # Don't do anything else this tick

    # ── Death check ──
    if check_death():
        return

    # ── Player chat → Claude API ──
    player_chat = check_player_chat()
    if player_chat:
        call_claude_chat(player_chat)
        return

    # ── Pending goal request (from chat agent) → Planning LLM ──
    global _pending_goal_request
    if _pending_goal_request:
        pending_desc = _pending_goal_request
        goals = goal_manager.goal_library.list_goals()
        goal_list = "\n".join(f"  - {g['name']}: {g['description']} ({g['task_count']} tasks)" for g in goals)
        call_llm_planner(
            "Player requested a custom goal",
            f'A player asked: "{pending_desc}"\n'
            f'This is a USER REQUEST — it has priority over auto-selected goals.\n\n'
            f'STEPS:\n'
            f'1. Call find_similar_goals("{pending_desc}") to check saved goals\n'
            f'2. If a similar goal exists → call set_grand_goal(name)\n'
            f'3. If not → call create_custom_grand_goal() with proper tasks\n\n'
            f'Saved goals:\n{goal_list}\n\n'
            f'AVAILABLE CHAINS for chain_name:\n'
            f'  get_wood, mine_stone, make_crafting_table, make_wooden_pickaxe,\n'
            f'  make_stone_pickaxe, make_iron_pickaxe, make_iron_sword,\n'
            f'  make_iron_armor, make_shield, make_bucket, mine_diamonds,\n'
            f'  make_diamond_pickaxe, make_diamond_sword, find_food,\n'
            f'  build_shelter, place_furnace, place_chest\n'
            f'  (or "" for tasks the LLM handles at execution time)'
        )
        # Safety: clear if tool didn't consume it
        if _pending_goal_request == pending_desc:
            _pending_goal_request = None
        return

    # ── Layer 1: Chain execution ──
    global _consecutive_blocked
    if chain_executor.has_active_chain():
        result = chain_executor.execute_tick()
        status = chain_executor.get_status_str()
        print(f"   🔗 L1 CHAIN: {result.action}")
        print(f"      → {result.result[:100]}")
        print(f"      Status: {status}")
        death_analyzer.record_action(f"chain:{result.action}")
        _consecutive_blocked = 0  # Reset blocked counter on chain activity

        if result.needs_llm:
            # Escalate to LLM
            print(f"   ⬆️ Escalating to LLM: {result.llm_context[:100]}")
            task = goal_manager.get_current_task()
            if task:
                goal_manager.record_task_failure(task.id)
            call_llm_planner("Chain step failed / needs decision", result.llm_context)
        return

    # ── Layer 2: Need a new chain ──
    # No chain active — need LLM to decide what to do next
    if not goal_manager.active_goal:
        goals = goal_manager.goal_library.list_goals()
        goal_list = "\n".join(f"  - {g['name']}: {g['description']} ({g['task_count']} tasks)" for g in goals)
        call_llm_planner("No grand goal set",
                        f"Pick a grand goal from saved goals or create a new one.\n\n"
                        f"Saved goals:\n{goal_list}\n\n"
                        f"Use set_grand_goal(name) to pick one, or create_custom_grand_goal() for a new one.")
        return

    # Try to auto-select task and chain without LLM
    task = goal_manager.get_current_task()
    if not task:
        task = goal_manager.pick_next_task()

    if task:
        _consecutive_blocked = 0  # Reset blocked counter on task found
        chain_name = task.chain_name
        if chain_name and get_chain(chain_name):
            # Check for infinite loop: same chain repeated without progress
            fail_count = goal_manager.task_fail_count.get(task.id, 0)
            if fail_count >= 5:
                # Stuck on this task — skip for now (will retry later)
                print(f"   ⚠️ Task '{task.id}' stuck ({fail_count} attempts). Skipping for now.")
                goal_manager.skip_task(task.id)
                call_llm_planner(
                    "Task stuck — skipped for now",
                    f"Task '{task.id}' (chain: {chain_name}) failed {fail_count} times.\n"
                    f"Completion needs: {task.completion_items or task.completion_blocks_placed}\n"
                    f"This task will be RETRIED after other tasks complete.\n"
                    f"For now, pick a DIFFERENT available task. If the failed task needed resources "
                    f"(like iron_ore, coal), consider doing a different chain first to change location "
                    f"or gather prerequisites. Use get_grand_goal_status() to see what's available."
                )
                return
            # Track that we're starting this chain again
            goal_manager.task_fail_count[task.id] = fail_count + 1
            # We know which chain to run — no LLM needed!
            # Pass task's completion_items so chain adjusts skip thresholds
            msg = chain_executor.start_chain(chain_name, task.completion_items)
            print(f"   ▶️ Auto-start: {msg}")
            death_analyzer.record_action(f"auto_start:{chain_name}")
            return
        else:
            # Task has no chain defined (late-game tasks) → LLM
            call_llm_planner(
                "Task needs planning",
                f"Current task: {task.id} — {task.description}\n"
                f"This task has no predefined chain. Use tools to accomplish it, "
                f"or call choose_next_chain if applicable."
            )
            return

    # All tasks blocked or done
    if goal_manager.active_goal.is_complete:
        _consecutive_blocked = 0
        call_llm_planner("Grand goal complete!",
                        "Pick a new grand goal or celebrate.")
    else:
        available = goal_manager.active_goal.get_available_tasks()
        if not available:
            _consecutive_blocked += 1

            # Force recovery after 3 consecutive blocked ticks
            if _consecutive_blocked >= 3:
                print(f"   ⚠️ All tasks blocked for {_consecutive_blocked} ticks — forcing recovery")
                _consecutive_blocked = 0
                # Force-retry any SKIPPED tasks regardless of retry limit
                goal = goal_manager.active_goal
                skipped = [t for t in goal.tasks if t.status == TaskStatus.SKIPPED]
                if skipped:
                    for t in skipped:
                        t.status = TaskStatus.AVAILABLE
                        goal_manager.task_fail_count[t.id] = 0
                        goal_manager.skip_retry_count[t.id] = 0
                    goal.refresh_availability()
                    goal_manager._save()
                    print(f"   🔄 Force-reset {len(skipped)} skipped tasks to AVAILABLE")
                    return  # Next tick will pick up the recovered tasks
                else:
                    # No skipped tasks either — force-complete remaining as best effort
                    remaining = [t for t in goal.tasks
                                 if t.status not in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)]
                    for t in remaining:
                        t.status = TaskStatus.SKIPPED
                    goal.refresh_availability()
                    goal_manager._save()
                    print(f"   ⏭️ Force-skipped {len(remaining)} stuck tasks to unblock goal")
                    return

            # Build detailed context for LLM
            goal = goal_manager.active_goal
            details = []
            for t in goal.tasks:
                if t.status == TaskStatus.BLOCKED:
                    details.append(f"  BLOCKED: {t.id} (requires: {t.requires})")
                elif t.status == TaskStatus.IN_PROGRESS:
                    details.append(f"  IN_PROGRESS: {t.id} (orphaned — no active chain)")
                elif t.status == TaskStatus.SKIPPED:
                    retries = goal_manager.skip_retry_count.get(t.id, 0)
                    details.append(f"  SKIPPED: {t.id} (retried {retries}/{goal_manager.MAX_SKIP_RETRIES}x)")

            chain_list = list_available_chains()
            call_llm_planner(
                "All tasks blocked",
                f"Remaining incomplete tasks:\n" + "\n".join(details) +
                f"\n\nAvailable chains (use ONLY these names with choose_next_chain):\n  {chain_list}"
                f"\n\nIMPORTANT: Do NOT call choose_next_chain with action names like 'craft_item' or 'mine_block'."
                f"\nThose are actions, not chain names. Use the chain names listed above."
                f"\nPick a chain that helps unblock the remaining tasks."
            )
        else:
            # Shouldn't reach here, but just in case
            _consecutive_blocked = 0
            call_llm_planner("Need direction",
                            f"Available tasks: {', '.join(t.id for t in available[:5])}")


# ============================================
# LOG FILE OUTPUT
# ============================================

class TeeLogger:
    """Writes to both stdout and a log file simultaneously."""
    def __init__(self, log_dir="logs"):
        os.makedirs(log_dir, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.log_path = os.path.join(log_dir, f"bot_{timestamp}.log")
        self.log_file = open(self.log_path, "w", encoding="utf-8")
        self.stdout = sys.stdout

    def write(self, msg):
        try:
            self.stdout.write(msg)
        except UnicodeEncodeError:
            self.stdout.write(msg.encode("utf-8", errors="replace").decode("ascii", errors="replace"))
        self.log_file.write(msg)
        self.log_file.flush()

    def flush(self):
        self.stdout.flush()
        self.log_file.flush()

    def close(self):
        self.log_file.close()


# ============================================
# MAIN LOOP
# ============================================

def run():
    # Install TeeLogger to save all output to logs/ directory
    tee = TeeLogger()
    sys.stdout = tee
    print(f"📝 Logging to: {tee.log_path}")
    print("=" * 60)
    print("🤖 Minecraft AI Agent v6 — Chain of Action")
    print(f"🧠 Planning: {LOCAL_LLM_MODEL} (action chains)")
    print(f"💬 Chat: {'Claude API' if ANTHROPIC_API_KEY else 'Local LLM (no ANTHROPIC_API_KEY)'}")
    print(f"⏱️  Tick: {TICK_INTERVAL}s")
    print(f"📋 Goal: {goal_manager.active_goal.description if goal_manager.active_goal else 'None'}")
    print("=" * 60)
    print()
    print("Layer 0: Instinct (eat, flee, shelter) → no LLM, instant")
    print("Layer 1: Chain execution (mine, craft, smelt) → no LLM, fast")
    print("Layer 2: Planning (what to do next) → Local LLM")
    print("Layer 2: Player chat → Claude API")
    print()

    tick = 0
    while True:
        tick += 1
        print(f"\n{'─' * 50}")
        
        # Compact status line
        goal_status = goal_manager.get_status()
        chain_status = chain_executor.get_status_str()
        if goal_status.get("has_grand_goal"):
            print(f"🔄 #{tick} | 🏆 {goal_status['goal_name']} {goal_status['progress']} | {chain_status}")
        else:
            print(f"🔄 #{tick} | No goal | {chain_status}")

        try:
            tick_once(tick)
        except KeyboardInterrupt:
            print("\n👋 Shutting down...")
            break
        except Exception as e:
            print(f"   ❌ Error: {e}")
            import traceback
            traceback.print_exc()

        time.sleep(TICK_INTERVAL)


if __name__ == "__main__":
    run()