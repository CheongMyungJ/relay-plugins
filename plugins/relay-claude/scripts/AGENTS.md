<!-- relay:kb:begin -->
Relay KB: `scripts/`에 묶인 제약이다. 전체는 docs/kb/INDEX.md, 조회: `python <plugin>/scripts/relay.py kb --input <json>`의 lookup(paths·terms·ids).
- C-883659bde77e469c91e0d2566483dfb3: 플러그인 패키지에는 server·session-host·web을 넣지 않고, 서버 ZIP의 app/에는 같은 커밋의 relay.json·relay_core·dispatcher·references를 함께 담으며 서버는 세션 CLI의 relay@relay 플러그인 버전이 배포물과 같을 때만 시작한다 (packaging/server_release.py, scripts/build_plugins.py, server/relay-server.spec, server/relay_server/cli.py:prerequisites)
<!-- relay:kb:end -->
