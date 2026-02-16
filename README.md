# Minecraft Autonomous AI Bot (v7.0 — Cave Intelligence + Chest Looting)

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
|    - Directional mining (staircase, tunnel, branch) |
|    - Block placement (9-pos + dig-out fallback)     |
|    - Smart pathfinding (auto-mine obstacles)       |
|    - Lava detection + water bucket neutralize      |
|    - Unreachable block skip (failedPositions)      |
|    - tunnelMove helper (reliable underground nav)  |
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
|      |   Shield block. Inventory full -> store.    |
|      |                                             |
|      +-- Layer 1: CHAIN EXECUTION (no LLM, fast)  |
|      |   chain_executor.py  Step-by-step actions   |
|      |   chain_library.py   Hardcoded chains (20)  |
|      |   experience_memory.py Learned solutions    |
|      |   - Cave-first search (scan -> memory ->    |
|      |     dig/explore fallback)                   |
|      |   - Chest looting (dungeon/ruin chests)     |
|      |   - Crafting table/furnace carry            |
|      |   - Auto-save locations (craft, shelter,    |
|      |     caves)                                  |
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
|      |   Shelters, crafting, furnace, caves         |
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

**Player chat can influence actions**: When a player says "go mine diamonds", Claude understands and calls `choose_next_chain("mine_diamonds")` or `set_grand_goal("defeat_ender_dragon")`. Players can also request entirely custom goals like "build a big house" — Claude triggers `request_custom_goal()`, and the planning LLM dynamically creates tasks from available chains.

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
HP < 5 + under attack -> flee()               instant
HP < 5 + no food      -> dig_shelter()        instant (sealed with blocks)
In water + O2 <= 12   -> escape_water()       instant (swim up + find land)
In water + O2 <= 5    -> escape_water()       instant (critical drowning)
  (turtle_helmet      -> auto-equip, threshold lowered to O2 <= 5)
Sudden HP drop (>=4)  -> shield + fight/flee  instant (based on threat rec)
Under attack          -> shield + fight/flee  instant (ranged mobs -> shield first)
Creeper within 5m     -> flee()               instant (faster than shelter)
Warden detected       -> flee()               instant
Flee recommendation   -> flee() or shelter    instant
Fight/fight_careful   -> attack nearest       instant (if hostile within 8m)
Avoid recommendation  -> flee()               instant (if hostile within 6m)
Night + surface       -> dig_shelter()        instant
Dusk + surface        -> dig_shelter()        instant
Hungry (food < 5)     -> eat_food()           instant
Mob inside shelter    -> attack_entity()      instant
Inventory full        -> store_items()        instant (if chest nearby)
Nearby drops          -> collect_drops()      instant (if safe)
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
  place_block(furnace)         # deterministic - 9-pos safe placement
  smelt_item(raw_iron, 3)      # deterministic
  craft_item(stick)            # deterministic
  craft_item(iron_pickaxe)     # deterministic
  equip_item(iron_pickaxe)     # deterministic
]
```

**Smart features:**
- **Auto-skip**: Already have cobblestone? Skip the mining step.
- **Cave-first search**: Scan for caves -> check spatial memory for known caves -> dig_down/tunnel fallback. Caves have exposed ores!
- **Search strategies**: `iron_ore` not found? -> check memory -> cave scan -> dig_down(32) -> dig_tunnel(north, 20) -- all without LLM.
- **Chest looting**: After exploring/digging, auto-detect dungeon/ruin chests within 16 blocks and loot valuables.
- **Crafting table/furnace carry**: Pick up placed crafting table and furnace after use (no waste).
- **Experience memory**: "Last time I found iron_ore at y=32 by digging down" -> try that first next time.
- **Auto-fix**: "No crafting table nearby" -> craft one -> place it -> retry. No LLM needed.
- **Auto-fix**: "No space to place block" (underground) -> mine adjacent block to clear space -> retry.
- **Auto-save locations**: Crafting table, furnace, shelter, cave positions saved to spatial memory.
- **Dynamic timeouts**: Each chain gets a timeout calculated from its steps (mine_block: 10s/block, smelt: 12s/item, dig_down: depth-based). Range: 5-15 min.
- **Shelter limit**: Only 3 most recent shelters saved (old ones auto-deleted).
- **Unreachable block skip**: If a block can't be reached, skip it and find the next one (failedPositions tracking).

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

**Available Claude tools**: `send_chat`, `set_grand_goal`, `choose_next_chain`, `skip_grand_task`, `complete_grand_task`, `get_grand_goal_status`, `request_custom_goal`

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
|     |   +- Step fails (search) -> cave-first strategy + search strategies
|     |   +- Step fails -> check experience for saved solution
|     |   +- Step fails (known fix) -> auto-fix (clear space, craft table, etc.)
|     |   +- Step fails (movement) -> mine obstacle -> escalate to LLM
|     |   +- Step fails 3x (unknown) -> escalate to Layer 2 -> save solution
|     |   +- After explore/dig -> loot nearby chests
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
  +- mine: "unreachable block" -> skip, find next block (failedPositions)
  +- move_to: "path blocked" -> mine obstacle -> escalate to LLM immediately
  |
  +- Retry 3 times -> escalate to LLM
  |     -> LLM solves it -> solution saved to experience_memory
  |     -> Next time: auto-handled at Layer 1!
  |
  +- Task stuck 5 times -> skip task -> LLM picks next
  |     -> Skipped tasks retried after other tasks complete (up to 2 retries)
```

