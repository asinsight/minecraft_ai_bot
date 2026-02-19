const mineflayer = require('mineflayer')
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')
const express = require('express')
require('dotenv').config()

// ============================================
// CONFIG (from .env)
// ============================================
const BOT_CONFIG = {
  host: process.env.BOT_HOST || 'localhost',
  port: parseInt(process.env.BOT_PORT) || 57823,
  username: process.env.BOT_USERNAME || 'AIBot',
  version: process.env.BOT_VERSION || '1.21.4',
  auth: process.env.BOT_AUTH || 'offline',
  disableChatSigning: true
}

const API_PORT = parseInt(process.env.API_PORT) || 3001

// ============================================
// CREATE BOT
// ============================================
const bot = mineflayer.createBot(BOT_CONFIG)
bot.loadPlugin(pathfinder)

let botReady = false
let lastChatMessages = []
let lastReadChatIndex = 0  // Track which messages agent has seen

// ── Abort Flag (Python timeout → cancel long-running loops) ──
let abortFlag = false

// ── Death Tracking ──
let deathLog = []
let lastHealthSnapshot = { health: 20, food: 20, position: null, entities: [], time: null }
let lastDeathMessage = ''  // Capture actual Minecraft death message

// ── Combat Tracking ──
let combatState = {
  isUnderAttack: false,      // Currently being hit
  lastHitTime: 0,            // timestamp of last damage taken
  lastAttacker: null,        // { type, distance, position }
  healthBefore: 20,          // health before last hit
  healthDelta: 0,            // damage taken in last hit
  recentAttacks: [],         // last 10 attacks [{type, damage, time, position}]
  combatStartTime: 0,        // when combat began (0 = not in combat)
}

// ── Drop Collection Tracking ──
let pendingDrops = []

// ── Lava Scan Helper ──
function scanForLava(pos, radius = 3) {
  if (!botReady) return []
  const lavaBlocks = bot.findBlocks({
    matching: b => b.name === 'lava' || b.name === 'flowing_lava',
    maxDistance: radius,
    count: 20,
    point: pos
  })
  return lavaBlocks.map(p => ({
    position: { x: p.x, y: p.y, z: p.z },
    distance: pos.distanceTo(p)
  }))
}

// ── Lava Safety: try to use water bucket to neutralize lava ──
async function tryWaterBucketOnLava(lavaPos) {
  const waterBucket = bot.inventory.items().find(i => i.name === 'water_bucket')
  if (!waterBucket) return false
  try {
    await bot.equip(waterBucket, 'hand')
    const lavaBlock = bot.blockAt(lavaPos)
    if (lavaBlock) {
      await bot.activateBlock(lavaBlock)
      await new Promise(r => setTimeout(r, 500))
      // Pick water back up (it should be a water source now turned to obsidian)
      const bucket = bot.inventory.items().find(i => i.name === 'bucket')
      if (bucket) {
        await bot.equip(bucket, 'hand')
        const waterBlock = bot.blockAt(lavaPos)
        if (waterBlock && (waterBlock.name === 'water' || waterBlock.name === 'flowing_water')) {
          await bot.activateBlock(waterBlock)
        }
      }
      console.log(`[lava-safety] Neutralized lava at ${lavaPos.x}, ${lavaPos.y}, ${lavaPos.z}`)
      return true
    }
  } catch (e) {
    console.log(`[lava-safety] Failed to neutralize lava: ${e.message}`)
  }
  return false
}

// ============================================
// EXPRESS API SERVER
// ============================================
const app = express()
app.use(express.json())

// ── ABORT ENDPOINT ──
app.post('/abort', (req, res) => {
  console.log('[abort] Abort requested by Python agent')
  abortFlag = true
  try { bot.pathfinder.setGoal(null) } catch (e) {}
  res.json({ success: true, message: 'Abort flag set' })
})

// Clear stale abort flag on new action requests (prevents race condition
// where a previous timeout's abort flag blocks the next request)
app.use('/action', (req, res, next) => {
  if (abortFlag) {
    console.log('[abort] Clearing stale abort flag before new action')
    abortFlag = false
  }
  next()
})

// ── STATE ENDPOINTS ──

// ── Auto-equip best tool helper (used by mine, dig_down, dig_tunnel, dig_shelter) ──
async function autoEquipBestTool(blockName) {
  if (!blockName) blockName = 'stone'

  const pickaxeBlocks = ['stone', 'cobblestone', 'iron_ore', 'coal_ore', 'gold_ore',
    'diamond_ore', 'copper_ore', 'redstone_ore', 'lapis_ore', 'emerald_ore',
    'deepslate', 'andesite', 'diorite', 'granite', 'netherrack', 'obsidian',
    'nether_quartz_ore', 'nether_gold_ore', 'sandstone', 'blackstone', 'basalt',
    'end_stone', 'terracotta', 'bricks', 'prismarine', 'concrete']
  const axeBlocks = ['log', 'wood', 'planks', 'fence', 'door', 'bookshelf',
    'crafting_table', 'chest', 'barrel', 'sign', 'bamboo', 'mushroom_block']
  const shovelBlocks = ['dirt', 'grass_block', 'sand', 'gravel', 'clay',
    'soul_sand', 'soul_soil', 'snow', 'mud', 'mycelium', 'podzol', 'farmland']

  let toolType = null
  for (const kw of pickaxeBlocks) {
    if (blockName.includes(kw)) { toolType = 'pickaxe'; break }
  }
  if (!toolType) {
    for (const kw of axeBlocks) {
      if (blockName.includes(kw)) { toolType = 'axe'; break }
    }
  }
  if (!toolType) {
    for (const kw of shovelBlocks) {
      if (blockName.includes(kw)) { toolType = 'shovel'; break }
    }
  }

  if (!toolType) toolType = 'pickaxe'

  // Search inventory for best tool of this type
  const tiers = ['netherite', 'diamond', 'iron', 'stone', 'wooden', 'golden']
  const available = bot.inventory.items().map(i => i.name)
  console.log(`[auto-equip] Block: "${blockName}" → need: ${toolType} | inventory: ${available.filter(n => n.includes(toolType) || n.includes('sword') || n.includes('axe') || n.includes('pickaxe') || n.includes('shovel')).join(', ') || 'no tools'}`)

  for (const tier of tiers) {
    const toolName = `${tier}_${toolType}`
    const item = bot.inventory.items().find(i => i.name === toolName)
    if (item) {
      await bot.equip(item, 'hand')
      console.log(`[auto-equip] Equipped ${toolName}`)
      return toolName
    }
  }
  console.log(`[auto-equip] No ${toolType} found! Using fist.`)
  return null
}

// GET /state - Full world state
app.get('/state', (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })

  const pos = bot.entity.position
  const nearbyBlocks = bot.findBlocks({
    matching: (block) => block.name !== 'air',
    maxDistance: 16,
    count: 50
  })
  const blockNames = [...new Set(nearbyBlocks.map(p => bot.blockAt(p)?.name).filter(Boolean))]

  const nearbyEntities = Object.values(bot.entities)
    .filter(e => e !== bot.entity && e.position.distanceTo(pos) < 30)
    .map(e => ({
      type: e.name || e.username || 'unknown',
      distance: parseFloat(e.position.distanceTo(pos).toFixed(1)),
      position: { x: e.position.x.toFixed(1), y: e.position.y.toFixed(1), z: e.position.z.toFixed(1) }
    }))
    .sort((a, b) => a.distance - b.distance)

  const inventory = bot.inventory.items().map(item => ({
    name: item.name,
    count: item.count,
    durability: item.maxDurability ? {
      current: item.maxDurability - (item.nbt?.value?.Damage?.value || 0),
      max: item.maxDurability,
      percent: Math.round(((item.maxDurability - (item.nbt?.value?.Damage?.value || 0)) / item.maxDurability) * 100)
    } : null
  }))
  const emptySlots = 36 - inventory.length

  // Time phases: 0-1000 sunrise, 1000-6000 morning, 6000-12000 afternoon,
  // 12000-13000 dusk, 13000-18000 night, 18000-23000 midnight, 23000-24000 dawn
  const t = bot.time.timeOfDay
  let timePhase = 'day'
  if (t >= 23000 || t < 1000) timePhase = 'dawn'
  else if (t < 6000) timePhase = 'morning'
  else if (t < 11000) timePhase = 'afternoon'
  else if (t < 13000) timePhase = 'dusk'
  else if (t < 18000) timePhase = 'night'
  else timePhase = 'midnight'

  // ── Environment detection ──
  const headPos = pos.offset(0, 1, 0)
  const blockLight = bot.blockAt(headPos)?.light ?? 0
  const skyLightVal = bot.blockAt(headPos)?.skyLight ?? 0

  // Check sky visibility: scan upward for solid blocks
  let canSeeSky = true
  let roofHeight = 0
  for (let dy = 2; dy <= 64 && pos.y + dy <= 320; dy++) {
    const above = bot.blockAt(pos.offset(0, dy, 0))
    if (above && above.name !== 'air' && above.name !== 'cave_air'
        && above.name !== 'void_air' && !above.name.includes('leaves')
        && !above.name.includes('glass')) {
      canSeeSky = false
      roofHeight = dy
      break
    }
  }

  // Determine environment type
  const y = Math.floor(pos.y)
  let environment = 'surface'
  if (!canSeeSky && y < 0) {
    environment = 'deep_underground'  // deepslate layer
  } else if (!canSeeSky && y < 50) {
    environment = 'underground'  // cave or mine
  } else if (!canSeeSky && y >= 50) {
    environment = 'indoors'  // under a roof but near surface level
  }

  const isDark = blockLight < 8

  // ── Water detection ──
  const isInWater = bot.entity.isInWater || false
  const oxygenLevel = bot.oxygenLevel ?? 20  // 0-20, 20 = full air
  const eyePos = pos.offset(0, 1.62, 0)
  const eyeBlock = bot.blockAt(eyePos.floored())
  const isUnderwater = isInWater && eyeBlock &&
    (eyeBlock.name === 'water' || eyeBlock.name === 'flowing_water')

  res.json({
    position: { x: pos.x.toFixed(1), y: pos.y.toFixed(1), z: pos.z.toFixed(1) },
    health: bot.health,
    food: bot.food,
    time: timePhase,
    timeOfDay: t,
    isSafeOutside: t < 12000 || t >= 23000,
    environment,
    canSeeSky,
    lightLevel: blockLight,
    isDark,
    roofHeight: canSeeSky ? null : roofHeight,
    isRaining: bot.isRaining,
    isInWater,
    oxygenLevel,
    isUnderwater,
    combat: {
      isUnderAttack: combatState.isUnderAttack && (Date.now() - combatState.lastHitTime < 5000),
      lastAttacker: combatState.lastAttacker,
      healthDelta: combatState.healthDelta,
      lastHitTime: combatState.lastHitTime,
      timeSinceHit: combatState.lastHitTime ? Math.floor((Date.now() - combatState.lastHitTime) / 1000) : null,
    },
    inventory,
    emptySlots,
    nearbyBlocks: blockNames,
    nearbyEntities,
    recentChat: lastChatMessages.slice(-10)
  })
})

// GET /combat_status - Detailed combat state for agent decision-making
app.get('/combat_status', (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })

  // Refresh isUnderAttack based on time
  const now = Date.now()
  if (combatState.isUnderAttack && now - combatState.lastHitTime > 5000) {
    combatState.isUnderAttack = false
    combatState.combatStartTime = 0
    combatState.lastAttacker = null
  }

  res.json({
    isUnderAttack: combatState.isUnderAttack,
    lastAttacker: combatState.lastAttacker,
    healthDelta: combatState.healthDelta,
    lastHitTime: combatState.lastHitTime,
    timeSinceHit: combatState.lastHitTime ? Math.floor((now - combatState.lastHitTime) / 1000) : null,
    combatDuration: combatState.combatStartTime ? Math.floor((now - combatState.combatStartTime) / 1000) : 0,
    recentAttacks: combatState.recentAttacks,
    health: bot.health,
    food: bot.food,
  })
})

// GET /inventory
app.get('/inventory', (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  const items = bot.inventory.items().map(item => ({
    name: item.name,
    count: item.count,
    slot: item.slot
  }))
  res.json({ items })
})

// GET /surrounding_blocks - Check blocks immediately around bot (for stuck detection)
app.get('/surrounding_blocks', (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  const pos = bot.entity.position.floored()
  // Check 4 horizontal directions at feet and head level
  const directions = [
    { name: 'north', dx: 0, dz: -1 },
    { name: 'south', dx: 0, dz: 1 },
    { name: 'east', dx: 1, dz: 0 },
    { name: 'west', dx: -1, dz: 0 }
  ]
  const passable = (block) => !block || block.name === 'air' || block.boundingBox !== 'block'
  const result = {}
  for (const dir of directions) {
    const feet = bot.blockAt(pos.offset(dir.dx, 0, dir.dz))
    const head = bot.blockAt(pos.offset(dir.dx, 1, dir.dz))
    const feetPassable = passable(feet)
    const headPassable = passable(head)
    result[dir.name] = {
      feet: { name: feet?.name || 'air', passable: feetPassable },
      head: { name: head?.name || 'air', passable: headPassable },
      open: feetPassable && headPassable,
      x: pos.x + dir.dx,
      z: pos.z + dir.dz
    }
  }
  // Also check above and below
  const above = bot.blockAt(pos.offset(0, 2, 0))
  const below = bot.blockAt(pos.offset(0, -1, 0))
  result.above = { name: above?.name || 'air', passable: passable(above) }
  result.below = { name: below?.name || 'air', passable: passable(below) }
  result.position = { x: pos.x, y: pos.y, z: pos.z }
  res.json(result)
})

// GET /nearby
app.get('/nearby', (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })

  const range = parseInt(req.query.range) || 16
  const pos = bot.entity.position

  const blocks = bot.findBlocks({
    matching: (block) => block.name !== 'air',
    maxDistance: range,
    count: 100
  })
  const blockCounts = {}
  blocks.forEach(p => {
    const name = bot.blockAt(p)?.name
    if (name) blockCounts[name] = (blockCounts[name] || 0) + 1
  })

  const entities = Object.values(bot.entities)
    .filter(e => e !== bot.entity && e.position.distanceTo(pos) < range)
    .map(e => ({
      type: e.name || e.username || 'unknown',
      distance: parseFloat(e.position.distanceTo(pos).toFixed(1)),
      position: { x: e.position.x.toFixed(1), y: e.position.y.toFixed(1), z: e.position.z.toFixed(1) }
    }))
    .sort((a, b) => a.distance - b.distance)

  res.json({ blocks: blockCounts, entities })
})

// GET /chat
app.get('/chat', (req, res) => {
  res.json({ messages: lastChatMessages.slice(-20) })
})

// GET /chat/unread - Get new chat messages since last check (for agent priority)
app.get('/chat/unread', (req, res) => {
  const unread = lastChatMessages.slice(lastReadChatIndex)
  lastReadChatIndex = lastChatMessages.length
  // Filter out bot's own messages
  const playerMessages = unread.filter(m => m.username !== bot.username)
  res.json({ messages: playerMessages, count: playerMessages.length })
})

