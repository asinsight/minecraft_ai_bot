# 🤖 Minecraft Autonomous AI Bot (v3 — Grand Goal Architecture)

An autonomous Minecraft bot that sets a grand objective (like defeating the Ender Dragon) and **decides for itself** how to get there — choosing which tasks to work on based on the situation, surviving, learning from deaths, remembering locations, and making smart combat decisions.

Powered by LangChain Agent + Local LLM (GLM-4.7-Flash). Zero API cost for autonomous play.

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
│    - Environment detection (surface/cave/indoor)   │
│    - Smart combat (heal, flee, auto-equip)         │
│    - Threat assessment engine                      │
│    - Furnace smelting (auto-craft furnace if needed)│
│    - Death snapshot tracking                       │
│    - Shelter: build (surface) or dig (underground) │
│    - Directional mining (staircase, tunnel)        │
│    - Item/block search via minecraft-data          │
├──────────────────────────────────────────────────┤
│                 HTTP (localhost:3001)              │
├──────────────────────────────────────────────────┤
│    [Python] LangChain Agent + Subsystems          │
│                                                    │
│    agent.py ─── Main loop (tick every 5s)         │
│      │                                             │
│      ├── Grand Goal ─── Dependency graph           │
│      │   grand_goal.py    LLM picks from available │
│      │                    tasks freely              │
│      │                                             │
│      ├── Goal Planner ── Step-by-step execution    │
│      │   goal_planner.py  "Mine 3 iron ore" etc.  │
│      │                                             │
│      ├── Death Analyzer ─ Learn from mistakes      │
│      │   death_analyzer.py Lessons persist         │
│      │                                             │
│      ├── Spatial Memory ─ Remember places          │
│      │   spatial_memory.py waypoints.json          │
│      │                                             │
│      └── Tools (25+) ─── Perception, actions       │
│          tools.py                                  │
│                                                    │
├──────────────────────────────────────────────────┤
│    [Local LLM] GLM-4.7-Flash via Open WebUI       │
│    29.9B MoE (3B active) · Native tool calling     │
│    Cost: $0                                        │
└──────────────────────────────────────────────────┘
```

---

## Core Design: Autonomous Decision-Making

### Dependency Graph, Not Fixed Order

Traditional approach: Phase 1 → Phase 2 → Phase 3 (rigid sequence).

Our approach: **Tasks have dependencies (`requires`), not a fixed order.** The LLM sees all available tasks and chooses what to do based on the current situation.

```
Task "mine_diamonds" requires ["make_iron_pickaxe"]
  → Blocked until iron pickaxe is done.

Task "find_food" requires []
  → Available from the start. Can be done anytime.

Task "build_shelter" requires []
  → Night coming? LLM can prioritize this over mining.
```

Phases (Survival → Iron → Diamond → Nether → Ender → End) are **organizational labels**, not gates. Multiple tasks across "phases" can be available simultaneously.

### What the LLM Sees Each Tick

```
🏆 GRAND GOAL: Defeat the Ender Dragon!
   Progress: Tasks 6/25 (24%)

   📋 Phase 1: Basic Survival [4/6]
      ✅ Gather wood
      ✅ Make crafting table
      ✅ Wooden pickaxe
      ✅ Stone pickaxe
      ⬜ Hunt animals for food
      ⬜ Build an enclosed shelter

   📋 Phase 2: Iron Age [2/5]
      ⬜ Craft an iron pickaxe
      ⬜ Craft an iron sword
      🔒 Craft iron armor [needs: make_stone_pickaxe]
      🔒 Craft a shield [needs: make_iron_sword]
      ⬜ Craft a bucket

👉 YOU CAN WORK ON (5 available):
   - find_food: Hunt animals for food → set_goal("find_food")
   - build_shelter: Build an enclosed shelter → set_goal("build_shelter")
   - make_iron_pickaxe: Craft an iron pickaxe → set_goal("make_iron_pickaxe")
   - make_iron_sword: Craft an iron sword
   - make_bucket: Craft a bucket

   Choose based on: health, hunger, time, nearby resources, threats.
   You are FREE to do tasks in any order. Prioritize survival if needed.
