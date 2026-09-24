# relay-server 사용 가이드

`relay-server`는 Windows 서버 한 대에서 Relay 작업을 감시하고, 다음 스킬 세션을 서버의 Claude Code·Codex로 실행합니다.
사용자는 자기 PC의 브라우저에서 GitHub로 로그인해 그 세션을 웹 터미널로 조작합니다.
판정 규칙은 [dispatch](dispatch.md)와 같고, 초안 검토·승인은 터미널 안에서 사람이 진행합니다.
플러그인 설치는 [README](../README.md#설치)를 참고하세요.

- [구성과 설치](#구성과-설치)
- [서버 준비](#서버-준비)
- [설정](#설정)
- [실행과 명령](#실행과-명령)
- [웹 터미널 사용](#웹-터미널-사용)
- [세션 수명과 결과](#세션-수명과-결과)
- [주의와 한계](#주의와-한계)

## 구성과 설치

이 저장소의 GitHub Release에 버전별 `relay-server-<버전>-windows-x64.zip`과 `SHA256SUMS`가 첨부됩니다.
ZIP의 SHA-256을 확인한 뒤 풀면 `relay-server-<버전>/` 폴더 하나가 생깁니다.

| 경로 | 용도 |
| --- | --- |
| `server/relay-server.exe` | 감시·대기열·웹 서버(HTTPS, GitHub 로그인, 웹 터미널) |
| `host/relay-session-host.exe` | 세션마다 하나씩 뜨는 터미널 호스트. 서버를 재시작해도 CLI와 함께 유지됩니다 |
| `app/`, `web/` | 같은 버전의 Relay 판정 코드와 웹 화면 |
| `THIRD_PARTY_LICENSES.txt`, `MANIFEST.json`, `SHA256SUMS` | 제3자 라이선스, 구성 목록, 파일별 체크섬 |

업그레이드는 새 버전 폴더를 옆에 풀고 새 `relay-server.exe`로 다시 시작합니다. 실행 중인 세션은 새 서버가 이어받습니다.
서버에서 쓰는 Relay 플러그인은 ZIP과 같은 버전이어야 합니다.

## 서버 준비

1. **서버 계정.** 서버 전용 Windows 계정에 `git`, `gh`, `python`(3.x), 사용할 CLI(`claude`, `codex`)를 설치하고 CLI에 로그인합니다.
   CLI마다 이 저장소의 `relay@relay` 플러그인을 user 범위로 설치합니다([README](../README.md#설치)).
2. **서버 토큰.** 등록할 저장소에 이슈·PR 쓰기 권한이 있는 GitHub 토큰을 파일에 한 줄로 저장합니다.
   서버는 이 파일의 토큰만 사용합니다. 세션의 `git push`도 이 토큰을 쓰도록 credential helper를 설정합니다.

   ```sh
   git config --global credential.helper ""
   git config --global --add credential.helper "!gh auth git-credential"
   ```

3. **GitHub OAuth App.** Settings → Developer settings → OAuth Apps에서 앱을 만들고 callback URL을 `<서버 주소>/auth/callback`으로 둡니다.
   Client ID는 설정에 적고, Client secret은 파일에 저장합니다.
4. **HTTPS 인증서.** 서버 주소용 인증서와 키 파일을 준비합니다. HTTPS 리버스 프록시를 쓰면 `web.trusted_proxy`를 `true`로 둘 수 있습니다.
5. **로컬 디스패처 중지.** 서버로 옮길 저장소는 먼저 `relay-dispatch repo rm <경로>`로 로컬 감시에서 뺍니다.
   한 저장소를 로컬 디스패처와 서버가 함께 감시하는 구성은 지원하지 않습니다.
6. **저장소 클론.** 서버 계정으로 감시할 저장소를 클론합니다.

## 설정

서버 홈(`RELAY_SERVER_HOME`, 기본 `%USERPROFILE%\.relay-server`)에 `config.json`을 만듭니다.

```json
{
  "repos": [
    {"path": "D:\\relay\\repos\\myrepo", "local_dispatch_stopped": true}
  ],
  "allowed_users": ["alice", "bob"],
  "operator_users": ["ops-lead"],
  "gh_token_file": "D:\\relay\\secrets\\gh-token.txt",
  "web": {
    "bind": "0.0.0.0",
    "port": 8443,
    "origin": "https://relay.example.internal:8443",
    "tls_cert": "D:\\relay\\tls\\server.pem",
    "tls_key": "D:\\relay\\tls\\server-key.pem"
  },
  "oauth": {
    "client_id": "Ov23li...",
    "client_secret_file": "D:\\relay\\secrets\\oauth-client-secret.txt"
  }
}
```

- `local_dispatch_stopped: true`는 그 저장소의 로컬 디스패처를 멈췄다는 확인입니다. 서버는 같은 계정의 디스패처 설정에 같은 저장소가 있으면 시작하지 않습니다.
- `allowed_users`: 이슈·PR의 assignee 중 이 목록에 있는 사람이 한 명 이상이면 세션이 자동으로 시작됩니다. 담당자는 여러 명이어도 됩니다.
- `operator_users`: 모든 세션을 보고 조작·종료할 수 있는 운영자입니다.
- `host`, `model`, `skills`, `auto`, `gated`, `defaults`는 [dispatch 설정](dispatch.md#자동-시작과-수동-시작)과 같은 의미이며 저장소 항목에서 덮어쓸 수 있습니다.
- 그 밖에 `max_sessions`(기본 10), `poll_seconds`(30), `no_artifact_days`(7), `no_controller_grace_hours`(24), `paused`를 바꿀 수 있습니다.

서버는 실행 중 설정 파일이 바뀌면 다시 읽고, 잘못된 파일이면 이전 설정을 유지하며 로그에 알립니다.

## 실행과 명령

```text
relay-server run --check     # 준비 상태만 확인
relay-server run             # 서버 시작
relay-server status          # 실행 중·최근 세션과 대기 사유
relay-server go <실행 ID>    # gated 대기 세션 시작(운영자, 서버 콘솔)
relay-server --version
```

`run`은 시작 전에 도구·로그인·플러그인 버전·토큰·인증서·OAuth 설정을 확인하고, 문제가 있으면 목록을 출력한 뒤 종료 코드 3으로 멈춥니다.
로그는 콘솔과 `RELAY_SERVER_HOME\logs\relay-server.log`에 남습니다.

## 웹 터미널 사용

1. 설정한 서버 주소로 접속해 GitHub로 로그인합니다. 자기가 담당자인 세션만 보이며, 운영자는 모든 세션을 봅니다.
2. 세션 목록에서 터미널을 엽니다. 한 세션은 한 번에 한 연결만 입력할 수 있고, 다른 탭의 연결은 앞선 연결이 닫힐 때까지 거부됩니다.
   새로 연결하면 현재 화면이 복원됩니다.
3. 터미널의 Relay 스킬이 게시 후보 전문을 보여 주고 승인을 묻습니다. 승인은 그때 입력하던 사용자의 승인으로 서버에 기록됩니다.
4. 작업이 끝나면 CLI를 종료(`/exit`)하거나 **종료** 버튼을 누릅니다. 같은 이슈의 다음 단계는 이전 세션이 끝난 뒤 시작됩니다.

## 세션 수명과 결과

- 산출물을 게시한 세션은 마지막 입력으로부터 24시간 뒤, 산출물이 없는 세션은 7일(`no_artifact_days`) 뒤 자동으로 종료됩니다.
  자격 있는 담당자와 제어 중인 운영자가 모두 없으면 24시간(`no_controller_grace_hours`) 뒤 종료됩니다.
- 결과는 산출물을 확인하면 `completed`, 산출물 없이 CLI가 정상 종료하면 `exited`("종료 (산출물 없음)"), 종료 요청·기한이면 `cancelled`, 그 밖의 실패는 `failed`입니다.
- 서버 세션이 만든 PR에는 `relay:watch` 라벨과 원본 이슈의 담당자가 복사됩니다. 서버 토큰 계정은 복사하지 않으므로,
  개인 토큰을 쓰는 경우 PR에 담당자를 직접 지정해야 다음 단계가 자동으로 시작됩니다.
- 서버를 재시작해도 실행 중인 CLI는 계속 동작하고, 서버가 다시 연결합니다. 끝난 세션을 다시 실행하지는 않습니다.

## 주의와 한계

- 서버 세션의 CLI는 도구 권한 확인 없이 실행됩니다(Codex는 샌드박스도 해제). 웹 사용자는 서버 계정 권한으로 명령을 실행할 수 있으므로,
  서버 계정의 권한과 네트워크를 제한하고 신뢰하는 저장소와 사용자만 등록하세요.
- 터미널 출력은 서버 홈에 7일간 보관됩니다. 서버 홈은 서버 계정만 접근하도록 제한됩니다.
- Windows Server 2022·2025는 자동 테스트로 확인했으며, 깨끗한 서버 설치와 서버에서의 한글 입력 수동 확인은 아직 검증하지 않았습니다.
  opencode 세션은 검증하지 않았습니다.
