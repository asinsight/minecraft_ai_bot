# 🤖 Minecraft Autonomous AI Bot (v6.1 — Chain of Action + Learning Loop)

An autonomous Minecraft bot that sets a grand objective (like defeating the Ender Dragon) and **executes most actions without LLM calls** — using hardcoded action chains for known tasks, experience memory for learned solutions, and LLM only for high-level planning decisions.

**When the LLM solves a novel problem, that solution is saved and replayed automatically next time** — the bot gets smarter over time without code changes.

Powered by a 3-Layer execution model + LangChain Agent + Local LLM. Near-zero latency for routine actions.

---

## Architecture

```
┌──────────────────────────────────────────────────┐
│          Minecraft Server (Java 1.21.4)           │
│          ↕ Game Protocol                          │
├──────────────────────────────────────────────────┤
│    [Node.js] Mineflayer + Express REST API        │
│    server.js                                      │
│    - Bot connection, world interaction             │
│    - Smart combat (heal, flee, auto-equip)         │
│    - Threat assessment engine                      │
│    - Furnace smelting (auto-craft furnace if needed)│
│    - Death snapshot tracking                       │
│    - Shelter: build (surface, with door)            │
│               or dig (underground, sealed)          │
│    - Directional mining (staircase, tunnel)        │
│    - Block placement (6-dir search, safe position) │
│    - Smart pathfinding (auto-mine obstacles)       │
│    - Item/block search via minecraft-data          │
├──────────────────────────────────────────────────┤
│                 HTTP (localhost:3001)              │
├──────────────────────────────────────────────────┤
│    [Python] 3-Layer Execution Engine              │
│                                                    │
│    agent.py ─────── Main tick loop (every 3s)     │
│      │                                             │
│      ├── Layer 0: INSTINCT (no LLM, instant)      │
│      │   HP < 5 → eat. Night → shelter. Flee.     │
│      │                                             │
│      ├── Layer 1: CHAIN EXECUTION (no LLM, fast)  │
│      │   chain_executor.py  Step-by-step actions   │
│      │   chain_library.py   Hardcoded chains       │
│      │   experience_memory.py Learned solutions    │
│      │   ↳ Auto-save locations (crafting, shelter) │
│      │   ↳ Experience check before escalation      │
│      │   ↳ Dynamic timeout scaling                 │
│      │                                             │
│      ├── Layer 2: LLM PLANNING (LLM, slow)        │
│      │   Only when: chain done, chain failed,      │
│      │   death, player chat, no goal               │
│      │   ↳ Solutions saved to experience_memory    │
│      │                                             │
│      ├── Grand Goal ─── Dependency graph           │
│      │   grand_goal.py    Auto-inventory checks    │
│      │                                             │
│      ├── Death Analyzer ─ Learn from mistakes      │
│      │   death_analyzer.py Lessons persist         │
│      │                                             │
│      ├── Spatial Memory ─ Remember places          │
│      │   spatial_memory.py waypoints.json          │
│      │                                             │
│      └── Tools (29) ──── Perception, actions       │
│          tools.py         (for LLM Layer 2 only)   │
│                                                    │
├──────────────────────────────────────────────────┤
│    [Local LLM] Qwen3:30b-a3b via Open WebUI      │
│    30B MoE (3B active) · Native tool calling       │
│    Cost: $0  ·  Called only for planning (~2-5min) │
└──────────────────────────────────────────────────┘
```

---

## Core Design: Chain of Action (Human-Like Thinking)

### Why 3 Layers?

Humans don't "think" about every action. We don't plan how to breathe. We don't deliberate each step when walking to the fridge. But we do think about *what* to eat for dinner.

The bot mirrors this:

| Layer | Human Analogy | Bot Action | LLM? | Speed |
|-------|--------------|------------|-------|-------|
| **Layer 0: Instinct** | Flinch from pain | HP < 5 → eat food | ❌ | ~0ms |
| **Layer 1: Chain** | Walk to fridge, open door, grab milk | mine_block → craft → smelt → craft | ❌ | ~1-2s/step |
| **Layer 2: Planning** | "What should I have for dinner?" | Pick next objective, handle unknowns | ✅ | ~5-15s |

### Layer 0: Instinct (No Thinking)

Immediate survival reactions. Checked every tick before anything else.