```

The LLM might decide:
- "It's getting dark and I have no shelter → `build_shelter` first"
- "I see iron ore right here → `make_iron_pickaxe` while I'm close"
- "Hunger is at 3 → `find_food` immediately"
- "I just died at night without a sword → `make_iron_sword` before going out again"

---

## How It Works

### Every 5 Seconds (One Tick)

```
┌─ 1. Threat Assessment ── fight / avoid / flee?
│
├─ 2. Survival Check ───── health < 5? hunger < 5? override!
│
├─ 3. Death Check ──────── just died? cancel task, analyze, learn
│
├─ 4. Grand Goal Context ─ which tasks are available?
│
├─ 5. GoalPlanner Context ─ current step of active task?
│
└─ 6. LLM Decides ──────── picks tools, executes, advances progress
```

### Environment Awareness

The bot knows where it is — not just coordinates, but what kind of space it's in:

```
Invoking: `get_world_state`

  Position: x=80.5, y=32.0, z=-60.3
  Health: 18/20, Hunger: 16/20
  Time: night (tick 15200)
  Environment: ⛏️ Underground (cave/mine) ⚠️ DARK (mobs can spawn!) (roof 18 blocks up)
  Weather: clear
  Inventory: stone_pickaxe x1, raw_iron x2, torch x8
  Nearby blocks: stone, deepslate, iron_ore, coal_ore
  Nearby entities: zombie(12m), bat(6m)
```

Environment is detected by scanning upward from the bot's head for solid blocks:

| Environment | Detection | Icon |
|-------------|-----------|------|
| Surface | Sky visible (no solid blocks above within 64 blocks) | 🌍 |
| Indoors | Sky blocked + y ≥ 50 (under a roof, near surface) | 🏠 |
| Underground | Sky blocked + y < 50 (cave, mine, or tunnel) | ⛏️ |
| Deep Underground | Sky blocked + y < 0 (deepslate layer) | 🕳️ |
| Dark warning | Light level < 8 (hostile mobs can spawn here) | ⚠️ |

This lets the LLM make contextual decisions:
- "I'm underground and it's dark → place torches"
- "I'm in a cave with a zombie nearby → fight or flee?"
- "I'm indoors in my shelter → safe, can craft"

### Death → Reassess → Adapt

When the bot dies, it doesn't just resume what it was doing:

```
💀 Bot died!
  │
  ├─ 1. Current GoalPlanner task AUTO-CANCELLED
  │     "You were doing 'mine_iron_ore' — cancelled."
  │
  ├─ 2. Death snapshot captured
  │     health=3, zombie(2m)+skeleton(5m), night, weapon=fist
  │
  ├─ 3. LLM analyzes → lesson stored
  │     [HIGH] "Craft a sword before exploring at night"
  │
  ├─ 4. LLM re-evaluates with full context:
  │     "I died because no weapon. Available tasks include
  │      make_iron_sword and build_shelter. I'll do those first
  │      before going back to mining."
  │
  └─ 5. Lessons persist — every future tick sees:
        ⚠️ LESSONS FROM PAST DEATHS:
          [HIGH] Craft sword before nightfall
          [MED] Carry food when exploring far from base
```

The bot gets smarter with every death. Lessons are injected into every future prompt, influencing all decisions.

### Smart Combat AI

The bot evaluates threats before engaging:

```
GET /threat_assessment

  Player Power = weapon(7) + armor(3) + health(18×0.3) + food_bonus(2) = 17.4
  Threat Level = zombie(2) + skeleton(3×distance_factor) = 5.8

  → Recommendation: "fight" (strong advantage)
