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
│    - Auto-equip best tool/weapon for every action  │
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
│      │   death_analyzer.py  death_lessons.json     │
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
│     (skipped if sheltered + threats far away)
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

### Auto-Equip: Never Mine or Fight Barehanded

Every action automatically equips the best available tool — the LLM doesn't need to call `equip_item` manually. This is enforced at the server level, not dependent on LLM judgment:

```
mine_block("oak_log")    → auto-equips: diamond_axe > iron_axe > stone_axe > wooden_axe
mine_block("iron_ore")   → auto-equips: diamond_pickaxe > iron_pickaxe > stone_pickaxe
mine_block("dirt")        → auto-equips: diamond_shovel > iron_shovel > stone_shovel
attack_entity("zombie")  → auto-equips: diamond_sword > iron_sword > stone_sword
dig_down / dig_tunnel     → auto-equips: best pickaxe available
dig_shelter               → auto-equips: best pickaxe available
```

If a tool breaks mid-action, the next best tool is auto-equipped. The response always reports what tool was used:
```
"Mined 3 iron_ore (using stone_pickaxe)"
"Mined 5 oak_log (using iron_axe)"
"Mined 2 stone (no tool available — used fist!)"
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

| Environment | Detection | Icon |
|-------------|-----------|------|
| Surface | Sky visible (no solid blocks above within 64 blocks) | 🌍 |
| Indoors | Sky blocked + y ≥ 50 (under a roof, near surface) | 🏠 |
| Underground | Sky blocked + y < 50 (cave, mine, or tunnel) | ⛏️ |
| Deep Underground | Sky blocked + y < 0 (deepslate layer) | 🕳️ |
| Dark warning | Light level < 8 (hostile mobs can spawn here) | ⚠️ |

### Shelter-Aware Survival

The bot won't panic and run outside when it's already safe:

```
check_survival_override()
  │
  ├─ Is sheltered? (indoors / underground / deep_underground)
  │   ├─ Threats > 10m away → ignore, continue working
  │   ├─ Threats 5-10m → "STAY INSIDE, don't go out"
  │   └─ Threats < 5m → "Fight it or seal entrance"
  │
  ├─ NOT sheltered + flee? → run / dig_shelter
  ├─ NOT sheltered + night? → "get underground NOW"
  └─ Sheltered + night? → nothing (safe, keep working)
```

Previously, the bot would detect a creeper 22m away while inside a shelter and panic-flee outside into more danger. Now it stays put.

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
  ├─ 3. LLM analyzes → lesson stored → saved to death_lessons.json
  │     [HIGH] "Craft a sword before exploring at night"
  │
  ├─ 4. LLM re-evaluates with full context:
  │     "I died because no weapon. Available tasks include
  │      make_iron_sword and build_shelter. I'll do those first."
  │
  └─ 5. Lessons persist forever (file + prompt injection):
        ⚠️ LESSONS FROM 3 PAST DEATH(S):
          [HIGH] Craft sword before nightfall
          [MED] Carry food when exploring far from base
```

The bot gets smarter with every death. Lessons survive restarts via `death_lessons.json`.

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
- Auto-equips the best available weapon
- Chases the target and attacks until it dies
- Eats food mid-fight if health drops below 8
- Flees immediately if health ≤ 4 and no food left
- Avoids creepers at close range (explosion risk)
- Unconditionally runs from Wardens
- Auto-collects item drops after a kill

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

**Full iron chain works end-to-end:**
```
stone_pickaxe → mine iron_ore (3) → mine coal (3) → craft furnace
→ smelt raw_iron → iron_ingot × 3 → craft iron_pickaxe
```

### Shelter: Build vs Dig

| Situation | Tool | How it works |
|-----------|------|-------------|
| Have 20+ blocks | `build_shelter` | 5×3×5 enclosed room on surface (walls + roof) |
| Emergency, no blocks | `dig_shelter` | 3×3×3 underground room, entrance sealed |

### Directional Mining

| Tool | What it does |
|------|-------------|
| `dig_down(target_y=12)` | Staircase mine downward, lava auto-stop |
| `dig_tunnel("north", 20)` | Horizontal 1×2 tunnel, reports ores found |

