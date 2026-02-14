# Minecraft Autonomous Bot v6.4

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
| `chain_executor.py` | Chain 실행 엔진, auto-fix, 3-phase 검색 전략, 자동 장비, prerequisite injection |
| `chain_library.py` | 20개 행동 체인 정의 + SEARCH_STRATEGIES + 체인별 completion_items |
| `grand_goal.py` | Goal/Task 의존성 그래프, 자동 완료 감지, skipped task retry |
| `grand_goal_tools.py` | LangChain 도구 (goal 관리) |
| `goal_planner.py` | Task 우선순위 + step 상태 추적 |
| `server.js` | Mineflayer bot + Express REST API (20+ endpoints), 전투 AI, pathfinding |
| `experience_memory.py` | 검색 성공/LLM 솔루션 기억 → experience.json |
| `spatial_memory.py` | 위치 기억 (쉘터 max 3, crafting_table, furnace) → waypoints.json |
| `death_analyzer.py` | 죽음 스냅샷 캡처 + 교훈 추출 → death_lessons.json |
| `death_tools.py` | LangChain 도구 (죽음 분석) |
| `tools.py` | 29개 LangChain 도구 (LLM Layer 2 전용) |
| `memory_tools.py` | LangChain 도구 (위치 기억) |
| `analyze_logs.py` | 로그 분석기 → report.md 생성 |

## Data Files

| File | Content | 재시작 후 유지 |
|------|---------|:-:|
| `grand_goal_state.json` | 현재 goal 진행 상태 + skip_retry_count | Yes |
| `experience.json` | 학습된 검색 방법 + LLM 에러 솔루션 | Yes |
| `waypoints.json` | 저장된 위치 (쉘터, crafting_table, furnace) | Yes |
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
| chain timeout | `chain_executor.py` dynamic timeout 계산 | mine_block: 10s/block, smelt: 12s/item 기준 |
| 자원 검색 실패 | `chain_library.py` SEARCH_STRATEGIES + `chain_executor.py` persistent search | 3-phase: static → persistent(8회) → LLM |
| LLM 과다 호출 | `agent.py` `call_llm_planner` 조건 | auto-fix/experience 커버리지 부족일 수 있음 |
| 자동 완료 감지 오류 | `grand_goal.py` `_check_auto_complete` | 인벤토리 스캔 조건 확인 |
| REST API 에러 | `server.js` 해당 endpoint | 각 POST/GET 핸들러 |
| 장비 미착용 | `chain_executor.py` `_auto_equip_best_gear` | tier 리스트 + 호출 시점 확인 |
| move_to 경로 차단 | `server.js` `/action/move` + `chain_executor.py` | 장애물 자동 채굴 → 즉시 LLM escalation |
| skipped task 미재시도 | `grand_goal.py` `pick_next_task` | MAX_SKIP_RETRIES, skip_retry_count 확인 |

---

## Core Architecture Details

### tick_once 실행 흐름 (agent.py)
```
1. state, threat 가져오기 (GET /status, /threat_assessment)
2. auto_check_progress (인벤토리 스캔 → task 자동 완료)
3. auto_equip_best_gear (chain 시작 시)
4. Layer 0: check_instinct → HP 낮음? 밤? 크리퍼? → 즉시 실행, return
5. death 체크 → 죽었으면 LLM 분석
6. player chat → 있으면 Claude API 응답
7. Layer 1: chain active? → execute_tick()
   - needs_llm이면 escalation → LLM 솔루션 캡처 → experience.json 저장
8. Layer 2: chain 없음
   - goal 없으면 LLM에 goal 설정 요청
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

### 3-Phase Resource Search
```
Phase 1: Static strategies (chain_library.py SEARCH_STRATEGIES) — 6~12 steps
Phase 2: Persistent search (chain_executor.py) — 최대 8회 dynamic 시도
         Ores: dig_down(optimal Y) + dig_tunnel(방향 회전, 길이 증가)
         Surface: explore(30 → 120 거리 증가)
Phase 3: LLM escalation (총 ~19회 시도 후)
```

### Block Placement (server.js /action/place)
```
Phase 1: 9개 후보 위치 시도
  - 발 높이 수평 4방향 (지상)
  - 머리 높이 수평 4방향 (동굴)
  - 머리 위 1개 (수직 갱도)
Phase 2: dig-out fallback — 인접 블록 dig → air 생성 → 배치
```

### Auto-Equip Best Gear
```
호출 시점: chain 시작, chain 완료, 전투 전 (chain + instinct), mine_block 전
Tier: Diamond > Iron > Chainmail > Leather (armor)
      Diamond > Iron > Stone > Wooden (sword, pickaxe)
Slots: head, torso, legs, feet, hand (sword), off-hand (shield)
```

---

## REST API Endpoints (server.js)

**Info**:
- GET `/status` — 봇 상태 (위치, HP, 인벤토리, 시간)
- GET `/inventory` — 인벤토리 목록
- GET `/equipment` — 장착 장비
- GET `/nearby_blocks` — 주변 블록
- GET `/threat_assessment` — 위협 평가

**Actions**:
- POST `/action/mine` — 블록 채굴 `{block_type, count}`
- POST `/action/craft` — 아이템 제작 `{item_name, count}`
- POST `/action/smelt` — 제련 `{item_name, count, fuel}`
- POST `/action/place` — 블록 배치 `{block_name, x?, y?, z?}` (9-pos + dig-out)
- POST `/action/equip` — 장비 장착 `{item_name, slot}`
- POST `/action/eat` — 음식 먹기
- POST `/action/attack` — 공격 `{entity_name}` (스마트 전투 AI)
- POST `/action/move` — 이동 `{x, y, z}` (장애물 자동 채굴)

**Search**:
- POST `/action/find_block` — 블록 찾기 `{block_type, max_distance}`
- POST `/action/find_entity` — 엔티티 찾기 `{entity_type, max_distance}`

**Complex**:
- POST `/action/dig_down` — 아래로 계단식 채굴 `{depth, target_y}`
- POST `/action/dig_tunnel` — 수평 터널 `{direction, length}`
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

### v6.4 (current)
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