```

| Recommendation | Action |
|---------------|--------|
| `safe` | No threats, continue current task |
| `fight` | Strong advantage — engage confidently |
| `fight_careful` | Slight advantage — watch health, eat if needed |
| `avoid` | Outmatched — craft better gear first, don't engage |
| `flee` | Danger! Run to shelter or `dig_shelter` underground |

**During combat**, the bot:
- Auto-equips the best available weapon (diamond_sword > iron > stone > wood > fist)
- Chases the target and attacks until it dies
- Eats food mid-fight if health drops below 8
- Flees immediately if health ≤ 4 and no food left
- Avoids creepers at close range (explosion risk)
- Unconditionally runs from Wardens
- Auto-collects item drops after a kill

**Pre-combat check**: Won't engage if unarmed + low health → flees and reports "Craft a sword first."

### Smelting & Iron Age

The bot can smelt ores in a furnace — essential for iron, gold, and cooked food:

```
smelt_item("raw_iron", 3)
  │
  ├─ Furnace nearby? → walk to it
  ├─ No furnace? → auto-craft (8 cobblestone) + place it
  ├─ Load fuel (coal, charcoal, planks, logs)
  ├─ Insert raw items → wait → collect output
  └─ Result: "Smelted raw_iron → got iron_ingot x3"
```

**Full iron chain now works end-to-end:**
```
stone_pickaxe → mine iron_ore (3) → mine coal (3) → craft furnace
→ smelt raw_iron → iron_ingot × 3 → craft iron_pickaxe
```

**No coal? Make charcoal:** `smelt_item("oak_log")` using planks as fuel → charcoal.

### Shelter: Build vs Dig

Two shelter strategies depending on the situation:

| Situation | Tool | How it works |
|-----------|------|-------------|
| Have 20+ blocks (cobble, dirt, planks) | `build_shelter` | Builds 5×3×5 enclosed room on surface |
| Emergency, no blocks, night coming | `dig_shelter` | Digs 3×3×3 underground room, seals entrance |

```
dig_shelter
  ├─ Dig entrance shaft (2 blocks down)
  ├─ Carve 3×3×3 room underground
  ├─ Move bot inside
  ├─ Seal entrance with any available block
  └─ Safe from all mobs! No materials needed.
```

When fleeing from threats with no saved shelter nearby, the bot can `dig_shelter` to instantly hide.

### Directional Mining

For reaching ore levels and strip mining:

| Tool | What it does |
|------|-------------|
| `dig_down(target_y=12)` | Staircase mine downward, stops at target Y or on lava |
| `dig_tunnel("north", 20)` | Horizontal 1×2 tunnel, reports ores found, stops on lava |

```
dig_down(target_y=12)
  ├─ Mines staircase pattern (2-high, 1 down each step)
  ├─ Checks for lava below each step → auto-stops
  └─ "Reached y=12 (40 blocks). Diamond level!"

dig_tunnel("east", 30)
  ├─ Digs 1×2 horizontal tunnel
  ├─ Tracks ores encountered while digging
  ├─ Stops on lava
  └─ "Tunnel complete: 30 blocks east at y=12. Ores found: iron_ore×4, diamond_ore×2"
```

### Spatial Memory

The bot remembers important locations, persisted in `waypoints.json` across restarts:

```
📍 KNOWN LOCATIONS (5):
  [CRAFTING]
    crafting_table: (105, 64, -48) (12m)
    furnace: (107, 64, -48) (14m)
  [SHELTER]
    shelter: (100, 64, -50) (8m) — Enclosed 5x3x5 cobblestone
    shelter_2: (95, 58, -55) (18m) — Emergency underground shelter
  [STORAGE]
    chest: (103, 64, -49) (10m)
  [RESOURCE]
    iron_cave: (80, 32, -60) (45m) — Found iron ore vein
