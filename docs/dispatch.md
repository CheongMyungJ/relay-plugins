# dispatch 사용 가이드

`relay-dispatch`는 새 Relay 산출물이 게시되면 다음 스킬을 Claude Code 또는 Codex의 새 대화형 세션으로 여는 CLI입니다.
한 프로세스에서 여러 로컬 저장소를 감시하며, 스킬별로 사용할 호스트와 모델을 선택할 수 있습니다.
플러그인 설치는 [README](../README.md#설치)를 참고하세요.

- [처음 시작하기](#처음-시작하기)
- [감시할 작업 등록](#감시할-작업-등록)
- [자동 시작과 수동 시작](#자동-시작과-수동-시작)
- [스킬별 호스트와 모델](#스킬별-호스트와-모델)
- [실행 패키지 선택](#실행-패키지-선택)
- [업데이트와 이전](#업데이트와-이전)
- [명령 모음](#명령-모음)
- [문제 해결](#문제-해결)

## 동작 방식

```text
등록한 저장소의 감시 중인 이슈·PR
  → 새 Relay 산출물의 다음 단계 확인
  → 허용 전이와 auto/gated 설정 확인
  → 스킬 호출이 입력된 새 터미널 탭 열기
  → 해당 세션에서 작업·검토·게시
  → 다음 산출물을 감지하면 반복
```

감시 조건은 **열린 이슈·PR + `relay:watch` 라벨 + `gh` 로그인 사용자 한 명만 assignee**입니다.
일반 코멘트나 제목을 명령으로 실행하지 않으며, Relay 산출물에 기록된 `relay:next`를 읽습니다.
플러그인 스킬이 디스패처를 실행하지는 않습니다. 사용자가 별도 터미널에서 시작해야 합니다.

기본 설정에서는 implement와 pr도 자동으로 열립니다. 시작된 구현·PR 세션은 스킬 범위 안에서 커밋·push·PR 생성까지 진행할 수 있습니다.
Claude는 `--dangerously-skip-permissions`, Codex는 `--dangerously-bypass-approvals-and-sandbox`로 실행되어
호스트의 도구 권한 확인을 생략하며, Codex는 샌드박스 제한도 해제합니다. 재개할 때도 같은 옵션을 사용합니다.
Relay 자체의 문서 초안 검토와 게시 승인 절차는 해당 스킬의 규칙대로 진행합니다.
`gated`는 세션을 시작할 시점만 제어하며, 시작된 세션의 권한을 제한하지 않습니다.

## 처음 시작하기

### 1. 도구와 설치 경로 확인

Python 3.10 이상, Git, `gh`와 사용할 호스트 CLI가 PATH에 있어야 합니다.
`python --version`과 `gh auth status`로 실제 실행과 로그인을 확인하세요.
새 세션에서 사용할 모든 호스트에 Relay를 설치하고 호스트 로그인도 마칩니다.

Windows의 기본 런처는 Windows Terminal(`wt`)입니다. 새 탭은 PowerShell 7(`pwsh`)을 우선 사용하고,
없으면 Windows PowerShell(`powershell`)을 사용합니다.
Linux/macOS는 실행 중인 tmux 세션에서 아래 설정의 `launcher`를 `tmux`로 바꿔 사용합니다.

설치된 패키지 루트는 사용할 호스트에 맞춰 확인합니다.

| 호스트 | 확인 방법 |
| --- | --- |
| Claude Code | `claude plugin list --json`의 `relay@relay` 항목에서 `installPath` 확인 |
| Codex | `codex plugin add relay@relay --json` 결과에서 `installedPath` 확인 |

이하 `<plugin>`은 `dispatcher/relay_dispatch.py`가 들어 있는 **설치 패키지 루트의 절대 경로**입니다.
Codex의 위 명령은 플러그인 설치·갱신도 수행합니다.

### 2. 터미널 명령 등록

Claude 설치 패키지를 디스패처 실행에 사용하는 경우:

```text
python "<plugin>/dispatcher/relay_dispatch.py" install-shim --host claude
```

Codex 설치 패키지를 사용하는 경우:

```text
python "<plugin>/dispatcher/relay_dispatch.py" install-shim --host codex
```

둘 중 하나만 실행합니다. 이 선택은 디스패처 코드의 출처이며, 이후 세션을 어느 호스트로 열지는 별도 설정입니다.
Claude의 실행 패키지 자동 조회는 user 범위 설치를 지원합니다.

생성된 명령 폴더를 사용자 PATH에 한 번 추가하고 새 터미널을 엽니다.

| 환경 | 기본 명령 폴더 |
| --- | --- |
| Windows | `%USERPROFILE%\.relay-dispatch\bin` |
| Linux/macOS | `~/.relay-dispatch/bin` |

Windows에서는 사용자 환경 변수 Path에 폴더 항목을 추가합니다. 래퍼와 부트스트랩은 같은 폴더에 두고,
래퍼 파일만 다른 위치로 복사하지 않습니다. `RELAY_DISPATCH_HOME`을 설정했다면 그 경로 아래 `bin`을 사용합니다.

### 3. 저장소와 세션 호스트 설정

아래 경로를 실제 작업할 로컬 Git 클론으로 바꿉니다.

```text
relay-dispatch repo add C:/git/myrepo
relay-dispatch package show
```

`repo add` 후 생성되는 `~/.relay-dispatch/config.json`을 엽니다.
Windows 기본 경로는 `%USERPROFILE%\.relay-dispatch\config.json`입니다.
Codex로 세션을 열고 구현·PR은 직접 시작하려면 다음처럼 설정합니다.
이미 저장소를 여러 개 등록했다면 기존 `repos` 목록을 유지하세요.

```json
{
  "host": "codex",
  "launcher": "wt",
  "poll_seconds": 30,
  "gated": ["implement", "pr"],
  "defaults": {"lang": "ko"},
  "repos": [
    {"path": "C:/git/myrepo"}
  ]
}
```

Claude를 쓰려면 `host`를 `claude`로, tmux 환경이면 `launcher`를 `tmux`로 바꿉니다.
위 예시는 수동 시작 단계를 넣은 설정입니다. 기본값은 `host: claude`, `gated: []`입니다.

### 4. 디스패처 실행

```text
relay-dispatch status
relay-dispatch run
```

`run`은 현재 터미널에서 계속 실행되며 `Ctrl+C`로 종료합니다. 이 탭에서 감지·대기·실행 로그를 확인합니다.
다른 터미널에서 `status`, `go` 등의 명령을 실행할 수 있습니다.
같은 기계에서 여러 저장소나 두 호스트를 사용해도 `run`은 하나만 실행합니다.

## 감시할 작업 등록

디스패처를 실행한 뒤, 대상 프로젝트의 호스트 **대화창**에서 새 요청을 등록합니다.

```text
/relay:open --watch 검색 결과가 비어 있을 때 안내를 보여주고 싶어.
```

Codex에서는 `$relay-open --watch 검색 결과가 비어 있을 때 안내를 보여주고 싶어.`를 입력합니다.
초안을 검토하고 게시하면 다음 단계 세션이 열립니다. 새 탭의 초안 검토 질문은 그 탭에서 답합니다.

기존 이슈나 PR에 감시를 켜려면 **대상 클론 안의 터미널**에서 실행합니다.

```text
relay-dispatch watch 13
```

열한 스킬 모두 `--watch`를 지원합니다. 예를 들어 `/relay:intent 13 --watch` 또는 `$relay-brief 13 --watch`는
산출물 게시 후 해당 이슈에 라벨과 호출자 assignee를 표시합니다. 표시 실패는 게시를 취소하지 않으며 결과의 `watch.error`로 안내합니다.

저장소를 처음 등록하거나 기존 항목에 나중에 감시를 켜면 **과거 산출물을 자동 재실행하지 않습니다**.
이후 새 산출물부터 처리하므로, 이미 게시된 문서에서 이어가려면 다음 스킬을 직접 호출하세요.
디스패처가 여는 호출에는 항상 `--watch`가 붙으며, pr이 새 PR을 만들면 그 PR도 감시 대상이 됩니다.

`relay-dispatch unwatch 13`은 라벨만 제거하고 assignee는 유지합니다. 이미 열린 세션을 종료하지는 않습니다.
assignee가 여러 명이면 감시에서 제외하므로 `status`와 GitHub의 담당자 목록을 확인하세요.

## 자동 시작과 수동 시작

`auto`는 다음 단계가 허용되면 자동으로 세션을 열 단계, `gated`는 대기 명령을 만들고 사람이 `go`로 시작할 단계입니다.
기본 `auto`는 `intent`, `design`, `plan`, `brief`, `investigate`, `implement`, `pr`, `review`이며 기본 `gated`는 비어 있습니다.
자동 시작 후보에 포함되어 있어도 산출물의 다음 단계와 전이 규칙이 허용해야 실행됩니다.

`gated`만 지정하면 해당 단계는 상속한 `auto`에서 빠집니다. 같은 범위에 두 목록을 모두 명시할 때는 겹치지 않게 작성합니다.
예를 들어 첫 설정의 `gated: ["implement", "pr"]`에서는 plan 또는 brief 게시 뒤 구현이 대기합니다.

```text
relay-dispatch status
relay-dispatch go 13
```

여러 저장소에 같은 번호가 있으면 `relay-dispatch go owner/repo#13` 또는 `relay-dispatch go 13 --repo owner/repo`로 지정합니다.
`go`는 대기 중인 명령을 시작합니다. 임의의 이슈에서 다음 단계를 새로 계산하는 명령은 아닙니다.

| 현재 단계 | 허용되는 다음 단계 |
| --- | --- |
| open | intent, brief |
| intent | design, brief |
| design | plan, brief |
| plan, brief | implement |
| implement | pr |
| pr | review |
| review | 없음 |
| investigate | intent, design, brief |
| kb | review (작은 KB PR을 만든 경우만) |
| kb-sync | review |

디스패처는 `kb`와 `kb-sync`를 자동으로 열지 않으므로 필요할 때 직접 호출하세요.
`kb-sync --watch`가 끝나 draft PR을 ready로 바꾸면 그 PR의 review는 다른 PR과 같은 규칙으로 시작될 수 있습니다.
예산에 닿아 일시정지된 `kb-sync`는 이어서 열리지 않으므로 새 세션에서 `--resume <run_id>`로 직접 이어갑니다.
머지된 PR에서 `kb --pr`로 만든 작은 KB PR의 review도 직접 호출합니다.

모든 단계에서 다음 단계가 `없음`(`null`)이면 자동 진행이 끝납니다. 이것이 작업 성공을 뜻하지는 않습니다.
자기 단계 재추천과 표 밖의 전이도 실행하지 않으며 `go`용 대기로 남기지 않습니다.
`review`는 author/reviewer 모두 자동 진행의 마지막 단계입니다. 후속 조사는 사람이 직접 `investigate`를 호출합니다.
`auto`나 `gated`를 바꿔도 이 전이 규칙을 우회할 수 없습니다.

여러 문서가 한꺼번에 바뀌면 현재 유효한 문서 체인의 가장 하류 문서를 기준으로 판단합니다.
상위 문서 개정으로 하위 문서가 stale이면 마지막 변경 뒤 두 폴링 주기 동안 기다릴 수 있습니다.
대기 명령의 기준 문서가 대체되면 기존 대기는 제거하며, 실제 실행 직전에도 전이 규칙을 확인합니다.

자동 시작을 잠시 멈추려면 `relay-dispatch pause`, 다시 허용하려면 `relay-dispatch resume`을 사용합니다.
일시정지 중에도 감시는 계속되고 허용된 자동 시작이 대기열에 쌓입니다. `resume` 후 다음 주기에 실행되며,
`gated` 항목은 계속 `go`를 기다립니다. 이미 열린 세션에는 영향을 주지 않습니다.

## 스킬별 호스트와 모델

`config.json`의 전역·저장소 기본값과 `skills.<스킬명>`에 `host`, `model`을 지정할 수 있습니다.
스킬명은 접두사 없는 `design`, `implement` 등을 사용합니다. 아래 모델 문자열은 사용할 계정의 모델 ID로 바꾸세요.

```json
{
  "host": "claude",
  "model": "MY_CLAUDE_MODEL",
  "launcher": "wt",
  "gated": ["implement", "pr"],
  "skills": {
    "implement": {"host": "codex", "model": "MY_CODEX_MODEL"},
    "review": {"host": "codex", "model": null}
  },
  "repos": [
    {
      "path": "C:/git/myrepo",
      "skills": {
        "implement": {"model": "MY_OTHER_CODEX_MODEL"}
      }
    }
  ]
}
```

이 예시에서 일반 문서 단계는 Claude, implement는 Codex의 `MY_OTHER_CODEX_MODEL`, review는 모델 옵션 없이 Codex로 열립니다.
모델은 호스트 CLI의 `--model` 인자로 전달합니다. 디스패처는 모델 목록이나 계정의 사용 가능 여부를 조회하지 않습니다.

우선순위는 낮은 순서로 **전역 기본 → 저장소 기본 → 전역 skills → 저장소 skills**입니다.
전역 스킬 설정이 저장소 기본값보다 우선한다는 점에 유의하세요.
`host`와 `model`은 각각 상속하며 `model` 생략은 상속, `model: null`은 상속 해제입니다.
모델 문자열은 비어 있거나 공백·따옴표·제어 문자를 포함하거나 `-`로 시작할 수 없습니다.

모델을 지정한 시점의 호스트와 최종 호스트가 다르면 해당 모델을 무시하고 모델 옵션 없이 실행합니다.
예를 들어 전역 Claude 모델을 지정한 뒤 저장소에서 host만 Codex로 바꾸면 Claude 모델을 Codex에 전달하지 않습니다.
그 경우 `status`와 로그에 무시 사유가 표시됩니다.

설정 변경은 이후 새 산출물을 판정할 때 적용됩니다. **이미 대기 중이거나 시작된 세션은 저장된 host·model·호출을 유지**합니다.
`go`, 런처 실패 재시도, `go --resume`도 저장된 선택을 사용합니다.
`status`는 현재 설정과 실제 대기·세션의 저장된 선택을 구분해 보여 줍니다.

그 밖의 설정:

| 키 | 용도와 기본값 |
| --- | --- |
| `poll_seconds` | 폴링 간격. 기본 30초, 최소 10초 |
| `launcher` | `wt`(기본), `tmux`, `dry-run` |
| `defaults.lang` | 지원 스킬에 전달하는 언어. 기본 `ko` |
| `repos[].path` | 등록한 로컬 클론의 루트 경로 |

저장소별로 `host`, `model`, `skills`, `auto`, `gated`, `defaults`를 덮어쓸 수 있습니다.
폴링 간격과 런처는 전역 설정입니다.
세션은 등록한 클론에서 시작하고 implement 호출에는 번호와 `--watch`만 전달합니다. 스킬이 이슈 작업공간을 준비합니다.
디스패처 자체는 브랜치를 바꾸거나 worktree를 만들지 않습니다.
예전 설정의 `worktree_template`는 더 이상 쓰지 않습니다. 파일은 그대로 두고 `run`·`status`가 경고만 출력하니 필요하면 직접 지우세요.
제거된 옵션이 든 구형 implement 대기 명령은 실행 전에 저장된 번호·host와 현재 설정으로 한 번 다시 만들어지며, host·model·게이트는 유지됩니다.

`run`은 설정 파일의 변경을 다음 주기에 읽습니다. JSON 또는 설정 검증에 실패하면 직전 설정을 유지하고 로그에 알립니다.
`dry-run`은 새 탭을 열지 않고 홈의 `launches.jsonl`에 명령을 기록하지만, 폴링과 장부 갱신은 수행합니다.
시험용으로 사용하려면 별도 `RELAY_DISPATCH_HOME`을 지정하세요.

## 실행 패키지 선택

디스패처 실행 코드의 출처는 홈의 `package.json`, 세션의 호스트·모델은 홈의 `config.json`으로 관리합니다.
예를 들어 Claude 플러그인에 동봉된 디스패처 코드로 Codex 세션을 열 수 있습니다.

```text
relay-dispatch package show
relay-dispatch package use codex
relay-dispatch package use claude
```

호스트 모드는 매 실행마다 선택한 호스트의 현재 Relay 설치를 찾습니다.
조회 실패 시 옛 캐시로 자동 전환하지 않고 원인과 복구 명령을 출력합니다.
선택 변경은 다음 실행부터 적용되므로 실행 중인 `run`은 재시작하세요.

## 업데이트와 이전

1. 실행 중인 `relay-dispatch run`을 `Ctrl+C`로 종료합니다.
2. [README의 업데이트 명령](../README.md#업데이트)으로 사용하는 플러그인을 갱신합니다. Codex는 marketplace upgrade 후 plugin add까지 실행합니다.
3. 아래 명령으로 실행 패키지와 상태를 확인하고 재시작합니다.

```text
relay-dispatch package show
relay-dispatch status
relay-dispatch run
```

최초 이전을 마친 호스트 모드에서는 일반 플러그인 업데이트마다 `install-shim`을 다시 실행할 필요가 없습니다.
설정·감시 저장소·처리 이력·로그와 PATH 등록은 유지됩니다. 이미 실행 중인 프로세스에는 새 코드가 적용되지 않습니다.

### 0.2.0 래퍼에서 최초 이전

0.2.0에서 만든 래퍼는 옛 설치 절대 경로를 기억합니다.
0.2.1부터 현재 설치를 찾는 부트스트랩을 사용하므로, 플러그인을 갱신한 뒤 [설치 경로 확인](#1-도구와-설치-경로-확인) 방법으로
**새 설치 경로**를 찾고 그 경로에서 한 번 실행합니다.

```text
python "<새 설치 경로>/dispatcher/relay_dispatch.py" install-shim --host codex
relay-dispatch package show
```

Claude 설치를 쓰면 `--host claude`로 바꿉니다.
`package` 명령을 모르거나 옛 경로가 사라져 래퍼가 실행되지 않을 때도 새 엔트리의 직접 실행으로 복구합니다.
이후 릴리스에서 `부트스트랩 갱신 가능: install-shim` 안내가 나오면 새 설치 경로에서 같은 명령을 다시 실행하세요.

## 명령 모음

아래 명령은 모두 터미널에서 `relay-dispatch` 뒤에 붙여 실행합니다.

| 명령 | 동작 |
| --- | --- |
| `run` / `run --once` | 계속 폴링 / 한 주기 처리 후 종료. `--once`도 세션을 열 수 있음 |
| `status` | 로그인·설정·저장소·감시 항목·대기 명령·세션·오류 확인 |
| `watch 13` / `unwatch 13` | 현재 클론의 이슈·PR 감시 켜기 / 라벨 제거 |
| `go 13` | 대기 중인 명령 실행 또는 런처 실패 재시도 |
| `go owner/repo#13` | 저장소를 지정해 대기 명령 실행 |
| `go 13 --resume` | 기록된 세션 다시 열기 |
| `pause` / `resume` | 자동 세션 시작 일시정지 / 재허용 |
| `repo add "<경로>"` | 로컬 클론 등록. 동일 저장소의 중복 클론 등록은 거부 |
| `repo list` | 등록한 클론과 저장소 확인 |
| `repo rm "<경로>"` | 감시 저장소 등록 해제. `owner/repo`도 사용 가능 |
| `package show` | 실행 패키지·버전·선택·래퍼 상태 확인 |
| `package use claude` / `package use codex` | 디스패처 코드의 출처 호스트 선택 |
| `install-shim --host codex` | 홈에 명령 래퍼와 부트스트랩 설치·갱신 |

`go --resume`은 Claude에서 기록된 세션 ID를 다시 열고, Codex에서는 resume 선택 화면을 열어 사용자가 세션을 고르게 합니다.
디스패처는 터미널 탭 종료를 감지하지 않으므로, 닫은 탭 때문에 새 세션이 자동으로 다시 열리지는 않습니다.
`repo rm`은 커서와 처리 이력을 보존합니다. 다시 등록할 때 대화형 터미널에서는 이전 커서를 사용할지 묻습니다.

## 문제 해결

| 증상 | 확인과 조치 |
| --- | --- |
| `relay-dispatch`를 찾지 못함 | 홈의 `bin`이 PATH에 있는지 확인하고 새 터미널 열기. 필요하면 설치 엔트리로 `install-shim` 실행 |
| 플러그인을 갱신했는데 옛 버전 실행 | `package show`로 선택·실행 루트 확인. `run` 재시작, 옛 래퍼 이전, 고정 경로 모드 여부 확인 |
| 실행 패키지 조회 실패 또는 제거·손상 | 표시된 호스트에서 플러그인 재설치 후 새 설치 엔트리로 `install-shim --host ...` 실행 |
| 하향 버전 감지로 실행 거부 | 호스트 설치 버전 확인. 의도한 다운그레이드는 해당 설치 엔트리로 `package use <호스트>` 직접 실행해 다시 선택 |
| 감시 항목에 탭이 열리지 않음 | 저장소 등록, 열린 상태, 라벨, 로그인 계정의 단독 assignee, 새 산출물, next, paused/gated 순으로 확인 |
| 다음 단계가 있는데 실행 종료 | `review`, 자기 재추천, 금지 전이 또는 null인지 로그 확인. 필요한 후속 스킬을 직접 호출 |
| 기존 문서에 watch를 붙여도 반응 없음 | 과거 문서는 재실행하지 않음. 다음 스킬을 직접 호출해 이어가기 |
| 대기 명령이 없어짐 | 새 기준 문서로 대체되었는지, 대상이 닫혔는지, 전이가 금지되었는지 로그 확인 |
| 런처 실패 | `wt`·PowerShell 또는 tmux 세션, 호스트 CLI의 PATH 확인. 원인 해결 후 `go`로 재시도 |
| 모델 오류 | 설정에 쓴 모델 ID와 계정 지원 여부 확인. 기존 대기·세션은 저장된 모델을 유지하므로 로그의 명령을 수정해 직접 실행하거나 새 작업에서 변경 설정 사용 |
| 클론이 사라졌거나 remote가 바뀜 | `status`의 사유 확인 후 실제 경로·remote 복구 또는 저장소 등록 수정 |
| GitHub·네트워크 오류 | 해당 GitHub 호스트의 `gh auth status`와 연결 확인. 실패 주기는 건너뛰고 다음 주기에 재시도 |
| `state.lock` 대기 시간 초과 | 잠금을 보유한 프로세스가 실행 중인지 먼저 확인. 로그에 남은 잠금으로 안내된 경우에만 실제 프로세스 종료 여부 확인 후 정리 |

설정·상태·로그는 기본 `~/.relay-dispatch`에 보관합니다. 위치를 바꾸려면 모든 관련 터미널에서 같은 `RELAY_DISPATCH_HOME`을 사용합니다.
`dispatch.log`에는 실행 탭과 같은 사건 로그가 남고 날짜별로 회전해 7일치를 보관합니다.
변화가 없으면 매 주기 출력하지 않으며 5분마다 heartbeat, 열린 세션은 30분마다 경과를 표시합니다.
`state.json`에는 처리 이력·세션·대기 명령이 있으므로 다시 실행할 목적으로 임의 삭제하지 않습니다.

opencode는 실행·재개 명령 백엔드는 있지만 이 배포의 호스트 패키지는 Claude Code와 Codex뿐입니다.
opencode 스킬 호출과 모델 적용의 실제 세션 검증, tmux 실제 실행 검증은 완료되지 않았습니다.
상태는 한 기계에 저장되며 여러 기기 간 동기화는 지원하지 않습니다.
