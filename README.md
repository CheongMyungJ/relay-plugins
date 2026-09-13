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

## 선택 기능: relay-dispatch

0.2.0부터 두 패키지에 `dispatcher/`와 `bin/`이 함께 들어 있습니다. `relay-dispatch`는 등록한 로컬 저장소의
`relay:watch` 라벨과 본인 단독 assignee가 있는 열린 이슈·PR을 폴링하고, 새 Relay 산출물의 다음 단계에 맞춰
Claude 또는 Codex 대화형 세션을 새 터미널 탭에 엽니다. 초안 검토와 승인은 해당 세션에서 진행합니다.
플러그인 설치만으로 디스패처가 시작되지는 않습니다. 두 도구를 사용해도 디스패처는 하나만 실행합니다.

### 최초 설정

Windows에서는 Windows Terminal(`wt`)과 PowerShell이 필요합니다. PowerShell 7(`pwsh`)을 우선 사용합니다.
Linux/macOS는 `tmux` 런처를 설정할 수 있습니다. Python은 `python --version`으로 실제 실행을 확인하세요.

먼저 설치된 패키지 루트를 확인합니다. Claude는 `claude plugin list --json`의 `relay@relay` 항목에서
`installPath`를 봅니다. Codex는 `codex plugin add relay@relay --json` 결과의 `installedPath`를 봅니다.
두 호스트 중 디스패처 실행에 사용할 패키지 하나를 선택하고, 아래 `<plugin>`을 그 절대 경로로 바꿉니다.

```text
python "<plugin>/dispatcher/relay_dispatch.py" install-shim
```

생성된 명령 폴더는 Windows의 `%USERPROFILE%\.relay-dispatch\bin`, Linux/macOS의 `~/.relay-dispatch/bin`입니다.
이 폴더를 사용자 PATH에 한 번 추가하고 새 터미널을 엽니다. Windows에서는 사용자 환경 변수의 Path에
폴더 항목만 추가합니다. 기존 Path 전체를 덮어쓰지 마세요.

```text
relay-dispatch repo add C:/git/myrepo
relay-dispatch status
relay-dispatch run
```

실제 로컬 클론 경로를 사용합니다. `run`은 현재 터미널에서 계속 실행되며 `Ctrl+C`로 종료합니다.
`~/.relay-dispatch/config.json`의 기본 호스트는 `claude`입니다. Codex를 사용하려면 `host`를 `codex`로 바꿉니다.
`repos` 항목별로도 `host`를 지정할 수 있습니다. 기본 런처는 `wt`이고 tmux 환경에서는 `launcher`를 `tmux`로 바꿉니다.
설정·장부는 패키지 밖의 `~/.relay-dispatch`에 보관되며 `RELAY_DISPATCH_HOME`으로 위치를 지정할 수 있습니다.

감시할 요청은 `/relay:open --watch 요청` 또는 `$relay-open --watch 요청`으로 등록합니다.
기존 이슈는 대상 클론에서 `relay-dispatch watch <번호>`로 표시합니다. 나중에 감시를 켠 항목은 다음 산출물부터 처리합니다.
기본 설정은 implement와 pr도 자동으로 세션을 엽니다. 수동 시작할 단계는 설정의 `gated`에 넣고,
`relay-dispatch status`에서 대기 항목을 확인한 뒤 `relay-dispatch go <번호>`로 시작합니다.

### 디스패처 업데이트

`0.2.1`부터 호스트 모드의 `relay-dispatch`는 실행할 때마다 선택된 Claude 또는 Codex의 현재 설치를 찾습니다.
아래 최초 이전을 마쳤다면 일반 플러그인 업데이트마다 `install-shim`을 다시 실행할 필요가 없습니다.

1. 실행 중인 `relay-dispatch run`을 `Ctrl+C`로 종료합니다.
2. [업데이트](#업데이트)의 명령으로 사용하는 호스트의 플러그인을 갱신합니다. Codex는 `marketplace upgrade` 다음 `plugin add`까지 실행합니다.
3. 실행 패키지의 버전과 상태를 확인한 뒤 디스패처를 다시 시작합니다.

```text
relay-dispatch package show
relay-dispatch status
relay-dispatch run
```

이미 실행 중인 디스패처에는 업데이트가 적용되지 않으므로 재시작이 필요합니다.
PATH와 감시 저장소는 다시 등록하지 않아도 됩니다. 설정·장부·로그도 유지됩니다.

#### 기존 0.2.0 래퍼의 최초 이전

`0.2.0`에서 만든 래퍼는 옛 패키지의 절대 경로를 기억합니다. 플러그인을 업데이트한 뒤
[최초 설정](#최초-설정)에서 안내한 방법으로 **새 설치 경로**를 확인하고, 아래 명령을 한 번 실행하세요.

```text
python "<새 설치 경로>/dispatcher/relay_dispatch.py" install-shim
relay-dispatch package show
```

`package` 명령을 인식하지 못하거나 옛 설치 경로가 없어 실행에 실패할 때도 같은 방법으로 이전할 수 있습니다.
새 래퍼가 표시하는 버전과 실행 루트를 확인한 뒤 `relay-dispatch run`을 다시 시작하세요.

이후 `부트스트랩 갱신 가능: install-shim` 안내가 나오는 릴리스에서는 새 설치 경로의 `install-shim`을 다시 실행합니다.
개발용 `--path` 고정 모드는 호스트 업데이트를 자동으로 따라가지 않습니다.
호스트 모드로 전환하려면 새 설치 경로에서 `install-shim --host claude` 또는 `install-shim --host codex`를 실행하세요.

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