```

**Auto-saved**: crafting table, chest, furnace, bed, shelter (both types).

**Manually saved**: resource veins, villages, points of interest (LLM decides).

**Used for navigation**: "Need to craft? → `find_nearest_location('crafting')` → walk there" instead of building a new one.

---

## Persistence (Survives Restarts)

| Data | File | Survives restart? |
|------|------|:-:|
| Grand goal progress | `grand_goal_state.json` | ✅ |
| Death lessons | in-memory (death_analyzer) | ❌ (planned) |
| Saved locations | `waypoints.json` | ✅ |
| GoalPlanner current task | in-memory | ❌ (resets, LLM picks new) |

Grand goal phase/task completion is saved after every state change. On restart, the bot loads progress and continues from where it left off. The LLM will pick a new task from the available set.

---

## Available Tools (25+)

### Perception (7)
| Tool | Description |
|------|-------------|
| `get_world_state` | Full snapshot: position, health, inventory, entities, time, **environment** (surface/cave/indoor + light + dark warning) |
| `get_inventory` | Detailed inventory contents |
| `get_nearby` | Block counts and entity list within range |
| `find_block` | Find nearest block of a specific type |
| `assess_threat` | Combat readiness vs nearby threats → fight/avoid/flee |
| `get_recipe` | Look up crafting recipe + check what's missing from inventory |
| `search_item` | Search item/block names by keyword (minecraft-data DB) |

### Movement (5)
| Tool | Description |
|------|-------------|
| `move_to` | Move to x, y, z coordinates |
| `move_to_player` | Move to a specific player |
| `follow_player` | Continuously follow a player |
| `explore` | Walk in a random direction |
| `stop_moving` | Stop all movement |

### Resource / Combat (3)
| Tool | Description |
|------|-------------|
| `mine_block` | Mine blocks by type (with pathfinding + auto-collect drops) |
| `place_block` | Place a block from inventory (auto-saves important blocks to memory) |
| `attack_entity` | Smart combat: chase → hit until dead → heal mid-fight → flee if losing → collect drops |

### Mining (2)
| Tool | Description |
|------|-------------|
| `dig_down` | Staircase mine downward to a target Y level. Lava detection auto-stop |
| `dig_tunnel` | Horizontal 1×2 tunnel in a direction. Reports ores found. Lava auto-stop |

### Survival (7)
| Tool | Description |
|------|-------------|
| `eat_food` | Eat best available food from inventory |
| `equip_item` | Equip weapon/armor/tool to hand or armor slot |
| `craft_item` | Craft items (auto-detects crafting table, walks to it if needed) |
| `smelt_item` | Smelt in furnace (auto-crafts furnace if needed). Requires fuel |
| `dig_shelter` | Emergency: dig 3×3×3 underground room + seal entrance. No blocks needed |
| `build_shelter` | Build enclosed 5×3×5 shelter with walls + roof (needs 20+ blocks) |
| `sleep_in_bed` | Sleep in a nearby bed |

### Communication (1)
| Tool | Description |
|------|-------------|
| `send_chat` | Send message in game chat |

### Goal Management (7)
| Tool | Description |
|------|-------------|
| `set_goal` | Start a predefined multi-step task |
| `complete_step` | Mark current step done → advance to next |
| `fail_step` | Mark step failed (retries up to 3×) |
| `skip_step` | Skip already-completed step |
| `cancel_goal` | Abandon current task |
| `get_goal_status` | Check current task progress |
| `list_available_goals` | See all predefined tasks |

### Grand Goal (4)
| Tool | Description |
|------|-------------|
| `set_grand_goal` | Set ultimate objective |
| `complete_grand_task` | Mark a major milestone done → unlock dependents |
| `skip_grand_task` | Skip optional tasks |
| `get_grand_goal_status` | See full dependency graph + available tasks |

### Death Analysis (4)
| Tool | Description |
|------|-------------|
| `check_death` | Detect if bot just died, get snapshot |
| `learn_from_death` | Store a lesson from death analysis |
| `get_lessons` | Review all stored death lessons |
| `get_death_stats` | Death count and recent causes |

### Spatial Memory (5)
| Tool | Description |
|------|-------------|
| `save_location` | Save a named waypoint with category |
| `delete_location` | Remove a waypoint |
| `find_nearest_location` | Find closest saved place by category |
| `list_locations` | List all saved locations with distances |
| `recall_location` | Look up a specific saved location |

---

## Grand Goals Available

### 🐉 Defeat the Ender Dragon (25 tasks, 6 phases)

Tasks have dependencies — LLM freely chooses from unlocked tasks:

```
Phase 1: Basic Survival
  get_wood ──→ make_crafting_table ──→ make_wooden_pickaxe ──→ make_stone_pickaxe
  find_food (no dependency — available anytime)
  build_shelter (no dependency — available anytime)

