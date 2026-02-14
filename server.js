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
  version: process.env.BOT_VERSION || '1.21.4'
}

const API_PORT = parseInt(process.env.API_PORT) || 3001

// ============================================
// CREATE BOT
// ============================================
const bot = mineflayer.createBot(BOT_CONFIG)
bot.loadPlugin(pathfinder)

let botReady = false
let lastChatMessages = []

// ── Death Tracking ──
let deathLog = []
let lastHealthSnapshot = { health: 20, food: 20, position: null, entities: [], time: null }

// ============================================
// EXPRESS API SERVER
// ============================================
const app = express()
app.use(express.json())

// ── STATE ENDPOINTS ──

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
    count: item.count
  }))

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
    inventory,
    nearbyBlocks: blockNames,
    nearbyEntities,
    recentChat: lastChatMessages.slice(-10)
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

  const block = bot.findBlock({
    matching: b => b.name.includes(blockType),
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
    const dist = bot.entity.position.distanceTo(new Vec3(x, y, z))
    // Dynamic timeout: 2 seconds per block, min 15s, max 120s
    const timeoutMs = Math.max(15000, Math.min(120000, dist * 2000))

    let timedOut = false
    const timer = setTimeout(() => {
      timedOut = true
      bot.pathfinder.setGoal(null)
    }, timeoutMs)

    try {
      await bot.pathfinder.goto(new goals.GoalNear(x, y, z, range || 2))
      clearTimeout(timer)
      if (timedOut) {
        const finalDist = bot.entity.position.distanceTo(new Vec3(x, y, z)).toFixed(1)
        res.json({ success: false, message: `Movement timed out after ${Math.round(timeoutMs/1000)}s. Got within ${finalDist} blocks of target. Path may be blocked.` })
      } else {
        res.json({ success: true, message: `Moved to ${x}, ${y}, ${z}` })
      }
    } catch (pathErr) {
      clearTimeout(timer)
      const finalDist = bot.entity.position.distanceTo(new Vec3(x, y, z)).toFixed(1)
      if (timedOut) {
        res.json({ success: false, message: `Movement timed out. Got within ${finalDist} blocks. Path may be blocked — try a closer target or dig through obstacles.` })
      } else {
        res.json({ success: false, message: `Pathfinding failed (${finalDist} blocks away): ${pathErr.message}. Try moving to a closer point first.` })
      }
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

    for (let i = 0; i < mineCount; i++) {
      const block = bot.findBlock({
        matching: b => b.name.includes(block_type),
        maxDistance: 64
      })

      if (!block) {
        if (mined === 0) return res.json({ success: false, message: `No ${block_type} found nearby` })
        break
      }

      // Move to block with timeout
      const moveTimeout = setTimeout(() => { bot.pathfinder.setGoal(null) }, 15000)
      try {
        await bot.pathfinder.goto(new goals.GoalNear(block.position.x, block.position.y, block.position.z, 1))
      } catch (e) {
        // Try to proceed even if pathfinding is imperfect
      }
      clearTimeout(moveTimeout)

      // Dig
      const targetBlock = bot.blockAt(block.position)
      if (targetBlock) {
        await bot.dig(targetBlock)
        mined++
      }

      // Wait a moment for item drops
      await new Promise(resolve => setTimeout(resolve, 300))

      // Collect nearby items
      const items = Object.values(bot.entities).filter(
        e => e.name === 'item' && e.position.distanceTo(bot.entity.position) < 5
      )
      for (const item of items) {
        try {
          const collectTimeout = setTimeout(() => { bot.pathfinder.setGoal(null) }, 5000)
          await bot.pathfinder.goto(new goals.GoalNear(item.position.x, item.position.y, item.position.z, 0))
          clearTimeout(collectTimeout)
        } catch (e) { /* item may have been collected already */ }
      }
    }

    res.json({ success: true, message: `Mined ${mined} ${block_type}` })
  } catch (err) {
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

    // Find a block to place against (below the bot)
    const pos = bot.entity.position
    const referenceBlock = bot.blockAt(pos.offset(0, -1, 0))
    if (!referenceBlock || referenceBlock.name === 'air') {
      return res.json({ success: false, message: 'No solid block below to place against' })
    }

    await bot.equip(item, 'hand')
    await bot.placeBlock(referenceBlock, new Vec3(0, 1, 0))
    res.json({ success: true, message: `Placed ${block_name}` })
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
      const timeout = setTimeout(() => { bot.pathfinder.setGoal(null) }, 15000)
      await bot.pathfinder.goto(new goals.GoalNear(
        craftingTable.position.x, craftingTable.position.y, craftingTable.position.z, 2
      ))
      clearTimeout(timeout)

      recipes = bot.recipesFor(item.id, null, 1, craftingTable)
      if (recipes.length) {
        await bot.craft(recipes[0], craftCount, craftingTable)
        return res.json({ success: true, message: `Crafted ${craftCount}x ${item_name} (at crafting table)` })
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

        // Place the furnace
        const furnaceInv = bot.inventory.items().find(i => i.name === 'furnace')
        if (furnaceInv) {
          const pos = bot.entity.position.floored()
          const refBlock = bot.blockAt(pos.offset(1, -1, 0))
          if (refBlock && refBlock.name !== 'air') {
            await bot.equip(furnaceInv, 'hand')
            try {
              await bot.placeBlock(refBlock, new Vec3(0, 1, 0))
            } catch (e) { /* try alternate placement */ }
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

// POST /action/dig_shelter - Emergency shelter: dig into ground and seal
app.post('/action/dig_shelter', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const pos = bot.entity.position.floored()
    let dug = 0

    bot.chat('Digging emergency shelter!')

    // Dig a 3x3x3 room below the bot
    // Step 1: Dig down 1 block (entrance)
    const entranceBlock = bot.blockAt(pos.offset(0, -1, 0))
    if (entranceBlock && entranceBlock.name !== 'air') {
      await bot.dig(entranceBlock)
      dug++
    }

    // Step 2: Drop down
    await new Promise(resolve => setTimeout(resolve, 500))

    // Step 3: Dig a 3x3x3 chamber at current level -2
    const roomY = pos.y - 2
    for (let dx = -1; dx <= 1; dx++) {
      for (let dz = -1; dz <= 1; dz++) {
        for (let dy = 0; dy <= 2; dy++) {
          const target = new Vec3(pos.x + dx, roomY + dy, pos.z + dz)
          const block = bot.blockAt(target)
          if (block && block.name !== 'air' && block.boundingBox === 'block') {
            try {
              await bot.dig(block)
              dug++
            } catch (e) { /* some blocks may not be diggable */ }
          }
        }
      }
    }

    // Step 4: Dig the entrance shaft (2 blocks down from surface)
    for (let dy = -1; dy >= -2; dy--) {
      const shaft = bot.blockAt(new Vec3(pos.x, pos.y + dy, pos.z))
      if (shaft && shaft.name !== 'air' && shaft.boundingBox === 'block') {
        try {
          await bot.dig(shaft)
          dug++
        } catch (e) {}
      }
    }

    // Step 5: Move into the room
    try {
      const roomCenter = new Vec3(pos.x, roomY, pos.z)
      await bot.pathfinder.goto(new goals.GoalNear(roomCenter.x, roomCenter.y, roomCenter.z, 0))
    } catch (e) { /* best effort */ }

    await new Promise(resolve => setTimeout(resolve, 300))

    // Step 6: Seal the entrance (place a block above to close the 1-block entrance)
    // Find a block to place (dirt, cobblestone, anything solid)
    const sealBlocks = ['dirt', 'cobblestone', 'stone', 'andesite', 'diorite', 'granite',
      'deepslate', 'oak_planks', 'spruce_planks', 'birch_planks', 'sandstone',
      'netherrack', 'oak_log', 'spruce_log', 'birch_log']

    let sealed = false
    for (const blockName of sealBlocks) {
      const item = bot.inventory.items().find(i => i.name === blockName)
      if (item) {
        try {
          // Seal the entrance shaft from below
          const sealPos = new Vec3(pos.x, pos.y - 1, pos.z)
          const refBlock = bot.blockAt(sealPos.offset(0, -1, 0))
          if (refBlock && refBlock.name !== 'air') {
            await bot.equip(item, 'hand')
            await bot.placeBlock(refBlock, new Vec3(0, 1, 0))
            sealed = true
          }
          // Also seal the surface level
          const surfaceRef = bot.blockAt(new Vec3(pos.x, pos.y, pos.z).offset(1, -1, 0))
          if (surfaceRef && surfaceRef.name !== 'air') {
            const item2 = bot.inventory.items().find(i => i.name === blockName)
            if (item2) {
              await bot.equip(item2, 'hand')
              await bot.placeBlock(surfaceRef, new Vec3(-1, 1, 0))
            }
          }
        } catch (e) { /* sealing is best-effort */ }
        break
      }
    }

    const sealMsg = sealed ? 'Entrance sealed!' : 'Warning: could not seal entrance (no blocks to place). Place a block above to close it.'
    bot.chat(`Underground shelter ready! ${sealMsg}`)

    res.json({
      success: true,
      message: `Dug emergency underground shelter (${dug} blocks mined). ${sealMsg} You are safe from mobs down here.`
    })
  } catch (err) {
    res.json({ success: false, message: err.message })
  }
})

// POST /action/dig_down - Mine downward in a staircase pattern (for finding ores/caves)
app.post('/action/dig_down', async (req, res) => {
  if (!botReady) return res.status(503).json({ error: 'Bot not ready' })
  try {
    const { depth, target_y } = req.body
    const maxDepth = depth || 10
    const pos = bot.entity.position.floored()
    const targetY = target_y || (pos.y - maxDepth)
    let dug = 0
    let currentY = pos.y

    bot.chat(`Mining downward to y=${targetY}...`)

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
      const dir = directions[dirIndex % 4]

      // Dig forward (2 blocks high for the player)
      const forward = currentPos.offset(dir.x, 0, dir.z)
      const forwardUp = currentPos.offset(dir.x, 1, dir.z)

      const b1 = bot.blockAt(forward)
      const b2 = bot.blockAt(forwardUp)

      if (b1 && b1.name !== 'air' && b1.boundingBox === 'block') {
        try { await bot.dig(b1); dug++ } catch (e) {}
      }
      if (b2 && b2.name !== 'air' && b2.boundingBox === 'block') {
        try { await bot.dig(b2); dug++ } catch (e) {}
      }

      // Dig down
      const below = currentPos.offset(dir.x, -1, dir.z)
      const b3 = bot.blockAt(below)
      if (b3 && b3.name !== 'air' && b3.boundingBox === 'block') {
        try { await bot.dig(b3); dug++ } catch (e) {}
      }

      // Move to new position
      currentPos = below
      try {
        await bot.pathfinder.goto(new goals.GoalNear(currentPos.x, currentPos.y, currentPos.z, 0))
      } catch (e) {
        // If pathfinding fails, try simple movement
        await new Promise(resolve => setTimeout(resolve, 300))
      }

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

    let currentPos = pos.clone()
    const oresFound = {}

    for (let i = 0; i < tunnelLength; i++) {
      const next = currentPos.offset(dir.x, 0, dir.z)

      // Dig 2-high tunnel (1x2)
      for (let dy = 0; dy <= 1; dy++) {
        const target = next.offset(0, dy, 0)
        const block = bot.blockAt(target)
        if (block && block.name !== 'air' && block.boundingBox === 'block') {
          // Track ores found
          if (block.name.includes('ore')) {
            oresFound[block.name] = (oresFound[block.name] || 0) + 1
          }

          // Safety: don't dig into lava/water
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
      currentPos = next
      try {
        await bot.pathfinder.goto(new goals.GoalNear(currentPos.x, currentPos.y, currentPos.z, 0))
      } catch (e) {
        await new Promise(resolve => setTimeout(resolve, 300))
      }
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

    // STEP 4: Door — break 2 blocks on the north wall for entry
    try {
      const door1 = bot.blockAt(new Vec3(bx, by, bz - S))
      const door2 = bot.blockAt(new Vec3(bx, by + 1, bz - S))
      if (door1 && door1.name !== 'air') await bot.dig(door1)
      if (door2 && door2.name !== 'air') await bot.dig(door2)
    } catch (e) {}

    // Move back inside
    try {
      await bot.pathfinder.goto(new goals.GoalNear(bx, by, bz, 0))
    } catch (e) {}

    bot.chat(`Shelter built! ${placed} blocks placed.`)
    res.json({
      success: true,
      message: `Built 5x3x5 shelter with ${placed} blocks (${materialName}) at (${bx}, ${by}, ${bz}). Roof complete. Door on north side.${failed > 0 ? ` (${failed} blocks couldn't be placed)` : ''}`
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
  bot.pathfinder.setMovements(new Movements(bot, mcData))

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

// ── Death Tracking: health snapshot every update ──
bot.on('health', () => {
  const pos = bot.entity.position
  const nearbyEntities = Object.values(bot.entities)
    .filter(e => e !== bot.entity && e.position.distanceTo(pos) < 20)
    .map(e => ({
      type: e.name || e.username || 'unknown',
      distance: parseFloat(e.position.distanceTo(pos).toFixed(1))
    }))
    .sort((a, b) => a.distance - b.distance)

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

// ── Death Tracking: capture death with pre-death snapshot ──
bot.on('death', () => {
  const deathEntry = {
    timestamp: Date.now() / 1000,
    message: 'death',
    health_before: lastHealthSnapshot.health,
    hunger_before: lastHealthSnapshot.food,
    position: lastHealthSnapshot.position,
    nearby_entities: lastHealthSnapshot.entities,
    time_of_day: lastHealthSnapshot.time,
  }

  deathLog.push(deathEntry)
  if (deathLog.length > 50) deathLog.shift()

  console.log('💀 Bot died!')
  console.log(`   Last health: ${lastHealthSnapshot.health}/20`)
  console.log(`   Nearby: ${lastHealthSnapshot.entities.map(e => `${e.type}(${e.distance}m)`).join(', ') || 'none'}`)
  console.log(`   Time: ${lastHealthSnapshot.time}`)

  bot.chat('I died... respawning!')
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