### Search Strategy: Cave-First Resource Finding

When a search-type step fails ("no iron_ore found nearby"), the system uses a cave-first strategy with 3-phase fallback:

```
mine_block(iron_ore, 3) FAILED: "No iron_ore found nearby"
  |
  +- Phase 0: Experience memory
  |     -> "iron_ore was found at (80, 32, -60) last time"
  |     -> move_to(80, 32, -60) -> retry mine_block -> Success!
  |
  +- Cave-First Strategy (before each persistent search attempt):
  |     Step 1: Scan for NEW caves (GET /scan_caves, radius 32)
  |       -> Cave found (size >= 5 blocks)? -> move_to(cave center)
  |       -> Already explored? -> skip (chunk-level dedup)
  |       -> Save to spatial_memory for future recall
  |
  |     Step 2: Check remembered caves (spatial_memory)
  |       -> Known caves sorted by distance -> visit nearest unvisited
  |       -> Max range: 200 blocks
  |
  |     Step 3: Fallback (no caves available)
  |       -> dig_down / dig_tunnel / explore (original strategy)
  |
  +- Phase 1: Static search strategies (resource-specific, 6-11 steps)
  |     -> find_block(iron_ore, 64) -> not found
  |     -> dig_down(target_y=32) -> scan -> not found
  |     -> dig_tunnel(north, 20) -> scan -> not found
  |     -> dig_tunnel(east, 20) -> scan -> not found
  |     -> dig_tunnel(south/west, 20) -> explore(40) -> dig_down(16) -> tunnels...
  |
  +- Phase 2: Persistent search (up to 8 dynamic attempts)
  |     Cave scan before EVERY attempt (cave-first!)
  |     For ores: alternating dig_down(optimal Y) + dig_tunnel(rotating directions, longer each time)
  |     For surface: explore(30, 45, 60, 75... up to 120)
  |     After each attempt -> find_block(target, 32) -> mine if found
  |     After each explore/dig -> loot nearby chests (dungeons!)
  |
  +- Phase 3: LLM escalation (only after ~19 total attempts)
        "Cannot find iron_ore after 19 search attempts.
         Tried: caves, dig_down, dig_tunnel (all directions), explore.
         Analyze what went wrong and try a DIFFERENT approach."
```