### Spatial Memory

Locations persist in `waypoints.json` across restarts:

```
📍 KNOWN LOCATIONS (5):
  [CRAFTING] crafting_table: (105, 64, -48) (12m)
  [CRAFTING] furnace: (107, 64, -48) (14m)
  [SHELTER]  shelter: (100, 64, -50) (8m)
  [STORAGE]  chest: (103, 64, -49) (10m)
  [RESOURCE] iron_cave: (80, 32, -60) (45m)
```

---

## Persistence (Survives Restarts)

| Data | File | Persists? |
|------|------|:-:|
| Grand goal progress | `grand_goal_state.json` | ✅ |
| Death lessons | `death_lessons.json` | ✅ |
| Saved locations | `waypoints.json` | ✅ |
| GoalPlanner current task | in-memory | ❌ (LLM picks new) |

---

## Available Tools (25+)

### Perception (7)
| Tool | Description |
|------|-------------|
| `get_world_state` | Position, health, inventory, entities, time, **environment** (surface/cave/indoor + light) |
| `get_inventory` | Detailed inventory contents |
| `get_nearby` | Block counts and entity list within range |
| `find_block` | Find nearest block of a specific type |
| `assess_threat` | Combat readiness vs nearby threats → fight/avoid/flee |
| `get_recipe` | Look up crafting recipe + check what's missing |
| `search_item` | Search item/block names by keyword |

### Movement (5)
| Tool | Description |
|------|-------------|
| `move_to` | Move to coordinates (dynamic timeout based on distance) |
| `move_to_player` | Move to a specific player |
| `follow_player` | Continuously follow a player |
| `explore` | Walk in a random direction |
| `stop_moving` | Stop all movement |

### Resource / Combat (3)
| Tool | Description |
|------|-------------|
| `mine_block` | Mine blocks by type (**auto-equips** best pickaxe/axe/shovel) |
| `place_block` | Place a block from inventory (auto-saves important blocks to memory) |
| `attack_entity` | Smart combat (**auto-equips** best weapon, heal, flee, collect drops) |

### Mining (2)
| Tool | Description |
|------|-------------|
| `dig_down` | Staircase mine downward (**auto-equips** pickaxe). Lava auto-stop |
| `dig_tunnel` | Horizontal tunnel (**auto-equips** pickaxe). Reports ores found |

### Survival (7)
| Tool | Description |
|------|-------------|
| `eat_food` | Eat best available food |
| `equip_item` | Manually equip specific item (rarely needed — most actions auto-equip) |
| `craft_item` | Craft items (auto-finds crafting table) |
| `smelt_item` | Smelt in furnace (auto-crafts furnace if needed) |
| `dig_shelter` | Emergency underground shelter (**auto-equips** pickaxe). No blocks needed |
| `build_shelter` | Build 5×3×5 enclosed shelter (roof built edge→center for reliability) |
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
| `set_grand_goal` | Set ultimate objective (ender_dragon / full_iron / cozy_base) |
| `complete_grand_task` | Mark a milestone done → unlock dependents |
| `skip_grand_task` | Skip optional tasks |
| `get_grand_goal_status` | See full dependency graph + available tasks |

### Death Analysis (4)
| Tool | Description |
|------|-------------|
| `check_death` | Detect if bot just died, get snapshot |
| `learn_from_death` | Store a lesson (persisted to `death_lessons.json`) |
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

```
Phase 1: Basic Survival
  get_wood ──→ make_crafting_table ──→ make_wooden_pickaxe ──→ make_stone_pickaxe
  find_food (always available)
  build_shelter (always available)

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

Phase 6: The End
  craft_eyes ──→ find_stronghold ──→ activate_portal ──→ prepare_for_fight ──→ defeat_dragon
```

### ⚔️ Full Iron Gear (8 tasks) · 🏠 Cozy Base (8 tasks)

---

## Setup

### Prerequisites
- **Node.js** v18+ · **Python** 3.10+ · **Minecraft Java Edition** 1.21.4
- **Local LLM** — Open WebUI + Ollama with GLM-4.7-Flash (or any model with tool calling)