```
HP < 5 + has food     → eat_food()           instant
HP < 5 + no food      → dig_shelter()        instant (sealed with blocks)
Creeper within 5m     → dig_shelter()        instant
Warden detected       → dig_shelter()        instant
Night + surface       → dig_shelter()        instant
Dusk + surface        → dig_shelter()        instant
Hungry (food < 5)     → eat_food()           instant
Flee recommendation   → dig_shelter()        instant
Mob inside shelter    → attack_entity()      instant
```

No LLM call. No chain. Pure `if/else` in Python.

### Layer 1: Chain Execution (No LLM)

Hardcoded action sequences for known Minecraft tasks. Executed step-by-step by Python directly calling the REST API.

```python
# Example: make_iron_pickaxe chain
[
  mine_block(iron_ore, 3)      # search type — has fallback strategy
  mine_block(coal_ore, 3)      # search type
  mine_block(stone, 8)         # for furnace
  craft_item(furnace)          # deterministic
  place_block(furnace)         # deterministic — 6-dir safe placement
  smelt_item(raw_iron, 3)      # deterministic
  craft_item(stick)            # deterministic
  craft_item(iron_pickaxe)     # deterministic
  equip_item(iron_pickaxe)     # deterministic
]
```

**Smart features:**
- **Auto-skip**: Already have cobblestone? Skip the mining step.
- **Search strategies**: `iron_ore` not found nearby? → check memory → dig_down(32) → dig_tunnel(north, 20) → dig_tunnel(east, 20) — all without LLM.
- **Experience memory**: "Last time I found iron_ore at y=32 by digging down" → try that first next time.
- **Auto-fix**: "No crafting table nearby" → craft one → place it → retry. No LLM needed.
- **Auto-fix**: "No space to place block" (underground) → mine adjacent block to clear space → retry.
- **Auto-save locations**: Crafting table, furnace, shelter positions saved to spatial memory on placement.
- **Dynamic timeouts**: mine_block(count=30) gets 240s timeout instead of 60s.

### Layer 2: LLM Planning (Only When Needed)

The LLM is called **only** for decisions that require judgment:

| Trigger | What LLM Decides |
|---------|-----------------|
| Chain completed | "What chain to run next?" |
| Chain failed (no known fix) | "How to solve this new problem?" |
| Movement blocked | "Path is blocked — find alternate route" |
| Death | "What went wrong? What lesson? What next?" |
| Player chat | "How to respond?" |
| No grand goal | "Which goal to pursue?" |
| Late-game tasks (no chain) | Free-form tool use |

