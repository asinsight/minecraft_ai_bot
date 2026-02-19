# Minecraft Autonomous Bot v7.0

## Architecture

3-Layer Chain of Action + Learning Loop. LLM은 최후의 수단으로만 사용.
Dual LLM: Local LLM (qwen3:30b) → 액션 플래닝, Claude API → 플레이어 채팅.

```
Python (agent.py)  ←REST API (localhost:3001)→  Node.js (server.js / Mineflayer)
```

- **Layer 0 (Instinct)**: 즉시 생존 반응 (먹기, 전투, 밤 대피). LLM 없음. ~0ms.
- **Layer 1 (Chain)**: 하드코딩된 20개 행동 체인 실행 (채굴, 제작, 제련). LLM 없음. ~1-2s/step.
- **Layer 2 (Planning)**: 다음 목표 결정 + 실패 해결. Local LLM. ~5-15s.
- **Layer 2 (Chat)**: 플레이어 대화. Claude API. ANTHROPIC_API_KEY 없으면 Local LLM fallback.

---

## Key Files

| File | Role |
|------|------|
| `agent.py` | 메인 3초 틱 루프, 3-Layer 실행, LLM 호출, TeeLogger (로그 자동 저장) |
| `chain_executor.py` | Chain 실행 엔진, auto-fix, cave-first 검색, chest 루팅, crafting_table/furnace 회수, 자동 장비, prerequisite injection, abort on timeout |
| `chain_library.py` | 20개 행동 체인 정의 + SEARCH_STRATEGIES + 체인별 completion_items |
| `grand_goal.py` | GoalLibrary (파일 기반) + Goal/Task 의존성 그래프, 자동 완료 감지, 동적 goal 생성 |
| `grand_goal_tools.py` | LangChain 도구 (goal 관리) — legacy, 도구는 agent.py에 inline 정의됨 |
| `goal_planner.py` | Task 우선순위 + step 상태 추적 |
| `server.js` | Mineflayer bot + Express REST API (20+ endpoints), 전투 AI, pathfinding, abort 메커니즘, tunnelMove, failedPositions |
| `experience_memory.py` | 검색 성공/LLM 솔루션/전투 경험 기억 → experience.json |
| `spatial_memory.py` | 위치 기억 (쉘터 max 3, crafting_table, furnace, 동굴 max 10, looted_chest) → waypoints.json |
| `death_analyzer.py` | 죽음 스냅샷 캡처 + 교훈 추출 → death_lessons.json |
| `death_tools.py` | LangChain 도구 (죽음 분석) |
| `tools.py` | 29개 LangChain 도구 (LLM Layer 2 전용) |
| `memory_tools.py` | LangChain 도구 (위치 기억) |
| `analyze_logs.py` | 로그 분석기 → report.md 생성 |

## Data Files

| File | Content | 재시작 후 유지 |
|------|---------|:-:|
| `grand_goal_state.json` | 현재 goal 진행 상태 + skip_retry_count + user_requested | Yes |
| `goal_library.json` | Goal 라이브러리 (built-in 3개 + 커스텀 goals) | Yes |
| `experience.json` | 학습된 검색 방법 + LLM 에러 솔루션 | Yes |
| `waypoints.json` | 저장된 위치 (쉘터, crafting_table, furnace, 동굴, looted_chest) | Yes |
| `death_lessons.json` | 죽음 교훈 | Yes |
| `logs/bot_*.log` | 봇 실행 로그 (TeeLogger 자동 생성) | Yes |
| `report.md` | 최근 분석 보고서 (analyze_logs.py 생성) | Yes (덮어쓰기) |

---

## Log Analysis Workflow

봇 문제 진단 시 다음 순서로 진행:

### 1단계: 분석 보고서 생성
```bash
python analyze_logs.py                    # 가장 최근 로그 분석
python analyze_logs.py logs/bot_xxx.log   # 특정 로그 파일
python analyze_logs.py --last 500         # 마지막 500 tick만
```

