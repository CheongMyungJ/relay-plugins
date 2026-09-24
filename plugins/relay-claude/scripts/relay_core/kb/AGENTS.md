<!-- relay:kb:begin -->
Relay KB: `scripts/relay_core/kb/`에 묶인 제약이다. 전체는 docs/kb/INDEX.md, 조회: `python <plugin>/scripts/relay.py kb --input <json>`의 lookup(paths·terms·ids).
- C-2ad3ca7d260f4ee5a5708ca56b41c655: 머지된 PR의 kb 기록에서 기본 브랜치 직접 push가 거부되면 --pr 사용 안내로 끝내고 별도 브랜치·작은 PR로 게시 방식을 자동 전환하지 않는다 (scripts/relay_core/kb/pr_mode.py)
- C-30ab6062c04841f0a9456484a63b0693: 재확인은 항목 경로 아래 tracked 변경이 하나라도 있으면 judge이며, 같은 파일의 다른 함수·상수·import 변경을 심볼 AST 구간 비교로 auto로 낮추지 않는다 (scripts/relay_core/kb/reconfirm.py)
- C-410803a951474bae8b26b7c9e7e2e498: 페이지 20,000자·kb 필드 8,000자 예산은 식별자·커서까지 합친 최종 응답과 단일 항목에 적용하고, 예산·배치 15개·호출 limit 60/80 변경은 references와 #29 spec·수용 기준을 함께 개정한다 (references/kb.md, scripts/relay_core/kb/fragment.py, scripts/relay_core/kb/lookup.py, scripts/relay_core/kb/sync.py)
- C-53e23c649cf84aaf916c6e41a5a80597: remote 단계를 재시도하는 호출부는 저장된 계획 값(push의 expected_remote 등)을 그대로 넘기고 현재 원격 tip이나 HEAD로 계획 인자를 다시 계산하지 않는다 (scripts/relay_core/kb/remote.py:Stages.plan, scripts/relay_core/kb/sync.py:checkpoint)
- C-56cb7edd44854f4a96f7d59b375fecdf: 자동 실행에 번호·run·limit 같은 인자가 필요한 흐름은 next_step에 필드를 더하거나 reason에서 추출하지 않고 kb-sync 인계처럼 별도 버전 앵커로 전달한다 (scripts/relay_core/kb/handoff.py, scripts/relay_core/next_step.py)
- C-5702f135a1bf4ff8b65406e4cadf823b: kb-sync 호출이 limit에 닿아 인계를 게시하면 그 AI 세션은 resume·begin --resume·kb-sync 재호출로 예산을 다시 받지 않고 끝나며, 이어가기는 dispatcher가 여는 새 세션이 한다 (scripts/relay_core/kb/sync.py:resume, skills/kb-sync/SKILL.md)
- C-6f7014a13d3b440fb49eaa4519bbc5bc: 게이트 검사에 실패한 후보는 오류로 돌려주며 checkpoint·prepare를 통과시키려고 rejected로 자동 강등하지 않는다 (scripts/relay_core/kb/gates.py, scripts/relay_core/kb/sync.py:checkpoint)
- C-b6d72c0a2444405e902acfde2452c732: 인계 프로토콜 kb-sync PR 본문은 세션 종료·재개·review 신호가 아니며 본문에 relay:next를 넣지 않고 dispatcher도 그 본문을 산출물로 판정하지 않는다 (dispatcher/detect.py:from_pull_body, scripts/relay_core/kb/sync.py:body_for)
- C-bfbd1bd69ffe48d5b14db52443b17202: GitHub 쓰기의 자동 재시도는 github.py가 HTTP 4xx 확정 거절로 분류한 github_rejected에만 허용하고, 그 밖의 github 오류는 원래 요청을 조회·조정할 불확실 상태로 다룬다 (scripts/relay_core/github.py:GitHub, scripts/relay_core/kb/remote.py)
- C-d81807369818444aa2e66eca7983e2e9: kb-sync 인계 코멘트 끝줄 앵커(다섯 필드 메타데이터)와 PR 본문 run 마커 형식은 게시된 PR에서 dispatcher가 읽으므로 호환 근거 없이 바꾸지 않는다 (dispatcher/detect.py, dispatcher/engine.py, dispatcher/loop.py, scripts/relay_core/kb/handoff.py)
<!-- relay:kb:end -->