Each resource has its own static strategy:

| Resource | Static Strategy Steps | Persistent Search |
|----------|----------------------|-------------------|
| `oak_log` | find_block -> birch/spruce -> explore(30/50/80) | explore(30->120) |
| `stone` | find_block -> dig_down(5) -> tunnel | explore(30->120) |
| `iron_ore` | find_block -> memory -> dig_down(32) -> tunnels N/E/S/W -> explore -> dig_down(16) -> tunnels (11 steps) | cave scan + dig_down(16) + tunnels (rotating) |
| `coal_ore` | find_block -> memory -> dig_down(48) -> tunnels W/N -> explore -> dig_down(40) -> tunnels E/S (9 steps) | cave scan + dig_down(48) + tunnels |
| `diamond_ore` | find_block -> deepslate -> memory -> dig_down(-58) -> tunnels all dirs -> explore -> more tunnels (12 steps) | cave scan + dig_down(-58) + tunnels |
| Animals | explore(30/50/80/60/100) | explore(30->120) |

### Chest Looting (Dungeon/Ruin Discovery)

During exploration and mining, the bot automatically detects and loots wild chests (dungeons, ruins, mineshafts):

```
After explore/dig_down/dig_tunnel/branch_mine:
  |
  +- Scan for chest/trapped_chest within 16 blocks
  +- Skip bot-placed chests (spatial_memory has "chest" category)
  +- Move to wild chest -> open_chest -> inspect contents
  +- Filter valuables (diamonds, emeralds, enchanted books, equipment, etc.)
  +- Retrieve valuable items -> save position as "looted_chest"
  +- Continue search
```

**Valuable items**: diamonds, emeralds, gold/iron ingots, enchanted books, name tags, saddles, golden apples, diamond/iron equipment, bows, arrows, shields, and more.

### Crafting Table & Furnace Carry

The bot picks up crafting tables and furnaces after use instead of leaving them in the world:

```
craft_item(iron_pickaxe) at crafting table
  |
  +- Craft succeeds
  +- Is this the last craft step in the chain?
  |    -> Yes: mine_block(crafting_table) -> carry it
  |    -> No: leave for next craft step
  |
smelt_item(raw_iron) at furnace
  |
  +- Smelt succeeds
  +- Is this the last smelt step?
  |    -> Yes: mine_block(furnace) -> carry it
```

### Skipped Task Retry

Tasks that fail 5 times are skipped temporarily, not permanently. The system retries them after other tasks complete:

```
Task "make_iron_armor" fails 5 times -> SKIPPED
  |
  +- Other tasks continue (make_shield, make_bucket, etc.)
  |
  +- All other tasks done -> RETRY skipped tasks
  |     Reset fail count to 0, get 5 more chain attempts
  |
  +- Retry 1/2: fails again -> SKIPPED again
  |
  +- Retry 2/2: final retry
  |
  +- Still failing -> permanently SKIPPED (goal completes without it)
```

This prevents one hard task from blocking all progress while still giving it multiple chances.

### Dynamic Chain Timeout

Each chain gets a timeout calculated from its steps:

| Step Type | Time Budget |
|-----------|------------|
| `mine_block` | 10s per block (includes search) |
| `smelt_item` | 12s per item |
| `dig_down` | dynamic: max(120s, depth * 4s) |
| `build_shelter` | 120s |
| `branch_mine` | 300s |
| `craft_item`, `place_block`, `equip_item` | 15s |
| Other | 30s |

Base: 120s + sum of step budgets. Clamped to 5-15 min range.

Example: `make_iron_armor` (8 iron ore + 8 coal + smelt + craft + equip) = ~7 min timeout.

### Water/Drowning Survival

The bot detects water submersion and low oxygen, escaping automatically before drowning.