### 2단계: report.md 확인
- **Chain Performance**: 어떤 chain이 실패하는지, 성공률, 평균 소요 tick
- **Top Errors**: 가장 빈번한 에러 메시지 (빈도 포함)
- **Stuck Loops**: 같은 step이 반복되는 구간 (낭비 tick)
- **LLM Escalations**: LLM이 호출된 이유와 빈도
- **Recommendations**: 자동 생성된 개선 제안 (문제 → 수정 위치 매핑)

### 3단계: 상세 로그 확인
report에서 문제 tick 범위를 찾았으면, 원본 로그 파일에서 해당 구간 상세 확인.

### 4단계: 코드 수정
아래 "Common Issues & Fix Locations" 참고하여 해당 파일 수정.

---

## Common Issues & Fix Locations

| Issue | Where to Fix | 설명 |
|-------|-------------|------|
| place_block 실패 (underground) | `server.js` `/action/place` (~line 706) | 9-position 후보 탐색 + dig-out fallback |
| furnace/crafting_table 없음 | `chain_executor.py` `_ensure_furnace` / `_ensure_crafting_table` | stone/dirt 채굴로 공간 확보 후 retry |
| chain timeout | `chain_executor.py` dynamic timeout 계산 | mine_block: 10s/block, smelt: 12s/item, dig_down: depth*4s |
| 자원 검색 실패 | `chain_library.py` SEARCH_STRATEGIES + `chain_executor.py` cave-first + persistent search | cave scan → remembered caves → dig/tunnel → LLM |
| LLM 과다 호출 | `agent.py` `call_llm_planner` 조건 | auto-fix/experience 커버리지 부족일 수 있음 |
| 자동 완료 감지 오류 | `grand_goal.py` `_check_auto_complete` | 인벤토리 스캔 조건 확인 |
| REST API 에러 | `server.js` 해당 endpoint | 각 POST/GET 핸들러 |
| 장비 미착용 | `chain_executor.py` `_auto_equip_best_gear` | tier 리스트 + 호출 시점 확인 |
| move_to 경로 차단 | `server.js` `/action/move` + `chain_executor.py` | 장애물 자동 채굴 → 즉시 LLM escalation |
| skipped task 미재시도 | `grand_goal.py` `pick_next_task` | MAX_SKIP_RETRIES, skip_retry_count 확인 |
| 익사/물 빠짐 | `chain_executor.py` `check_instinct` + `server.js` `/action/escape_water` | Layer 0: oxygen ≤ 12 → escape_water, Layer 1: oxygen < 10 → 체인 중단 후 탈출 |
| 몹 공격 무반응 | `chain_executor.py` `check_instinct` + `server.js` `combatState` | 실시간 공격 감지 → fight/flee/avoid 즉시 반응, 체인 자동 중단 |
| 전투 경험 미기억 | `experience_memory.py` `record_combat` | 전투 결과 (승리/도주/사망) + 위치 + 장비 기억, 위험 지역 감지 |
| HTTP timeout → 서버 루프 안 멈춤 | `chain_executor.py` `call_tool` + `server.js` `/abort` | timeout 감지 → POST /abort → abortFlag → 루프 중단 |
| pathfinder 충돌 에러 | `server.js` abortFlag 체크 + `chain_executor.py` abort_bot_action | "goal was changed" 에러 → abort로 선행 작업 정리 |
| 커스텀 goal 생성 실패 | `grand_goal.py` `GoalLibrary._validate_goal` | chain_name 유효성, 중복 id, 순환 의존성 검사 |
| goal_library.json 손상 | `grand_goal.py` `GoalLibrary._load` | corruption 시 built-in 3개로 자동 re-seed |
| 봇 stuck (안 움직임) | `chain_executor.py` `_check_stuck` + `execute_tick` | 3 tick 이상 1블록 미만 이동 → dig_down/explore 시도 |
| 인벤토리 꽉 참 | `chain_executor.py` `check_instinct` | emptySlots ≤ 3 → 근처 chest에 store_items |
| 도구 내구도 부족 | `chain_executor.py` `_auto_equip_for_mining` | durability < 10% → 다음 등급 도구로 자동 교체 |
| 용암 사고 | `server.js` `scanForLava` + `tryWaterBucketOnLava` | 채굴 전 3블록 반경 스캔 → water_bucket으로 중화 or 방향 변경 |
| 도달 불가 블록 무한루프 | `server.js` `/action/mine` failedPositions | findBlock에서 도달 실패한 위치 제외, MAX_FAILS=10 |
| 터널 내 빙글빙글 회전 | `server.js` tunnelMove helper | GoalNear(1) + 5s timeout + manual walk fallback + floored() 실제 위치 |
| crafting_table/furnace 낭비 | `chain_executor.py` execute_tick | 마지막 craft/smelt step 후 자동 회수 |
| 동굴 탐색 실패 | `chain_executor.py` `_get_ore_search_action` + `spatial_memory.py` | cave scan → remembered caves → fallback |

