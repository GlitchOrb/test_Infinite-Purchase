# 마켓 데이터 시스템 아키텍처

## 데이터 플로우 다이어그램

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           TradingScreen (Main Thread)                       │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │ _start_chart_polling()                                                  │ │
│  │   ↓                                                                     │ │
│  │ PollingController  ←──────── MarketStatusManager                       │ │
│  │   │ (QTimer)                  │                                         │ │
│  │   │  정규장: 3초               │ get_status()                           │ │
│  │   │  프리/애프터: 5초           │  → MarketSession enum                  │ │
│  │   │  폐장: 중지                │  → poll_interval_ms                    │ │
│  │   │                           │  → display_label (한국어)               │ │
│  │   ↓                                                                     │ │
│  │ _on_tick()                                                              │ │
│  │   ↓                                                                     │ │
│  │ ChartDataWorker (QRunnable / QThreadPool)  ←─── _fetch_ohlcv()         │ │
│  │   │                                              │                      │ │
│  │   │ (Worker Thread)                              │ Broker Resolution:   │ │
│  │   │  ① API call                                  │  Guest → PaperBroker │ │
│  │   │  ② Extended-hours detection                  │  Paper → PaperBroker │ │
│  │   │  ③ Cache save                                │  Live  → KiwoomRest  │ │
│  │   │  ④ Signal emit                               │                      │ │
│  │   ↓                                                                     │ │
│  │ ChartDataSignals (cross-thread via pyqtSignal)                          │ │
│  │   │                                                                     │ │
│  │   ├── candles_ready(symbol, candles, is_cached)                         │ │
│  │   │     → _on_chart_candles()                                           │ │
│  │   │       → ChartDataManager.on_candles_received() [incremental merge] │ │
│  │   │       → _refresh_chart() → ChartWidget.set_candles()               │ │
│  │   │                                                                     │ │
│  │   ├── error(symbol, msg)                                                │ │
│  │   │     → _on_chart_error()                                             │ │
│  │   │       → record_failure() → toast or persistent label               │ │
│  │   │                                                                     │ │
│  │   └── extended_hours_status(symbol, supported)                          │ │
│  │         → _on_extended_status()                                         │ │
│  │           → update badge label                                          │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌──────────────────────────────────────────────────┐                       │
│  │ CandleCacheManager (SQLite)                       │                       │
│  │                                                    │                       │
│  │ candle_cache table:                                │                       │
│  │   symbol | timeframe | dt | O | H | L | C | V     │                       │
│  │                                                    │                       │
│  │ cache_meta table:                                  │                       │
│  │   symbol | last_fetch | count | extended_hours     │                       │
│  │                                                    │                       │
│  │ Features:                                          │                       │
│  │  • Upsert (동일 시간 → 업데이트, 새 시간 → 추가)   │                       │
│  │  • Auto-prune (심볼당 500개 최대)                   │                       │
│  │  • Instant load on app start                       │                       │
│  │  • Weekend/holiday access                          │                       │
│  └──────────────────────────────────────────────────┘                       │
└──────────────────────────────────────────────────────────────────────────────┘
```

## 컴포넌트 설명

### 1. `MarketStatusManager` (`market/market_status.py`)
- 미국 동부 시간 기준 시장 세션 판별
- 5가지 세션: `PRE_MARKET`, `REGULAR`, `AFTER_HOURS`, `CLOSED`, `WEEKEND`
- US 공휴일 목록 내장 (2025-2027)
- 각 세션별 폴링 간격 결정

### 2. `CandleCacheManager` (`market/candle_cache.py`)
- SQLite 테이블 2개: `candle_cache`, `cache_meta`
- `save_candles()`: 벌크 upsert
- `load_candles()`: 즉시 반환 (SELECT ORDER BY dt ASC)
- `append_or_update_candle()`: 실시간 증분 업데이트
- 심볼당 최대 500개 캔들 자동 정리

### 3. `ChartDataManager` (`market/chart_data_manager.py`)
- 2-레이어 아키텍처 오케스트레이터
- Layer A (Primary): Kiwoom REST API
- Layer B (Fallback): SQLite 캐시
- 증분 업데이트 로직: 날짜 기반 merge (중복 → update, 신규 → append)
- 연속 실패 카운터 관리
- 확장시간 지원 여부 감지

### 4. `PollingController` (`market/polling_controller.py`)
- 단일 QTimer — 중복 타이머 완전 방지
- 시장 상태에 따라 자동 간격 전환
- 에러 재시도: 실패 → 10초 후 재시도
- 3회 연속 실패 → 자동 일시정지
- 상태 시그널: `ACTIVE` | `RETRY` | `PAUSED` | `STOPPED`

### 5. `ChartDataWorker` (in `chart_data_manager.py`)
- `QRunnable` 기반 — UI 블로킹 없음
- 워커 스레드에서: API 호출 → 캐시 저장 → 시그널 emit
- 실패 시 자동으로 캐시 폴백

## 에러 핸들링 흐름

```
API 호출 실패
  │
  ├─ 캐시 데이터 있음?
  │   ├─ YES → 캐시 데이터 표시 + 토스트: "캐시 데이터 표시 중"
  │   └─ NO  → 토스트: "시세 데이터를 불러올 수 없습니다"
  │
  ├─ 연속 실패 < 3회 → 10초 후 재시도 (polling_dot: 주황색)
  │
  └─ 연속 실패 >= 3회 → 폴링 일시정지 (polling_dot: 빨강)
                        + 영구 에러 라벨 표시
                        + 토스트: "데이터 갱신 중단"
```

## UI 헤더 상태 표시

```
┌──────────────────────────────────────────────────────────────┐
│ [SOXL ▼] [Guest ▼] [☑ 자동매매] OFF                          │
│                                                              │
│ ┌──────────────────┐ ● ET 2026-02-22 10:42:15 EST           │
│ │ 미국 10:42 (정규장)│     SOXL $67.11 (+2.88%)    [설정] [초기화]│
│ └──────────────────┘                                         │
│                                                              │
│ 시장 상태 뱃지 색상:                                            │
│  🟢 정규장 — 녹색                                              │
│  🟡 프리/애프터 — 주황색                                        │
│  ⚪ 폐장/주말 — 회색                                           │
│                                                              │
│ 폴링 인디케이터 (●):                                           │
│  🟢 ACTIVE — 실시간 갱신 중                                    │
│  🟡 RETRY — 재시도 중                                          │
│  🔴 PAUSED — 일시정지                                          │
│  ⚪ STOPPED — 시장 폐장                                        │
└──────────────────────────────────────────────────────────────┘
```

## 기존 매매 로직과의 관계

```
기존 로직 (변경 없음):          새 마켓 데이터 시스템:
─────────────────────         ──────────────────────
t_quote (1.5초) .................. 유지
t_account (4초) .................. 유지
t_ohlcv (45초) ←── 제거 ──→ PollingController (3초/5초/0)
t_clock (1초) .................... 유지

BrokerBase.get_ohlcv() ←─── 동일 API 사용 ───→ ChartDataWorker._fetch_fn
PaperBroker .......................... 유지
KiwoomRestBroker ..................... 유지
ConditionEngine ...................... 유지
AutoTradingController ................ 유지
OrderPanel ........................... 유지
TapeWidget ........................... 유지
```

> 핵심: OHLCV 타이머만 PollingController로 대체.
> 나머지 모든 기존 매매 로직은 한 줄도 변경되지 않음.
