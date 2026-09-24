<!-- relay:kb:begin -->
Relay KB: `references/`에 묶인 제약이다. 전체는 docs/kb/INDEX.md, 조회: `python <plugin>/scripts/relay.py kb --input <json>`의 lookup(paths·terms·ids).
- C-03a31b36b89240eca419631cfdb335c9: 구현 보류 조건은 runs.HOLD_CONDITIONS의 다섯 코드(checks·user_or_permission·scope·basis·git)가 소유하며 drift 심각도를 보류 조건이나 승인 대기로 추가하지 않는다 (references/implementation.md, scripts/relay_core/runs.py)
- C-2036482c8ad347f3bf0406b49c656998: 서버 세션의 승인 기록 user는 고정값 relay-server이고 웹 로그인 identity는 CLI·프롬프트·helper에 넘기지 않으며, 실제 승인자는 host 저널의 seq·epoch를 서버의 입력권 구간에 대응시켜 판정한다 (references/server.md, scripts/relay_core/server_context.py, server/relay_server/controller.py, server/relay_server/receipts.py)
- C-410803a951474bae8b26b7c9e7e2e498: 응답 예산(페이지 20,000자·kb 필드 8,000자)과 배치 15개·호출 limit 60/80 기본값을 바꾸면 references 계약과 함께 #29 spec·수용 기준 개정이 필요하다 (references/kb.md, scripts/relay_core/kb/lookup.py, scripts/relay_core/kb/sync.py)
- C-4b618d3dbdac40c4b4b6a9922ec6f8f7: 서버 세션 명령은 엔진의 watch=False로 조립해 --watch를 넣지 않고 완성된 문자열에서 지우지도 않으며, 서버는 사람이 지정한 라벨·assignee를 바꾸지 않는다(예외는 서버 세션이 만든 새 PR 표시뿐) (dispatcher/engine.py:DispatchEngine, dispatcher/policy.py:handoff_prompt, dispatcher/policy.py:prompt, references/server.md, server/relay_server/collect.py)
- C-9c5f8e53057d4f14b89d17a8a9a70305: helper 구현 불변식(해시 범위·상태 파일·잠금·저장 필드)은 스킬 세션에 로드되는 references/와 skills/가 아니라 docs/internals.md에 둔다 (docs/internals.md, references/, skills/)
<!-- relay:kb:end -->