---

## Core Architecture Details

### tick_once 실행 흐름 (agent.py)
```
1. state, threat 가져오기 (GET /status, /threat_assessment)
2. auto_check_progress (인벤토리 스캔 → task 자동 완료)
   - equip_item step만 남았으면 chain 유지 (취소 안 함)
3. auto_equip_best_gear (chain 시작 시)
4. Layer 0: check_instinct → HP 낮음? 물? 공격받음? 전투? 밤? → 즉시 실행, return
5. death 체크 → 죽었으면 LLM 분석
6. player chat → 있으면 Claude API 응답 (request_custom_goal 가능)
7. pending goal 체크 → _pending_goal_request 있으면 LLM에 goal 생성 요청, return
8. Layer 1: chain active? → execute_tick()
   - needs_llm이면 escalation → LLM 솔루션 캡처 → experience.json 저장
   - timeout 시 abort_bot_action() → 서버 루프 중단
9. Layer 2: chain 없음
   - goal 없으면 저장된 goal 목록 표시 → LLM이 선택 or 생성
   - task 있고 chain 있으면 auto-start (LLM 없이!)
   - task 없거나 chain 없으면 LLM 호출
```

### Chain Execution Flow
```
grand_goal.py (pick_next_task)
  → chain_library.py (get chain steps)
    → chain_executor.py (execute step by step)
      → server.js REST API (actual Minecraft actions)
```

### Failure Handling (_handle_step_failure)
```
retry_count += 1
experience 솔루션 있음? → 삽입 (LLM 없이!)     ← escalation보다 먼저!
retry_count > 3? → LLM escalation → 솔루션 experience.json에 저장
place_block "no position"? → 인접 블록 채굴 → retry
move_to "blocked"? → 즉시 LLM escalation        ← retry 안 함
"crafting table" 에러? → _ensure_crafting_table (stone/dirt 채굴 + retry)
"furnace" 에러? → _ensure_furnace (stone/dirt 채굴 + retry)
"pickaxe" 에러? → prerequisite chain 삽입
그 외? → retry 대기 (다음 틱에 재시도)
```

### Learning Loop
```
Chain step 실패 → experience 확인 → auto-fix → retry 3회 → LLM escalation
                                                              ↓
                                                  LLM tool calls 캡처
                                                              ↓
                                                  experience.json에 저장
                                                              ↓
                                                  다음에 같은 에러 → LLM 없이 해결!
```

### Cave-First Resource Search
```
Phase 0: Experience memory (이전 성공 위치)
Phase 1: Static strategies (chain_library.py SEARCH_STRATEGIES) — 6~12 steps
Phase 2: Persistent search (chain_executor.py) — 최대 8회 dynamic 시도
         매 시도 전:
           Step 1: scan_caves(32) → 새 동굴 발견 → 이동 + spatial_memory 저장
           Step 2: remembered caves (spatial_memory) → 가까운 미방문 동굴 이동
           Step 3: fallback — dig_down(optimal Y) + dig_tunnel(방향 회전, 길이 증가)
         매 시도 후: _try_loot_nearby_chests() → 던전/유적 chest 루팅
Phase 3: LLM escalation (총 ~19회 시도 후)
```