```
In water + oxygen <= 12 -> escape_water()  Layer 0 instinct (highest priority after HP)
In water + oxygen < 10  -> escape_water()  Layer 1 mid-chain interrupt
turtle_helmet available -> auto-equip, lower threshold to oxygen <= 5
```

**Escape strategy (3-phase):**

| Phase | Action | When |
|-------|--------|------|
| Phase 1 | Swim up (jump) + dig blocks above if trapped | Always first |
| Phase 2 | Find nearest land (15-block radius) + pathfind | After surfacing |
| Phase 3 | Build pillar with inventory blocks | If Phase 1 fails (deep water) |

**Mid-chain water awareness**: If the bot falls into water during a chain, the chain pauses while the bot escapes, then resumes next tick.

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
| `avoid` | Don't engage -- flee if hostile within 6m |
| `flee` | flee() immediately, shelter as fallback |

**During combat**: auto-equip best weapon + shield, chase target, eat if HP < 8, flee if HP <= 4, avoid creepers, run from Wardens, shield block against ranged mobs, collect drops.

### Real-Time Attack Detection

```
bot.on('health') -> HP decreased?
  |
  +- Identify attacker (nearest hostile mob)
  +- Update combatState: attacker, damage, timestamp
  +- Track recent attacks (last 10)
  +- Auto-clear after 5 seconds of no hits
  |
GET /combat_status -> { isUnderAttack, lastAttacker, healthDelta, recentAttacks }
  |
check_instinct (every tick):
  +- Under attack? -> auto-equip weapon + shield -> fight/flee/avoid (based on rec)
  +- Ranged mob? -> shield_block first, then attack
  +- HP dropped >= 4 in one tick? -> emergency response
  +- Chain running? -> interrupt chain, let instinct handle next tick
```

**Chain interruption**: When attacked during a chain, execution pauses. Instinct handles the threat. Chain resumes automatically after combat ends.

### Combat Experience Memory

The bot remembers combat outcomes and learns from them:

```
record_combat(mob, outcome, position, damage, weapon, armor, time_of_day)
  -> experience.json (persistent, last 30 encounters)

get_combat_summary() -> "zombie: 3W/1F/0D (avg dmg: 4)"
get_dangerous_area(position) -> "DANGER ZONE: Died 2x nearby (mobs: skeleton)"
```

| Data Tracked | Purpose |
|-------------|---------|
| Mob type + outcome (won/fled/died) | Win rate per mob type |
| Damage taken + weapon used | Assess readiness |
| Position + time of day | Identify danger zones |
| Armor equipped | Correlate gear with survival |

**LLM context**: Combat summary and threat status are included in LLM planning calls for better decision-making.

### Auto-Equip Best Gear

The bot automatically equips the best available gear at key moments:

| When | What |
|------|------|
| Chain start | All slots: sword, armor (head/torso/legs/feet), shield |
| Chain complete | All slots (newly crafted gear equipped immediately) |
| Before mining | Best pickaxe (diamond > iron > stone > wooden), skip if <10% durability |
| Before combat | Best sword + shield (in instinct layer too) |

**Tier priority** (high -> low): Diamond > Iron > Chainmail > Leather. Old gear returns to inventory automatically.

### Block Placement (9-Position + Dig-Out Fallback)

```
place_block("crafting_table")
  |
  +- Phase 1: Try 9 candidate positions around bot
  |     Priority 1: feet-level horizontal (4 dirs) -- works on surface
  |     Priority 2: head-level horizontal (4 dirs) -- works in caves
  |     Priority 3: above head (1 pos) -- works in vertical shafts
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

**Why 9 positions?** After `dig_down`, bot is in a 1x1 vertical shaft -- all 4 horizontal blocks at feet level are stone. But head-level or above-head positions may have air from the shaft the bot dug through.

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

### Tunnel Navigation (tunnelMove)

Underground movement (dig_down, dig_tunnel, branch_mine) uses a specialized `tunnelMove()` helper:

```
tunnelMove(target)
  |
  +- Try pathfinder.goto(GoalNear(target, 1))
  |     5s timeout to prevent stuck pathfinder
  |
  +- Pathfinder fails? -> manual movement fallback
  |     lookAt(target) -> walk forward 800ms -> stop
  |
  +- Always use bot.entity.position.floored() for actual position
  |     (never assume arrival based on target coordinates)
