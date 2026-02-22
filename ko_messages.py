"""Korean i18n message catalog for Telegram/UI/log outputs."""

from __future__ import annotations

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
}

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