// GET /threat_assessment - Evaluate combat readiness vs nearby threats
app.get('/threat_assessment', (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })

  const pos = bot.entity.position
  const health = bot.health
  const food = bot.food

  // ── Inventory analysis ──
  const items = bot.inventory.items()

  const weaponTiers = {
    'diamond_sword': 7, 'iron_sword': 6, 'stone_sword': 5, 'wooden_sword': 4,
    'diamond_axe': 6, 'iron_axe': 5, 'stone_axe': 4, 'wooden_axe': 3,
  }
  const armorValues = {
    'diamond_helmet': 3, 'diamond_chestplate': 8, 'diamond_leggings': 6, 'diamond_boots': 3,
    'iron_helmet': 2, 'iron_chestplate': 6, 'iron_leggings': 5, 'iron_boots': 2,
    'chainmail_helmet': 2, 'chainmail_chestplate': 5, 'chainmail_leggings': 4, 'chainmail_boots': 1,
    'leather_helmet': 1, 'leather_chestplate': 3, 'leather_leggings': 2, 'leather_boots': 1,
  }

  // Best weapon
  let bestWeapon = null
  let weaponPower = 0
  for (const item of items) {
    if (weaponTiers[item.name] && weaponTiers[item.name] > weaponPower) {
      bestWeapon = item.name
      weaponPower = weaponTiers[item.name]
    }
  }

  // Total armor
  let totalArmor = 0
  const armorSlots = [bot.inventory.slots[5], bot.inventory.slots[6], bot.inventory.slots[7], bot.inventory.slots[8]]
  for (const slot of armorSlots) {
    if (slot && armorValues[slot.name]) {
      totalArmor += armorValues[slot.name]
    }
  }

  // Shield
  const hasShield = items.some(i => i.name === 'shield')

  // Food count
  const foods = ['cooked_beef', 'cooked_porkchop', 'cooked_chicken', 'cooked_mutton',
    'bread', 'golden_apple', 'apple', 'melon_slice', 'baked_potato',
    'beef', 'porkchop', 'chicken', 'mutton', 'potato', 'carrot',
    'sweet_berries', 'dried_kelp']
  let foodCount = 0
  for (const item of items) {
    if (foods.includes(item.name)) foodCount += item.count
  }

  // ── Threat analysis ──
  const hostileMobs = {
    'zombie': { danger: 2, drops: true },
    'husk': { danger: 2, drops: true },
    'skeleton': { danger: 3, drops: true },
    'stray': { danger: 3, drops: true },
    'spider': { danger: 2, drops: true },
    'creeper': { danger: 5, drops: false },
    'enderman': { danger: 4, drops: true },
    'witch': { danger: 4, drops: true },
    'drowned': { danger: 2, drops: true },
    'phantom': { danger: 3, drops: false },
    'pillager': { danger: 3, drops: true },
    'vindicator': { danger: 5, drops: true },
    'ravager': { danger: 6, drops: false },
    'warden': { danger: 10, drops: false },
  }

  const nearby = Object.values(bot.entities)
    .filter(e => e !== bot.entity && e.position.distanceTo(pos) < 20)
    .map(e => ({
      type: e.name || 'unknown',
      distance: parseFloat(e.position.distanceTo(pos).toFixed(1)),
    }))
    .sort((a, b) => a.distance - b.distance)

  const threats = nearby.filter(e => hostileMobs[e.type])
  let totalDanger = 0
  const threatDetails = threats.map(t => {
    const mob = hostileMobs[t.type]
    const dangerScaled = mob.danger * (1 + Math.max(0, (10 - t.distance)) / 10) // closer = more dangerous
    totalDanger += dangerScaled
    return { type: t.type, distance: t.distance, danger: mob.danger }
  })

  // ── Combat score ──
  // Player power = weapon + armor + health + food buffer
  const playerPower = weaponPower + (totalArmor * 0.5) + (health * 0.3) + (foodCount > 0 ? 2 : 0)

  // ── Decision ──
  let recommendation = 'safe'
  let reason = ''

  if (threats.length === 0) {
    recommendation = 'safe'
    reason = 'No threats nearby.'
  } else if (totalDanger >= 8 && playerPower < 5) {
    recommendation = 'flee'
    reason = `High danger (${totalDanger.toFixed(1)}) vs low power (${playerPower.toFixed(1)}). Multiple/strong threats without gear.`
  } else if (threats.some(t => t.type === 'creeper' && t.distance < 8)) {
    recommendation = 'flee'
    reason = 'Creeper nearby! Risk of explosion. Back away and use ranged or wait for it to pass.'
  } else if (threats.some(t => t.type === 'warden')) {
    recommendation = 'flee'
    reason = 'WARDEN detected! Extremely dangerous. Sneak away immediately.'
  } else if (health <= 6 && foodCount === 0) {
    recommendation = 'flee'
    reason = `Low health (${health}/20) and no food. Cannot sustain combat.`
  } else if (weaponPower === 0 && totalDanger > 3) {
    recommendation = 'avoid'
    reason = `No weapon against ${threats.length} hostile(s). Craft a sword first.`
  } else if (playerPower > totalDanger * 1.5) {
    recommendation = 'fight'
    reason = `Strong advantage (power: ${playerPower.toFixed(1)} vs danger: ${totalDanger.toFixed(1)}). Should be safe to engage.`
  } else if (playerPower > totalDanger) {
    recommendation = 'fight_careful'
    reason = `Slight advantage (power: ${playerPower.toFixed(1)} vs danger: ${totalDanger.toFixed(1)}). Watch health and eat if needed.`
  } else {
    recommendation = 'avoid'
    reason = `Outmatched (power: ${playerPower.toFixed(1)} vs danger: ${totalDanger.toFixed(1)}). Gear up first or reduce threat count.`
  }

  const isNight = bot.time.timeOfDay > 13000

  res.json({
    recommendation,  // 'safe', 'fight', 'fight_careful', 'avoid', 'flee'
    reason,
    combat_readiness: {
      weapon: bestWeapon || 'none (fist)',
      weapon_power: weaponPower,
      armor_points: totalArmor,
      shield: hasShield,
      health,
      food: bot.food,
      food_items: foodCount,
      player_power: parseFloat(playerPower.toFixed(1)),
    },
    threats: {
      count: threats.length,
      total_danger: parseFloat(totalDanger.toFixed(1)),
      details: threatDetails,
      is_night: isNight,
    },
  })
})
app.get('/find_block', (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })

  const blockType = req.query.type
  const maxDist = parseInt(req.query.range) || 64

  // Exact match + deepslate variant for ores (e.g., iron_ore → also matches deepslate_iron_ore)
  const block = bot.findBlock({
    matching: b => b.name === blockType || b.name === 'deepslate_' + blockType,
    maxDistance: maxDist
  })

  if (block) {
    res.json({
      success: true,
      message: `Found ${block.name} at (${block.position.x}, ${block.position.y}, ${block.position.z})`,
      block: { name: block.name, position: { x: block.position.x, y: block.position.y, z: block.position.z } }
    })
  } else {
    res.json({ success: false, message: `No ${blockType} found within ${maxDist} blocks` })
  }
})

// GET /search_item - Search for item/block names by keyword
app.get('/search_item', (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })

  const query = (req.query.q || '').toLowerCase()
  if (!query) return res.json({ error: 'Provide ?q=keyword' })

  const mcData = require('minecraft-data')(bot.version)

  // Search items
  const items = Object.values(mcData.itemsByName)
    .filter(i => i.name.includes(query) || (i.displayName || '').toLowerCase().includes(query))
    .map(i => ({ name: i.name, displayName: i.displayName, id: i.id, type: 'item' }))
    .slice(0, 20)

  // Search blocks
  const blocks = Object.values(mcData.blocksByName)
    .filter(b => b.name.includes(query) || (b.displayName || '').toLowerCase().includes(query))
    .map(b => ({ name: b.name, displayName: b.displayName, id: b.id, type: 'block' }))
    .slice(0, 20)

  res.json({
    query,
    results: [...items, ...blocks],
    total: items.length + blocks.length,
    tip: 'Use the "name" field (e.g., "oak_log", "wooden_pickaxe") when calling tools.'
  })
})

// GET /death_log - Recent deaths with pre-death snapshots
app.get('/death_log', (req, res) => {
  const count = parseInt(req.query.count) || 10
  res.json({
    total_deaths: deathLog.length,
    deaths: deathLog.slice(-count)
  })
})

// POST /death_log/clear
app.post('/death_log/clear', (req, res) => {
  const cleared = deathLog.length
  deathLog = []
  res.json({ success: true, message: `Cleared ${cleared} death records` })
})

// ── ACTION ENDPOINTS ──