```

**Why?** Underground pathfinding often fails in tight 2x1 tunnels. The manual walkforward fallback ensures progress. Using actual bot position prevents phantom position tracking bugs.

### Abort Mechanism

Long-running server actions (mining 32 blocks, digging tunnels) can outlive the Python HTTP timeout. Previously this caused pathfinder collisions and infinite retry loops.

```
Python call_tool("mine_block", count=32, timeout=256s)
  |
  +- timeout! (requests.exceptions.ReadTimeout)
  |
  +- call_tool detects "timed out" in error
  +- abort_bot_action() -> POST /abort
  |     -> server.js: abortFlag = true + pathfinder.setGoal(null)
  |     -> mine loop checks abortFlag at next iteration -> stops cleanly
  +- sleep(1.5s) for cleanup
  +- return {success: false, message: "Timeout: ...aborted"}
  |
Next API call is safe (no pathfinder conflict)
```

**Abort checks in**: `/action/mine`, `/action/dig_down`, `/action/dig_tunnel`, `/action/build_shelter`, `/action/branch_mine`

### Spatial Memory

Persisted in `waypoints.json`:

```
KNOWN LOCATIONS (8):
  [CRAFTING]
    crafting_table: (105, 64, -48) (12m)
    furnace: (107, 64, -48) (14m)
  [SHELTER]
    shelter_50: (15, 58, -7) (8m) - Enclosed shelter
    shelter_49: (13, 67, -8) (20m)
    shelter_48: (13, 69, -8) (22m)
  [STORAGE]
    chest: (15, 58, -7) (8m)
  [CAVE]
    cave_1: (120, 32, -80) (45m) - size 15
    cave_2: (85, 48, -120) (90m) - size 8
```

**Auto-saved** (Layer 1 + Layer 2): crafting table, chest, furnace, bed, shelter, caves (max 10), looted chests.

**Cave memory**: Discovered caves are saved with position + size. On next ore search, nearest unvisited cave is prioritized over blind tunneling.

---

## Log Analysis System

All bot output is automatically saved to `logs/bot_YYYYMMDD_HHMMSS.log` via TeeLogger. Use the analyzer to generate reports for Claude Code diagnosis.

### Workflow

```
1. Run bot          python agent.py              -> logs/bot_*.log (auto)
2. Analyze          python analyze_logs.py       -> report.md
3. Claude Code      Read report.md               -> diagnose issues
4. Deep dive        Read specific tick range      -> identify root cause
5. Fix              Edit code based on analysis   -> re-run bot
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
- Common issues -> fix locations table
- REST API endpoint list

---

## Grand Goals: Dynamic Goal Library

Goals are stored in `goal_library.json` (file-based, persistent). The bot ships with 3 built-in goals, but players can request custom goals via chat, and the LLM can create new ones dynamically.

### How Custom Goals Work

```
Player: "Build a big house"
  |
  +-- Claude (Chat Agent): request_custom_goal("Build a big house")
  +-- _pending_goal_request set
  |
Next tick:
  +-- Planning LLM: find_similar_goals("Build a big house")
  |     -> Found "cozy_base"? -> set_grand_goal("cozy_base")
  |     -> Not found? -> create_custom_grand_goal(name, tasks_json, ...)
  |           -> Validates chain_names -> Saves to goal_library.json
  |           -> Goal activated with user_requested=True (priority)
  |
Goal completes:
  +-- user_requested reset -> LLM auto-selects next from library
```

**Priority**: User-requested goals always take priority. Auto-selected goals won't override a user request.

