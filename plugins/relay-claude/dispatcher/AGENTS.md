<!-- relay:kb:begin -->
Relay KB: `dispatcher/`에 묶인 제약이다. 전체는 docs/kb/INDEX.md, 조회: `python <plugin>/scripts/relay.py kb --input <json>`의 lookup(paths·terms·ids).
- C-068b032be9514fd287feaae7ff673200: 저장된 kb-sync 재개 명령을 실행하는 경로(go·일시정지 해제·런처 재시도·새 명령)는 loop.launch를 거쳐 PR 열림·본문 run 마커·최신 인계를 재확인하고 verified는 이번 주기 판정 직후만 쓴다 (dispatcher/cli.py:cmd_go, dispatcher/loop.py:handoff_current, dispatcher/loop.py:launch, dispatcher/loop.py:release_paused)
- C-375f46b84fda4f6f9042786264255771: Codex cachebuster로 재설치한 개발 패키지는 install-shim --path 고정 모드로 실행하고 boot의 relay.json 버전과 기록 버전 일치 검사를 완화하지 않는다 (dispatcher/boot.py:resolve, docs/install.md)
- C-4b618d3dbdac40c4b4b6a9922ec6f8f7: 서버 세션 명령은 엔진의 watch=False로 조립해 --watch를 넣지 않고 완성된 문자열에서 지우지도 않으며, 서버는 사람이 지정한 라벨·assignee를 바꾸지 않는다(예외는 서버 세션이 만든 새 PR 표시뿐) (dispatcher/engine.py:DispatchEngine, dispatcher/policy.py:handoff_prompt, dispatcher/policy.py:prompt, references/server.md, server/relay_server/collect.py)
- C-77d3bba7faa04a338b6d8e649c933b8d: 디스패처 기본 auto는 open을 제외한 모든 단계(implement·pr 포함)이며 신중한 저장소는 gated로 늦추고, 기본값에서 부작용 단계를 빼지 않는다 (dispatcher/config.py, docs/dispatcher.md)
- C-9d0db96404614c599e4b9519bfdc41f7: PR 항목의 번호 검사 예외는 검증된 paused kb-sync 인계 하나뿐이며 kb-sync의 relay.json targets를 pr로 바꿔 재개하지 않는다 (dispatcher/engine.py:DispatchEngine.judge, dispatcher/loop.py:judge, dispatcher/policy.py:resumes_run, relay.json)
- C-b26275261d274774b58744b75ecfea26: 새 자동 스킬은 DEFAULT_AUTO에만 추가하고 사용자가 auto를 직접 적은 config.json은 dispatcher가 고치거나 마이그레이션하지 않는다 (dispatcher/config.py)
- C-b6d72c0a2444405e902acfde2452c732: 인계 프로토콜 kb-sync PR 본문은 세션 종료·재개·review 신호가 아니며 본문에 relay:next를 넣지 않고 dispatcher도 그 본문을 산출물로 판정하지 않는다 (dispatcher/detect.py:from_pull_body, scripts/relay_core/kb/sync.py:body_for)
- C-d81807369818444aa2e66eca7983e2e9: kb-sync 인계 코멘트 끝줄 앵커(다섯 필드 메타데이터)와 PR 본문 run 마커 형식은 게시된 PR에서 dispatcher가 읽으므로 호환 근거 없이 바꾸지 않는다 (dispatcher/detect.py, dispatcher/engine.py, dispatcher/loop.py, scripts/relay_core/kb/handoff.py)
- C-e92f4cb1ca3d4d30adfdcd12f6ffdbf5: dispatcher·서버 세션 프롬프트에는 번호·레지스트리 스킬 이름·검증된 설정값·검증된 인계 메타데이터만 넣고 GitHub에서 읽은 제목·본문·사유 문자열과 웹 로그인 identity는 넣지 않는다 (dispatcher/config.py:option_value, dispatcher/engine.py:DispatchEngine.judge, dispatcher/loop.py:judge, dispatcher/policy.py)
- C-f044a5b6ce9143e491c14818181adcf6: kb-sync 인계 중복 억제는 산출물 digest 키가 아니라 장부 handoffs의 run별 (round, state) 순서로 판정하며, 새롭지 않은 인계는 세션·대기를 건드리지 않고 처리됨으로만 기록한다 (dispatcher/engine.py:DispatchEngine.drop_stale_handoffs, dispatcher/engine.py:DispatchEngine.pull, dispatcher/ledger.py:Ledger.record_handoff, dispatcher/loop.py:apply)
<!-- relay:kb:end -->