Phase 2: Iron Age (requires smelting!)
  make_stone_pickaxe ──→ make_iron_pickaxe   [mine ore → smelt → craft]
                    ──→ make_iron_sword ──→ make_shield (optional)
                    ──→ make_iron_armor (optional)
                    ──→ make_bucket

Phase 3: Diamond Age
  make_iron_pickaxe ──→ mine_diamonds ──→ make_diamond_pickaxe
                                      ──→ make_diamond_sword
                                      ──→ enchanting_setup (optional)

Phase 4: The Nether
  make_diamond_pickaxe + make_bucket ──→ get_obsidian ──→ build_portal
  build_portal ──→ find_fortress ──→ kill_blazes

Phase 5: Eyes of Ender
  kill_blazes ──→ craft_blaze_powder ─┐
  make_diamond_sword ──→ kill_endermen ├──→ craft_eyes
                                       │
Phase 6: The End
  craft_eyes ──→ find_stronghold ──→ activate_portal ──→ prepare_for_fight ──→ defeat_dragon
```

### ⚔️ Full Iron Gear (8 tasks, 2 phases)
```
get_wood → crafting_table → wooden_pick → stone_pick → iron_pick + iron_sword + iron_armor + shield
```

### 🏠 Cozy Base (8 tasks, 2 phases)
```
Gather: wood, stone, iron (parallel)
Build: shelter + crafting_table + furnace + chests + bed
```

---

## Prerequisites

- **Node.js** (v18+)
- **Python** (3.10+)
- **Minecraft Java Edition** (1.21.4)
- **Local LLM** — Open WebUI + Ollama with GLM-4.7-Flash (or any model with tool calling)

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

```bash
copy .env.example .env       # Windows
# cp .env.example .env       # Mac/Linux
```

Edit `.env`:
```env
# Minecraft Bot
BOT_HOST=localhost
BOT_PORT=55888               # LAN port from Minecraft
BOT_USERNAME=PenguinBot
BOT_VERSION=1.21.4

# API Server
API_PORT=3001

# Local LLM (Open WebUI)
LOCAL_LLM_BASE_URL=http://192.168.86.250:12000
LOCAL_LLM_API_KEY=your-jwt-token
LOCAL_LLM_MODEL=glm-4.7-flash:latest

# Agent
TICK_INTERVAL=5
MAX_ITERATIONS=6

# Claude API (optional — for player chat)
ANTHROPIC_API_KEY=sk-your-key-here
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

> Make sure Minecraft is running with "Open to LAN" before starting.

---

## Project Structure

```
minecraft-bot/
│
├── server.js              # [Node.js] Mineflayer + Express REST API
│                            Environment detection, smart combat, threat assessment,
│                            death tracking, smelting, shelter (build + dig),
│                            directional mining, item search
│
├── agent.py               # [Python] Main agent loop
│                            Tick loop, survival override, death reassessment,
│                            context injection (goal + death + memory)
│
├── tools.py               # 25+ LangChain tools (perception, combat, mining, survival)
│
├── grand_goal.py          # Grand goal system — dependency graph, phases, persistence
├── grand_goal_tools.py    # LangChain tools for grand goal management
├── grand_goal_state.json  # [Auto-generated] Saved grand goal progress
│
├── goal_planner.py        # Step-by-step task execution (8 predefined goals)
├── goal_tools.py          # LangChain tools for goal step management
│
├── death_analyzer.py      # Death snapshot capture + lesson extraction
├── death_tools.py         # LangChain tools for death learning
│
├── spatial_memory.py      # Named waypoint storage with categories + distances
├── memory_tools.py        # LangChain tools for location memory
├── waypoints.json         # [Auto-generated] Saved locations (persistent)
│
├── .env                   # Configuration (git-ignored)
├── .env.example           # Template
├── package.json           # Node.js dependencies
├── requirements.txt       # Python dependencies
└── README.md
```

---

## Example Agent Log