### Install

```bash
# Node.js
npm install mineflayer mineflayer-pathfinder express dotenv minecraft-data vec3

# Python
pip install langchain langchain-openai requests python-dotenv
```

### Configure `.env`

```env
BOT_HOST=localhost
BOT_PORT=55888
BOT_USERNAME=PenguinBot
BOT_VERSION=1.21.4
API_PORT=3001
LOCAL_LLM_BASE_URL=http://192.168.86.250:12000
LOCAL_LLM_API_KEY=your-jwt-token
LOCAL_LLM_MODEL=glm-4.7-flash:latest
TICK_INTERVAL=5
MAX_ITERATIONS=6
```

### Run

```bash
node server.js     # Terminal 1: Minecraft bot
python agent.py    # Terminal 2: AI agent
```

---

## Project Structure

```
minecraft-bot/
├── server.js              # Mineflayer REST API (auto-equip, combat, smelt, dig, build)
├── agent.py               # Agent loop (survival override, death reassess, context injection)
├── tools.py               # 25+ LangChain tools
├── grand_goal.py          # Dependency graph + persistence
├── grand_goal_tools.py    # Grand goal LangChain tools
├── grand_goal_state.json  # [auto] Grand goal progress
├── goal_planner.py        # Step-by-step task execution
├── goal_tools.py          # Goal LangChain tools
├── death_analyzer.py      # Death snapshots + lesson learning + persistence
├── death_tools.py         # Death LangChain tools
├── death_lessons.json     # [auto] Persistent death lessons
├── spatial_memory.py      # Waypoint storage with categories
├── memory_tools.py        # Memory LangChain tools
├── waypoints.json         # [auto] Persistent locations
├── .env                   # Configuration
└── README.md
```

---

## Design Philosophy: Code > Prompts

A key lesson from building with a small local LLM (3B active parameters):

**Mechanical actions are enforced in code, not prompted.** The LLM decides *what* to do (strategic), but the server handles *how* to do it (tactical):

| Decision | Who decides | Why |
|----------|-------------|-----|
| "Should I mine iron or build shelter?" | **LLM** | Context-dependent strategy |
| "Which pickaxe to equip before mining?" | **Server code** | Always the same answer: best available |
| "Should I flee or fight this zombie?" | **LLM** (with threat assessment data) | Depends on gear, health, situation |
| "Eat food when health < 8 mid-combat?" | **Server code** | Always yes — no judgment needed |
| "Seal shelter entrance during flee?" | **Server code** | Always yes |
| "Which task to do after dying?" | **LLM** | Requires analyzing death + available tasks |

This means the bot works reliably even with a small model. The LLM only needs to make high-level decisions — everything else is automated.

---

## Cost

| Component | Cost |
|-----------|------|
| Autonomous agent loop | **$0** (local LLM) |
| Player chat via Claude | ~500 tokens/message |

---

## Roadmap

- [x] Mineflayer REST API server
- [x] LangChain Agent with 25+ tools
- [x] Grand Goal system with dependency graph
- [x] Auto-equip best tool/weapon for all actions
- [x] Environment detection (surface/cave/indoor/deep + light level)
- [x] Shelter-aware survival (don't flee when already safe)
- [x] Smart combat AI (heal, flee, auto-equip)
- [x] Threat assessment engine
- [x] Furnace smelting (auto-craft furnace)
- [x] Full iron tool chain (ore → smelt → craft)
- [x] Emergency underground shelter (dig + seal)
- [x] Directional mining (staircase, tunnel, lava detection)
- [x] Death lessons with file persistence
- [x] Death → auto-cancel → reassess
- [x] Spatial memory with persistent waypoints
- [x] Grand goal persistence
- [x] Reliable shelter building (roof edge→center)
- [x] Dynamic move_to timeout (distance-based)
- [ ] Nether navigation + portal building
- [ ] Claude API for player conversation
- [ ] Chest inventory management
- [ ] Content automation (screenshots → social media)

---

**Author**: Jun · **Version**: v3.4 — Auto-Equip, Death Persistence & Shelter-Aware Survival