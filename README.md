# Relay plugins

요청부터 조사·설계·구현·PR 리뷰까지, AI와 진행한 작업을 GitHub 이슈와 PR에 기록하는 Claude Code·Codex 플러그인입니다.
이 저장소에서 바로 설치할 수 있으며 소스 clone이나 build는 필요하지 않습니다.

- [설치](#설치)
- [사용법](#사용법)
- [dispatch 사용 가이드](docs/dispatch.md): 최초 설정, 자동 시작과 수동 시작, 스킬별 host·model, 업데이트와 복구
- [업데이트](#업데이트)

## 사전 요건

Python 3.10 이상, Git, GitHub CLI (`gh`), Claude Code 또는 Codex가 필요합니다.
사용할 도구가 호스트 세션의 PATH에 있어야 하며, 작업 대상 프로젝트의 테스트·빌드 도구도 준비하세요.
호스트 로그인과 `gh auth login`을 마친 뒤 remote가 설정된 로컬 Git checkout에서 사용합니다.
이슈·코멘트 작성, 구현 브랜치 push, PR 생성 등 사용할 기능에 필요한 GitHub 권한이 있어야 합니다.

```sh
python --version
git --version
gh auth status
git remote -v
```

GitHub Enterprise는 `gh auth login --hostname <회사 GitHub 호스트>`로 별도 로그인합니다.

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

설치 후 작업 대상 프로젝트에서 새 세션을 시작하세요. 아래 스킬 명령은 터미널 셸이 아닌 **Claude Code 또는 Codex 대화창**에 입력합니다.

## 사용법

| 작업 | Claude Code | Codex | 남기는 기록 |
| --- | --- | --- | --- |
| 새 요청 등록 | `/relay:open 요청 내용` | `$relay-open 요청 내용` | 새 이슈 |
| 원인 조사 | `/relay:investigate 13` | `$relay-investigate 13` | 재현·가설·실험 결과 코멘트 |
| 작업 정의 | `/relay:intent 13` | `$relay-intent 13` | intent 코멘트 |
| 설계 | `/relay:design 13` | `$relay-design 13` | spec 코멘트 |
| 구현 계획 | `/relay:plan 13` | `$relay-plan 13` | plan 코멘트 |
| 간소화 정의 | `/relay:brief 13` | `$relay-brief 13` | brief 코멘트 |
| 구현 | `/relay:implement 13 --worktree` | `$relay-implement 13 --worktree` | 코드·커밋·push·구현 보고 |
| PR 생성 | `/relay:pr 13` | `$relay-pr 13` | 브랜치의 실제 변경을 설명하는 PR |
| PR 리뷰 | `/relay:review 21 --reviewer` | `$relay-review 21 --reviewer` | 리뷰 초안과 선택한 수정·게시 |

`13`은 이슈번호, `21`은 PR번호의 예시입니다. `open`에는 번호를 넣지 않습니다.
`pr`과 `review`는 번호를 생략할 수 있으며 현재 브랜치 등 확인된 문맥에서 대상을 찾습니다.
기존 리뷰에 대한 작성자 대응은 `review 21 --author`로 요청합니다.

### 작업 흐름 예시

설계와 구현 계획을 나눠 검토하려면 `open → intent → design → plan → implement → pr → review` 순서로 진행합니다.
범위가 작은 작업은 `open → brief → implement → pr → review`로 진행할 수 있습니다.
원인을 먼저 확인해야 한다면 기존 이슈에서 `investigate`를 호출하고 조사 결과에 따라 다음 단계를 선택합니다.

Codex에서 간소화 경로로 진행하는 예시입니다. 각 단계의 결과를 확인한 뒤 다음 줄을 호출하세요.

```text
$relay-open 검색 결과가 비어 있을 때 안내를 보여주고 싶어.
$relay-brief 13 --lang ko 기존 화면 구성을 유지해줘.
$relay-implement 13 --worktree
$relay-pr 13
$relay-review 21 --reviewer
```

`open`이 만든 실제 이슈번호와 `pr`이 만든 실제 PR번호로 바꿉니다.
Claude Code에서는 같은 인자를 사용하고 `$relay-`를 `/relay:`로 바꾸면 됩니다.
옵션은 자연어 설명 앞에 둡니다. worktree 경로를 직접 지정하려면 `--worktree="../my-task"`처럼 씁니다.
formal 문서 묶음과 brief가 모두 있으면 implement에 `--basis formal` 또는 `--basis brief`를 지정합니다.

문서 스킬은 같은 세션에서 전체 초안을 검토하고 피드백을 반영한 뒤 확정본을 승인받아 게시합니다.
문서를 개정하면 기존 코멘트를 갱신합니다. 구현 호출은 범위 안의 개발·검증·커밋·push·보고를,
PR 호출은 필요한 일반 push와 PR 생성을 포함합니다. 리뷰는 초안을 본 뒤 게시만 할지, 선택한 수정까지 할지 결정합니다.
보류 사유나 필요한 입력이 있으면 해당 세션에서 안내합니다.

각 결과에는 **다음 단계와 사유**가 남습니다. 기본 사용에서는 사용자가 다음 스킬을 직접 호출합니다.
`review` 이후에는 자동으로 다른 단계로 이어지지 않으며, 남은 지적이나 후속 작업은 사람이 판단합니다.

## 선택 기능: dispatch

동봉된 `relay-dispatch`를 실행하면 등록한 저장소의 새 Relay 산출물을 감지해 다음 스킬 세션을 새 터미널 탭에 엽니다.
감시 대상은 `relay:watch` 라벨이 있고 `gh` 로그인 사용자가 **유일한 assignee**인 열린 이슈·PR입니다.
아홉 스킬 모두 `--watch`를 지원하며, 새 요청은 `/relay:open --watch 요청` 또는 `$relay-open --watch 요청`으로 등록합니다.

플러그인 설치만으로 디스패처가 실행되지는 않습니다. 별도 CLI에서 실행 패키지와 감시 저장소를 한 번 설정합니다.
여러 저장소와 두 호스트를 사용해도 디스패처는 하나만 실행합니다.
스킬마다 Claude·Codex와 모델을 다르게 지정할 수 있고, `gated`에 넣은 단계는 `go` 명령으로 시작할 수 있습니다.

기본값은 implement·pr도 자동 시작하며, 열린 세션은 커밋·push·PR 생성까지 진행할 수 있습니다.
Claude·Codex 세션은 도구 권한 확인을 생략하는 옵션으로 실행되고 Codex는 샌드박스도 해제합니다.
Relay의 문서 검토·게시 승인 절차는 각 스킬의 규칙을 따릅니다.

**[dispatch 사용 가이드 →](docs/dispatch.md)** 에서 설치 경로 확인부터 첫 실행, 설정 예제, 재개와 문제 해결까지 안내합니다.

## 업데이트

```sh
# Claude Code
claude plugin marketplace update relay
claude plugin update relay@relay

# Codex
codex plugin marketplace upgrade relay
codex plugin add relay@relay
```

업데이트 후 새 세션을 시작하세요. 디스패처를 사용 중이면 먼저 `Ctrl+C`로 종료하고,
플러그인 갱신 후 `relay-dispatch package show`로 확인한 다음 `relay-dispatch run`으로 재시작합니다.
옛 절대 경로 래퍼를 쓰고 있다면 [디스패처 업데이트와 이전](docs/dispatch.md#업데이트와-이전)을 먼저 확인하세요.

## 버전과 배포 구성

현재 배포 버전은 [release.json](release.json)에서 확인합니다.
두 호스트 패키지는 같은 버전으로 함께 배포되며, `main`은 최신 배포를, `vMAJOR.MINOR.PATCH` 태그는 해당 배포를 가리킵니다.

| 경로 | 용도 |
| --- | --- |
| `.claude-plugin/marketplace.json` | Claude Code 설치 카탈로그 |
| `.agents/plugins/marketplace.json` | Codex 설치 카탈로그 |
| `plugins/relay-claude` / `plugins/relay-codex` | 호스트별 스킬·런타임·디스패처 패키지 |
| [docs/dispatch.md](docs/dispatch.md) | 공개 dispatch 사용 가이드 |

이 저장소의 패키지와 문서만으로 설치하고 사용할 수 있습니다.
자동 테스트·설치 검증과 전체 모델 대화 시나리오 검증은 별개이며, 모든 호스트·환경의 전체 대화 검증이 완료된 것은 아닙니다.

[MIT License](LICENSE).
