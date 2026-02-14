# Minecraft Autonomous AI Bot (v6.4 — Log Analysis + Underground Fix)

An autonomous Minecraft bot that sets a grand objective (like defeating the Ender Dragon) and **executes most actions without LLM calls** — using hardcoded action chains for known tasks, experience memory for learned solutions, and LLM only for high-level planning decisions.

**When the LLM solves a novel problem, that solution is saved and replayed automatically next time** — the bot gets smarter over time without code changes.

**Dual LLM architecture**: Local LLM (Qwen3) for fast action planning, Claude API for intelligent player conversations that can influence bot behavior.

---

## Architecture

```
+--------------------------------------------------+
|          Minecraft Server (Java 1.21.4)           |
|          | Game Protocol                          |
+--------------------------------------------------+
|    [Node.js] Mineflayer + Express REST API        |
|    server.js                                      |
|    - Bot connection, world interaction             |
|    - Smart combat (heal, flee, auto-equip)         |
|    - Threat assessment engine                      |
|    - Furnace smelting (auto-craft furnace if needed)|
|    - Death snapshot tracking                       |
|    - Shelter: build (surface, with door)            |
|               or dig (underground, sealed)          |
|    - Directional mining (staircase, tunnel)        |
|    - Block placement (9-pos + dig-out fallback)     |
|    - Smart pathfinding (auto-mine obstacles)       |
|    - Item/block search via minecraft-data          |
+--------------------------------------------------+
|                 HTTP (localhost:3001)              |
+--------------------------------------------------+
|    [Python] 3-Layer Execution Engine              |
|                                                    |
|    agent.py ------- Main tick loop (every 3s)     |
|      |                                             |
|      +-- Layer 0: INSTINCT (no LLM, instant)      |
|      |   HP < 5 -> eat. Night -> shelter. Flee.    |
|      |                                             |
|      +-- Layer 1: CHAIN EXECUTION (no LLM, fast)  |
|      |   chain_executor.py  Step-by-step actions   |
|      |   chain_library.py   Hardcoded chains (20)  |
|      |   experience_memory.py Learned solutions    |
|      |   - Auto-save locations (crafting, shelter)  |
|      |   - Experience check before escalation      |
|      |   - Dynamic timeout scaling per chain       |
|      |   - Shelter limit (max 3 saved)             |
|      |                                             |
|      +-- Layer 2: PLANNING (Local LLM, slow)      |
|      |   Only when: chain done, chain failed,      |
|      |   death, no goal                            |
|      |   - Solutions saved to experience_memory    |
|      |   - Bot state (HP, food, pos, time) sent    |
|      |                                             |
|      +-- Layer 2: CHAT (Claude API)               |
|      |   Player conversations -> Claude Sonnet     |
|      |   - Can change goals/chains via tools       |
|      |   - Falls back to local LLM if unavailable  |
|      |                                             |
|      +-- Log Analysis -- Auto-save + analyzer      |
|      |   TeeLogger -> logs/bot_*.log               |
|      |   analyze_logs.py -> report.md              |
|      |   CLAUDE.md -> Claude Code auto-context     |
|      |                                             |
|      +-- Grand Goal --- Dependency graph           |
|      |   grand_goal.py    Auto-inventory checks    |
|      |                                             |
|      +-- Death Analyzer - Learn from mistakes      |
|      |   death_analyzer.py Lessons persist         |
|      |                                             |
|      +-- Spatial Memory - Remember places          |
|      |   spatial_memory.py waypoints.json          |
|      |                                             |
|      +-- Tools (29) ---- Perception, actions       |
|          tools.py         (for LLM Layer 2 only)   |
|                                                    |
+--------------------------------------------------+
|    [Local LLM] Qwen3:30b via Open WebUI           |
|    Action planning - chain selection, error fixing  |
|    Cost: $0  -  Called only for planning (~2-5min) |
+--------------------------------------------------+
|    [Claude API] Sonnet (optional)                  |
|    Player chat - natural conversation, goal change  |
|    Cost: ~500 tokens/message                       |
+--------------------------------------------------+
```

---

## Dual LLM: Why Two Models?