// POST /action/move
app.post('/action/move', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const { x, y, z, range } = req.body
    const target = new Vec3(x, y, z)
    const dist = bot.entity.position.distanceTo(target)
    // Dynamic timeout: 2 seconds per block, min 15s, max 120s
    const timeoutMs = Math.max(15000, Math.min(120000, dist * 2000))

    // Helper: try mining the block in front of the bot (toward target) to clear path
    async function tryMineObstacle() {
      const pos = bot.entity.position
      const dir = target.minus(pos)
      const len = Math.sqrt(dir.x * dir.x + dir.z * dir.z) || 1
      // Normalized direction (only X/Z, rounded to nearest block)
      const dx = Math.round(dir.x / len)
      const dz = Math.round(dir.z / len)
      // Try mining at eye level and foot level in front of bot
      for (const dy of [0, 1]) {
        const blockPos = pos.floored().offset(dx, dy, dz)
        const block = bot.blockAt(blockPos)
        if (block && block.name !== 'air' && block.name !== 'cave_air' && block.name !== 'water'
            && block.boundingBox === 'block' && block.diggable) {
          try {
            await bot.dig(block)
            return true
          } catch (e) { /* can't dig */ }
        }
      }
      return false
    }

    let timedOut = false
    const timer = setTimeout(() => {
      timedOut = true
      bot.pathfinder.setGoal(null)
    }, timeoutMs)

    try {
      await bot.pathfinder.goto(new goals.GoalNear(x, y, z, range || 2))
      clearTimeout(timer)
      if (timedOut) {
        // First attempt failed — try mining obstacle and retry once
        const mined = await tryMineObstacle()
        if (mined) {
          let retry2TimedOut = false
          const retryTimer = setTimeout(() => {
            retry2TimedOut = true
            bot.pathfinder.setGoal(null)
          }, Math.min(timeoutMs, 30000))
          try {
            await bot.pathfinder.goto(new goals.GoalNear(x, y, z, range || 2))
            clearTimeout(retryTimer)
            if (!retry2TimedOut) {
              return res.json({ success: true, message: `Moved to ${x}, ${y}, ${z} (mined obstacle)` })
            }
          } catch (e) { clearTimeout(retryTimer) }
        }
        const finalDist = bot.entity.position.distanceTo(target).toFixed(1)
        res.json({ success: false, message: `Movement blocked. ${finalDist} blocks away from target (${x}, ${y}, ${z}). Mined obstacle: ${mined ? 'yes' : 'no'}.` })
      } else {
        res.json({ success: true, message: `Moved to ${x}, ${y}, ${z}` })
      }
    } catch (pathErr) {
      clearTimeout(timer)
      // Try mining obstacle before giving up
      const mined = await tryMineObstacle()
      if (mined) {
        try {
          const retryTimer = setTimeout(() => { bot.pathfinder.setGoal(null) }, Math.min(timeoutMs, 30000))
          await bot.pathfinder.goto(new goals.GoalNear(x, y, z, range || 2))
          clearTimeout(retryTimer)
          return res.json({ success: true, message: `Moved to ${x}, ${y}, ${z} (mined obstacle)` })
        } catch (e) { /* still failed */ }
      }
      const finalDist = bot.entity.position.distanceTo(target).toFixed(1)
      res.json({ success: false, message: `Movement blocked. ${finalDist} blocks away from target (${x}, ${y}, ${z}). Mined obstacle: ${mined ? 'yes' : 'no'}.` })
    }
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// POST /action/move_to_player
app.post('/action/move_to_player', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const { player_name } = req.body
    const target = player_name
      ? bot.players[player_name]?.entity
      : Object.values(bot.players).find(p => p.entity && p.username !== bot.username)?.entity

    if (!target) return res.json({ success: false, message: `Player ${player_name || 'any'} not found nearby` })

    const timeout = setTimeout(() => { bot.pathfinder.setGoal(null) }, 30000)
    await bot.pathfinder.goto(new goals.GoalNear(target.position.x, target.position.y, target.position.z, 2))
    clearTimeout(timeout)
    res.json({ success: true, message: `Moved to player ${player_name || target.username}` })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// POST /action/follow
app.post('/action/follow', (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  const { player_name } = req.body
  const target = player_name
    ? bot.players[player_name]?.entity
    : Object.values(bot.players).find(p => p.entity && p.username !== bot.username)?.entity

  if (!target) return res.json({ success: false, message: 'Player not found' })

  bot.pathfinder.setGoal(new goals.GoalFollow(target, 3), true)
  res.json({ success: true, message: `Following ${player_name || 'player'}` })
})

// POST /action/stop
app.post('/action/stop', (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  bot.pathfinder.setGoal(null)
  res.json({ success: true, message: 'Stopped moving' })
})

// POST /action/explore
app.post('/action/explore', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const { distance } = req.body
    const dist = distance || 20
    const x = bot.entity.position.x + (Math.random() - 0.5) * dist * 2
    const z = bot.entity.position.z + (Math.random() - 0.5) * dist * 2
    const y = bot.entity.position.y

    const timeout = setTimeout(() => { bot.pathfinder.setGoal(null) }, 30000)
    bot.pathfinder.setGoal(new goals.GoalNear(x, y, z, 3))
    clearTimeout(timeout)
    res.json({ success: true, message: `Exploring towards x=${x.toFixed(0)}, z=${z.toFixed(0)}` })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// POST /action/mine
app.post('/action/mine', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const { block_type, count } = req.body
    const mineCount = count || 1
    let mined = 0

    // Auto-equip best tool for the job
    let equippedTool = await autoEquipBestTool(block_type)

    // Fallback: if autoEquipBestTool found nothing, check if bot is already holding a valid tool
    // (Python-side _auto_equip_for_mining may have equipped it via /action/equip before this call)
    if (!equippedTool && bot.heldItem) {
      const held = bot.heldItem.name
      if (held.includes('pickaxe') || held.includes('axe') || held.includes('shovel')) {
        console.log(`[auto-equip] Fallback: bot already holding ${held}`)
        equippedTool = held
      }
    }

    // ── Tool requirement checks ──
    const requiresPickaxe = ['iron_ore', 'coal_ore', 'gold_ore', 'diamond_ore', 'copper_ore',
      'redstone_ore', 'lapis_ore', 'emerald_ore', 'nether_quartz_ore', 'nether_gold_ore',
      'obsidian', 'deepslate_iron_ore', 'deepslate_coal_ore', 'deepslate_gold_ore',
      'deepslate_diamond_ore', 'deepslate_copper_ore', 'deepslate_redstone_ore',
      'deepslate_lapis_ore', 'deepslate_emerald_ore']
    const requiresTool = ['stone', 'cobblestone', 'deepslate', ...requiresPickaxe]

    const needsTool = requiresTool.some(kw => block_type.includes(kw))
    if (needsTool && !equippedTool) {
      return res.json({
        success: false,
        message: `Cannot mine ${block_type} without a tool! Craft a pickaxe first.`
      })
    }
    const needsStonePlus = requiresPickaxe.some(kw => block_type.includes(kw))
    if (needsStonePlus && equippedTool && equippedTool.startsWith('wooden_')) {
      return res.json({ success: false, message: `${block_type} needs stone_pickaxe or better!` })
    }
    const needsIronPlus = ['diamond_ore', 'deepslate_diamond_ore', 'gold_ore', 'deepslate_gold_ore',
      'emerald_ore', 'deepslate_emerald_ore', 'redstone_ore', 'deepslate_redstone_ore']
    if (needsIronPlus.some(kw => block_type.includes(kw)) && equippedTool &&
        (equippedTool.startsWith('wooden_') || equippedTool.startsWith('stone_'))) {
      return res.json({ success: false, message: `${block_type} needs iron_pickaxe or better!` })
    }

    const isWood = ['log', 'wood'].some(kw => block_type.includes(kw))

    // ── Helper: force-equip the mining tool and verify it's held ──
    const ensureToolHeld = async () => {
      if (!equippedTool) return
      const held = bot.heldItem?.name
      if (held === equippedTool) return  // already correct
      // Try to equip
      const toolItem = bot.inventory.items().find(i => i.name === equippedTool)
      if (!toolItem) {
        // Tool broke or lost — find next best
        equippedTool = await autoEquipBestTool(block_type)
        await new Promise(r => setTimeout(r, 150))
        return
      }
      try {
        await bot.equip(toolItem, 'hand')
        await new Promise(r => setTimeout(r, 150))  // wait for server to process
        console.log(`[mine] Re-equipped ${equippedTool} (was holding: ${held || 'nothing'})`)
      } catch (e) {
        console.log(`[mine] Re-equip failed: ${e.message}, retrying...`)
        // Second attempt
        try {
          const freshItem = bot.inventory.items().find(i => i.name === equippedTool)
          if (freshItem) {
            await bot.equip(freshItem, 'hand')
            await new Promise(r => setTimeout(r, 150))
          }
        } catch (e2) { /* give up */ }
      }
    }

    // ── Use non-scaffolding movements for mine pathfinding ──
    // Prevents pathfinder from equipping blocks (which changes held tool)
    const mcData = require('minecraft-data')(bot.version)
    const mineMovements = new Movements(bot, mcData)
    mineMovements.allowSprinting = true
    mineMovements.scafoldingBlocks = []  // CRITICAL: don't scaffold during mining
    bot.pathfinder.setMovements(mineMovements)

    // ── Helper: clear obstructing blocks between bot and target ──
    const clearPathTo = async (targetPos) => {
      const botPos = bot.entity.position.floored()
      const dx = Math.sign(targetPos.x - botPos.x)
      const dz = Math.sign(targetPos.z - botPos.z)

      // Clear blocks in the direction of the target (feet + head level, 3 blocks ahead)
      for (let step = 1; step <= 3; step++) {
        if (abortFlag) break
        for (let dy = 0; dy <= 1; dy++) {
          const checkPos = botPos.offset(dx * step, dy, dz * step)
          const b = bot.blockAt(checkPos)
          if (b && b.boundingBox === 'block' && b.name !== 'air' && b.name !== 'cave_air'
              && !b.name.includes(block_type)) {
            try { await bot.dig(b) } catch (e) { /* ignore */ }
          }
        }
      }
    }

    // ── Helper: collect dropped items near a position ──
    const collectItemsNear = async (pos, radius = 8) => {
      const nearbyItems = Object.values(bot.entities).filter(
        e => e.name === 'item' && e.position.distanceTo(pos) < radius
      )
      for (const item of nearbyItems) {
        if (abortFlag) break
        // Clear any lingering pathfinder goal before each item attempt
        try { bot.pathfinder.setGoal(null) } catch (e) {}
        const t = setTimeout(() => { try { bot.pathfinder.setGoal(null) } catch(e) {} }, 3000)
        try {
          await bot.pathfinder.goto(new goals.GoalNear(item.position.x, item.position.y, item.position.z, 1))
          await new Promise(r => setTimeout(r, 250))
        } catch (e) {
          /* item may have been collected already or path blocked */
        } finally {
          clearTimeout(t)
        }
      }
    }

    // ── Main mining loop ──
    const failedPositions = new Set()  // Track unreachable block positions
    const MAX_FAILS = 10  // Safety limit for unreachable blocks
    for (let i = 0; i < mineCount; i++) {
      if (failedPositions.size >= MAX_FAILS) break
      if (abortFlag) {
        abortFlag = false
        return res.json({ success: false, message: `Aborted after ${mined} ${block_type}` })
      }

      // 1. Find the target block using block IDs (more reliable than callback matching)
      const blockType1 = mcData.blocksByName[block_type]
      const blockType2 = mcData.blocksByName['deepslate_' + block_type]
      const matchIds = []
      if (blockType1) matchIds.push(blockType1.id)
      if (blockType2) matchIds.push(blockType2.id)

      let block = null
      if (matchIds.length > 0) {
        // findBlocks returns Vec3 positions — filter by failedPositions, then get Block
        const foundPositions = bot.findBlocks({
          matching: matchIds,
          maxDistance: 64,
          count: 20
        })
        for (const pos of foundPositions) {
          const key = `${pos.x},${pos.y},${pos.z}`
          if (!failedPositions.has(key)) {
            block = bot.blockAt(pos)
            if (block && block.name !== 'air') break
            block = null
          }
        }
      }
      if (!block) {
        if (mined === 0) {
          if (failedPositions.size > 0) {
            return res.json({ success: false, message: `Found ${failedPositions.size} ${block_type} but all unreachable (enclosed in stone). Try mining toward them.` })
          }
          return res.json({ success: false, message: `No ${block_type} found nearby` })
        }
        break
      }

      const targetPos = block.position
      const dist = bot.entity.position.distanceTo(targetPos)
      console.log(`[mine] Found ${block.name} at ${targetPos} (dist=${dist.toFixed(1)})`)

      // 2. For trees: clear leaves around target so bot can approach and items can drop
      if (isWood) {
        for (let dx = -2; dx <= 2; dx++) {
          for (let dy = -1; dy <= 2; dy++) {
            for (let dz = -2; dz <= 2; dz++) {
              if (abortFlag) break
              const b = bot.blockAt(targetPos.offset(dx, dy, dz))
              if (b && b.name.includes('leaves')) {
                try { await bot.dig(b) } catch (e) { /* ignore */ }
              }
            }
          }
        }
      }

      // 3. Move to within reach of the block
      let reachedTarget = false
      const REACH = 4.5

      // Attempt 1: normal pathfinding
      try {
        const t = setTimeout(() => { bot.pathfinder.setGoal(null) }, 15000)
        await bot.pathfinder.goto(new goals.GoalNear(targetPos.x, targetPos.y, targetPos.z, 2))
        clearTimeout(t)
      } catch (e) { /* pathfinding may fail partially */ }

      if (bot.entity.position.distanceTo(targetPos) <= REACH) {
        reachedTarget = true
      }

      // Attempt 2: if still too far, clear obstacles and walk closer
      if (!reachedTarget) {
        console.log(`[mine] Pathfind incomplete (dist=${bot.entity.position.distanceTo(targetPos).toFixed(1)}), clearing path...`)
        await clearPathTo(targetPos)
        try {
          const t = setTimeout(() => { bot.pathfinder.setGoal(null) }, 10000)
          await bot.pathfinder.goto(new goals.GoalNear(targetPos.x, targetPos.y, targetPos.z, 2))
          clearTimeout(t)
        } catch (e) { /* best effort */ }
        reachedTarget = bot.entity.position.distanceTo(targetPos) <= REACH
      }

      if (!reachedTarget) {
        console.log(`[mine] Cannot reach ${block.name} at ${targetPos} (dist=${bot.entity.position.distanceTo(targetPos).toFixed(1)}), blacklisting`)
        failedPositions.add(`${targetPos.x},${targetPos.y},${targetPos.z}`)
        i--  // Don't count failed attempts against mineCount
        continue
      }

      // 4. Force re-equip tool right before dig (handles pathfinder scaffold, tool break, etc.)
      await ensureToolHeld()

      // 4b. If tool broke mid-loop and block requires one, stop mining
      if (!equippedTool && needsTool) {
        console.log(`[mine] Tool broke and no replacement available — stopping after ${mined} mined`)
        break
      }

      // 5. Dig the block
      const targetBlock = bot.blockAt(targetPos)
      if (targetBlock && targetBlock.name !== 'air' && targetBlock.name !== 'cave_air'
          && targetBlock.boundingBox === 'block') {
        console.log(`[mine] Digging ${targetBlock.name} with: ${bot.heldItem?.name || 'BARE HANDS'}`)
        try {
          await bot.dig(targetBlock)
          mined++
        } catch (digErr) {
          console.log(`[mine] Dig failed at ${targetPos}: ${digErr.message}`)
        }
      } else {
        console.log(`[mine] Block at ${targetPos} is ${targetBlock?.name || 'null'}, skipping`)
      }

      // 5b. Cluster mining: scan nearby blocks for more of the same ore
      //     bot.blockAt() directly reads chunk data — more reliable than findBlock for adjacent blocks
      if (!isWood && mined > 0) {
        const CLUSTER_RADIUS = 4
        const clusterQueue = []
        for (let dx = -CLUSTER_RADIUS; dx <= CLUSTER_RADIUS; dx++) {
          for (let dy = -CLUSTER_RADIUS; dy <= CLUSTER_RADIUS; dy++) {
            for (let dz = -CLUSTER_RADIUS; dz <= CLUSTER_RADIUS; dz++) {
              if (dx === 0 && dy === 0 && dz === 0) continue
              const adjPos = targetPos.offset(dx, dy, dz)
              const adjBlock = bot.blockAt(adjPos)
              if (adjBlock && (adjBlock.name === block_type || adjBlock.name === 'deepslate_' + block_type)) {
                const key = `${adjPos.x},${adjPos.y},${adjPos.z}`
                if (!failedPositions.has(key)) {
                  clusterQueue.push(adjPos.clone())
                }
              }
            }
          }
        }
        if (clusterQueue.length > 0) {
          console.log(`[mine] Cluster detected! ${clusterQueue.length} more ${block_type} within ${CLUSTER_RADIUS} blocks`)
        }
        for (const clusterPos of clusterQueue) {
          if (mined >= mineCount) break
          if (abortFlag) break

          // Re-equip tool before each cluster dig
          await ensureToolHeld()
          if (!equippedTool && needsTool) break

          // Check if block is still there (might have been mined by earlier cluster iteration)
          const cb = bot.blockAt(clusterPos)
          if (!cb || cb.name === 'air' || cb.name === 'cave_air') continue

          const clusterDist = bot.entity.position.distanceTo(clusterPos)
          if (clusterDist > REACH) {
            // Need to move closer
            try {
              const t = setTimeout(() => { bot.pathfinder.setGoal(null) }, 8000)
              await bot.pathfinder.goto(new goals.GoalNear(clusterPos.x, clusterPos.y, clusterPos.z, 2))
              clearTimeout(t)
            } catch (e) { /* best effort */ }
            if (bot.entity.position.distanceTo(clusterPos) > REACH) {
              console.log(`[mine] Cannot reach cluster block at ${clusterPos}, skipping`)
              continue
            }
          }

          try {
            console.log(`[mine] Cluster mining ${cb.name} at ${clusterPos}`)
            await bot.dig(cb)
            mined++
          } catch (e) {
            console.log(`[mine] Cluster dig failed: ${e.message}`)
          }
        }
      }

      // 6. Collect dropped items
      await new Promise(r => setTimeout(r, 600))  // wait for drops to settle
      await collectItemsNear(targetPos)
      // Second pass for slow-falling items (trees)
      if (isWood) {
        await new Promise(r => setTimeout(r, 500))
        await collectItemsNear(bot.entity.position, 6)
      }
    }

    // Restore default movements (with scaffolding) after mining
    const defaultMovements = new Movements(bot, mcData)
    defaultMovements.allowSprinting = true
    bot.pathfinder.setMovements(defaultMovements)

    const toolMsg = equippedTool ? ` (using ${equippedTool})` : ' (no tool — used fist!)'
    if (mined === 0) {
      res.json({ success: false, message: `Found ${block_type} but failed to mine any${toolMsg}` })
    } else {
      res.json({ success: true, message: `Mined ${mined} ${block_type}${toolMsg}` })
    }
  } catch (err) {
    // Restore default movements on error too
    try {
      const mcData2 = require('minecraft-data')(bot.version)
      const defaultMovements = new Movements(bot, mcData2)
      defaultMovements.allowSprinting = true
      bot.pathfinder.setMovements(defaultMovements)
    } catch (e) { /* ignore */ }
    res.json({ success: false, message: err.message })
  }
})

// POST /action/place
app.post('/action/place', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const { block_name, x, y, z } = req.body
    const item = bot.inventory.items().find(i => i.name === block_name)
    if (!item) return res.json({ success: false, message: `No ${block_name} in inventory` })

    // If coordinates given, move there first
    if (x && y && z) {
      const timeout = setTimeout(() => { bot.pathfinder.setGoal(null) }, 15000)
      await bot.pathfinder.goto(new goals.GoalNear(x, y, z, 3))
      clearTimeout(timeout)
    }

    // Find an air block near the bot and place there
    const pos = bot.entity.position.floored()
    const botFeet = pos
    const botHead = pos.offset(0, 1, 0)

    // Candidate positions: feet level → head level → above head (for underground shafts)
    const candidates = [
      // Priority 1: feet-level horizontal (works on surface)
      pos.offset(1, 0, 0), pos.offset(-1, 0, 0), pos.offset(0, 0, 1), pos.offset(0, 0, -1),
      // Priority 2: head-level horizontal (works in caves)
      pos.offset(1, 1, 0), pos.offset(-1, 1, 0), pos.offset(0, 1, 1), pos.offset(0, 1, -1),
      // Priority 3: above head (works in vertical shafts from dig_down)
      pos.offset(0, 2, 0),
    ]

    await bot.equip(item, 'hand')

    // Helper: try to place at a target position
    const dirs = [[0,-1,0],[0,1,0],[1,0,0],[-1,0,0],[0,0,1],[0,0,-1]]
    const tryPlace = async (target) => {
      const targetBlock = bot.blockAt(target)
      if (!targetBlock || (targetBlock.name !== 'air' && targetBlock.name !== 'cave_air')) return false

      for (const [dx, dy, dz] of dirs) {
        const refPos = target.offset(dx, dy, dz)
        // Skip if reference is at bot's body (feet or head)
        if (refPos.x === botFeet.x && refPos.z === botFeet.z &&
            (refPos.y === botFeet.y || refPos.y === botHead.y)) continue
        const refBlock = bot.blockAt(refPos)
        if (refBlock && refBlock.name !== 'air' && refBlock.name !== 'cave_air' && refBlock.boundingBox === 'block') {
          const faceVec = new Vec3(-dx, -dy, -dz)
          await bot.placeBlock(refBlock, faceVec)
          await new Promise(r => setTimeout(r, 100))
          return true
        }
      }
      return false
    }

    // Phase 1: try all candidate positions
    for (const target of candidates) {
      if (await tryPlace(target)) {
        return res.json({ success: true, message: `Placed ${block_name} at ${target.x}, ${target.y}, ${target.z}` })
      }
    }

    // Phase 2: dig out an adjacent block to create space, then place there
    const digTargets = [pos.offset(1,0,0), pos.offset(-1,0,0), pos.offset(0,0,1), pos.offset(0,0,-1)]
    for (const target of digTargets) {
      const block = bot.blockAt(target)
      if (block && block.name !== 'air' && block.name !== 'cave_air' && block.name !== 'bedrock') {
        try {
          await bot.dig(block)
          await new Promise(r => setTimeout(r, 100))
          if (await tryPlace(target)) {
            return res.json({ success: true, message: `Placed ${block_name} at ${target.x}, ${target.y}, ${target.z} (after digging)` })
          }
        } catch (e) { /* continue to next direction */ }
      }
    }

    return res.json({ success: false, message: 'No suitable position to place block (need adjacent air + solid reference)' })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// POST /action/attack - Smart combat: attack, heal, flee if needed
app.post('/action/attack', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const { entity_type } = req.body
    const hostiles = ['zombie', 'skeleton', 'spider', 'creeper', 'enderman', 'witch', 'drowned', 'husk', 'stray', 'phantom']
    const animals = ['cow', 'pig', 'chicken', 'sheep', 'rabbit']
    const pos = bot.entity.position

    // Find target
    let target = Object.values(bot.entities)
      .filter(e => {
        if (entity_type) return e.name === entity_type
        return hostiles.includes(e.name) || animals.includes(e.name)
      })
      .filter(e => e.position.distanceTo(pos) < 30)
      .sort((a, b) => a.position.distanceTo(pos) - b.position.distanceTo(pos))[0]

    if (!target) return res.json({ success: false, message: `No ${entity_type || 'attackable'} entity nearby` })

    const targetName = target.name
    const targetId = target.id
    const isHostile = hostiles.includes(targetName)
    let hits = 0
    const maxHits = 30
    const startTime = Date.now()
    const timeout = 25000

    // Equip best weapon
    const weapons = ['diamond_sword', 'iron_sword', 'stone_sword', 'wooden_sword', 'diamond_axe', 'iron_axe', 'stone_axe', 'wooden_axe']
    let equippedWeapon = 'fist'
    for (const wpn of weapons) {
      const item = bot.inventory.items().find(i => i.name === wpn)
      if (item) {
        await bot.equip(item, 'hand')
        equippedWeapon = wpn
        break
      }
    }

    // Helper: try to eat food mid-combat
    const foods = ['cooked_beef', 'cooked_porkchop', 'cooked_chicken', 'cooked_mutton',
      'bread', 'golden_apple', 'apple', 'melon_slice', 'baked_potato',
      'beef', 'porkchop', 'chicken', 'mutton', 'potato', 'carrot',
      'sweet_berries', 'dried_kelp']

    async function tryHeal() {
      const food = bot.inventory.items().find(i => foods.includes(i.name))
      if (food) {
        try {
          await bot.equip(food, 'hand')
          await bot.consume()
          // Re-equip weapon after eating
          const wpn = bot.inventory.items().find(i => i.name === equippedWeapon)
          if (wpn) await bot.equip(wpn, 'hand')
          return true
        } catch (e) { return false }
      }
      return false
    }

    function hasFood() {
      return bot.inventory.items().some(i => foods.includes(i.name))
    }

    // Helper: flee from combat
    async function flee() {
      const entity = bot.entities[targetId]
      if (!entity) return

      // Run in opposite direction from enemy
      const dx = bot.entity.position.x - entity.position.x
      const dz = bot.entity.position.z - entity.position.z
      const dist = Math.sqrt(dx * dx + dz * dz) || 1
      const fleeX = bot.entity.position.x + (dx / dist) * 30
      const fleeZ = bot.entity.position.z + (dz / dist) * 30

      try {
        bot.pathfinder.setGoal(new goals.GoalNear(fleeX, bot.entity.position.y, fleeZ, 3))
        await new Promise(resolve => setTimeout(resolve, 3000))
        bot.pathfinder.setGoal(null)
      } catch (e) { /* best effort */ }
    }

    // Pre-combat: check if we should even fight
    if (isHostile && equippedWeapon === 'fist' && bot.health < 10) {
      await flee()
      return res.json({
        success: false,
        message: `Fled from ${targetName}! No weapon and low health (${bot.health}/20). Craft a sword first.`
      })
    }

    // Combat loop
    let fled = false
    let healed = 0

    while (hits < maxHits && (Date.now() - startTime) < timeout) {
      // === SURVIVAL CHECK ===
      if (bot.health <= 4) {
        // Critical: try to eat, if no food → flee immediately
        if (hasFood()) {
          const ate = await tryHeal()
          if (ate) {
            healed++
            continue
          }
        }
        // No food or eating failed → RUN
        await flee()
        fled = true
        break
      }

      if (bot.health <= 8 && hasFood()) {
        // Low health but have food: eat and keep fighting
        const ate = await tryHeal()
        if (ate) healed++
      }

      // === CREEPER CHECK: never melee a creeper that's hissing ===
      const entity = bot.entities[targetId]
      if (!entity || !entity.isValid) {
        // Target is dead
        break
      }

      if (entity.name === 'creeper') {
        const dist = entity.position.distanceTo(bot.entity.position)
        // If creeper is close (about to explode), run
        if (dist < 4) {
          await flee()
          fled = true
          break
        }
      }

      const dist = entity.position.distanceTo(bot.entity.position)

      // Chase if too far
      if (dist > 3) {
        try {
          bot.pathfinder.setGoal(new goals.GoalNear(
            entity.position.x, entity.position.y, entity.position.z, 2
          ))
          await new Promise(resolve => setTimeout(resolve, 500))
        } catch (e) { /* keep trying */ }
      }

      // Attack if close enough
      if (dist <= 4) {
        try {
          await bot.lookAt(entity.position.offset(0, entity.height * 0.8, 0))
          bot.attack(entity)
          hits++
          await new Promise(resolve => setTimeout(resolve, 600))
        } catch (e) {
          break
        }
      } else {
        await new Promise(resolve => setTimeout(resolve, 200))
      }
    }

    // Post-combat: collect drops
    const finalEntity = bot.entities[targetId]
    const killed = !finalEntity || !finalEntity.isValid

    if (killed) {
      // Wait for drops
      await new Promise(resolve => setTimeout(resolve, 500))
      const drops = Object.values(bot.entities).filter(
        e => e.name === 'item' && e.position.distanceTo(bot.entity.position) < 10
      )
      for (const drop of drops) {
        try {
          const ct = setTimeout(() => { bot.pathfinder.setGoal(null) }, 3000)
          await bot.pathfinder.goto(new goals.GoalNear(drop.position.x, drop.position.y, drop.position.z, 0))
          clearTimeout(ct)
        } catch (e) { /* item may be collected already */ }
      }
    }

    bot.pathfinder.setGoal(null)

    // Build result message
    const healthNow = bot.health
    let msg = ''
    if (fled) {
      msg = `Fled from ${targetName}! Health was critical (${healthNow}/20 now). ${hits} hits landed.`
      if (healed > 0) msg += ` Ate food ${healed}x during combat.`
      msg += ` Need better gear or more food before fighting.`
    } else if (killed) {
      msg = `Killed ${targetName}! (${hits} hits, weapon: ${equippedWeapon}, health: ${healthNow}/20).`
      if (healed > 0) msg += ` Ate food ${healed}x during combat.`
      msg += ` Item drops auto-collected.`
    } else {
      msg = `Fought ${targetName} for ${hits} hits but it escaped. Health: ${healthNow}/20.`
    }

    res.json({ success: killed || fled, message: msg })
  } catch (err) {
    bot.pathfinder.setGoal(null)
    res.json({ success: false, message: err.message })
  }
})