### Built-in Goals (3)

#### Defeat the Ender Dragon (24 tasks, 6 phases)

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

#### Full Iron Gear (8 tasks, 2 phases)
```
get_wood -> crafting_table -> wooden_pick -> stone_pick -> iron_pick + iron_sword + iron_armor + shield
```

#### Cozy Base (6 tasks, 2 phases)
```
Gather: get_wood, mine_stone (dedicated mining, no shelter building)
Build: crafting_table + build_shelter (with door) + place_furnace + place_chest
```

### LLM-Created Goals

The planning LLM can create new goals using any combination of 17 available chains. Created goals are validated (valid chain names, no circular dependencies, no duplicate IDs) and saved to `goal_library.json` for future reuse.

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

**17 chains valid for goal tasks** (used by `create_custom_grand_goal`): `get_wood`, `mine_stone`, `make_crafting_table`, `make_wooden_pickaxe`, `make_stone_pickaxe`, `make_iron_pickaxe`, `make_iron_sword`, `make_iron_armor`, `make_shield`, `make_bucket`, `mine_diamonds`, `make_diamond_pickaxe`, `make_diamond_sword`, `find_food`, `build_shelter`, `place_furnace`, `place_chest`

---

## Persistence (Survives Restarts)

| Data | File | Survives restart? |
|------|------|:-:|
| Grand goal progress | `grand_goal_state.json` | Yes |
| Goal library (built-in + custom goals) | `goal_library.json` | Yes |
| Death lessons | `death_lessons.json` | Yes |
| Saved locations (shelters, caves, etc.) | `waypoints.json` | Yes |
| Experience memory (search + errors + combat) | `experience.json` | Yes |
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
MAX_ITERATIONS=20
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
+-- chain_executor.py      # Layer 0+1 execution engine + auto-fix + cave search + chest loot
+-- experience_memory.py   # Remember what worked (search, errors, combat encounters)
|
+-- tools.py               # 29 LangChain tools (for LLM Layer 2 only)
|
+-- grand_goal.py          # Grand goal dependency graph + GoalLibrary (file-based)
+-- grand_goal_tools.py    # LangChain tools for goal management (legacy)
+-- goal_planner.py        # Task priority + step state tracking
|
+-- death_analyzer.py      # Death snapshot capture + lesson extraction
+-- death_tools.py         # LangChain tools for death learning
|
+-- spatial_memory.py      # Named waypoint storage (shelters, caves, looted chests)
+-- memory_tools.py        # LangChain tools for location memory
|
+-- analyze_logs.py       # Log analyzer -> report.md
+-- CLAUDE.md             # Claude Code auto-context (project guide)
|
+-- grand_goal_state.json  # [Auto] Saved goal progress
+-- goal_library.json      # [Auto] Goal library (3 built-in + custom goals)
+-- death_lessons.json     # [Auto] Persistent death lessons
+-- waypoints.json         # [Auto] Persistent saved locations (shelters, caves, chests)
+-- experience.json        # [Auto] Persistent experience data
+-- report.md              # [Auto] Latest analysis report
+-- logs/                  # [Auto] Bot execution logs
|
+-- .env                   # Configuration
+-- package.json           # Node.js dependencies
+-- README.md
```

---

## Performance: v3 vs v7.0

| Metric | v3 (LLM every tick) | v7.0 (Dual LLM + Chain) |
|--------|--------------------|--------------------|
| LLM calls per minute | ~12 | ~0.3 |
| Time per action | 5-15s (LLM thinking) | 1-2s (direct API) |
| Actions per minute | ~4-6 | ~20-30 |
| Iron pickaxe time | ~15-30 min | ~3-5 min |
| Memory between ticks | None | Chain state + history |
| Resource search | LLM guesses | Cave-first + 3-phase systematic (19+ attempts) |
| Error recovery | LLM every time | Experience-first, LLM fallback |
| Same error twice | LLM again | Auto-handled from experience |
| Player chat | Same slow LLM | Claude API (fast, natural) |
| Failed tasks | Stuck forever | Skip -> retry later (up to 2 retries) |
| Gear management | Manual | Auto-equip best gear at key moments |
| Combat response | None | Real-time attack detect -> fight/flee/avoid + shield |
| Combat memory | None | Record outcomes, track danger zones |
| Dungeon loot | None | Auto-detect and loot wild chests |
| Cave exploration | None | Scan + remember + revisit caves |

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
- [x] Smart combat AI + shield blocking
- [x] Threat assessment engine
- [x] Furnace smelting
- [x] Emergency shelter (sealed) + surface shelter (with door)
- [x] Directional mining (staircase, tunnel, branch mine)
- [x] Death analysis + lesson learning
- [x] Spatial memory (shelters, caves, chests)
- [x] Structure scan + rebuild
- [x] **9-position block placement + dig-out fallback (underground fix)**
- [x] **Auto-save locations from Layer 1 (crafting table, furnace, shelter, cave)**
- [x] **LLM solution capture -> experience memory (learning loop)**
- [x] **Smart pathfinding (auto-mine obstacles)**
- [x] **Dynamic timeout scaling per chain complexity**
- [x] **Claude API for player conversation (dual LLM)**
- [x] **Bot state in LLM context (HP, food, position, time)**
- [x] **Dedicated chains: mine_stone, place_furnace, place_chest**
- [x] **Auto-equip best gear (armor, weapon, shield) + durability check**
- [x] **Skipped task retry system (up to 2 retries after other tasks complete)**
- [x] **3-phase resource search (static -> persistent -> LLM escalation)**
- [x] **Persistent search mode (8 dynamic explore/dig attempts before LLM)**
- [x] **TeeLogger -- auto-save all output to logs/bot_*.log**
- [x] **Log analyzer (analyze_logs.py) -- chain stats, error patterns, stuck loops, recommendations**
- [x] **CLAUDE.md -- Claude Code auto-context for project analysis**
- [x] **Water/Drowning survival -- oxygen monitoring, 3-phase escape, turtle helmet support**
- [x] **Combat response system -- real-time attack detection, fight/flee/avoid, chain interruption**
- [x] **Combat experience memory -- record outcomes, danger zones, LLM context enrichment**
- [x] **Flee action -- sprint away from threats (separate from shelter)**
- [x] **Real death message capture -- actual Minecraft death messages instead of hardcoded**
- [x] **Dynamic Grand Goal system -- file-based GoalLibrary, user chat -> custom goals**
- [x] **Goal priority system -- user-requested goals override auto-selected goals**
- [x] **Abort mechanism -- Python timeout -> POST /abort -> server stops long-running loops**
- [x] **Lava detection + water bucket neutralization (preemptive scan)**
- [x] **Stuck detection -- 3-tick position tracking, dig_down/explore to unstick**
- [x] **Inventory management -- emptySlots check, auto store_items to chest**
- [x] **Tool durability tracking -- <10% auto-switch to next tier**
- [x] **Cave detection + cave-first search strategy -- scan/remember/revisit caves**
- [x] **Cave spatial memory -- save discovered caves, sort by distance, max 10**
- [x] **Drop collection -- entityDead event, collect_drops instinct**
- [x] **Chest looting -- auto-detect dungeon/ruin chests, loot valuables**
- [x] **Crafting table/furnace carry -- pick up after use, no waste**
- [x] **Unreachable block skip -- failedPositions tracking in mine endpoint**
- [x] **tunnelMove helper -- reliable underground navigation with fallback**
- [ ] Nether navigation + portal building
- [ ] Enchanting workflow
- [ ] Villager trading

---

**Author**: Jun
**Created**: 2026-02-13
**Version**: v7.0 -- Cave Intelligence + Chest Looting