### Chest Looting (_try_loot_nearby_chests)
```
탐색/채굴 액션 후 자동 실행:
  find_block(chest/trapped_chest, 16) → 발견
    → spatial_memory에 "chest" 카테고리? → skip (봇이 배치한 chest)
    → 야생 chest → move_to → open_chest → 가치 아이템 필터링
    → retrieve_items(valuable) → spatial_memory에 "looted_chest" 저장
가치 아이템: diamond, emerald, enchanted_book, golden_apple, diamond/iron 장비 등
```

### Crafting Table/Furnace Carry
```
craft_item 성공 후:
  → 다음 step도 craft_item? → 유지 (연속 제작)
  → 마지막 craft step? → mine_block(crafting_table) → 인벤토리 회수

smelt_item 성공 후:
  → 다음 step도 smelt_item? → 유지 (연속 제련)
  → 마지막 smelt step? → mine_block(furnace) → 인벤토리 회수
```

### Block Placement (server.js /action/place)
```
Phase 1: 9개 후보 위치 시도
  - 발 높이 수평 4방향 (지상)
  - 머리 높이 수평 4방향 (동굴)
  - 머리 위 1개 (수직 갱도)
Phase 2: dig-out fallback — 인접 블록 dig → air 생성 → 배치
```

### Combat Response Flow (check_instinct)
```
우선순위 (높은 순):
1. Critical HP (< 5) → 먹기/flee(공격중)/셸터
2. 익사 위험 (oxygen ≤ 12) → escape_water
3. 갑작스런 HP 감소 (≥ 4) → shield_block + fight or flee (권고 기반)
4. 공격받는 중 (combatState) → shield_block(ranged) + fight/flee/avoid (권고 기반)
5. Creeper 근접 → flee (shelter보다 빠름)
6. Warden → flee
7. Flee 권고 → flee (실패 시 shelter)
8. Fight/fight_careful 권고 → shield_block(ranged) + 8m 내 적 선제 공격
9. Avoid 권고 → 6m 내 접근 시 flee
10. 밤/황혼 → shelter
11. 배고픔 → eat
12. 쉘터 안 몹 → attack
13. 인벤토리 거의 가득 (empty ≤ 3) → store_items (chest 근처 시)
14. 근처 드롭 아이템 → collect_drops (안전할 때만)
```

### Combat State Tracking (server.js)
```
bot.on('health') → 체력 감소 감지 → 가장 가까운 적 식별
  → combatState 업데이트 (공격자, 피해량, 시간)
  → recentAttacks 배열 (최근 10회)
  → 5초 후 자동 해제 (공격 멈추면)

GET /combat_status → 전투 상태 + 추천 반응 반환
```

### Abort Mechanism (Python ↔ Node.js)
```
call_tool() timeout 발생 → "timed out" 감지
  → abort_bot_action() → POST /abort → abortFlag = true
  → bot.pathfinder.setGoal(null) (진행 중 pathfinding 즉시 중단)
  → 서버 루프 (mine/dig_down/dig_tunnel/build_shelter/branch_mine) 다음 iteration에서 체크 → 종료
  → Python 1.5초 대기 (서버 정리 시간)
  → 다음 API 요청 안전하게 실행
```

### Tunnel Navigation (tunnelMove)
```
tunnelMove(target) — dig_down/dig_tunnel/branch_mine 공용 헬퍼
  → pathfinder.goto(GoalNear(target, 1)) + 5s timeout
  → pathfinder 실패? → lookAt(target) + walk forward 800ms (manual fallback)
  → 항상 bot.entity.position.floored()로 실제 위치 확인 (phantom position 방지)
```

