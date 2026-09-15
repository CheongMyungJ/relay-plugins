<!-- relay:kb:begin -->
Relay KB: `references/`에 묶인 제약이다. 전체는 docs/kb/INDEX.md, 조회: `python <plugin>/scripts/relay.py kb --input <json>`의 lookup(paths·terms·ids).
- C-03a31b36b89240eca419631cfdb335c9: 구현 보류 조건은 runs.HOLD_CONDITIONS의 다섯 코드(checks·user_or_permission·scope·basis·git)가 소유하며 drift 심각도를 보류 조건이나 승인 대기로 추가하지 않는다 (references/implementation.md, scripts/relay_core/runs.py)
- C-410803a951474bae8b26b7c9e7e2e498: 응답 예산(페이지 20,000자·kb 필드 8,000자)과 배치 15개·호출 limit 60/80 기본값을 바꾸면 references 계약과 함께 #29 spec·수용 기준 개정이 필요하다 (references/kb.md, scripts/relay_core/kb/lookup.py, scripts/relay_core/kb/sync.py)
- C-9c5f8e53057d4f14b89d17a8a9a70305: helper 구현 불변식(해시 범위·상태 파일·잠금·저장 필드)은 스킬 세션에 로드되는 references/와 skills/가 아니라 docs/internals.md에 둔다 (docs/internals.md, references/, skills/)
<!-- relay:kb:end -->