```
══════════════════════════════════════════
🔄 Tick #42
🏆 Defeat the Ender Dragon [Tasks 8/25 (32%)]
🎯 Active: make_iron_pickaxe [Step 5/10]
══════════════════════════════════════════

Invoking: `get_world_state`
  Position: x=80.5, y=32.0, z=-60.3
  Health: 18/20, Hunger: 16/20
  Time: afternoon
  Environment: ⛏️ Underground (cave/mine) ⚠️ DARK (roof 18 blocks up)
  Nearby: stone, iron_ore, coal_ore, zombie(15m)

  "I'm underground near iron. Let me mine it and smelt."

Invoking: `mine_block` with {'block_type': 'iron_ore', 'count': 3}
  Mined 3 iron_ore → got raw_iron x3

Invoking: `smelt_item` with {'item_name': 'raw_iron', 'count': 3}
  Smelted raw_iron → got iron_ingot x3

Invoking: `craft_item` with {'item_name': 'iron_pickaxe'}
  Crafted iron_pickaxe

✅ Agent: Found iron in cave, smelted, and crafted iron pickaxe!
⏱️  Tick took 14.2s

══════════════════════════════════════════
🔄 Tick #55  (night, on surface)
══════════════════════════════════════════

⚔️ Threat: FLEE — Low health (6/20) and no food.

Invoking: `get_world_state`
  Environment: 🌍 Surface ⚠️ DARK (mobs can spawn!)
  Nearby entities: zombie(8m), skeleton(14m)
  No shelter saved nearby.

  "Critical! Night, low health, no food, no shelter.
   Digging emergency shelter NOW."

Invoking: `dig_shelter`
  Dug emergency underground shelter (22 blocks mined).
  Entrance sealed! | 📍 Saved as 'shelter'

✅ Agent: Emergency — dug underground shelter. Safe from mobs.
```

---

## Cost

| Component | Cost |
|-----------|------|
| Autonomous agent loop | **$0** (local LLM) |
| Player chat via Claude | ~500 tokens/message |
| No player chat = no Claude calls | $0 |

---

## Roadmap

- [x] Mineflayer REST API server (all endpoints)
- [x] LangChain Agent with 25+ tools
- [x] Grand Goal system with dependency graph (not fixed order)
- [x] LLM autonomous task selection from available tasks
- [x] Multi-step Goal Planner (8 predefined task chains)
- [x] Environment detection (surface / cave / indoor / deep underground + light level)
- [x] Smart combat AI (heal mid-fight, flee if losing, auto-equip)
- [x] Threat assessment engine (fight/avoid/flee)
- [x] Furnace smelting (auto-craft furnace, fuel detection)
- [x] Full iron tool chain (ore → smelt → ingot → craft)
- [x] Emergency underground shelter (dig + seal, no blocks needed)
- [x] Directional mining (staircase down, horizontal tunnel, lava detection)
- [x] Death analysis + lesson learning (injected into every prompt)
- [x] Death → auto-cancel task → LLM reassesses approach
- [x] Spatial memory with persistent waypoints
- [x] Grand goal progress persistence (survives restarts)
- [x] Item/block name search (minecraft-data integration)
- [x] Surface shelter building (enclosed 5×3×5, mob-proof)
- [x] .env configuration for all settings
- [ ] Nether navigation + portal building
- [ ] Claude API integration for player conversation
- [ ] Death lessons persistence to file
- [ ] Chest inventory management (store/retrieve items)
- [ ] Content automation (screenshots → social media)

---

## Tech Stack

- **Mineflayer** — Minecraft bot framework (Node.js)
- **mineflayer-pathfinder** — Navigation and pathfinding
- **Express** — REST API server
- **minecraft-data** — Item/block/recipe database
- **vec3** — 3D vector math for block placement
- **LangChain** — Agent framework with tool calling
- **GLM-4.7-Flash** — Local LLM (29.9B MoE, 3B active)
- **Open WebUI + Ollama** — Local LLM inference server

---

**Author**: Jun
**Created**: 2026-02-13
**Version**: v3.3 — Environment Awareness