### Mine Endpoint — Unreachable Block Skip
```
/action/mine 에서 failedPositions Set 관리:
  → findBlock() → 도달 시도 → 실패 → failedPositions.add(pos)
  → 다음 findBlock()에서 failedPositions 제외
  → MAX_FAILS=10 → 10개 실패 시 중단
  → 매 요청마다 failedPositions 리셋 (새 시도)
```

### Dynamic Grand Goal System (GoalLibrary)
```
goal_library.json — 파일 기반 goal 저장소
  ├── 3개 built-in goals (첫 실행 시 자동 생성)
  ├── LLM이 create_custom_grand_goal()로 추가한 goals
  └── 검증: chain_name 유효성, 중복 id, 순환 의존성

유저 채팅 → request_custom_goal(desc) → _pending_goal_request
  → 다음 tick: find_similar_goals() → 유사 goal 있으면 재사용
  → 없으면: create_custom_grand_goal() → 검증 → 저장 → 활성화

user_requested 플래그:
  - True: 유저 요청 goal → 자동 선택으로 덮어쓰기 불가
  - False: LLM 자동 선택 → 유저 요청 시 교체 가능
  - goal 완료 시 False로 리셋 → 다음에 자동 선택 가능
```

### Auto-Equip Best Gear
```
호출 시점: chain 시작, chain 완료, 전투 전 (chain + instinct), mine_block 전
Tier: Diamond > Iron > Chainmail > Leather (armor)
      Diamond > Iron > Stone > Wooden (sword, pickaxe)
Slots: head, torso, legs, feet, hand (sword), off-hand (shield)
mine_block 전: durability < 10% → skip, 다음 등급 시도
heldItem fallback: autoEquipBestTool 실패 시 bot.heldItem 확인
```

---

## REST API Endpoints (server.js)

**Info**:
- GET `/status` — 봇 상태 (위치, HP, 인벤토리, 시간, 물/전투 상태)
- GET `/inventory` — 인벤토리 목록
- GET `/equipment` — 장착 장비
- GET `/nearby_blocks` — 주변 블록
- GET `/threat_assessment` — 위협 평가 (playerPower vs totalDanger → 권고)
- GET `/combat_status` — 실시간 전투 상태 (공격 감지, 최근 공격, 공격자 정보)
- GET `/scan_caves` — 동굴 감지 `{radius}` (지하 공기 블록 클러스터링 → 동굴 위치)
- GET `/pending_drops` — 수집 대기 중인 드롭 아이템

**Control**:
- POST `/abort` — 진행 중인 장시간 작업 중단 (abortFlag 설정 + pathfinder 중지)

**Actions**:
- POST `/action/mine` — 블록 채굴 `{block_type, count}` (abort 체크 + 용암 스캔 + failedPositions 스킵)
- POST `/action/craft` — 아이템 제작 `{item_name, count}`
- POST `/action/smelt` — 제련 `{item_name, count, fuel}`
- POST `/action/place` — 블록 배치 `{block_name, x?, y?, z?}` (9-pos + dig-out)
- POST `/action/equip` — 장비 장착 `{item_name, slot}`
- POST `/action/eat` — 음식 먹기
- POST `/action/attack` — 공격 `{entity_name}` (스마트 전투 AI)
- POST `/action/move` — 이동 `{x, y, z}` (장애물 자동 채굴, 스프린트 활성화)
- POST `/action/flee` — 도주 (위협 반대 방향으로 스프린트 이동)
- POST `/action/escape_water` — 물 탈출 (3-phase: 수영상승 → 육지이동 → 블록배치)
- POST `/action/shield_block` — 방패 블로킹 `{duration}` (off-hand 방패 활성화)
- POST `/action/store_items` — 비필수 아이템 chest에 저장 (자동 분류)
- POST `/action/retrieve_items` — chest에서 아이템 꺼내기 `{item_name, count}`
- POST `/action/open_chest` — 근처 chest 열기 (내용물 반환)
- POST `/action/use_bucket` — 양동이 사용 `{action}` (fill_water/place_water/fill_lava/place_lava)
- POST `/action/collect_drops` — 근처 드롭 아이템 수집

