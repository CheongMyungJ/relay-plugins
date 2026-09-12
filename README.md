# Relay plugins

기획·조사·설계·계획·구현·PR·리뷰를 GitHub 이슈와 PR에 기록하는 Claude Code·Codex 플러그인입니다.
이 저장소는 검증된 설치 패키지를 배포합니다. 소스 clone이나 build는 필요하지 않습니다.

## 사전 요건

Python 3.10 이상, Git, GitHub CLI (`gh`), Claude Code 또는 Codex가 필요합니다.
실행 도구가 호스트 세션의 PATH에 있어야 하며 `gh auth login`으로 작업 대상 GitHub 계정에 로그인하세요.
작업 대상은 remote가 설정된 로컬 Git checkout입니다. 실제 작업에 필요한 이슈·PR·push 권한을 준비하세요.

## 설치

Claude Code:

```sh
claude plugin marketplace add https://github.com/CheongMyungJ/relay-plugins.git
claude plugin install relay@relay
```

Codex:

```sh
codex plugin marketplace add https://github.com/CheongMyungJ/relay-plugins.git
codex plugin add relay@relay
```

새 세션을 작업 대상 프로젝트에서 시작하세요. Claude는 `/relay:open`, Codex는 `$relay-open`으로 새 요청을 등록합니다.
이후 investigate, intent, design, plan, brief, implement, pr, review를 사용자가 각각 명시적으로 호출합니다.
GitHub 기록은 초안을 검토하고 승인한 뒤 게시합니다.

## 업데이트

```sh
# Claude Code
claude plugin marketplace update relay
claude plugin update relay@relay

# Codex
codex plugin marketplace upgrade relay
codex plugin add relay@relay
```

업데이트 후 새 세션을 시작하세요. 설치와 업데이트 명령은 최신 호스트 CLI를 기준으로 합니다.
기존 `relay-local` 사용자는 기존 플러그인을 제거한 뒤 위 원격 marketplace에서 설치하세요.
Claude: `claude plugin uninstall relay@relay-local`, Codex: `codex plugin remove relay@relay-local`.
로컬 개발용 marketplace 등록 자체는 유지해도 됩니다.

## 버전과 구성

두 호스트 패키지는 같은 버전으로 함께 배포됩니다. `release.json`에 버전과 원본 커밋 SHA가 기록됩니다.
`main`은 최신 안정 배포를 가리키고, `vMAJOR.MINOR.PATCH` 태그는 해당 배포를 고정합니다.

- `.claude-plugin/marketplace.json` → `plugins/relay-claude`
- `.agents/plugins/marketplace.json` → `plugins/relay-codex`

생성된 파일은 직접 수정하지 않습니다. 원본 저장소 `CheongMyungJ/relay`의 릴리스 workflow에서 갱신합니다.
배포 패키지는 공개되며 원본 저장소 접근 권한 없이 설치할 수 있습니다.
자동 테스트 및 설치 검증과 전체 모델 대화 시나리오 검증은 별개입니다.

MIT License. [LICENSE](LICENSE).