| Role | Model | When Called | Why |
|------|-------|-----------|-----|
| **Action Planning** | Local LLM (Qwen3) | Chain fails, chain done, death, no goal | Fast, free, good enough for chain selection |
| **Player Chat** | Claude API (Sonnet) | Player sends message | Natural language understanding, multilingual, can modify bot behavior |

**Player chat can influence actions**: When a player says "go mine diamonds", Claude understands and calls `choose_next_chain("mine_diamonds")` or `set_grand_goal("defeat_ender_dragon")`.

**Automatic fallback**: If `ANTHROPIC_API_KEY` is not set or Claude fails, player chat falls back to the local LLM.

---

## Core Design: Chain of Action (Human-Like Thinking)

### Why 3 Layers?

Humans don't "think" about every action. We don't plan how to breathe. We don't deliberate each step when walking to the fridge. But we do think about *what* to eat for dinner.

The bot mirrors this:

| Layer | Human Analogy | Bot Action | LLM? | Speed |
|-------|--------------|------------|-------|-------|
| **Layer 0: Instinct** | Flinch from pain | HP < 5 -> eat food | None | ~0ms |
| **Layer 1: Chain** | Walk to fridge, open door, grab milk | mine_block -> craft -> smelt -> craft | None | ~1-2s/step |
| **Layer 2: Planning** | "What should I have for dinner?" | Pick next objective, handle unknowns | Local LLM | ~5-15s |
| **Layer 2: Chat** | "Hey, what are you doing?" | Respond to player, change plans | Claude API | ~2-5s |

### Layer 0: Instinct (No Thinking)

Immediate survival reactions. Checked every tick before anything else.

```
HP < 5 + has food     -> eat_food()           instant
HP < 5 + no food      -> dig_shelter()        instant (sealed with blocks)
Creeper within 5m     -> dig_shelter()        instant
Warden detected       -> dig_shelter()        instant
Night + surface       -> dig_shelter()        instant
Dusk + surface        -> dig_shelter()        instant
Hungry (food < 5)     -> eat_food()           instant
Flee recommendation   -> dig_shelter()        instant
Mob inside shelter    -> attack_entity()      instant
```

No LLM call. No chain. Pure `if/else` in Python.

### Layer 1: Chain Execution (No LLM)

Hardcoded action sequences for known Minecraft tasks. Executed step-by-step by Python directly calling the REST API.

```python
# Example: make_iron_pickaxe chain
[
  mine_block(iron_ore, 3)      # search type - has fallback strategy
  mine_block(coal_ore, 3)      # search type
  mine_block(stone, 8)         # for furnace
  craft_item(furnace)          # deterministic
  place_block(furnace)         # deterministic - 6-dir safe placement
  smelt_item(raw_iron, 3)      # deterministic
  craft_item(stick)            # deterministic
  craft_item(iron_pickaxe)     # deterministic
  equip_item(iron_pickaxe)     # deterministic
]
```

**Smart features:**
- **Auto-skip**: Already have cobblestone? Skip the mining step.
- **Search strategies**: `iron_ore` not found nearby? -> check memory -> dig_down(32) -> dig_tunnel(north, 20) -> dig_tunnel(east, 20) -- all without LLM.
- **Experience memory**: "Last time I found iron_ore at y=32 by digging down" -> try that first next time.
- **Auto-fix**: "No crafting table nearby" -> craft one -> place it -> retry. No LLM needed.
- **Auto-fix**: "No space to place block" (underground) -> mine adjacent block to clear space -> retry.
- **Auto-save locations**: Crafting table, furnace, shelter positions saved to spatial memory on placement.
- **Dynamic timeouts**: Each chain gets a timeout calculated from its steps (mine_block: 10s/block, smelt: 12s/item, etc). Range: 5-15 min.
- **Shelter limit**: Only 3 most recent shelters saved (old ones auto-deleted).

### Layer 2: LLM Planning (Only When Needed)

The local LLM is called **only** for decisions that require judgment:

| Trigger | What LLM Decides |
|---------|-----------------|
| Chain completed | "What chain to run next?" |
| Chain failed (no known fix) | "How to solve this new problem?" |
| Movement blocked | "Path is blocked -- find alternate route" |
| Death | "What went wrong? What lesson? What next?" |
| No grand goal | "Which goal to pursue?" |
| Late-game tasks (no chain) | Free-form tool use |