**Search**:
- POST `/action/find_block` — 블록 찾기 `{block_type, max_distance}`
- POST `/action/find_entity` — 엔티티 찾기 `{entity_type, max_distance}`

**Complex**:
- POST `/action/dig_down` — 아래로 계단식 채굴 `{depth, target_y}` (선제 용암 스캔, tunnelMove, dynamic timeout)
- POST `/action/dig_tunnel` — 수평 터널 `{direction, length}` (선제 용암 스캔, tunnelMove)
- POST `/action/branch_mine` — 가지형 채굴 `{direction, main_length, branch_length, branch_spacing}` (선제 용암 스캔, tunnelMove)
- POST `/action/bridge` — 다리 건설
- POST `/action/build_shelter` — 지상 셸터 (5x3x5 + 문)
- POST `/action/dig_shelter` — 긴급 지하 셸터 (봉인)
- POST `/action/explore` — 탐험 `{distance}`
- POST `/action/seal_mineshaft` — 수직 갱도 봉인

**Chat**:
- POST `/action/chat` — 채팅 전송 `{message}`
- GET `/chat/unread` — 읽지 않은 채팅

---

## Version History

### v7.0 (current)
- **Cave-First Search**: 자원 검색 시 동굴 우선 탐색 (scan_caves → spatial_memory 동굴 → fallback 채굴)
- **Cave Memory**: spatial_memory.py에 동굴 저장 (save_cave/get_caves_sorted, max 10), 세션 간 유지
- **Chest Looting**: 탐색 중 야생 chest 자동 감지 → 가치 아이템 루팅 (_try_loot_nearby_chests)
- **Crafting Table/Furnace Carry**: 사용 후 자동 회수 (마지막 craft/smelt step 후)
- **tunnelMove Helper**: dig_down/dig_tunnel/branch_mine 공용 — GoalNear(1) + 5s timeout + manual walk fallback
- **Unreachable Block Skip**: mine endpoint에서 도달 불가 블록 failedPositions로 건너뛰기
- **dig_down Dynamic Timeout**: 깊이 기반 timeout — max(120s, depth * 4s)
- **heldItem Fallback**: autoEquipBestTool 실패 시 bot.heldItem으로 이미 들고 있는 도구 확인
- **Auto-equip Logging**: _auto_equip_for_mining에 성공/실패/도구없음 로깅 추가
- **equip_item Step 보호**: auto_check_progress에서 equip_item만 남은 chain 취소 방지
- **_explored_caves 중복 방지**: chunk-level 키로 같은 동굴 재방문 방지 (chain 내)

### v6.9
- **4 New Actions**: Shield blocking, Chest management (open/store/retrieve), Bucket usage (fill/place water/lava), Sprint (pathfinder allowSprinting)
- **6 New Sensing**: Lava scan (preemptive, dig_down/tunnel/branch_mine), Stuck detection (3-tick position tracking), Inventory full (emptySlots ≤ 3 → store_items), Tool durability (< 10% → auto-switch), Cave detection (GET /scan_caves, underground air cluster), Drop collection (entityDead → collect_drops)
- Shield: combat 시 ranged mob (skeleton/pillager/blaze) → shield_block 후 공격
- Lava safety: scanForLava() + tryWaterBucketOnLava() → 채굴 전 용암 감지 + water_bucket 중화
- Cave detection: _get_ore_search_action() → 동굴 발견 시 이동 (blind tunneling보다 효율적)
- Instinct 우선순위: 14단계 (기존 12 + 인벤토리 꽉 참 + 드롭 수집)
- call_tool 매핑: 7개 새 도구 (shield_block, store_items, retrieve_items, open_chest, use_bucket, collect_drops, scan_caves)

### v6.8
- Abort 메커니즘: Python timeout 시 POST /abort → 서버 루프 즉시 중단
- abortFlag 체크: /action/mine, dig_down, dig_tunnel, build_shelter 루프에 삽입
- call_tool() timeout 감지 → abort_bot_action() 자동 호출 + 1.5초 정리 대기
- pathfinder 충돌 방지: abort 시 bot.pathfinder.setGoal(null) 즉시 호출