**LLM call frequency: ~once every 2-5 minutes** (vs. v3's every 5 seconds).

### Learning Loop: LLM Solutions → Experience Memory

When the LLM solves a novel problem, **its solution is captured and saved** so the same situation can be handled automatically next time:

```
Chain step fails → unknown error
  │
  ├─ 1. Check experience_memory for saved solution
  │     → Found? → inject solution steps → Layer 1 handles it (no LLM!)
  │
  ├─ 2. Try auto-fix (clear space, ensure crafting table, etc.)
  │
  ├─ 3. Retry 3 times → still failing
  │
  ├─ 4. Escalate to LLM (Layer 2)
  │     → LLM calls tools: mine_block, move_to, craft_item, etc.
  │
  └─ 5. LLM's tool calls saved to experience.json
        → Next time same error → Step 1 handles it automatically!
```

Example saved solution:
```json
{
  "place_block:no suitable position": {
    "chain": [
      {"tool": "mine_block", "args": {"block_type": "stone", "count": 1}},
      {"tool": "place_block", "args": {"block_name": "furnace"}}
    ],
    "success_count": 3,
    "last_used": 1707800000
  }
}
```

---

## How It Works

### Every 3 Seconds (One Tick)

```
┌─ 1. Auto-Progress Check ── inventory scan → auto-complete tasks
│
├─ 2. Layer 0: Instinct ──── HP low? Night? Creeper? → instant action
│         ↓ (if no instinct triggered)
├─ 3. Death Check ─────────── just died? → LLM analyzes, picks new chain
│         ↓ (if alive)
├─ 4. Player Chat Check ──── someone talking? → LLM responds
│         ↓ (if no chat)
├─ 5. Layer 1: Chain ──────── active chain? → execute next step
│     │   ├─ Step succeeds → advance
│     │   ├─ Step fails (search) → try search strategy
│     │   ├─ Step fails → check experience for saved solution
│     │   ├─ Step fails (known fix) → auto-fix (clear space, craft table, etc.)
│     │   ├─ Step fails (movement) → mine obstacle → escalate to LLM
│     │   └─ Step fails 3x (unknown) → escalate to Layer 2 → save solution
│     ↓ (if no active chain)
└─ 6. Layer 2: Planning ──── need new chain
      ├─ Grand Goal has next task with known chain?
      │     → auto-start chain (NO LLM!)
      └─ No chain or novel situation?
            → LLM decides → starts chain → back to Layer 1
```

### Failure Handling: Smart Escalation

```
Step fails
  │
  ├─ Experience has solution? → inject & execute (Layer 1, no LLM)
  │
  ├─ place_block: "no position" → mine block for space → retry
  ├─ craft: "no crafting table" → craft + place one → retry
  ├─ craft: "no furnace" → craft + place one → retry
  ├─ mine: "need pickaxe" → inject make_pickaxe chain → resume
  ├─ move_to: "path blocked" → mine obstacle → escalate to LLM immediately
  │
  ├─ Retry 3 times → escalate to LLM
  │     → LLM solves it → solution saved to experience_memory
  │     → Next time: auto-handled at Layer 1!
  │
  └─ Task stuck 5 times → skip task → LLM picks next
```

### Search Strategy: Finding Resources Without LLM

When a search-type step fails ("no iron_ore found nearby"), the system doesn't call the LLM. Instead it follows a predefined search strategy:

```
mine_block(iron_ore, 3) FAILED: "No iron_ore found nearby"
  │
  ├─ 1. Check experience memory
  │     → "iron_ore was found at (80, 32, -60) last time"
  │     → move_to(80, 32, -60) → retry mine_block → ✅ Success!
  │
  ├─ 2. find_block(iron_ore, 64) → not found
  │
  ├─ 3. dig_down(target_y=32) → arrived at y=32
  │     → find_block(iron_ore) → found! → mine → ✅
  │
  ├─ 4. dig_tunnel(north, 20) → scan for ore
  │
  ├─ 5. dig_tunnel(east, 20) → scan for ore
  │
  └─ 6. All strategies exhausted → escalate to LLM
        "I can't find iron_ore. Tried digging to y=32
         and 2 tunnels. What should I do?"
```

Each resource has its own strategy:

| Resource | Search Strategy |
|----------|----------------|
| `oak_log` | find_block → try birch/spruce → explore(30) → explore(50) |
| `stone` | find_block → dig_down(5) |
| `iron_ore` | find_block → memory → dig_down(32) → tunnels N/E/S |
| `coal_ore` | find_block → memory → dig_down(48) → tunnel W |
| `diamond_ore` | find_block → memory → dig_down(-58) → tunnels N/E/S/W |
| Animals | explore(30) → explore(50) → explore(80) |

### Experience Memory: Learn Once, Remember Forever

Two types of persistent memory:

**1. Search successes** — "Where did I find this resource?"
```json
{
  "iron_ore": {
    "method": "dig_down:{\"target_y\":32}",
    "location": {"x": 80, "y": 32, "z": -60},
    "success_count": 3,
    "last_used": 1707800000
  }
}
```

**2. Error solutions** — "How did the LLM fix this problem?"
```json
{
  "place_block:no suitable position": {
    "chain": [
      {"tool": "mine_block", "args": {"block_type": "stone", "count": 1}},
      {"tool": "place_block", "args": {"block_name": "furnace"}}
    ],
    "success_count": 2
  }
}
```

Both persist to `experience.json` across restarts. Error solutions are **automatically captured from LLM tool calls** when the LLM resolves an escalated failure.

### Death → Reassess → Adapt

When the bot dies:

```
💀 Bot died!
  │
  ├─ 1. Active chain AUTO-CANCELLED
  │
  ├─ 2. Death snapshot captured
  │     health=3, zombie(2m)+skeleton(5m), night, weapon=fist
  │
  ├─ 3. LLM called (Layer 2):
  │     "You died. Analyze and call learn_from_death.
  │      Then choose_next_chain to resume."
  │
  ├─ 4. LLM stores lesson:
  │     [HIGH] "Craft a sword before exploring at night"
  │
  ├─ 5. LLM picks new chain:
  │     choose_next_chain("make_iron_sword")
  │
  └─ 6. Chain executes automatically (Layer 1)
        Lessons injected into all future LLM prompts.
```

### Smart Combat AI

```
GET /threat_assessment

  Player Power = weapon(7) + armor(3) + health(18×0.3) + food_bonus(2) = 17.4
  Threat Level = zombie(2) + skeleton(3×distance_factor) = 5.8

  → Recommendation: "fight" (strong advantage)
```

| Recommendation | Action |
|---------------|--------|
| `safe` | No threats, continue chain |
| `fight` | Strong advantage — engage (Layer 0 handles) |
| `fight_careful` | Watch health, eat mid-fight |
| `avoid` | Don't engage — continue task, watch distance |
| `flee` | dig_shelter immediately (Layer 0 instinct) |

**During combat**: auto-equip best weapon, chase target, eat if HP < 8, flee if HP ≤ 4, avoid creepers, run from Wardens, collect drops.

### Smelting & Iron Age

```
smelt_item("raw_iron", 3)
  ├─ Furnace nearby? → walk to it
  ├─ No furnace? → auto-craft (8 cobblestone) + place it (6-dir safe placement)
  ├─ Load fuel (coal, charcoal, planks, logs)
  ├─ Insert raw items → wait → collect output
  └─ Result: "Smelted raw_iron → got iron_ingot x3"
```

### Block Placement (6-Direction Safe Placement)

All block placement (crafting table, furnace, doors) uses a robust algorithm:

```
place_block("crafting_table")
  ├─ Find air block adjacent to bot (not where bot stands)
  ├─ Search 6 directions from target for solid reference block
  ├─ Skip bot's own position as reference
  ├─ Calculate correct face vector
  ├─ Place block + 100ms server delay
  ├─ Underground? No air? → mine adjacent block to create space → retry
  └─ Auto-save location to spatial memory (crafting_table, furnace, etc.)
```

### Shelter: Build vs Dig

| Situation | Tool | Layer | How it works |
|-----------|------|:-----:|-------------|
| Have 20+ blocks | `build_shelter` | Layer 1 (Chain) | Builds 5×3×5 room, crafts + places door, saves location |
| Emergency | `dig_shelter` | Layer 0 (Instinct) | Digs 3×3×3 underground, seals entrance with blocks |

**build_shelter chain** (Layer 1):
```
mine oak_log(2) → craft planks → craft door → mine stone(30) → build_shelter
```

The shelter gets a real door (not just a wall opening), and its location is auto-saved to spatial memory.

### Smart Pathfinding (Auto-Mine Obstacles)

When movement is blocked:

```
move_to(x, y, z) FAILED: "Path blocked"
  │
  ├─ 1. Detect direction toward target
  ├─ 2. Mine block at foot + eye level in that direction
  ├─ 3. Retry pathfinding once (30s timeout)
  ├─ 4. Still blocked? → report to chain_executor
  └─ 5. Chain_executor → immediate LLM escalation (no 3x retry)
        → LLM picks alternate route or strategy
```

### Directional Mining

| Tool | What it does |
|------|-------------|
| `dig_down(target_y=12)` | Staircase mine downward, stops at target Y or on lava |
| `dig_tunnel("north", 20)` | Horizontal 1×2 tunnel, reports ores found, stops on lava |

### Spatial Memory

Persisted in `waypoints.json`:

```
📍 KNOWN LOCATIONS (5):
  [CRAFTING]
    crafting_table: (105, 64, -48) (12m)
    furnace: (107, 64, -48) (14m)
  [SHELTER]
    shelter: (100, 64, -50) (8m)
  [RESOURCE]
    iron_cave: (80, 32, -60) (45m) — Found iron ore vein
```

**Auto-saved** (Layer 1 + Layer 2): crafting table, chest, furnace, bed, shelter.
**Used for navigation**: "Need to craft? → find_nearest_location('crafting') → walk there."

---

## Dependency Graph, Not Fixed Order

Tasks have dependencies (`requires`), not a fixed order:

```
🏆 GRAND GOAL: Defeat the Ender Dragon!
   Progress: 6/25 (24%)

   📋 Phase 1: Basic Survival [4/6]
      ✅ Gather wood
      ✅ Make crafting table
      ✅ Wooden pickaxe
      ✅ Stone pickaxe
      ⬜ Hunt animals for food
      ⬜ Build an enclosed shelter

   📋 Phase 2: Iron Age [0/5]
      ⬜ Craft an iron pickaxe      ← auto-starts chain
      ⬜ Craft an iron sword        ← auto-starts chain
      🔒 Craft iron armor [needs: stone_pickaxe]
      🔒 Craft a shield [needs: iron_sword]
      ⬜ Craft a bucket
```

Most task → chain mapping is automatic. No LLM call needed for known tasks.

---

## Persistence (Survives Restarts)

| Data | File | Survives restart? |
|------|------|:-:|
| Grand goal progress | `grand_goal_state.json` | ✅ |
| Death lessons | `death_lessons.json` | ✅ |
| Saved locations | `waypoints.json` | ✅ |
| Experience memory (search + error solutions) | `experience.json` | ✅ |
| Active chain state | in-memory | ❌ (auto-restarts) |

---

## Available Tools (29+)

### Perception (7)
| Tool | Description |
|------|-------------|
| `get_world_state` | Full snapshot: position, health, inventory, entities, chat, time |
| `get_inventory` | Detailed inventory contents |
| `get_nearby` | Block counts and entity list within range |
| `find_block` | Find nearest block of a specific type |
| `assess_threat` | Combat readiness vs nearby threats → fight/avoid/flee |
| `get_recipe` | Look up crafting recipe + check what's missing |
| `search_item` | Search item/block names by keyword |

### Movement (5)
| Tool | Description |
|------|-------------|
| `move_to` | Move to x, y, z coordinates (auto-mines obstacles) |
| `move_to_player` | Move to a specific player |
| `follow_player` | Continuously follow a player |
| `explore` | Walk in a random direction |
| `stop_moving` | Stop all movement |

### Resource / Combat (3)
| Tool | Description |
|------|-------------|
| `mine_block` | Mine blocks by type (pathfinding + auto-collect) |
| `place_block` | Place block from inventory (6-dir safe placement, auto-saves locations) |
| `attack_entity` | Smart combat: chase → hit → heal → flee → collect |

### Mining (2)
| Tool | Description |
|------|-------------|
| `dig_down` | Staircase mine to target Y. Lava auto-stop |
| `dig_tunnel` | Horizontal 1×2 tunnel. Reports ores. Lava auto-stop |

### Survival (7)
| Tool | Description |
|------|-------------|
| `eat_food` | Eat best available food |
| `equip_item` | Equip weapon/armor/tool |
| `craft_item` | Craft items (auto-finds crafting table) |
| `smelt_item` | Smelt in furnace (auto-crafts furnace if needed) |
| `dig_shelter` | Emergency underground shelter (sealed, location saved) |
| `build_shelter` | Build 5×3×5 shelter with door (location saved) |
| `sleep_in_bed` | Sleep in a nearby bed |

### Structures (3)
| Tool | Description |
|------|-------------|
| `scan_structure` | Save a build's blocks for rebuilding |
| `list_structures` | List saved structures |
| `rebuild_structure` | Rebuild at an offset |

### Communication (1)
| Tool | Description |
|------|-------------|
| `send_chat` | Send message in game chat |

### Grand Goal (4) — LLM Layer 2
| Tool | Description |
|------|-------------|
| `set_grand_goal` | Set ultimate objective |
| `complete_grand_task` | Mark a task done |
| `skip_grand_task` | Skip optional tasks |
| `get_grand_goal_status` | Full dependency graph |

### Chain Control (1) — LLM Layer 2
| Tool | Description |
|------|-------------|
| `choose_next_chain` | Start an action chain |

### Death Analysis (4)
| Tool | Description |
|------|-------------|
| `check_death` | Detect if bot just died |
| `learn_from_death` | Store a death lesson |
| `get_lessons` | Review all death lessons |
| `get_death_stats` | Death count and causes |

### Spatial Memory (5)
| Tool | Description |
|------|-------------|
| `save_location` | Save a named waypoint |
| `delete_location` | Remove a waypoint |
| `find_nearest_location` | Find closest by category |
| `list_locations` | List all saved locations |
| `recall_location` | Look up a specific location |

---

## Grand Goals Available

### 🐉 Defeat the Ender Dragon (25 tasks, 6 phases)

```
Phase 1: Basic Survival
  get_wood ──→ make_crafting_table ──→ make_wooden_pickaxe ──→ make_stone_pickaxe
  find_food (anytime)  ·  build_shelter (anytime, with door)

Phase 2: Iron Age
  make_stone_pickaxe ──→ make_iron_pickaxe + make_iron_sword + make_iron_armor + make_bucket + make_shield

Phase 3: Diamond Age
  make_iron_pickaxe ──→ mine_diamonds ──→ make_diamond_pickaxe + make_diamond_sword

Phase 4-6: Nether → Ender → End
  diamond_pickaxe + bucket ──→ obsidian ──→ portal ──→ fortress ──→ blazes
  blazes ──→ blaze_powder ─┐
  diamond_sword ──→ endermen ├──→ eyes ──→ stronghold ──→ portal ──→ dragon
```

### ⚔️ Full Iron Gear (8 tasks, 2 phases)
```
get_wood → crafting_table → wooden_pick → stone_pick → iron_pick + iron_sword + iron_armor + shield
```

### 🏠 Cozy Base (6 tasks, 2 phases)
```
Gather: wood, stone (parallel)
Build: shelter (with door) + crafting_table + furnace + chests
```

---

## Prerequisites

- **Node.js** (v18+)
- **Python** (3.10+)
- **Minecraft Java Edition** (1.21.4)
- **Local LLM** — Open WebUI + Ollama with Qwen3:30b-a3b (or any model with tool calling)

## Setup

### 1. Node.js (Mineflayer API Server)

```bash
cd minecraft-bot
npm install mineflayer mineflayer-pathfinder express dotenv minecraft-data vec3
```

### 2. Python (LangChain Agent)

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux
pip install langchain langchain-openai requests python-dotenv
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

# Local LLM (Open WebUI)
LOCAL_LLM_BASE_URL=http://192.168.86.250:12000
LOCAL_LLM_API_KEY=your-jwt-token
LOCAL_LLM_MODEL=qwen3:30b-a3b

# Agent
TICK_INTERVAL=3
MAX_ITERATIONS=5
```

## Running

**Terminal 1 — Minecraft bot API server:**
```bash
node server.js
```

**Terminal 2 — AI Agent:**
```bash
python agent.py
```

---

## Project Structure

```
minecraft-bot/
│
├── server.js              # [Node.js] Mineflayer + Express REST API
│
├── agent.py               # [Python] Main 3-layer tick loop + LLM solution capture
├── chain_library.py       # Hardcoded action chains + search strategies (14 chains)
├── chain_executor.py      # Layer 0+1 execution engine + auto-fix + experience check
├── experience_memory.py   # Remember what worked (search successes, LLM error fixes)
│
├── tools.py               # 29 LangChain tools (for LLM Layer 2 only)
│
├── grand_goal.py          # Grand goal dependency graph + auto-inventory checks
├── grand_goal_tools.py    # LangChain tools for goal management
│
├── death_analyzer.py      # Death snapshot capture + lesson extraction
├── death_tools.py         # LangChain tools for death learning
│
├── spatial_memory.py      # Named waypoint storage with categories
├── memory_tools.py        # LangChain tools for location memory
│
├── grand_goal_state.json  # [Auto] Saved goal progress
├── death_lessons.json     # [Auto] Persistent death lessons
├── waypoints.json         # [Auto] Persistent saved locations
├── experience.json        # [Auto] Persistent experience data (search + error solutions)
│
├── .env                   # Configuration
├── package.json           # Node.js dependencies
├── requirements.txt       # Python dependencies
└── README.md
```

---

## Performance: v3 vs v6

| Metric | v3 (LLM every tick) | v6.1 (Chain of Action + Learning) |
|--------|--------------------|--------------------|
| LLM calls per minute | ~12 | ~0.3 |
| Time per action | 5-15s (LLM thinking) | 1-2s (direct API) |
| Actions per minute | ~4-6 | ~20-30 |
| Iron pickaxe time | ~15-30 min | ~3-5 min |
| Memory between ticks | ❌ None | ✅ Chain state + history |
| Resource search | LLM guesses | Systematic strategy |
| Error recovery | LLM every time | Experience-first, LLM as fallback |
| Same error twice | LLM again | Auto-handled from experience |

---

## Cost

| Component | Cost |
|-----------|------|
| Autonomous agent loop | **$0** (local LLM) |
| LLM calls (~0.3/min) | **$0** (local) |
| Player chat via Claude | ~500 tokens/message (optional) |

---

## Roadmap

- [x] Mineflayer REST API server
- [x] LangChain Agent with 29 tools
- [x] Grand Goal dependency graph
- [x] **3-Layer execution (instinct / chain / LLM)**
- [x] **Hardcoded action chains (14 chains)**
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
- [x] Spatial memory
- [x] Structure scan + rebuild
- [x] **6-direction safe block placement**
- [x] **Auto-save locations from Layer 1 (crafting table, furnace, shelter)**
- [x] **LLM solution capture → experience memory (learning loop)**
- [x] **Smart pathfinding (auto-mine obstacles)**
- [x] **Dynamic timeout scaling for long operations**
- [ ] Nether navigation + portal building
- [ ] Claude API for player conversation
- [ ] Chest inventory management
- [ ] Dynamic chain generation by LLM

---

**Author**: Jun
**Created**: 2026-02-13
**Version**: v6.1 — Chain of Action + Learning Loop
