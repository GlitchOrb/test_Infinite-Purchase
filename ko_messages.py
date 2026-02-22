"""한국어 메시지 카탈로그 — UI, 텔레그램, 로그 출력 전용."""

from __future__ import annotations

# ─── 시스템 / 텔레그램 봇 메시지 ───
MESSAGES = {
    "unauthorized": "❌ 권한 없음 — 관리자 전용 명령입니다.",
    "help": "사용 가능한 명령어 안내\n/help /status /positions /balance /userinfo /kill /resume <비밀번호> /exit /set_drawdown_alert 20% /set_daily_summary 08:00",
    "kill": "🚨 긴급 정지 모드가 활성화되었습니다. 모든 매매가 중단됩니다.",
    "resume_ok": "✅ 시스템이 정상적으로 재개되었습니다.",
    "resume_fail": "❌ 재개 실패 — 포지션 불일치가 존재합니다.",
    "resume_bad_pw": "❌ 재개 실패 — 비밀번호가 올바르지 않습니다.",
    "resume_prompt": "비밀번호를 다음 메시지로 입력해주세요.",
    "exit": "봇 루프를 종료합니다.",
    "no_positions": "보유 종목이 없습니다.",
    "config_menu": "설정 메뉴",
    "daily_summary_on": "일일 요약 알림: 활성",
    "daily_summary_off": "일일 요약 알림: 비활성",
    "drawdown_set": "드로우다운 경보 기준이 설정되었습니다: {pct}",
    "summary_time_set": "일일 요약 시간 설정 완료: {hhmm}",
    "invalid_time": "시간 형식이 올바르지 않습니다. HH:MM 형식을 사용하세요.",
    "status_title": "📊 시스템 상태 보고서",
    "status_sep": "────────────────",
    "positions_title": "📈 보유 종목 현황",
    "balance_title": "💵 잔고 정보",
    "userinfo": "봇 설정 정보",
    "log_resume_verified": "재개 명령 검증 완료: {ok}",
    "log_kill": "🚨 긴급 정지 모드가 활성화되었습니다.",
    "log_regime_bull": "📢 레짐이 상승 모드로 전환되었습니다.",
    "log_regime_bear": "📢 레짐이 하락 모드로 전환되었습니다.",
    "log_trailing": "📉 트레일링 스탑 조건 충족 — 부분 청산 실행",
    "log_vampire": "🩸 수익 재투입 실행 — SOXL 평단가 하향 조정",
}

# ─── 로그인 화면 ───
LOGIN = {
    "title": "키움 트레이딩",
    "subtitle": "안전하고 편리한 해외주식 자동매매",
    "server_label": "서버 선택 (실전 / 모의)",
    "server_live": "실전 서버 (api.kiwoom.com)",
    "server_paper": "모의 서버 (mockapi.kiwoom.com)",
    "appkey_label": "앱키",
    "appkey_hint": "발급받은 앱키를 입력하세요",
    "secret_label": "시크릿",
    "secret_hint": "발급받은 시크릿을 입력하세요",
    "account_label": "계좌번호",
    "account_hint": "예: 5747-1946 또는 57471946",
    "account_desc": "숫자만 입력하세요 ( - 는 자동 제거)",
    "remember": "로그인 정보 저장",
    "btn_connect": "연결하기",
    "btn_guest": "게스트로 시작",
    "btn_telegram": "텔레그램 연결",
    "connecting": "연결 중입니다… 잠시만 기다려주세요.",
    "connect_success": "연결이 완료되었습니다.",
    "connect_fail": "연결 실패: {error}",
    "appkey_required": "앱키를 입력해주세요.",
    "secret_required": "시크릿을 입력해주세요.",
    "account_required": "계좌번호를 입력해주세요.",
    "footer": "© 2026 Alpha Predator — Kiwoom REST Trading Platform",
}

# ─── 텔레그램 다이얼로그 ───
TELEGRAM = {
    "dialog_title": "텔레그램 알림 설정",
    "desc": "봇 토큰과 채팅 ID를 입력하면\n매매 알림을 텔레그램으로 받을 수 있습니다.",
    "token_label": "봇 토큰",
    "token_hint": "@BotFather에서 발급받은 토큰",
    "chat_label": "채팅 ID",
    "chat_hint": "봇과 대화한 채팅방 ID",
    "chk_enabled": "텔레그램 알림 사용",
    "chk_remember": "설정 저장",
    "btn_test": "테스트 전송",
    "btn_save": "저장",
    "btn_close": "닫기",
    "loaded": "저장된 텔레그램 설정을 불러왔습니다.",
    "token_required": "봇 토큰을 입력해주세요.",
    "token_required_for_enable": "알림을 사용하려면 봇 토큰을 입력해주세요.",
    "test_success": "✅ 토큰 검증 성공! 테스트 메시지를 전송했습니다.",
    "test_fail": "❌ 검증 실패: {error}",
    "save_success": "✅ 텔레그램 설정이 저장되었습니다.",
    "validate_fail": "❌ 토큰 검증 실패: {error}",
    "test_msg_body": "✅ 텔레그램 테스트 메시지 — 알림이 정상 연결되었습니다.",
    "testing": "테스트 중입니다… 잠시만 기다려주세요.",
    "saving": "저장 중입니다…",
}

# ─── 토스트 / 알림 ───
TOAST = {
    "order_submitted": "주문이 접수되었습니다.",
    "order_failed": "주문 실패: {error}",
    "condition_created": "조건주문이 생성되었습니다.",
    "condition_failed": "조건주문 실패: {error}",
    "emergency_stop": "🚨 긴급 정지가 활성화되었습니다.",
    "connection_lost": "서버 연결이 끊어졌습니다.",
    "telegram_saved": "텔레그램 설정이 저장되었습니다.",
    "telegram_test_ok": "텔레그램 메시지 전송 성공",
    "telegram_test_fail": "텔레그램 메시지 전송 실패",
    "paper_reset": "모의 계좌가 초기화되었습니다.",
}

# ─── 버튼 라벨 ───
BUTTONS = {
    "status": "📊 상태보기",
    "kill": "🛑 긴급정지",
    "resume": "▶ 재개",
    "positions": "📈 보유현황",
    "config": "⚙ 설정",
    "help": "❓ 도움말",
    "refresh": "새로고침",
    "back": "뒤로",
    "toggle_summary": "일일 요약 알림 전환",
    "set_threshold": "알림 임계치 설정",
    "connect": "연결하기",
    "guest": "게스트로 시작",
    "telegram_settings": "텔레그램 연결",
    "save": "저장",
    "close": "닫기",
    "test_send": "테스트 전송",
    "remember_login": "로그인 정보 저장",
}

# ─── 레짐 / 엔진 모드 ───
REGIME_KO = {
    "BULL_ACTIVE": "상승 모드",
    "BEAR_ACTIVE": "하락 모드",
    "TRANSITION": "전환 구간",
    "NEUTRAL": "관망 모드",
}

ENGINE_MODE_KO = {
    "SOXL": "SOXL",
    "SOXS": "SOXS",
    "NONE": "대기",
}

SERVER_LABELS = {
    "live": "실전",
    "paper": "모의",
    "guest": "게스트",
}