### v6.7
- Dynamic Grand Goal System: 파일 기반 GoalLibrary (goal_library.json)
- 하드코딩 GRAND_GOAL_REGISTRY 제거 → GoalLibrary._seed_builtin_goals()로 대체
- 유저 채팅 → request_custom_goal() → Planning LLM이 goal 동적 생성
- find_similar_goals(): 저장된 goal에서 유사 검색 → 재사용
- create_custom_grand_goal(): 새 goal 생성 + chain_name 검증 + 순환 의존성 검사
- user_requested 플래그: 유저 요청 goal > 자동 선택 goal 우선순위
- max_tokens 500→2000 (goal 생성 시 긴 JSON 출력 지원)
- MAX_ITERATIONS=20 (goal 생성에 충분한 LLM 호출 횟수)
- Windows UTF-8 콘솔 인코딩 수정 (이모지 출력 에러 방지)

### v6.6
- 실시간 공격 감지: bot.on('health') 체력 변화 → combatState 추적 (공격자, 피해량, 최근 공격 목록)
- GET /combat_status 엔드포인트: 전투 상태 실시간 조회
- POST /action/flee 엔드포인트: 위협 반대 방향으로 스프린트 도주
- check_instinct 전투 반응: 공격받으면 fight/flee/avoid 즉시 반응 (권고 기반)
- 체력 변화 감지: 틱 간 HP 감소 → 위협 인식 (갑작스런 4+ HP 감소 시 긴급 대응)
- 체인 실행 중 전투 중단: isUnderAttack + 2초 이내 → 체인 일시중단, 본능 반응 우선
- Creeper/Warden → shelter 대신 flee 사용 (더 빠름)
- 전투 경험 기억: 승리/도주/사망 + 몹 종류 + 장비 + 위치 → experience.json
- 위험 지역 감지: 같은 위치 근처 2회 이상 사망 시 경고
- LLM 컨텍스트에 위협/전투 정보 포함 (더 나은 플래닝)
- 실제 죽음 메시지 캡처 (하드코딩 제거)

### v6.5
- Water/Drowning 생존 시스템: 물 빠짐 감지 + 자동 탈출
- /state에 isInWater, oxygenLevel, isUnderwater 필드 추가
- /action/escape_water: 3-phase 탈출 (수영상승 → 육지이동 → 블록배치)
- Layer 0: oxygen ≤ 12 → 자동 escape_water (turtle_helmet 착용 시 ≤ 5)
- Layer 1: 체인 실행 중 oxygen < 10 → 체인 일시중단 후 탈출

### v6.4
- place_block: 4방향 → 9-position + dig-out fallback (지하 완전 지원)
- _ensure_crafting_table: dirt only → stone 먼저 시도 (지하 호환)
- _ensure_furnace: 배치 실패 시 공간 확보 retry 추가
- TeeLogger: 모든 출력을 logs/bot_*.log에 자동 저장
- analyze_logs.py: 로그 분석기 (chain 통계, 에러 패턴, stuck loop, recommendations)
- CLAUDE.md: Claude Code 자동 컨텍스트

### v6.3
- Auto-equip best gear (armor, weapon, shield) at chain start/end/combat
- Skipped task retry (MAX_SKIP_RETRIES=2)
- 3-phase resource search (static → persistent 8회 → LLM)
- Dual LLM (Local LLM + Claude API for chat)

### v6.1
- place_block 6방향 탐색 패턴 (build_shelter 기반)
- mine_block 동적 timeout (count * 8초)
- Layer 1 위치 자동 저장 (crafting_table, furnace, shelter)
- build_shelter 문 설치
- experience_memory 학습 루프 (LLM 솔루션 캡처 → 자동 재활용)
- move_to 장애물 자동 채굴 + 즉시 LLM escalation
