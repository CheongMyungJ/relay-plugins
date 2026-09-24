<!-- relay:kb:begin -->
Relay KB: `scripts/relay_core/`에 묶인 제약이다. 전체는 docs/kb/INDEX.md, 조회: `python <plugin>/scripts/relay.py kb --input <json>`의 lookup(paths·terms·ids).
- C-03a31b36b89240eca419631cfdb335c9: 구현 보류 조건은 runs.HOLD_CONDITIONS의 다섯 코드(checks·user_or_permission·scope·basis·git)가 소유하며 drift 심각도를 보류 조건이나 승인 대기로 추가하지 않는다 (references/implementation.md, scripts/relay_core/runs.py)
- C-2036482c8ad347f3bf0406b49c656998: 서버 세션의 승인 기록 user는 고정값 relay-server이고 웹 로그인 identity는 CLI·프롬프트·helper에 넘기지 않으며, 실제 승인자는 host 저널의 seq·epoch를 서버의 입력권 구간에 대응시켜 판정한다 (references/server.md, scripts/relay_core/server_context.py, server/relay_server/controller.py, server/relay_server/receipts.py)
- C-5692a7092d3b418ea95c3f3971c279df: 쓰이지 않는 기존 .relay/transitions 파일은 저장소 초기화·설치·실행 경로에서 삭제하거나 옮기지 않으며 자동 마이그레이션·정리 명령을 두지 않는다 (scripts/relay_core/state.py)
- C-56cb7edd44854f4a96f7d59b375fecdf: 자동 실행에 번호·run·limit 같은 인자가 필요한 흐름은 next_step에 필드를 더하거나 reason에서 추출하지 않고 kb-sync 인계처럼 별도 버전 앵커로 전달한다 (scripts/relay_core/kb/handoff.py, scripts/relay_core/next_step.py)
- C-83bc434415c545d69282c2b13d298421: #24 이전 형식(conclusion.next_action·reason)으로 게시된 조사 코멘트의 복구·재개 거절은 의도된 호환 제외이며, 옛 필드 수용이나 자동 보정 게시를 추가하지 않는다 (scripts/relay_core/investigation.py:validate_result, scripts/relay_core/investigation_publishing.py:restored_summary)
- C-bacd04735efe40c895f93ccf2f9b1dd4: 서버 세션이 만든 PR은 helper 영수증을 GitHub read-back(request 마커·저장소·head/base)과 대조한 뒤에만 표시하고, 담당자는 PR이 비어 있을 때만 원본 이슈 목록을 복사하며 서버 토큰 계정 추가와 사람 편집 덮어쓰기를 하지 않는다 (scripts/relay_core/watch.py:add_assignees, scripts/relay_core/watch.py:attach_label, server/relay_server/outbox.py, server/relay_server/receipts.py)
- C-bfbd1bd69ffe48d5b14db52443b17202: GitHub 쓰기의 자동 재시도는 github.py가 HTTP 4xx 확정 거절로 분류한 github_rejected에만 허용하고, 그 밖의 github 오류는 원래 요청을 조회·조정할 불확실 상태로 다룬다 (scripts/relay_core/github.py:GitHub, scripts/relay_core/kb/remote.py)
- C-dba18e64a5244262a1822a79b0a6bb1f: runs.parse_evidence는 게시된 근거 JSON을 필드 추가·삭제 없이 그대로 반환하고, basis_summary 복원·plan_summary 제거 같은 보완은 restore에서만 한다 (scripts/relay_core/review_snapshot.py:snapshot, scripts/relay_core/runs.py:parse_evidence, scripts/relay_core/runs.py:restore)
<!-- relay:kb:end -->