// POST /action/eat
app.post('/action/eat', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const foods = ['cooked_beef', 'cooked_porkchop', 'cooked_chicken', 'cooked_mutton',
      'bread', 'golden_apple', 'apple', 'melon_slice', 'baked_potato',
      'beef', 'porkchop', 'chicken', 'mutton', 'potato', 'carrot',
      'sweet_berries', 'dried_kelp']

    const food = bot.inventory.items().find(i => foods.includes(i.name))
    if (!food) return res.json({ success: false, message: 'No food in inventory' })

    await bot.equip(food, 'hand')
    await bot.consume()
    res.json({ success: true, message: `Ate ${food.name}` })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// POST /action/equip
app.post('/action/equip', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const { item_name, destination } = req.body

    // Check if already equipped in the target slot
    const slotMap = { 'head': 5, 'torso': 6, 'legs': 7, 'feet': 8, 'off-hand': 45 }
    const targetSlot = slotMap[destination]
    if (targetSlot) {
      const equipped = bot.inventory.slots[targetSlot]
      if (equipped && equipped.name === item_name) {
        return res.json({ success: true, message: `${item_name} already equipped in ${destination}` })
      }
    }
    // Also check hand slot (mainhand = slot 36+hotbar selection)
    if (!destination || destination === 'hand') {
      const heldItem = bot.heldItem
      if (heldItem && heldItem.name === item_name) {
        return res.json({ success: true, message: `${item_name} already in hand` })
      }
    }

    const item = bot.inventory.items().find(i => i.name === item_name)
    if (!item) return res.json({ success: false, message: `No ${item_name} in inventory` })

    await bot.equip(item, destination || 'hand')
    res.json({ success: true, message: `Equipped ${item.name}` })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// POST /action/craft
app.post('/action/craft', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const { item_name, count } = req.body
    const craftCount = count || 1
    const mcData = require('minecraft-data')(bot.version)
    const item = mcData.itemsByName[item_name]
    if (!item) return res.json({ success: false, message: `Unknown item: ${item_name}` })

    const invSummary = bot.inventory.items().map(i => `${i.name} x${i.count}`).join(', ') || 'empty'

    // Try without crafting table (2x2)
    let recipes = bot.recipesFor(item.id, null, 1, null)
    if (recipes.length) {
      await bot.craft(recipes[0], craftCount)
      return res.json({ success: true, message: `Crafted ${craftCount}x ${item_name} (no table needed)` })
    }

    // Try with nearby crafting table
    const craftingTable = bot.findBlock({
      matching: b => b.name === 'crafting_table',
      maxDistance: 32
    })

    if (craftingTable) {
      // Close any open window first to avoid stale window issues
      if (bot.currentWindow) {
        bot.closeWindow(bot.currentWindow)
        await new Promise(r => setTimeout(r, 300))
      }

      const timeout = setTimeout(() => { bot.pathfinder.setGoal(null) }, 15000)
      await bot.pathfinder.goto(new goals.GoalNear(
        craftingTable.position.x, craftingTable.position.y, craftingTable.position.z, 2
      ))
      clearTimeout(timeout)

      recipes = bot.recipesFor(item.id, null, 1, craftingTable)
      if (recipes.length) {
        // Retry up to 2 times on windowOpen timeout
        for (let attempt = 0; attempt < 3; attempt++) {
          try {
            // Re-find the crafting table block reference (may go stale)
            const freshTable = bot.findBlock({
              matching: b => b.name === 'crafting_table',
              maxDistance: 6
            })
            const tableRef = freshTable || craftingTable
            await bot.craft(recipes[0], craftCount, tableRef)
            return res.json({ success: true, message: `Crafted ${craftCount}x ${item_name} (at crafting table)` })
          } catch (craftErr) {
            if (craftErr.message.includes('windowOpen') && attempt < 2) {
              console.log(`   ⚠️ Craft windowOpen failed (attempt ${attempt + 1}/3), retrying...`)
              // Close any stuck window and wait before retry
              if (bot.currentWindow) {
                bot.closeWindow(bot.currentWindow)
              }
              await new Promise(r => setTimeout(r, 1000))
              continue
            }
            throw craftErr  // Non-windowOpen error or final attempt → propagate
          }
        }
      }

      return res.json({
        success: false,
        message: `At crafting table but missing materials for ${item_name}. Inventory: ${invSummary}`
      })
    }

    return res.json({
      success: false,
      message: `No crafting table nearby. To craft ${item_name} you need a crafting_table. Inventory: ${invSummary}`
    })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// POST /action/smelt - Smelt items in a furnace
app.post('/action/smelt', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const { item_name, count } = req.body
    const smeltCount = count || 1
    const mcData = require('minecraft-data')(bot.version)

    // Find or require a furnace
    let furnace = bot.findBlock({
      matching: b => b.name === 'furnace' || b.name === 'blast_furnace' || b.name === 'smoker',
      maxDistance: 32
    })

    if (!furnace) {
      // Try to craft and place a furnace if we have cobblestone
      const cobble = bot.inventory.items().find(i => i.name === 'cobblestone')
      if (cobble && cobble.count >= 8) {
        // Craft furnace
        const furnaceItem = mcData.itemsByName['furnace']
        const craftingTable = bot.findBlock({ matching: b => b.name === 'crafting_table', maxDistance: 32 })

        if (craftingTable) {
          const timeout1 = setTimeout(() => { bot.pathfinder.setGoal(null) }, 15000)
          await bot.pathfinder.goto(new goals.GoalNear(
            craftingTable.position.x, craftingTable.position.y, craftingTable.position.z, 2
          ))
          clearTimeout(timeout1)

          const recipes = bot.recipesFor(furnaceItem.id, null, 1, craftingTable)
          if (recipes.length) {
            await bot.craft(recipes[0], 1, craftingTable)
          } else {
            return res.json({ success: false, message: 'Cannot craft furnace. Need 8 cobblestone + crafting table.' })
          }
        } else {
          return res.json({ success: false, message: 'No furnace or crafting table nearby. Place a crafting table first, then craft a furnace (8 cobblestone).' })
        }

        // Place the furnace (same pattern as /action/place)
        const furnaceInv = bot.inventory.items().find(i => i.name === 'furnace')
        if (furnaceInv) {
          const pos = bot.entity.position.floored()
          const candidates = [
            pos.offset(1, 0, 0), pos.offset(-1, 0, 0),
            pos.offset(0, 0, 1), pos.offset(0, 0, -1),
          ]
          await bot.equip(furnaceInv, 'hand')
          for (const target of candidates) {
            const tb = bot.blockAt(target)
            if (!tb || tb.name !== 'air') continue
            const dirs = [[0,-1,0],[0,1,0],[1,0,0],[-1,0,0],[0,0,1],[0,0,-1]]
            let placed = false
            for (const [dx, dy, dz] of dirs) {
              const refPos = target.offset(dx, dy, dz)
              if (refPos.x === pos.x && refPos.y === pos.y && refPos.z === pos.z) continue
              const refBlock = bot.blockAt(refPos)
              if (refBlock && refBlock.name !== 'air' && refBlock.name !== 'cave_air' && refBlock.boundingBox === 'block') {
                try {
                  await bot.placeBlock(refBlock, new Vec3(-dx, -dy, -dz))
                  await new Promise(r => setTimeout(r, 100))
                  placed = true
                } catch (e) {}
                break
              }
            }
            if (placed) break
          }
        }

        // Re-find the placed furnace
        furnace = bot.findBlock({
          matching: b => b.name === 'furnace',
          maxDistance: 8
        })
        if (!furnace) {
          return res.json({ success: false, message: 'Crafted furnace but failed to place it. Try placing it manually.' })
        }
      } else {
        const inv = bot.inventory.items().map(i => `${i.name} x${i.count}`).join(', ') || 'empty'
        return res.json({ success: false, message: `No furnace nearby. Need 8 cobblestone to craft one. Inventory: ${inv}` })
      }
    }

    // Walk to furnace
    const timeout2 = setTimeout(() => { bot.pathfinder.setGoal(null) }, 15000)
    await bot.pathfinder.goto(new goals.GoalNear(
      furnace.position.x, furnace.position.y, furnace.position.z, 2
    ))
    clearTimeout(timeout2)

    // Open furnace
    const furnaceBlock = await bot.openFurnace(furnace)

    // Find fuel in inventory
    const fuels = ['coal', 'charcoal', 'oak_planks', 'spruce_planks', 'birch_planks',
      'jungle_planks', 'acacia_planks', 'dark_oak_planks', 'mangrove_planks',
      'oak_log', 'spruce_log', 'birch_log', 'lava_bucket', 'blaze_rod',
      'dried_kelp_block', 'bamboo']

    // Check if fuel is needed
    if (!furnaceBlock.fuelItem()) {
      let fuelItem = null
      for (const fuelName of fuels) {
        fuelItem = bot.inventory.items().find(i => i.name === fuelName)
        if (fuelItem) break
      }
      if (!fuelItem) {
        furnaceBlock.close()
        return res.json({ success: false, message: 'No fuel available. Need coal, charcoal, planks, or logs.' })
      }
      // Put fuel in
      await furnaceBlock.putFuel(fuelItem.type, null, Math.min(fuelItem.count, smeltCount))
    }

    // Put input item
    const inputItem = bot.inventory.items().find(i => i.name === item_name)
    if (!inputItem) {
      furnaceBlock.close()
      return res.json({ success: false, message: `No ${item_name} in inventory to smelt.` })
    }

    const putCount = Math.min(inputItem.count, smeltCount)
    await furnaceBlock.putInput(inputItem.type, null, putCount)

    // Wait for smelting (each item takes ~10 seconds)
    const waitTime = Math.min(putCount * 10500, 120000) // max 2 min
    bot.chat(`Smelting ${putCount}x ${item_name}... (${Math.ceil(waitTime/1000)}s)`)

    await new Promise(resolve => setTimeout(resolve, waitTime))

    // Collect output
    const output = furnaceBlock.outputItem()
    let resultMsg = ''
    if (output) {
      await furnaceBlock.takeOutput()
      resultMsg = `Smelted ${item_name} → got ${output.name} x${output.count}`
    } else {
      resultMsg = `Smelting may still be in progress or failed. Check furnace later.`
    }

    furnaceBlock.close()
    res.json({ success: true, message: resultMsg })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// POST /action/recipe
app.post('/action/recipe', (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const { item_name } = req.body
    const mcData = require('minecraft-data')(bot.version)
    const item = mcData.itemsByName[item_name]
    if (!item) return res.json({ success: false, message: `Unknown item: ${item_name}` })

    // Check recipes with and without table
    const recipesNoTable = bot.recipesFor(item.id, null, 1, null)
    const craftingTable = bot.findBlock({ matching: b => b.name === 'crafting_table', maxDistance: 32 })
    const recipesWithTable = craftingTable ? bot.recipesFor(item.id, null, 1, craftingTable) : []

    const inv = bot.inventory.items().map(i => `${i.name} x${i.count}`).join(', ') || 'empty'

    if (recipesNoTable.length > 0) {
      const r = recipesNoTable[0]
      const ingredients = r.ingredients?.map(i => mcData.items[i]?.name || `id:${i}`).join(', ') || 'unknown'
      return res.json({ success: true, message: `Recipe for ${item_name}: ${ingredients}. No crafting table needed. Can craft now! Inventory: ${inv}` })
    }

    if (recipesWithTable.length > 0) {
      const r = recipesWithTable[0]
      const ingredients = r.ingredients?.map(i => mcData.items[i]?.name || `id:${i}`).join(', ') || 'unknown'
      return res.json({ success: true, message: `Recipe for ${item_name}: ${ingredients}. Needs crafting table (found nearby). Inventory: ${inv}` })
    }

    return res.json({ success: false, message: `Cannot craft ${item_name} right now. May need a crafting table or missing materials. Inventory: ${inv}` })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// POST /action/chat
app.post('/action/chat', (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  const { message } = req.body
  bot.chat(message)
  res.json({ success: true, message: `Sent: ${message}` })
})

// POST /action/flee - Run away from threats (independent flee action)
app.post('/action/flee', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const pos = bot.entity.position
    const fleeDistance = req.body.distance || 30

    // Find the most dangerous nearby hostile to flee FROM
    const hostileNames = ['zombie', 'husk', 'skeleton', 'stray', 'spider', 'creeper',
      'enderman', 'witch', 'drowned', 'phantom', 'pillager', 'vindicator', 'ravager',
      'warden', 'blaze', 'wither_skeleton', 'piglin_brute', 'cave_spider']

    const hostiles = Object.values(bot.entities)
      .filter(e => e !== bot.entity && hostileNames.includes(e.name) && e.position.distanceTo(pos) < 20)
      .sort((a, b) => a.position.distanceTo(pos) - b.position.distanceTo(pos))

    if (hostiles.length === 0) {
      return res.json({ success: true, message: 'No threats nearby, no need to flee' })
    }

    // Calculate average threat direction to flee AWAY from all hostiles
    let threatDx = 0
    let threatDz = 0
    for (const h of hostiles) {
      const dx = h.position.x - pos.x
      const dz = h.position.z - pos.z
      const dist = Math.sqrt(dx * dx + dz * dz) || 1
      // Weight by inverse distance (closer = more weight)
      const weight = 1 / dist
      threatDx += (dx / dist) * weight
      threatDz += (dz / dist) * weight
    }

    // Normalize and flee in opposite direction
    const len = Math.sqrt(threatDx * threatDx + threatDz * threatDz) || 1
    const fleeX = pos.x - (threatDx / len) * fleeDistance
    const fleeZ = pos.z - (threatDz / len) * fleeDistance

    bot.chat(`Fleeing from ${hostiles[0].name}!`)

    // Sprint away
    bot.setControlState('sprint', true)

    try {
      const mcData = require('minecraft-data')(bot.version)
      const movements = new Movements(bot, mcData)
      movements.canDig = false  // Don't waste time digging while fleeing
      movements.scafoldingBlocks = []  // Don't waste blocks
      bot.pathfinder.setMovements(movements)

      const goal = new goals.GoalNear(fleeX, pos.y, fleeZ, 5)
      const timeout = setTimeout(() => { bot.pathfinder.setGoal(null) }, 8000)
      await bot.pathfinder.goto(goal)
      clearTimeout(timeout)
    } catch (e) {
      // Even partial flee is better than nothing
    }

    bot.setControlState('sprint', false)

    // Check if we actually got away
    const newPos = bot.entity.position
    const closestNow = hostiles
      .filter(h => h.isValid)
      .map(h => h.position.distanceTo(newPos))
      .sort((a, b) => a - b)[0] || 999

    const fled = closestNow > 10
    const msg = fled
      ? `Fled ${Math.floor(newPos.distanceTo(pos))}m from ${hostiles.length} hostile(s). Nearest threat now ${closestNow.toFixed(0)}m away.`
      : `Tried to flee but threats still close (${closestNow.toFixed(0)}m). May need shelter.`

    res.json({ success: fled, message: msg })
  } catch (err) {
    bot.setControlState('sprint', false)
    bot.pathfinder.setGoal(null)
    res.json({ success: false, message: `flee error: ${err.message}` })
  }
})

// POST /action/escape_water - Escape from water to avoid drowning (3-phase)
app.post('/action/escape_water', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    // Not in water? Nothing to do
    if (!bot.entity.isInWater) {
      return res.json({ success: true, message: 'Not in water, no action needed' })
    }

    bot.chat('Water detected! Escaping...')

    // ── Phase 1: Swim up to surface ──
    // Check if blocks above are blocking escape — dig them out first
    for (let dy = 2; dy <= 4; dy++) {
      const above = bot.blockAt(bot.entity.position.offset(0, dy, 0).floored())
      if (above && above.name !== 'air' && above.name !== 'cave_air'
          && above.name !== 'water' && above.name !== 'flowing_water'
          && above.boundingBox === 'block') {
        // Phase 3 preemptive: dig upward to clear path
        if (above.diggable && above.name !== 'bedrock') {
          bot.chat('Clearing blocks above to escape water...')
          try { await bot.dig(above) } catch (e) { /* continue anyway */ }
          const above2 = bot.blockAt(bot.entity.position.offset(0, dy + 1, 0).floored())
          if (above2 && above2.diggable && above2.name !== 'bedrock'
              && above2.name !== 'air' && above2.name !== 'water') {
            try { await bot.dig(above2) } catch (e) { /* continue */ }
          }
        }
        break
      }
    }

    // Swim up by holding jump
    bot.setControlState('jump', true)
    // Also try sprinting in water for faster movement
    bot.setControlState('sprint', true)

    // Wait until head is above water or timeout (10s)
    const swimStart = Date.now()
    const swimTimeout = 10000
    let escaped = false

    await new Promise((resolve) => {
      const check = setInterval(() => {
        const eyePos = bot.entity.position.offset(0, 1.62, 0)
        const eyeBlock = bot.blockAt(eyePos.floored())
        const headClear = !eyeBlock ||
          (eyeBlock.name !== 'water' && eyeBlock.name !== 'flowing_water')

        if (headClear || !bot.entity.isInWater) {
          escaped = true
          clearInterval(check)
          resolve()
        } else if (Date.now() - swimStart > swimTimeout) {
          clearInterval(check)
          resolve()
        }
      }, 200)
    })

    bot.setControlState('jump', false)
    bot.setControlState('sprint', false)

    if (!escaped && bot.entity.isInWater) {
      // ── Phase 3: Trapped underwater — try placing block under feet ──
      const inv = bot.inventory.items()
      const placeableBlocks = ['cobblestone', 'dirt', 'stone', 'netherrack',
        'cobbled_deepslate', 'sand', 'gravel', 'oak_planks', 'spruce_planks',
        'birch_planks', 'jungle_planks', 'acacia_planks', 'dark_oak_planks']
      let blockItem = null
      for (const name of placeableBlocks) {
        blockItem = inv.find(i => i.name === name)
        if (blockItem) break
      }
      if (!blockItem) blockItem = inv.find(i => i.stackSize > 1 && !i.name.includes('sword')
        && !i.name.includes('pickaxe') && !i.name.includes('axe')
        && !i.name.includes('shovel') && !i.name.includes('hoe'))

      if (blockItem) {
        // Build pillar upward to escape
        bot.chat('Building pillar to escape water...')
        for (let i = 0; i < 8; i++) {
          if (!bot.entity.isInWater) break
          const belowPos = bot.entity.position.offset(0, -1, 0).floored()
          const belowBlock = bot.blockAt(belowPos)
          if (belowBlock && (belowBlock.name === 'water' || belowBlock.name === 'flowing_water'
              || belowBlock.name === 'air' || belowBlock.name === 'cave_air')) {
            try {
              // Find a reference block to place against
              const refOffsets = [
                new Vec3(0, -2, 0), new Vec3(1, -1, 0), new Vec3(-1, -1, 0),
                new Vec3(0, -1, 1), new Vec3(0, -1, -1)
              ]
              for (const off of refOffsets) {
                const refPos = bot.entity.position.offset(off.x, off.y, off.z).floored()
                const refBlock = bot.blockAt(refPos)
                if (refBlock && refBlock.boundingBox === 'block'
                    && refBlock.name !== 'water' && refBlock.name !== 'flowing_water') {
                  await bot.equip(blockItem, 'hand')
                  const faceVec = belowPos.minus(refPos)
                  await bot.placeBlock(refBlock, faceVec)
                  bot.setControlState('jump', true)
                  await new Promise(r => setTimeout(r, 400))
                  bot.setControlState('jump', false)
                  break
                }
              }
            } catch (e) { /* continue trying */ }
          }
          // Jump up onto placed block
          bot.setControlState('jump', true)
          await new Promise(r => setTimeout(r, 500))
          bot.setControlState('jump', false)
        }
      }

      // Final check
      if (bot.entity.isInWater) {
        return res.json({ success: false, message: 'Could not fully escape water, still submerged' })
      }
    }

    // ── Phase 2: Find and move to land ──
    const pos = bot.entity.position
    let bestLand = null
    let bestDist = Infinity

    for (let dx = -15; dx <= 15; dx += 1) {
      for (let dz = -15; dz <= 15; dz += 1) {
        for (let dy = 5; dy >= -3; dy--) {
          const checkPos = pos.offset(dx, dy, dz).floored()
          const block = bot.blockAt(checkPos)
          if (!block || block.boundingBox !== 'block') continue
          if (block.name === 'water' || block.name === 'flowing_water'
              || block.name === 'lava' || block.name === 'flowing_lava') continue

          const above1 = bot.blockAt(checkPos.offset(0, 1, 0))
          const above2 = bot.blockAt(checkPos.offset(0, 2, 0))
          if (!above1 || !above2) continue
          if (above1.boundingBox === 'block' || above2.boundingBox === 'block') continue
          // Must not be water above either
          if (above1.name === 'water' || above1.name === 'flowing_water') continue

          const dist = pos.distanceTo(checkPos)
          if (dist < bestDist) {
            bestDist = dist
            bestLand = checkPos.offset(0, 1, 0) // stand ON the block
          }
        }
      }
    }

    if (bestLand && bestDist > 2) {
      try {
        const mcData = require('minecraft-data')(bot.version)
        const movements = new Movements(bot, mcData)
        movements.canDig = false  // don't dig while escaping water
        bot.pathfinder.setMovements(movements)
        const goal = new goals.GoalBlock(bestLand.x, bestLand.y, bestLand.z)
        await bot.pathfinder.goto(goal)
        const endPos = bot.entity.position
        bot.chat('Escaped water safely!')
        return res.json({
          success: true,
          message: `Escaped water, moved to land at (${endPos.x.toFixed(1)}, ${endPos.y.toFixed(1)}, ${endPos.z.toFixed(1)})`
        })
      } catch (moveErr) {
        // Pathfinding failed but we might still be out of water
        if (!bot.entity.isInWater) {
          return res.json({ success: true, message: 'Escaped water (pathfinding partial)' })
        }
        return res.json({ success: false, message: `Escaped surface but failed to reach land: ${moveErr.message}` })
      }
    }

    // Already on land or close enough
    const endPos = bot.entity.position
    return res.json({
      success: true,
      message: `Escaped water at (${endPos.x.toFixed(1)}, ${endPos.y.toFixed(1)}, ${endPos.z.toFixed(1)})`
    })

  } catch (err) {
    bot.setControlState('jump', false)
    bot.setControlState('sprint', false)
    return res.json({ success: false, message: `escape_water error: ${err.message}` })
  }
})

// POST /action/dig_shelter - Emergency shelter: dig deep shaft and hide underground
app.post('/action/dig_shelter', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const startPos = bot.entity.position.floored()
    const depth = 8  // dig 8 blocks deep — safely underground
    let dug = 0

    bot.chat('Digging underground!')

    // Auto-equip best pickaxe
    await autoEquipBestTool('stone')

    const sealBlocks = ['cobblestone', 'dirt', 'stone', 'andesite', 'diorite', 'granite',
      'deepslate', 'oak_planks', 'spruce_planks', 'birch_planks', 'sandstone',
      'netherrack', 'oak_log', 'spruce_log', 'birch_log', 'sand', 'gravel']

    // Step 1: Dig straight down (1x1 shaft)
    for (let i = 1; i <= depth; i++) {
      const block = bot.blockAt(new Vec3(startPos.x, startPos.y - i, startPos.z))
      if (block && block.name !== 'air' && block.name !== 'cave_air' && block.boundingBox === 'block') {
        // Safety: don't dig into lava or water
        if (block.name === 'lava' || block.name === 'flowing_lava' ||
            block.name === 'water' || block.name === 'flowing_water') {
          console.log(`[dig_shelter] Stopping at depth ${i}: ${block.name} detected`)
          break
        }
        try {
          await bot.dig(block)
          dug++
        } catch (e) {
          console.log(`[dig_shelter] Dig failed at depth ${i}: ${e.message}`)
          break
        }
      }
    }

    // Step 2: Wait for bot to fall into the shaft
    await new Promise(r => setTimeout(r, 1500))

    // Step 3: Dig a 1x2 pocket to the side (so bot has room to stand)
    const botPos = bot.entity.position.floored()
    for (let dy = 0; dy <= 1; dy++) {
      const block = bot.blockAt(new Vec3(botPos.x + 1, botPos.y + dy, botPos.z))
      if (block && block.name !== 'air' && block.boundingBox === 'block') {
        try {
          await bot.dig(block)
          dug++
        } catch (e) {}
      }
    }

    // Step 4: Seal the shaft top (place block above head to close entrance)
    let sealed = false
    const sealItem = bot.inventory.items().find(i => sealBlocks.includes(i.name))
    if (sealItem) {
      try {
        await bot.equip(sealItem, 'hand')
        // Find a solid wall block adjacent to the shaft opening to place against
        const sealY = botPos.y + 2  // top of head + 1
        for (const offset of [new Vec3(0, 1, 0), new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1)]) {
          const refBlock = bot.blockAt(new Vec3(botPos.x, sealY, botPos.z).plus(offset))
          if (refBlock && refBlock.name !== 'air' && refBlock.boundingBox === 'block') {
            const faceDir = new Vec3(-offset.x, -offset.y, -offset.z)
            try {
              await bot.placeBlock(refBlock, faceDir)
              sealed = true
              break
            } catch (e) {}
          }
        }
      } catch (e) { /* best effort */ }
    }

    const sealMsg = sealed ? 'Sealed!' : 'Warning: could not seal entrance (no blocks to place)'
    const finalDepth = Math.round(startPos.y - bot.entity.position.y)
    bot.chat(`Underground! (${finalDepth} blocks deep) ${sealMsg}`)

    res.json({
      success: true,
      message: `Dug ${finalDepth} blocks deep (${dug} mined). ${sealMsg}`
    })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// ── Helper: short-distance move for tunneling (pathfinder + fallback) ──
async function tunnelMove(target) {
  const t = setTimeout(() => { try { bot.pathfinder.setGoal(null) } catch(e) {} }, 5000)
  try {
    await bot.pathfinder.goto(new goals.GoalNear(target.x, target.y, target.z, 1))
  } catch (e) {
    // Pathfinder failed — try looking and walking toward target
    try {
      const lookVec = new Vec3(target.x + 0.5, target.y + bot.entity.height, target.z + 0.5)
      await bot.lookAt(lookVec)
      bot.setControlState('forward', true)
      await new Promise(r => setTimeout(r, 800))
      bot.setControlState('forward', false)
      await new Promise(r => setTimeout(r, 200))
    } catch (e2) { /* best effort */ }
  } finally {
    clearTimeout(t)
  }
}

// POST /action/dig_down - Mine downward in a staircase pattern (for finding ores/caves)
app.post('/action/dig_down', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const { depth, target_y, emergency } = req.body
    const maxDepth = depth || 10
    const pos = bot.entity.position.floored()
    const targetY = target_y || (pos.y - maxDepth)
    let dug = 0
    let currentY = pos.y

    bot.chat(`Mining downward to y=${targetY}...`)

    // Auto-equip best pickaxe
    const digTool = await autoEquipBestTool('stone')
    if (!digTool) {
      if (emergency) {
        // Emergency (night evasion etc.) — dig by hand through dirt/gravel
        console.log('[dig_down] EMERGENCY: No pickaxe — digging by hand')
      } else {
        return res.json({
          success: false,
          message: 'Cannot dig without a pickaxe! Craft one first: wood → planks → sticks → crafting_table → wooden_pickaxe.'
        })
      }
    }

    // Staircase pattern: dig 2 blocks forward + 1 down, repeat
    const directions = [
      new Vec3(1, 0, 0),
      new Vec3(0, 0, 1),
      new Vec3(-1, 0, 0),
      new Vec3(0, 0, -1),
    ]
    let dirIndex = 0
    let currentPos = pos.clone()

    while (currentPos.y > targetY && dug < maxDepth * 4) {
      if (abortFlag) {
        abortFlag = false
        return res.json({ success: false, message: `Aborted dig_down at y=${currentPos.y} after ${dug} blocks (abort requested)` })
      }

      const dir = directions[dirIndex % 4]

      // Dig forward (2 blocks high for the player)
      const forward = currentPos.offset(dir.x, 0, dir.z)
      const forwardUp = currentPos.offset(dir.x, 1, dir.z)
      const below = currentPos.offset(dir.x, -1, dir.z)

      // ── Preemptive lava scan: check blocks we're about to dig + their neighbors ──
      const digTargets = [forward, forwardUp, below]
      let lavaAhead = false
      for (const target of digTargets) {
        const nearby = scanForLava(target, 2)
        if (nearby.length > 0) {
          const neutralized = await tryWaterBucketOnLava(new Vec3(nearby[0].position.x, nearby[0].position.y, nearby[0].position.z))
          if (!neutralized) {
            bot.chat(`⚠️ LAVA detected near dig path at y=${target.y}! Changing direction.`)
            lavaAhead = true
            break
          }
        }
      }
      if (lavaAhead) {
        dirIndex++  // try a different direction next iteration
        continue
      }

      const b1 = bot.blockAt(forward)
      const b2 = bot.blockAt(forwardUp)

      if (b1 && b1.name !== 'air' && b1.boundingBox === 'block') {
        try { await bot.dig(b1); dug++ } catch (e) {}
      }
      if (b2 && b2.name !== 'air' && b2.boundingBox === 'block') {
        try { await bot.dig(b2); dug++ } catch (e) {}
      }

      // Dig down
      const b3 = bot.blockAt(below)
      if (b3 && b3.name !== 'air' && b3.boundingBox === 'block') {
        try { await bot.dig(b3); dug++ } catch (e) {}
      }

      // Move to new position
      await tunnelMove(below)
      currentPos = bot.entity.position.floored()  // Use ACTUAL position, not target

      dirIndex++

      // Safety: check for lava below
      const checkBelow = bot.blockAt(currentPos.offset(0, -1, 0))
      if (checkBelow && (checkBelow.name === 'lava' || checkBelow.name === 'flowing_lava')) {
        bot.chat('⚠️ LAVA detected below! Stopping descent.')
        res.json({
          success: true,
          message: `Stopped! Lava detected at y=${currentPos.y - 1}. Mined ${dug} blocks, reached y=${currentPos.y}.`
        })
        return
      }

      // Safety: check for water
      const checkWater = bot.blockAt(currentPos.offset(0, -1, 0))
      if (checkWater && (checkWater.name === 'water' || checkWater.name === 'flowing_water')) {
        bot.chat('💧 Water detected below.')
      }
    }

    const finalY = currentPos.y
    bot.chat(`Reached y=${finalY}. Mined ${dug} blocks.`)
    res.json({
      success: true,
      message: `Staircase mined down to y=${finalY} (${dug} blocks). ${finalY <= 16 ? 'Diamond level! Look for diamond_ore nearby.' : finalY <= 48 ? 'Iron/gold ore range.' : 'Still above ore levels.'}`
    })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// POST /action/dig_tunnel - Mine horizontally in a direction (for exploring caves, strip mining)
app.post('/action/dig_tunnel', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const { direction, length } = req.body
    const tunnelLength = length || 10
    const pos = bot.entity.position.floored()
    let dug = 0

    // Direction: north(-z), south(+z), east(+x), west(-x)
    const dirs = {
      'north': new Vec3(0, 0, -1),
      'south': new Vec3(0, 0, 1),
      'east': new Vec3(1, 0, 0),
      'west': new Vec3(-1, 0, 0),
    }

    const dir = dirs[direction || 'north']
    if (!dir) {
      return res.json({ success: false, message: `Invalid direction '${direction}'. Use: north, south, east, west.` })
    }

    bot.chat(`Digging tunnel ${direction}, ${tunnelLength} blocks...`)

    // Auto-equip best pickaxe
    const tunnelTool = await autoEquipBestTool('stone')
    if (!tunnelTool) {
      return res.json({
        success: false,
        message: 'Cannot dig tunnel without a pickaxe! Craft one first: wood → planks → sticks → crafting_table → wooden_pickaxe → mine cobblestone → stone_pickaxe.'
      })
    }

    let currentPos = pos.clone()
    const oresFound = {}

    for (let i = 0; i < tunnelLength; i++) {
      if (abortFlag) {
        abortFlag = false
        const oreStr = Object.keys(oresFound).length > 0
          ? ` Ores found: ${Object.entries(oresFound).map(([k,v]) => `${k}×${v}`).join(', ')}`
          : ''
        return res.json({ success: false, message: `Aborted tunnel after ${i} blocks, ${dug} mined (abort requested).${oreStr}` })
      }

      const next = currentPos.offset(dir.x, 0, dir.z)

      // ── Preemptive lava scan before digging ──
      const lavaCheck = scanForLava(next, 2)
      if (lavaCheck.length > 0) {
        const neutralized = await tryWaterBucketOnLava(new Vec3(lavaCheck[0].position.x, lavaCheck[0].position.y, lavaCheck[0].position.z))
        if (!neutralized) {
          bot.chat('⚠️ LAVA ahead! Stopping tunnel.')
          const oreStr = Object.keys(oresFound).length > 0
            ? ` Ores found: ${Object.entries(oresFound).map(([k,v]) => `${k}×${v}`).join(', ')}`
            : ''
          return res.json({
            success: true,
            message: `Stopped! Lava detected at block ${i + 1}. Mined ${dug} blocks.${oreStr}`
          })
        }
      }

      // Dig 2-high tunnel (1x2)
      for (let dy = 0; dy <= 1; dy++) {
        const target = next.offset(0, dy, 0)
        const block = bot.blockAt(target)
        if (block && block.name !== 'air' && block.boundingBox === 'block') {
          // Track ores found
          if (block.name.includes('ore')) {
            oresFound[block.name] = (oresFound[block.name] || 0) + 1
          }

          // Safety: don't dig into lava/water (fallback check)
          if (block.name === 'lava' || block.name === 'flowing_lava') {
            bot.chat('⚠️ LAVA ahead! Stopping tunnel.')
            const oreStr = Object.keys(oresFound).length > 0
              ? ` Ores found: ${Object.entries(oresFound).map(([k,v]) => `${k}×${v}`).join(', ')}`
              : ''
            return res.json({
              success: true,
              message: `Stopped! Lava at block ${i + 1}. Mined ${dug} blocks.${oreStr}`
            })
          }

          try { await bot.dig(block); dug++ } catch (e) {}
        }
      }

      // Move forward
      await tunnelMove(next)
      currentPos = bot.entity.position.floored()  // Use ACTUAL position, not target
    }

    const oreStr = Object.keys(oresFound).length > 0
      ? ` Ores found: ${Object.entries(oresFound).map(([k,v]) => `${k}×${v}`).join(', ')}`
      : ' No ores found in this tunnel.'
    res.json({
      success: true,
      message: `Tunnel complete: ${tunnelLength} blocks ${direction} at y=${pos.y}. Mined ${dug} blocks.${oreStr}`
    })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// POST /action/branch_mine - Efficient branch mining pattern (main tunnel + perpendicular branches)
app.post('/action/branch_mine', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const { direction = 'north', main_length = 20, branch_length = 5, branch_spacing = 3 } = req.body
    const pos = bot.entity.position.floored()

    const dirs = {
      'north': new Vec3(0, 0, -1),
      'south': new Vec3(0, 0, 1),
      'east': new Vec3(1, 0, 0),
      'west': new Vec3(-1, 0, 0),
    }

    const mainDir = dirs[direction]
    if (!mainDir) {
      return res.json({ success: false, message: `Invalid direction '${direction}'. Use: north, south, east, west.` })
    }

    // Perpendicular directions for branches
    const perpDirs = (direction === 'north' || direction === 'south')
      ? [new Vec3(1, 0, 0), new Vec3(-1, 0, 0)]   // east/west branches
      : [new Vec3(0, 0, 1), new Vec3(0, 0, -1)]     // north/south branches

    // Auto-equip best pickaxe
    const tool = await autoEquipBestTool('stone')
    if (!tool) {
      return res.json({ success: false, message: 'Cannot branch mine without a pickaxe!' })
    }

    bot.chat(`Branch mining ${direction}, main=${main_length}, branches=${branch_length}...`)

    let currentPos = pos.clone()
    const oresFound = {}
    let totalDug = 0

    // Helper: dig a 1x2 block and track ores
    async function digBlock(target) {
      const block = bot.blockAt(target)
      if (!block || block.name === 'air' || block.boundingBox !== 'block') return false
      if (block.name === 'lava' || block.name === 'flowing_lava') return 'lava'
      if (block.name.includes('ore')) {
        oresFound[block.name] = (oresFound[block.name] || 0) + 1
      }
      try { await bot.dig(block); totalDug++ } catch (e) {}
      return true
    }

    // Helper: dig + move one step in a direction
    async function digStep(fromPos, dir) {
      const next = fromPos.offset(dir.x, 0, dir.z)
      // Preemptive lava scan before digging
      const lavaAhead = scanForLava(next, 2)
      if (lavaAhead.length > 0) {
        const neutralized = await tryWaterBucketOnLava(new Vec3(lavaAhead[0].position.x, lavaAhead[0].position.y, lavaAhead[0].position.z))
        if (!neutralized) return null  // can't proceed, lava blocking
      }
      const lavaCheck1 = await digBlock(next)
      if (lavaCheck1 === 'lava') return null
      const lavaCheck2 = await digBlock(next.offset(0, 1, 0))
      if (lavaCheck2 === 'lava') return null
      await tunnelMove(next)
      return bot.entity.position.floored()  // Use ACTUAL position, not target
    }

    // ── Main tunnel with branches ──
    for (let i = 0; i < main_length; i++) {
      if (abortFlag) {
        abortFlag = false
        const oreStr = Object.entries(oresFound).map(([k,v]) => `${k}×${v}`).join(', ')
        return res.json({ success: false, message: `Aborted branch mine after ${i} blocks. Dug ${totalDug}. Ores: ${oreStr || 'none'}` })
      }

      // Dig forward in main tunnel
      const next = await digStep(currentPos, mainDir)
      if (!next) {
        const oreStr = Object.entries(oresFound).map(([k,v]) => `${k}×${v}`).join(', ')
        return res.json({ success: true, message: `Stopped at lava after ${i} main blocks. Dug ${totalDug}. Ores: ${oreStr || 'none'}` })
      }
      currentPos = next

      // ── Branch at intervals ──
      if (i > 0 && i % branch_spacing === 0) {
        const junctionPos = currentPos.clone()

        for (const perpDir of perpDirs) {
          let branchPos = junctionPos.clone()
          for (let j = 0; j < branch_length; j++) {
            if (abortFlag) break
            const nextBranch = await digStep(branchPos, perpDir)
            if (!nextBranch) break  // lava or can't proceed
            branchPos = nextBranch
          }
          // Return to junction
          const actualPos = bot.entity.position.floored()
          if (actualPos.x !== junctionPos.x || actualPos.z !== junctionPos.z) {
            await tunnelMove(junctionPos)
          }
        }
      }
    }

    const oreStr = Object.entries(oresFound).map(([k,v]) => `${k}×${v}`).join(', ')
    res.json({
      success: true,
      message: `Branch mine complete: ${main_length} main + branches at y=${pos.y}. Dug ${totalDug} blocks. Ores: ${oreStr || 'none'}`
    })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// POST /action/build_shelter - Build a simple enclosed shelter around the bot
app.post('/action/build_shelter', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })

  try {
    // Find building material in inventory
    const buildBlocks = ['cobblestone', 'dirt', 'oak_planks', 'spruce_planks', 'birch_planks',
      'stone', 'deepslate', 'sandstone', 'oak_log', 'spruce_log', 'birch_log', 'andesite', 'diorite', 'granite']

    let materialName = null
    for (const name of buildBlocks) {
      const item = bot.inventory.items().find(i => i.name === name)
      if (item && item.count >= 20) {
        materialName = name
        break
      }
    }

    if (!materialName) {
      // Also check combined total across multiple block types
      let total = 0
      for (const name of buildBlocks) {
        const item = bot.inventory.items().find(i => i.name === name)
        if (item) total += item.count
      }
      if (total < 20) {
        return res.json({
          success: false,
          message: `Need at least 20 building blocks. Have ${total} total. Mine cobblestone (stone) to get more.`
        })
      }
      // Use whatever we have most of
      materialName = buildBlocks.reduce((best, name) => {
        const item = bot.inventory.items().find(i => i.name === name)
        const count = item ? item.count : 0
        const bestItem = bot.inventory.items().find(i => i.name === best)
        const bestCount = bestItem ? bestItem.count : 0
        return count > bestCount ? name : best
      }, buildBlocks[0])
    }

    const pos = bot.entity.position.floored()
    const bx = pos.x
    const by = pos.y
    const bz = pos.z
    let placed = 0
    let failed = 0

    // Helper: equip any building material (may switch types if one runs out)
    async function equipMaterial() {
      // First try primary material
      let item = bot.inventory.items().find(i => i.name === materialName)
      if (item) {
        await bot.equip(item, 'hand')
        return true
      }
      // Fallback to any building block
      for (const name of buildBlocks) {
        item = bot.inventory.items().find(i => i.name === name)
        if (item) {
          await bot.equip(item, 'hand')
          return true
        }
      }
      return false
    }

    // Helper: place a block, moving closer if needed
    async function placeAt(x, y, z) {
      try {
        const target = new Vec3(x, y, z)
        const existing = bot.blockAt(target)
        if (existing && existing.name !== 'air' && existing.name !== 'cave_air') return true // already filled

        // Move closer if too far (need to be within ~4.5 blocks to place)
        const dist = bot.entity.position.distanceTo(target)
        if (dist > 4) {
          try {
            await bot.pathfinder.goto(new goals.GoalNear(x, y, z, 3))
          } catch (e) { /* best effort */ }
        }

        // Find adjacent reference block
        const dirs = [[0,-1,0],[0,1,0],[1,0,0],[-1,0,0],[0,0,1],[0,0,-1]]
        for (const [dx, dy, dz] of dirs) {
          const refBlock = bot.blockAt(new Vec3(x+dx, y+dy, z+dz))
          if (refBlock && refBlock.name !== 'air' && refBlock.name !== 'cave_air' && refBlock.boundingBox === 'block') {
            if (!(await equipMaterial())) return false // no blocks left

            const faceVec = new Vec3(-dx, -dy, -dz)
            await bot.placeBlock(refBlock, faceVec)
            placed++
            await new Promise(r => setTimeout(r, 100)) // small delay for server
            return true
          }
        }
        failed++
        return false
      } catch (e) {
        failed++
        return false
      }
    }

    bot.chat('Building shelter...')

    // SHELTER DESIGN: 5x3x5 (x: -2..+2, y: 0..2 walls + 3 roof, z: -2..+2)
    //
    //   Roof (y=3):       Walls (y=0,1,2):       Floor plan:
    //   # # # # #         # # # # #              # # # # #
    //   # # # # #         # . . . #              # . . . #
    //   # # # # #         # . . . #              # . D . #   D = door (north)
    //   # # # # #         # . . . #              # . . . #
    //   # # # # #         # # # # #              # # # # #

    const S = 2 // half-size

    // STEP 1: Place a ground block under the bot if standing on something soft/air
    const groundBlock = bot.blockAt(pos.offset(0, -1, 0))
    if (!groundBlock || groundBlock.name === 'air') {
      // Place ground first so we have a reference
      try {
        if (await equipMaterial()) {
          // try to place on something nearby below
          for (let dx = -1; dx <= 1; dx++) {
            for (let dz = -1; dz <= 1; dz++) {
              const below = bot.blockAt(pos.offset(dx, -2, dz))
              if (below && below.name !== 'air' && below.boundingBox === 'block') {
                await bot.placeBlock(below, new Vec3(0, 1, 0))
                placed++
                break
              }
            }
          }
        }
      } catch (e) {}
    }

    // STEP 2: Walls — layer by layer, bottom to top
    // This ensures each layer has reference blocks from the layer below
    for (let y = 0; y <= 2; y++) {
      if (abortFlag) {
        abortFlag = false
        return res.json({ success: false, message: `Aborted build_shelter after ${placed} blocks placed (abort requested)` })
      }
      // North wall (z = -S)
      for (let x = -S; x <= S; x++) {
        await placeAt(bx + x, by + y, bz - S)
      }
      // South wall (z = +S)
      for (let x = -S; x <= S; x++) {
        await placeAt(bx + x, by + y, bz + S)
      }
      // West wall (x = -S), excluding corners (already placed)
      for (let z = -S + 1; z <= S - 1; z++) {
        await placeAt(bx - S, by + y, bz + z)
      }
      // East wall (x = +S), excluding corners
      for (let z = -S + 1; z <= S - 1; z++) {
        await placeAt(bx + S, by + y, bz + z)
      }
    }

    // STEP 3: Roof — from edges inward (spiral) so each block has an adjacent reference
    // First the border (directly on top of walls at y=3)
    for (let x = -S; x <= S; x++) {
      await placeAt(bx + x, by + 3, bz - S)
      await placeAt(bx + x, by + 3, bz + S)
    }
    for (let z = -S + 1; z <= S - 1; z++) {
      await placeAt(bx - S, by + 3, bz + z)
      await placeAt(bx + S, by + 3, bz + z)
    }
    // Then the inner roof (these have the border as reference)
    for (let x = -S + 1; x <= S - 1; x++) {
      for (let z = -S + 1; z <= S - 1; z++) {
        await placeAt(bx + x, by + 3, bz + z)
      }
    }

    // STEP 4: Door — break 2 wall blocks and place an actual door if available
    let doorPlaced = false
    try {
      const door1 = bot.blockAt(new Vec3(bx, by, bz - S))
      const door2 = bot.blockAt(new Vec3(bx, by + 1, bz - S))
      if (door1 && door1.name !== 'air') await bot.dig(door1)
      if (door2 && door2.name !== 'air') await bot.dig(door2)
      await new Promise(r => setTimeout(r, 200))

      // Try to place an actual door
      const doorItem = bot.inventory.items().find(i => i.name.includes('door'))
      if (doorItem) {
        // Move outside the door opening to place it
        try {
          await bot.pathfinder.goto(new goals.GoalNear(bx, by, bz - S - 1, 1))
        } catch (e) {}
        await new Promise(r => setTimeout(r, 200))

        // Place door on the floor block below the opening
        const floorBlock = bot.blockAt(new Vec3(bx, by - 1, bz - S))
        if (floorBlock && floorBlock.name !== 'air' && floorBlock.boundingBox === 'block') {
          try {
            await bot.equip(doorItem, 'hand')
            await bot.placeBlock(floorBlock, new Vec3(0, 1, 0))
            await new Promise(r => setTimeout(r, 100))
            doorPlaced = true
          } catch (e) {
            console.log(`[build_shelter] Door placement failed: ${e.message}`)
          }
        }
      }
    } catch (e) {}

    // Move back inside
    try {
      await bot.pathfinder.goto(new goals.GoalNear(bx, by, bz, 0))
    } catch (e) {}

    const doorMsg = doorPlaced ? 'Door placed.' : 'Door opening (no door item).'
    bot.chat(`Shelter built! ${placed} blocks placed. ${doorMsg}`)
    res.json({
      success: true,
      message: `Built 5x3x5 shelter with ${placed} blocks (${materialName}) at (${bx}, ${by}, ${bz}). Roof complete. ${doorMsg}${failed > 0 ? ` (${failed} blocks couldn't be placed)` : ''}`
    })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// POST /action/sleep
app.post('/action/sleep', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const bed = bot.findBlock({
      matching: b => b.name.includes('bed'),
      maxDistance: 32
    })
    if (!bed) return res.json({ success: false, message: 'No bed nearby' })

    const timeout = setTimeout(() => { bot.pathfinder.setGoal(null) }, 15000)
    await bot.pathfinder.goto(new goals.GoalNear(bed.position.x, bed.position.y, bed.position.z, 2))
    clearTimeout(timeout)
    await bot.sleep(bed)
    res.json({ success: true, message: 'Sleeping' })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// ============================================
// BOT EVENT HANDLERS
// ============================================
bot.once('spawn', () => {
  console.log('✅ Bot connected to Minecraft!')
  console.log(`📍 Position: ${bot.entity.position}`)

  const mcData = require('minecraft-data')(bot.version)
  const movements = new Movements(bot, mcData)
  movements.allowSprinting = true
  bot.pathfinder.setMovements(movements)

  botReady = true
  bot.chat('Hello! AI bot online.')
})

bot.on('chat', (username, message) => {
  if (username === bot.username) return
  lastChatMessages.push({
    username,
    message,
    time: new Date().toISOString()
  })
  if (lastChatMessages.length > 50) lastChatMessages.shift()
  console.log(`💬 ${username}: ${message}`)
})

// ── Drop Collection: track mob deaths for loot pickup ──
bot.on('entityDead', (entity) => {
  if (!entity || !entity.position) return
  const dist = entity.position.distanceTo(bot.entity.position)
  if (dist < 20) {
    pendingDrops.push({
      position: { x: entity.position.x.toFixed(1), y: entity.position.y.toFixed(1), z: entity.position.z.toFixed(1) },
      time: Date.now(),
      type: entity.name || 'unknown'
    })
    // Expire old drops
    pendingDrops = pendingDrops.filter(d => Date.now() - d.time < 60000)
    console.log(`[drops] Entity ${entity.name} died ${dist.toFixed(0)}m away — tracking drop`)
  }
})

// ── Death Tracking + Combat Detection: health snapshot every update ──
bot.on('health', () => {
  const pos = bot.entity.position
  const nearbyEntities = Object.values(bot.entities)
    .filter(e => e !== bot.entity && e.position.distanceTo(pos) < 20)
    .map(e => ({
      type: e.name || e.username || 'unknown',
      distance: parseFloat(e.position.distanceTo(pos).toFixed(1)),
      position: { x: e.position.x.toFixed(1), y: e.position.y.toFixed(1), z: e.position.z.toFixed(1) }
    }))
    .sort((a, b) => a.distance - b.distance)

  // ── Combat detection: health dropped = likely attacked ──
  const prevHealth = lastHealthSnapshot.health ?? 20
  const currentHealth = bot.health
  const damage = prevHealth - currentHealth

  if (damage > 0) {
    // Health went DOWN — we're being attacked
    const now = Date.now()

    // Find most likely attacker: closest hostile mob
    const hostileNames = ['zombie', 'husk', 'skeleton', 'stray', 'spider', 'creeper',
      'enderman', 'witch', 'drowned', 'phantom', 'pillager', 'vindicator', 'ravager',
      'warden', 'blaze', 'wither_skeleton', 'piglin_brute', 'cave_spider']
    const attacker = nearbyEntities.find(e => hostileNames.includes(e.type))
      || nearbyEntities.find(e => e.type !== 'item' && e.type !== 'experience_orb')

    combatState.isUnderAttack = true
    combatState.lastHitTime = now
    combatState.healthBefore = prevHealth
    combatState.healthDelta = damage
    combatState.lastAttacker = attacker ? {
      type: attacker.type,
      distance: attacker.distance,
      position: attacker.position
    } : null

    // Record to recent attacks (keep last 10)
    combatState.recentAttacks.push({
      type: attacker?.type || 'unknown',
      damage: parseFloat(damage.toFixed(1)),
      time: now,
      position: { x: pos.x.toFixed(1), y: pos.y.toFixed(1), z: pos.z.toFixed(1) },
      health_after: currentHealth
    })
    if (combatState.recentAttacks.length > 10) combatState.recentAttacks.shift()

    // Set combat start time
    if (!combatState.combatStartTime) combatState.combatStartTime = now

    console.log(`⚔️ UNDER ATTACK! Damage: ${damage.toFixed(1)}, HP: ${currentHealth}/20, Attacker: ${attacker?.type || 'unknown'} (${attacker?.distance || '?'}m)`)
  }

  // Clear combat state if no damage for 5 seconds
  if (combatState.isUnderAttack && Date.now() - combatState.lastHitTime > 5000) {
    combatState.isUnderAttack = false
    combatState.combatStartTime = 0
    combatState.lastAttacker = null
  }

  lastHealthSnapshot = {
    health: bot.health,
    food: bot.food,
    position: { x: pos.x.toFixed(1), y: pos.y.toFixed(1), z: pos.z.toFixed(1) },
    entities: nearbyEntities.slice(0, 10),
    time: bot.time.timeOfDay > 13000 ? 'night' : 'day'
  }

  if (bot.health < 5) {
    console.log(`⚠️ Critical health: ${bot.health}/20`)
  }
})

// ── Death cause categorization helper ──
const HOSTILE_SET = new Set(['zombie', 'husk', 'skeleton', 'stray', 'spider', 'creeper',
  'enderman', 'witch', 'drowned', 'phantom', 'pillager', 'vindicator', 'ravager',
  'warden', 'blaze', 'wither_skeleton', 'piglin_brute', 'cave_spider'])

function categorizeDeathMessage(msg) {
  msg = msg.toLowerCase()
  if (msg.includes('slain') || msg.includes('killed') || msg.includes('shot')) return 'combat'
  if (msg.includes('drowned')) return 'drowning'
  if (msg.includes('fell')) return 'fall'
  if (msg.includes('burned') || msg.includes('lava')) return 'fire'
  if (msg.includes('starved')) return 'starvation'
  if (msg.includes('blew up') || msg.includes('blown up')) return 'explosion'
  if (msg.includes('suffocated')) return 'suffocation'
  if (msg.includes('withered')) return 'wither'
  if (msg.includes('pricked') || msg.includes('poked')) return 'cactus'
  return 'unknown'
}

// ── Death Tracking: capture death with pre-death snapshot ──
bot.on('death', () => {
  // Build death message from combat context — prioritized cause detection
  let deathMessage = 'unknown cause'
  let deathCategory = 'unknown'

  // Priority 1: Recent combat (10 seconds) — most reliable indicator
  if (combatState.lastAttacker && Date.now() - combatState.lastHitTime < 10000) {
    deathMessage = `Killed by ${combatState.lastAttacker.type} (${combatState.lastAttacker.distance}m away)`
    deathCategory = 'combat'
    if (lastHealthSnapshot.food <= 0) {
      deathMessage += ' (weakened by starvation)'
    }
  }
  // Priority 2: Recent combat in attack history (30 seconds)
  else if (combatState.recentAttacks.length > 0) {
    const lastAttack = combatState.recentAttacks[combatState.recentAttacks.length - 1]
    const timeSince = Date.now() - lastAttack.time
    if (timeSince < 30000) {
      deathMessage = `Likely killed by ${lastAttack.type} (${(timeSince / 1000).toFixed(0)}s ago)`
      deathCategory = 'combat'
      if (lastHealthSnapshot.food <= 0) {
        deathMessage += ' (weakened by starvation)'
      }
    }
  }
  // Priority 3: Minecraft's actual death message from chat
  if (deathCategory === 'unknown' && lastDeathMessage) {
    deathMessage = lastDeathMessage
    deathCategory = categorizeDeathMessage(lastDeathMessage)
  }
  // Priority 4: Nearby hostile mobs — probable combat
  if (deathCategory === 'unknown' && lastHealthSnapshot.entities.length > 0) {
    const hostiles = lastHealthSnapshot.entities.filter(e => HOSTILE_SET.has(e.type))
    if (hostiles.length > 0) {
      deathMessage = `Died near ${hostiles[0].type} (${hostiles[0].distance}m) — probable combat`
      deathCategory = 'combat'
    }
  }
  // Priority 5: Starvation (only if food was 0 and nothing else detected)
  if (deathCategory === 'unknown' && lastHealthSnapshot.food <= 0) {
    deathMessage = 'Starvation (hard mode) or starvation-weakened death'
    deathCategory = 'starvation'
  }

  const deathEntry = {
    timestamp: Date.now() / 1000,
    message: deathMessage,
    category: deathCategory,
    killed_by: combatState.lastAttacker?.type || null,
    health_before: lastHealthSnapshot.health,
    hunger_before: lastHealthSnapshot.food,
    position: lastHealthSnapshot.position,
    nearby_entities: lastHealthSnapshot.entities,
    time_of_day: lastHealthSnapshot.time,
    recent_combat: combatState.recentAttacks.slice(-5),
  }

  deathLog.push(deathEntry)
  if (deathLog.length > 50) deathLog.shift()

  console.log('💀 Bot died!')
  console.log(`   Cause: ${deathMessage}`)
  console.log(`   Category: ${deathCategory}`)
  console.log(`   Last health: ${lastHealthSnapshot.health}/20, Food: ${lastHealthSnapshot.food}/20`)
  console.log(`   Nearby: ${lastHealthSnapshot.entities.map(e => `${e.type}(${e.distance}m)`).join(', ') || 'none'}`)
  console.log(`   Time: ${lastHealthSnapshot.time}`)

  // Reset combat state on death
  combatState.isUnderAttack = false
  combatState.combatStartTime = 0
  combatState.lastAttacker = null
  combatState.recentAttacks = []
  lastDeathMessage = ''

  bot.chat('I died... respawning!')
})

// ── Capture actual Minecraft death messages from chat ──
bot.on('messagestr', (message) => {
  const botName = bot.username
  if (message.includes(botName) && (
    message.includes('slain by') || message.includes('was killed') ||
    message.includes('was shot') || message.includes('blew up') ||
    message.includes('was blown up') || message.includes('drowned') ||
    message.includes('fell') || message.includes('burned') ||
    message.includes('was pricked') || message.includes('suffocated') ||
    message.includes('starved') || message.includes('withered')
  )) {
    lastDeathMessage = message
    console.log(`💀 Death message captured: ${message}`)

    // Race condition fix: update most recent death log entry if it was just created
    if (deathLog.length > 0) {
      const lastEntry = deathLog[deathLog.length - 1]
      const timeSince = Date.now() / 1000 - lastEntry.timestamp
      if (timeSince < 5) {
        lastEntry.message = message
        lastEntry.category = categorizeDeathMessage(message)
        console.log(`   Updated death log with actual message`)
      }
    }
  }
})

bot.on('error', (err) => console.log('❌ Error:', err.message))
bot.on('kicked', (reason) => console.log('🚫 Kicked:', reason))
bot.on('end', () => {
  console.log('🔌 Disconnected')
  botReady = false
})

// ============================================
// START API SERVER
// ============================================
// POST /action/scan_structure - Scan blocks around a position and save the structure
app.post('/action/scan_structure', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const { name, radius } = req.body
    const r = radius || 5
    const center = bot.entity.position.floored()
    const blocks = []

    for (let dx = -r; dx <= r; dx++) {
      for (let dy = -r; dy <= r; dy++) {
        for (let dz = -r; dz <= r; dz++) {
          const pos = center.offset(dx, dy, dz)
          const block = bot.blockAt(pos)
          if (block && block.name !== 'air') {
            blocks.push({
              dx, dy, dz,
              name: block.name,
              x: pos.x, y: pos.y, z: pos.z
            })
          }
        }
      }
    }

    const structure = {
      name: name || 'unnamed_structure',
      center: { x: center.x, y: center.y, z: center.z },
      radius: r,
      blocks: blocks,
      scanned_at: Date.now(),
      block_count: blocks.length
    }

    // Save to file
    const fs = require('fs')
    let structures = {}
    try {
      structures = JSON.parse(fs.readFileSync('structures.json', 'utf8'))
    } catch (e) {}
    structures[structure.name] = structure
    fs.writeFileSync('structures.json', JSON.stringify(structures, null, 2))

    // Summarize block types
    const typeCounts = {}
    blocks.forEach(b => { typeCounts[b.name] = (typeCounts[b.name] || 0) + 1 })
    const summary = Object.entries(typeCounts)
      .sort((a, b) => b[1] - a[1])
      .map(([name, count]) => `${name} x${count}`)
      .join(', ')

    res.json({
      success: true,
      message: `Scanned "${structure.name}": ${blocks.length} blocks in radius ${r}. Types: ${summary}. Structure saved to structures.json.`
    })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// GET /action/list_structures - List all saved structures
app.get('/action/list_structures', (req, res) => {
  try {
    const fs = require('fs')
    let structures = {}
    try {
      structures = JSON.parse(fs.readFileSync('structures.json', 'utf8'))
    } catch (e) {
      return res.json({ success: true, structures: [], message: 'No saved structures.' })
    }

    const list = Object.values(structures).map(s => ({
      name: s.name,
      center: s.center,
      block_count: s.block_count,
      radius: s.radius,
      scanned_at: s.scanned_at
    }))

    res.json({ success: true, structures: list })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// POST /action/rebuild_structure - Rebuild a saved structure at its original or new location
app.post('/action/rebuild_structure', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const { name, offset_x, offset_y, offset_z } = req.body
    const fs = require('fs')
    let structures = {}
    try {
      structures = JSON.parse(fs.readFileSync('structures.json', 'utf8'))
    } catch (e) {
      return res.json({ success: false, message: 'No structures.json file found.' })
    }

    const structure = structures[name]
    if (!structure) {
      const available = Object.keys(structures).join(', ')
      return res.json({ success: false, message: `Structure "${name}" not found. Available: ${available}` })
    }

    const ox = offset_x || 0
    const oy = offset_y || 0
    const oz = offset_z || 0

    let placed = 0
    let failed = 0
    let missingBlocks = {}

    // Sort blocks bottom to top for stable placement
    const sortedBlocks = [...structure.blocks].sort((a, b) => a.dy - b.dy)

    for (const block of sortedBlocks) {
      const targetPos = new Vec3(
        structure.center.x + block.dx + ox,
        structure.center.y + block.dy + oy,
        structure.center.z + block.dz + oz
      )

      // Check if already has the right block
      const existing = bot.blockAt(targetPos)
      if (existing && existing.name === block.name) {
        placed++ // already there
        continue
      }

      // Find block in inventory
      const item = bot.inventory.items().find(i => i.name === block.name)
      if (!item) {
        missingBlocks[block.name] = (missingBlocks[block.name] || 0) + 1
        failed++
        continue
      }

      // Move close if needed
      const dist = bot.entity.position.distanceTo(targetPos)
      if (dist > 4) {
        try {
          const moveTimeout = setTimeout(() => { bot.pathfinder.setGoal(null) }, 10000)
          await bot.pathfinder.goto(new goals.GoalNear(targetPos.x, targetPos.y, targetPos.z, 3))
          clearTimeout(moveTimeout)
        } catch (e) {}
      }

      // Place
      try {
        await bot.equip(item, 'hand')
        // Find a reference block to place against
        const neighbors = [
          targetPos.offset(0, -1, 0), targetPos.offset(0, 1, 0),
          targetPos.offset(1, 0, 0), targetPos.offset(-1, 0, 0),
          targetPos.offset(0, 0, 1), targetPos.offset(0, 0, -1)
        ]
        let refBlock = null
        for (const np of neighbors) {
          const nb = bot.blockAt(np)
          if (nb && nb.name !== 'air') {
            refBlock = nb
            break
          }
        }
        if (refBlock) {
          await bot.placeBlock(refBlock, targetPos.minus(refBlock.position))
          placed++
        } else {
          failed++
        }
      } catch (e) {
        failed++
      }
    }

    let msg = `Rebuilt "${name}": ${placed} placed, ${failed} failed.`
    if (Object.keys(missingBlocks).length > 0) {
      const missing = Object.entries(missingBlocks)
        .map(([n, c]) => `${n} x${c}`)
        .join(', ')
      msg += ` Missing materials: ${missing}`
    }

    res.json({ success: true, message: msg })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// ============================================
// CAVE DETECTION
// ============================================
app.get('/scan_caves', (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const pos = bot.entity.position.floored()
    const radius = parseInt(req.query.radius) || 16

    // Find air/cave_air blocks below current position (= caves)
    const airBlocks = bot.findBlocks({
      matching: b => b.name === 'air' || b.name === 'cave_air',
      maxDistance: radius,
      count: 500,
      point: pos
    }).filter(p => p.y < pos.y - 2)  // only below us

    if (airBlocks.length === 0) {
      return res.json({ caves: [], count: 0 })
    }

    // Cluster nearby air blocks into cave candidates using simple grid bucketing
    const bucketSize = 5
    const buckets = {}
    for (const p of airBlocks) {
      const key = `${Math.floor(p.x / bucketSize)},${Math.floor(p.y / bucketSize)},${Math.floor(p.z / bucketSize)}`
      if (!buckets[key]) buckets[key] = []
      buckets[key].push(p)
    }

    // Sort by cluster size (largest first), take top 3
    const clusters = Object.values(buckets)
      .filter(c => c.length >= 3)  // at least 3 air blocks = real cave
      .sort((a, b) => b.length - a.length)
      .slice(0, 3)
      .map(cluster => {
        // Calculate center of cluster
        const cx = cluster.reduce((s, p) => s + p.x, 0) / cluster.length
        const cy = cluster.reduce((s, p) => s + p.y, 0) / cluster.length
        const cz = cluster.reduce((s, p) => s + p.z, 0) / cluster.length
        return {
          center: { x: Math.round(cx), y: Math.round(cy), z: Math.round(cz) },
          size: cluster.length,
          distance: Math.round(pos.distanceTo(new Vec3(cx, cy, cz)))
        }
      })

    res.json({ caves: clusters, count: clusters.length })
  } catch (err) {
    res.json({ caves: [], count: 0, error: err.message })
  }
})

// ============================================
// DROP COLLECTION
// ============================================
app.get('/pending_drops', (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  pendingDrops = pendingDrops.filter(d => Date.now() - d.time < 60000)
  res.json({ drops: pendingDrops, count: pendingDrops.length })
})

app.post('/action/collect_drops', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const items = Object.values(bot.entities).filter(
      e => e.name === 'item' && e.position.distanceTo(bot.entity.position) < 16
    ).sort((a, b) => a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position))

    if (items.length === 0) {
      return res.json({ success: true, message: 'No drops nearby to collect' })
    }

    let collected = 0
    for (const item of items.slice(0, 10)) {
      try {
        const timeout = setTimeout(() => { bot.pathfinder.setGoal(null) }, 5000)
        await bot.pathfinder.goto(new goals.GoalNear(item.position.x, item.position.y, item.position.z, 0))
        clearTimeout(timeout)
        collected++
        await new Promise(r => setTimeout(r, 200))
      } catch (e) { /* item may have been collected already */ }
    }
    pendingDrops = []
    res.json({ success: true, message: `Collected ${collected} drop(s) from ${items.length} nearby` })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// ============================================
// SHIELD BLOCKING
// ============================================
app.post('/action/shield_block', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const duration = req.body.duration || 2000

    // Check for shield in off-hand first, then inventory
    const offhand = bot.inventory.slots[45]
    if (!offhand || offhand.name !== 'shield') {
      const shield = bot.inventory.items().find(i => i.name === 'shield')
      if (!shield) return res.json({ success: false, message: 'No shield available' })
      await bot.equip(shield, 'off-hand')
    }

    // Activate shield (right-click off-hand = block)
    bot.activateItem(true)  // true = off-hand
    await new Promise(r => setTimeout(r, duration))
    bot.deactivateItem()

    res.json({ success: true, message: `Shield blocked for ${duration}ms` })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// ============================================
// CHEST MANAGEMENT
// ============================================
app.post('/action/open_chest', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const chest = bot.findBlock({ matching: b => b.name === 'chest' || b.name === 'trapped_chest' || b.name === 'barrel', maxDistance: 32 })
    if (!chest) return res.json({ success: false, message: 'No chest/barrel nearby' })

    const timeout = setTimeout(() => { bot.pathfinder.setGoal(null) }, 15000)
    await bot.pathfinder.goto(new goals.GoalNear(chest.position.x, chest.position.y, chest.position.z, 2))
    clearTimeout(timeout)

    const window = await bot.openContainer(chest)
    const items = window.containerItems().map(i => ({ name: i.name, count: i.count }))
    window.close()

    res.json({ success: true, message: `Chest contents: ${items.length} stacks`, items })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

app.post('/action/store_items', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    // Items to always keep (tools, weapons, food, building essentials)
    const keepPatterns = [
      '_pickaxe', '_sword', '_axe', '_shovel', '_hoe',
      '_helmet', '_chestplate', '_leggings', '_boots',
      'shield', 'torch', 'bucket', 'water_bucket',
      'crafting_table', 'furnace'
    ]
    const keepWithLimit = {
      'cobblestone': 32, 'dirt': 16, 'torch': 32,
      'cooked_beef': 16, 'cooked_porkchop': 16, 'cooked_chicken': 16,
      'bread': 16, 'apple': 16, 'stick': 16, 'coal': 16,
      'oak_planks': 16, 'spruce_planks': 16, 'birch_planks': 16
    }

    const chest = bot.findBlock({ matching: b => b.name === 'chest' || b.name === 'trapped_chest' || b.name === 'barrel', maxDistance: 32 })
    if (!chest) return res.json({ success: false, message: 'No chest nearby to store items' })

    const timeout = setTimeout(() => { bot.pathfinder.setGoal(null) }, 15000)
    await bot.pathfinder.goto(new goals.GoalNear(chest.position.x, chest.position.y, chest.position.z, 2))
    clearTimeout(timeout)

    const window = await bot.openContainer(chest)
    let stored = 0

    for (const item of bot.inventory.items()) {
      const isEssential = keepPatterns.some(p => item.name.includes(p))

      if (isEssential) {
        // Check if we have more than the keep limit
        const limit = keepWithLimit[item.name]
        if (limit && item.count > limit) {
          try {
            await window.deposit(item.type, null, item.count - limit)
            stored++
          } catch (e) { /* chest may be full */ }
        }
        // Essential items: keep all if no limit defined
      } else {
        // Non-essential: check if there's a keep limit
        const limit = keepWithLimit[item.name]
        if (limit && item.count > limit) {
          try {
            await window.deposit(item.type, null, item.count - limit)
            stored++
          } catch (e) {}
        } else if (!limit) {
          // No limit defined and not essential → store all
          try {
            await window.deposit(item.type, null, item.count)
            stored++
          } catch (e) {}
        }
      }
    }

    window.close()
    res.json({ success: true, message: `Stored ${stored} item stacks in chest` })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

app.post('/action/retrieve_items', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const { item_name, count = 1 } = req.body
    if (!item_name) return res.json({ success: false, message: 'item_name required' })

    const chest = bot.findBlock({ matching: b => b.name === 'chest' || b.name === 'trapped_chest' || b.name === 'barrel', maxDistance: 32 })
    if (!chest) return res.json({ success: false, message: 'No chest nearby' })

    const timeout = setTimeout(() => { bot.pathfinder.setGoal(null) }, 15000)
    await bot.pathfinder.goto(new goals.GoalNear(chest.position.x, chest.position.y, chest.position.z, 2))
    clearTimeout(timeout)

    const window = await bot.openContainer(chest)
    const item = window.containerItems().find(i => i.name === item_name)
    if (!item) {
      window.close()
      return res.json({ success: false, message: `No ${item_name} in chest` })
    }

    const retrieveCount = Math.min(count, item.count)
    await window.withdraw(item.type, null, retrieveCount)
    window.close()

    res.json({ success: true, message: `Retrieved ${retrieveCount} ${item_name} from chest` })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// ============================================
// BUCKET USAGE
// ============================================
app.post('/action/use_bucket', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const { action, x, y, z } = req.body
    // action: "fill_water", "place_water", "fill_lava", "place_lava"

    if (action === 'fill_water' || action === 'fill_lava') {
      const bucket = bot.inventory.items().find(i => i.name === 'bucket')
      if (!bucket) return res.json({ success: false, message: 'No empty bucket in inventory' })

      const targetType = action === 'fill_water' ? 'water' : 'lava'
      const source = bot.findBlock({
        matching: b => b.name === targetType || b.name === `flowing_${targetType}`,
        maxDistance: 6
      })
      if (!source) return res.json({ success: false, message: `No ${targetType} source nearby` })

      await bot.equip(bucket, 'hand')
      await bot.activateBlock(source)
      res.json({ success: true, message: `Filled bucket with ${targetType}` })
    }
    else if (action === 'place_water' || action === 'place_lava') {
      const itemName = action === 'place_water' ? 'water_bucket' : 'lava_bucket'
      const item = bot.inventory.items().find(i => i.name === itemName)
      if (!item) return res.json({ success: false, message: `No ${itemName} in inventory` })

      await bot.equip(item, 'hand')
      if (x !== undefined && y !== undefined && z !== undefined) {
        const targetBlock = bot.blockAt(new Vec3(x, y, z))
        if (targetBlock) {
          await bot.activateBlock(targetBlock)
        } else {
          bot.activateItem()
        }
      } else {
        bot.activateItem()
      }
      res.json({ success: true, message: `Placed ${itemName}` })
    }
    else {
      return res.json({ success: false, message: `Unknown bucket action: ${action}. Use: fill_water, place_water, fill_lava, place_lava` })
    }
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

app.listen(API_PORT, () => {
  console.log(`🌐 API server running on http://localhost:${API_PORT}`)
  console.log('📡 Endpoints:')
  console.log('   GET  /state         - Full world state')
  console.log('   GET  /inventory     - Inventory')
  console.log('   GET  /nearby        - Nearby blocks & entities')
  console.log('   GET  /chat          - Recent chat')
  console.log('   GET  /find_block    - Find nearest block')
  console.log('   GET  /death_log     - Death history with snapshots')
  console.log('   POST /action/move   - Move to coordinates')
  console.log('   POST /action/mine   - Mine blocks')
  console.log('   POST /action/craft  - Craft items')
  console.log('   POST /action/recipe - Look up recipe')
  console.log('   POST /action/eat    - Eat food')
  console.log('   POST /action/equip  - Equip item')
  console.log('   POST /action/attack - Attack entity')
  console.log('   POST /action/place  - Place block')
  console.log('   POST /action/sleep  - Sleep in bed')
  console.log('   POST /action/chat   - Send chat')
  console.log('   ... and more')
})

console.log(`🚀 Starting Mineflayer bot + API server...`)