**Context sent to LLM**: Grand goal progress, death lessons, saved locations, bot state (HP, food, position, time of day), inventory (top 20 items).

**LLM call frequency: ~once every 2-5 minutes** (vs. v3's every 5 seconds).

### Layer 2: Player Chat (Claude API)

Player messages are handled by Claude Sonnet for natural conversation:

```
Player: "Hey what are you doing?"
  |
  +-- Claude sees: goal progress, current chain, bot state, inventory
  +-- Claude calls: send_chat("Making iron armor! Almost done")
  |
Player: "Can you mine diamonds instead?"
  |
  +-- Claude calls: choose_next_chain("mine_diamonds")
  +-- Claude calls: send_chat("Sure! Heading to diamond level")
```

**Available Claude tools**: `send_chat`, `set_grand_goal`, `choose_next_chain`, `skip_grand_task`, `complete_grand_task`, `get_grand_goal_status`

### Learning Loop: LLM Solutions -> Experience Memory

When the LLM solves a novel problem, **its solution is captured and saved** so the same situation can be handled automatically next time:

```
Chain step fails -> unknown error
  |
  +- 1. Check experience_memory for saved solution
  |     -> Found? -> inject solution steps -> Layer 1 handles it (no LLM!)
  |
  +- 2. Try auto-fix (clear space, ensure crafting table, etc.)
  |
  +- 3. Retry 3 times -> still failing
  |
  +- 4. Escalate to LLM (Layer 2)
  |     -> LLM calls tools: mine_block, move_to, craft_item, etc.
  |
  +- 5. LLM's tool calls saved to experience.json
        -> Next time same error -> Step 1 handles it automatically!
```

---

## How It Works

### Every 3 Seconds (One Tick)

```
+- 1. Auto-Progress Check -- inventory scan -> auto-complete tasks
|
+- 2. Layer 0: Instinct ---- HP low? Night? Creeper? -> instant action
|         | (if no instinct triggered)
+- 3. Death Check ----------- just died? -> LLM analyzes, picks new chain
|         | (if alive)
+- 4. Player Chat Check ----- someone talking? -> Claude API responds
|         | (if no chat)
+- 5. Layer 1: Chain -------- active chain? -> execute next step
|     |   +- Step succeeds -> advance
|     |   +- Step fails (search) -> try search strategy
|     |   +- Step fails -> check experience for saved solution
|     |   +- Step fails (known fix) -> auto-fix (clear space, craft table, etc.)
|     |   +- Step fails (movement) -> mine obstacle -> escalate to LLM
|     |   +- Step fails 3x (unknown) -> escalate to Layer 2 -> save solution
|     | (if no active chain)
+- 6. Layer 2: Planning ----- need new chain
      +- Grand Goal has next task with known chain?
      |     -> auto-start chain (NO LLM!)
      +- No chain or novel situation?
            -> LLM decides -> starts chain -> back to Layer 1
```

### Failure Handling: Smart Escalation

```
Step fails
  |
  +- Experience has solution? -> inject & execute (Layer 1, no LLM)
  |
  +- place_block: "no position" -> mine block for space -> retry
  +- craft: "no crafting table" -> craft + place one -> retry
  +- craft: "no furnace" -> craft + place one -> retry
  +- mine: "need pickaxe" -> inject make_pickaxe chain -> resume
  +- move_to: "path blocked" -> mine obstacle -> escalate to LLM immediately
  |
  +- Retry 3 times -> escalate to LLM
  |     -> LLM solves it -> solution saved to experience_memory
  |     -> Next time: auto-handled at Layer 1!
  |
  +- Task stuck 5 times -> skip task -> LLM picks next
  |     -> Skipped tasks retried after other tasks complete (up to 2 retries)
```

### Search Strategy: 3-Phase Resource Finding

When a search-type step fails ("no iron_ore found nearby"), the system uses a 3-phase search before calling the LLM:

```
mine_block(iron_ore, 3) FAILED: "No iron_ore found nearby"
  |
  +- Phase 0: Experience memory
  |     -> "iron_ore was found at (80, 32, -60) last time"
  |     -> move_to(80, 32, -60) -> retry mine_block -> Success!
  |
  +- Phase 1: Static search strategies (resource-specific, 6-11 steps)
  |     -> find_block(iron_ore, 64) -> not found
  |     -> dig_down(target_y=32) -> scan -> not found
  |     -> dig_tunnel(north, 20) -> scan -> not found
  |     -> dig_tunnel(east, 20) -> scan -> not found
  |     -> dig_tunnel(south/west, 20) -> explore(40) -> dig_down(16) -> tunnels...
  |
  +- Phase 2: Persistent search (up to 8 dynamic attempts)
  |     For ores: alternating dig_down(optimal Y) + dig_tunnel(rotating directions, longer each time)
  |     For surface: explore(30, 45, 60, 75... up to 120)
  |     After each attempt -> find_block(target, 32) -> mine if found
  |
  +- Phase 3: LLM escalation (only after ~19 total attempts)
        "Cannot find iron_ore after 19 search attempts.
         Tried: dig_down, dig_tunnel (all directions), explore.
         Analyze what went wrong and try a DIFFERENT approach."
```

Each resource has its own static strategy:

| Resource | Static Strategy Steps | Persistent Search |
|----------|----------------------|-------------------|
| `oak_log` | find_block -> birch/spruce -> explore(30/50/80) | explore(30→120) |
| `stone` | find_block -> dig_down(5) -> tunnel | explore(30→120) |
| `iron_ore` | find_block -> memory -> dig_down(32) -> tunnels N/E/S/W -> explore -> dig_down(16) -> tunnels (11 steps) | dig_down(16) + tunnels (rotating) |
| `coal_ore` | find_block -> memory -> dig_down(48) -> tunnels W/N -> explore -> dig_down(40) -> tunnels E/S (9 steps) | dig_down(48) + tunnels |
| `diamond_ore` | find_block -> deepslate -> memory -> dig_down(-58) -> tunnels all dirs -> explore -> more tunnels (12 steps) | dig_down(-58) + tunnels |
| Animals | explore(30/50/80/60/100) | explore(30→120) |

### Skipped Task Retry

Tasks that fail 5 times are skipped temporarily, not permanently. The system retries them after other tasks complete:

```
Task "make_iron_armor" fails 5 times → SKIPPED
  |
  +- Other tasks continue (make_shield, make_bucket, etc.)
  |
  +- All other tasks done → RETRY skipped tasks
  |     Reset fail count to 0, get 5 more chain attempts
  |
  +- Retry 1/2: fails again → SKIPPED again
  |
  +- Retry 2/2: final retry
  |
  +- Still failing → permanently SKIPPED (goal completes without it)
```

This prevents one hard task from blocking all progress while still giving it multiple chances.

### Dynamic Chain Timeout

Each chain gets a timeout calculated from its steps:

| Step Type | Time Budget |
|-----------|------------|
| `mine_block` | 10s per block (includes search) |
| `smelt_item` | 12s per item |
| `dig_down`, `build_shelter` | 120s |
| `craft_item`, `place_block`, `equip_item` | 15s |
| Other | 30s |

Base: 120s + sum of step budgets. Clamped to 5-15 min range.

Example: `make_iron_armor` (8 iron ore + 8 coal + smelt + craft + equip) = ~7 min timeout.

### Shelter Management

| Feature | Details |
|---------|---------|
| Max saved shelters | 3 (oldest auto-deleted) |
| dig_shelter | Layer 0 (emergency), sealed with blocks |
| build_shelter | Layer 1 (chain), 5x3x5 with door |
| Location saved | Both types auto-save to spatial memory |

### Smart Combat AI

```
GET /threat_assessment

  Player Power = weapon(7) + armor(3) + health(18x0.3) + food_bonus(2) = 17.4
  Threat Level = zombie(2) + skeleton(3xdistance_factor) = 5.8

  -> Recommendation: "fight" (strong advantage)
```

| Recommendation | Action |
|---------------|--------|
| `safe` | No threats, continue chain |
| `fight` | Strong advantage -- engage (Layer 0 handles) |
| `fight_careful` | Watch health, eat mid-fight |
| `avoid` | Don't engage -- continue task, watch distance |
| `flee` | dig_shelter immediately (Layer 0 instinct) |

**During combat**: auto-equip best weapon, chase target, eat if HP < 8, flee if HP <= 4, avoid creepers, run from Wardens, collect drops.

### Auto-Equip Best Gear

The bot automatically equips the best available gear at key moments:

| When | What |
|------|------|
| Chain start | All slots: sword, armor (head/torso/legs/feet), shield |
| Chain complete | All slots (newly crafted gear equipped immediately) |
| Before mining | Best pickaxe (diamond > iron > stone > wooden) |
| Before combat | Best sword + in instinct layer too |

**Tier priority** (high → low): Diamond > Iron > Chainmail > Leather. Old gear returns to inventory automatically.

### Block Placement (9-Position + Dig-Out Fallback)

```
place_block("crafting_table")
  |
  +- Phase 1: Try 9 candidate positions around bot
  |     Priority 1: feet-level horizontal (4 dirs) — works on surface
  |     Priority 2: head-level horizontal (4 dirs) — works in caves
  |     Priority 3: above head (1 pos) — works in vertical shafts
  |     For each: find air block -> find solid reference -> calculate face vector -> place
  |     Skip bot's feet + head positions as reference
  |
  +- Phase 2: Dig-out fallback (if all 9 positions fail)
  |     Pick adjacent solid block (not bedrock) -> dig it -> creates air space
  |     Try placing at newly cleared position
  |
  +- 100ms server delay after placement
  +- Auto-save location to spatial memory (crafting_table, furnace, etc.)
```

**Why 9 positions?** After `dig_down`, bot is in a 1x1 vertical shaft — all 4 horizontal blocks at feet level are stone. But head-level or above-head positions may have air from the shaft the bot dug through.

### Smart Pathfinding (Auto-Mine Obstacles)

```
move_to(x, y, z) FAILED: "Path blocked"
  |
  +- 1. Detect direction toward target
  +- 2. Mine block at foot + eye level in that direction
  +- 3. Retry pathfinding once (30s timeout)
  +- 4. Still blocked? -> report to chain_executor
  +- 5. Chain_executor -> immediate LLM escalation (no 3x retry)
        -> LLM picks alternate route or strategy
```

### Spatial Memory

Persisted in `waypoints.json` (max 3 shelters):

```
KNOWN LOCATIONS (6):
  [CRAFTING]
    crafting_table: (105, 64, -48) (12m)
    furnace: (107, 64, -48) (14m)
  [SHELTER]
    shelter_50: (15, 58, -7) (8m) - Enclosed shelter
    shelter_49: (13, 67, -8) (20m)
    shelter_48: (13, 69, -8) (22m)
  [STORAGE]
    chest: (15, 58, -7) (8m)
```

**Auto-saved** (Layer 1 + Layer 2): crafting table, chest, furnace, bed, shelter.

---

## Log Analysis System

All bot output is automatically saved to `logs/bot_YYYYMMDD_HHMMSS.log` via TeeLogger. Use the analyzer to generate reports for Claude Code diagnosis.

### Workflow

```
1. Run bot          python agent.py              → logs/bot_*.log (auto)
2. Analyze          python analyze_logs.py       → report.md
3. Claude Code      Read report.md               → diagnose issues
4. Deep dive        Read specific tick range      → identify root cause
5. Fix              Edit code based on analysis   → re-run bot
```

### Analyzer Features

```bash
python analyze_logs.py                    # Most recent log
python analyze_logs.py logs/bot_xxx.log   # Specific log
python analyze_logs.py --last 500         # Last 500 ticks only
```

**Report includes:**
- **Chain Performance**: success/fail/avg ticks per chain
- **Top Errors**: most frequent error messages with count
- **Stuck Loops**: consecutive identical steps (wasted ticks)
- **LLM Escalations**: why and when LLM was called
- **Deaths**: death events with tick numbers
- **Recommendations**: auto-generated fix suggestions mapped to code locations

### CLAUDE.md (Auto-Context)

`CLAUDE.md` at project root is automatically read by Claude Code on session start. Contains:
- Architecture overview, key files, data files
- Log analysis workflow (4-step)
- Common issues → fix locations table
- REST API endpoint list

---

## Grand Goals Available

### Defeat the Ender Dragon (25 tasks, 6 phases)

```
Phase 1: Basic Survival
  get_wood --> make_crafting_table --> make_wooden_pickaxe --> make_stone_pickaxe
  find_food (anytime)  .  build_shelter (anytime, with door)

Phase 2: Iron Age
  make_stone_pickaxe --> make_iron_pickaxe + make_iron_sword + make_iron_armor + make_bucket + make_shield

Phase 3: Diamond Age
  make_iron_pickaxe --> mine_diamonds --> make_diamond_pickaxe + make_diamond_sword

Phase 4-6: Nether -> Ender -> End
  diamond_pickaxe + bucket --> obsidian --> portal --> fortress --> blazes
  blazes --> blaze_powder -+
  diamond_sword --> endermen +---> eyes --> stronghold --> portal --> dragon
```

### Full Iron Gear (8 tasks, 2 phases)
```
get_wood -> crafting_table -> wooden_pick -> stone_pick -> iron_pick + iron_sword + iron_armor + shield
```

### Cozy Base (6 tasks, 2 phases)
```
Gather: get_wood, mine_stone (dedicated mining, no shelter building)
Build: crafting_table + build_shelter (with door) + place_furnace + place_chest
```

---

## Available Chains (20)

| Category | Chains |
|----------|--------|
| Gathering | `get_wood`, `mine_stone` |
| Basic Tools | `make_crafting_table`, `make_wooden_pickaxe`, `make_stone_pickaxe` |
| Iron Age | `make_iron_pickaxe`, `make_iron_sword`, `make_iron_armor`, `make_shield`, `make_bucket` |
| Diamond Age | `mine_diamonds`, `make_diamond_pickaxe`, `make_diamond_sword` |
| Building | `build_shelter`, `place_furnace`, `place_chest` |
| Survival | `find_food` |
| Emergency | `emergency_eat`, `emergency_shelter`, `emergency_flee` |

---

## Persistence (Survives Restarts)

| Data | File | Survives restart? |
|------|------|:-:|
| Grand goal progress | `grand_goal_state.json` | Yes |
| Death lessons | `death_lessons.json` | Yes |
| Saved locations | `waypoints.json` | Yes |
| Experience memory (search + error solutions) | `experience.json` | Yes |
| Bot execution logs | `logs/bot_*.log` | Yes |
| Analysis report | `report.md` | Yes (overwritten) |
| Active chain state | in-memory | No (auto-restarts) |

---

## Prerequisites

- **Node.js** (v18+)
- **Python** (3.10+)
- **Minecraft Java Edition** (1.21.4)
- **Local LLM** -- Open WebUI + Ollama with Qwen3:30b (or any model with tool calling)
- **Claude API key** (optional) -- for player chat

## Setup

### 1. Node.js (Mineflayer API Server)

```bash
npm install mineflayer mineflayer-pathfinder express dotenv minecraft-data vec3
```

### 2. Python (LangChain Agent)

```bash
pip install langchain langchain-openai langchain-anthropic requests python-dotenv
```

### 3. Environment Variables

```env
# Minecraft Bot
BOT_HOST=localhost
BOT_PORT=55888
BOT_USERNAME=PenguinBot
BOT_VERSION=1.21.4

# API Server
API_PORT=3001

# Local LLM (Open WebUI) - action planning
LOCAL_LLM_BASE_URL=http://192.168.86.250:12000
LOCAL_LLM_API_KEY=your-jwt-token
LOCAL_LLM_MODEL=qwen3:30b

# Claude API (optional) - player chat
ANTHROPIC_API_KEY=sk-ant-...

# Agent
TICK_INTERVAL=3
MAX_ITERATIONS=5
```

## Running

**Terminal 1 -- Minecraft bot API server:**
```bash
node server.js
```

**Terminal 2 -- AI Agent:**
```bash
python agent.py
```

---

## Project Structure

```
minecraft-bot/
|
+-- server.js              # [Node.js] Mineflayer + Express REST API
|
+-- agent.py               # [Python] Main 3-layer tick loop + dual LLM routing
+-- chain_library.py       # Hardcoded action chains + search strategies (20 chains)
+-- chain_executor.py      # Layer 0+1 execution engine + auto-fix + experience check
+-- experience_memory.py   # Remember what worked (search successes, LLM error fixes)
|
+-- tools.py               # 29 LangChain tools (for LLM Layer 2 only)
|
+-- grand_goal.py          # Grand goal dependency graph + auto-inventory checks
+-- grand_goal_tools.py    # LangChain tools for goal management
|
+-- death_analyzer.py      # Death snapshot capture + lesson extraction
+-- death_tools.py         # LangChain tools for death learning
|
+-- spatial_memory.py      # Named waypoint storage (max 3 shelters)
+-- memory_tools.py        # LangChain tools for location memory
|
+-- analyze_logs.py       # Log analyzer -> report.md
+-- CLAUDE.md             # Claude Code auto-context (project guide)
|
+-- grand_goal_state.json  # [Auto] Saved goal progress
+-- death_lessons.json     # [Auto] Persistent death lessons
+-- waypoints.json         # [Auto] Persistent saved locations
+-- experience.json        # [Auto] Persistent experience data
+-- report.md              # [Auto] Latest analysis report
+-- logs/                  # [Auto] Bot execution logs
|
+-- .env                   # Configuration
+-- package.json           # Node.js dependencies
+-- README.md
```

---

## Performance: v3 vs v6.4

| Metric | v3 (LLM every tick) | v6.4 (Dual LLM + Chain) |
|--------|--------------------|--------------------|
| LLM calls per minute | ~12 | ~0.3 |
| Time per action | 5-15s (LLM thinking) | 1-2s (direct API) |
| Actions per minute | ~4-6 | ~20-30 |
| Iron pickaxe time | ~15-30 min | ~3-5 min |
| Memory between ticks | None | Chain state + history |
| Resource search | LLM guesses | 3-phase systematic (up to 19 attempts) |
| Error recovery | LLM every time | Experience-first, LLM fallback |
| Same error twice | LLM again | Auto-handled from experience |
| Player chat | Same slow LLM | Claude API (fast, natural) |
| Failed tasks | Stuck forever | Skip → retry later (up to 2 retries) |
| Gear management | Manual | Auto-equip best gear at key moments |

---

## Cost

| Component | Cost |
|-----------|------|
| Action planning | **$0** (local LLM) |
| Chain execution | **$0** (no LLM) |
| Player chat (Claude) | ~$0.003/message (optional) |

---

## Roadmap

- [x] Mineflayer REST API server
- [x] LangChain Agent with 29 tools
- [x] Grand Goal dependency graph
- [x] **3-Layer execution (instinct / chain / LLM)**
- [x] **Hardcoded action chains (20 chains)**
- [x] **Search strategies for all resources**
- [x] **Experience memory (persistent)**
- [x] **Auto-skip + auto-fix in chains**
- [x] **Auto-inventory task completion**
- [x] Smart combat AI
- [x] Threat assessment engine
- [x] Furnace smelting
- [x] Emergency shelter (sealed) + surface shelter (with door)
- [x] Directional mining
- [x] Death analysis + lesson learning
- [x] Spatial memory (max 3 shelters)
- [x] Structure scan + rebuild
- [x] **6-direction safe block placement**
- [x] **Auto-save locations from Layer 1 (crafting table, furnace, shelter)**
- [x] **LLM solution capture -> experience memory (learning loop)**
- [x] **Smart pathfinding (auto-mine obstacles)**
- [x] **Dynamic timeout scaling per chain complexity**
- [x] **Claude API for player conversation (dual LLM)**
- [x] **Bot state in LLM context (HP, food, position, time)**
- [x] **Dedicated chains: mine_stone, place_furnace, place_chest**
- [x] **Auto-equip best gear (armor, weapon, shield) at chain start/end/combat**
- [x] **Skipped task retry system (up to 2 retries after other tasks complete)**
- [x] **3-phase resource search (static → persistent → LLM escalation)**
- [x] **Persistent search mode (8 dynamic explore/dig attempts before LLM)**
- [x] **9-position block placement + dig-out fallback (underground fix)**
- [x] **TeeLogger — auto-save all output to logs/bot_*.log**
- [x] **Log analyzer (analyze_logs.py) — chain stats, error patterns, stuck loops, recommendations**
- [x] **CLAUDE.md — Claude Code auto-context for project analysis**
- [ ] Nether navigation + portal building
- [ ] Chest inventory management
- [ ] Dynamic chain generation by LLM

---

**Author**: Jun
**Created**: 2026-02-13
**Version**: v6.4 -- Log Analysis + Underground Block Placement Fix
