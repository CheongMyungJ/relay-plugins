"""relay-dispatch commands: run, status, go, pause, resume, watch, unwatch, repo, install-shim."""
import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

import relay_core
from relay_core import RelayError, watch
from relay_core.github import GitHub
from relay_core.state import read_json, write_json
from . import boot, config as configuration, launcher as launchers, log as logs, loop
from .ledger import Ledger, iso, item_key, parse
from .launcher import resume_hint, shell_text

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "dispatcher" / "relay_dispatch.py"
REGISTRY_PATH = ROOT / "relay.json"
COMMAND_WAIT = 90.0  # seconds a one-shot command waits for `run` to finish its cycle before giving up

CMD_WRAPPER = '@echo off\r\npython "%~dp0relay_dispatch_boot.py" %*\r\n'
SH_WRAPPER = '#!/bin/sh\nexec python "$(dirname "$0")/relay_dispatch_boot.py" "$@"\n'


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


class Env:
    """Everything a command needs; tests replace the remote, launcher and clock."""

    def __init__(self, *, out, cwd, registry, remote_for, launcher_for, clock, stdin_interactive):
        self.out = out
        self.cwd = Path(cwd)
        self.registry = registry
        self.remote_for = remote_for
        self.launcher_for = launcher_for
        self.clock = clock
        self.stdin_interactive = stdin_interactive
        self.home = configuration.home()
        self.ledger = Ledger(self.home)


def github_for(found):
    return GitHub(found["slug"], host=found["host"])


def parse_target(text):
    """`7` or `owner/repo#7`."""
    slug, sep, number = text.rpartition("#")
    if not sep:
        slug, number = None, text
    if not number.isdigit() or int(number) < 1:
        raise RelayError("input", "use a positive issue/PR number, optionally as owner/repo#number")
    return slug, int(number)


def registered(env, config):
    """Registered repositories with their identity, skipping unreadable clones."""
    result = []
    for entry in config["repos"]:
        try:
            found = configuration.identity(entry["path"])
            result.append((entry, found, None))
        except RelayError as exc:
            result.append((entry, None, str(exc)))
    return result


def cwd_repository(env, config):
    try:
        found = configuration.identity(env.cwd)
    except RelayError:
        return None
    for entry, known, _ in registered(env, config):
        if known and configuration.slug_key(known["host"], known["slug"]) == configuration.slug_key(found["host"], found["slug"]):
            return known
    return None


def context(env, config, launcher=None):
    return loop.Context(config=config, ledger=env.ledger, launcher=launcher or env.launcher_for(config["launcher"], env.home),
                        log=logs.Log(env.home, write=env.out, clock=env.clock), clock=env.clock, registry=env.registry,
                        login=env.ledger.data.get("login") if env.ledger.data else None, remote_for=env.remote_for)


def start_package(env, *, record_last=True):
    env.selected, env.resolved, env.package_error = None, None, None
    try:
        chosen, signature = boot.read_selection(env.home)
        env.selected = chosen
        try:
            inherited = json.loads(os.environ.get("RELAY_DISPATCH_RESOLVED", "null"))
        except ValueError:
            inherited = None
        if (isinstance(inherited, dict) and inherited.get("selection_hash") == signature
                and all(isinstance(inherited.get(k), str) for k in ("root", "version", "source"))
                and Path(inherited["root"]).is_absolute()):
            env.resolved = inherited
        else:
            env.resolved = boot.resolve(chosen)
        if record_last and Path(env.resolved["root"]).resolve() == ROOT:
            # A concurrent selection change must not be replaced by this old execution.
            current, latest_hash = boot.read_selection(env.home)
            if latest_hash == signature:
                current["last"] = {"root": env.resolved["root"], "version": env.resolved["version"], "at": iso(env.clock())}
                try:
                    write_json(env.home / "package.json", current)
                except OSError as exc:
                    # The last-run record is a secondary defense; failing to write it must not stop the command.
                    env.out(f"경고: last 기록 실패 ({env.home / 'package.json'}): {exc}")
    except boot.BootError as exc:
        env.package_error = exc


def package_line(env):
    actual_version = read_json(REGISTRY_PATH).get("version", "(없음)")
    return f"패키지: {boot.describe(env.selected or {})} 실행 버전 {actual_version} 실행 루트 {ROOT}"


def check_wrapper(env):
    for name in ("relay-dispatch.cmd", "relay-dispatch"):
        wrapper = env.home / "bin" / name
        if wrapper.exists() and "relay_dispatch.py" in wrapper.read_text(encoding="utf-8"):
            env.out(f"래퍼 이전 필요: install-shim ({wrapper})")
    copied = env.home / "bin/relay_dispatch_boot.py"
    if not copied.exists():
        env.out("부트스트랩 없음: install-shim")
    elif copied.read_bytes() != (ROOT / "dispatcher/boot.py").read_bytes():
        env.out("부트스트랩 갱신 가능: install-shim")
    if env.package_error:
        env.out(f"경고: {env.package_error}")
    elif env.resolved and Path(env.resolved["root"]).resolve() != ROOT:
        env.out(f"경고: 선택 루트 {env.resolved['root']} != 실행 루트 {ROOT}; last는 유지한다")


def cmd_run(args, env):
    live = configuration.Config(env.registry)
    live.load()
    if live.error:
        raise RelayError("input", "config.json: " + live.error)
    config = live.value
    if not config["repos"]:
        env.out("감시할 저장소가 없다. `relay-dispatch repo add <clone-path>` 먼저 실행하라.")
    for warning in configuration.legacy_warnings(config):
        env.out("경고: " + warning)
    check_wrapper(env)
    hosts = [found["host"] for _, found, _ in registered(env, config) if found] or ["github.com"]
    login = env.remote_for({"slug": "", "host": hosts[0]}).viewer()
    with env.ledger.locked(timeout=COMMAND_WAIT):
        env.ledger.data["login"] = login
        env.ledger.save()
    ctx = context(env, config)
    ctx.login = login
    ctx.log.line(package_line(env))
    ctx.log.line(f"relay-dispatch 시작: 로그인 {login}, 저장소 {len(config['repos'])}개, launcher {config['launcher']}, "
                 f"host {config.get('host', 'claude')}, poll {config['poll_seconds']}s, 설정 {live.file}")
    live.mtime = None  # let the loop announce the applied configuration once
    stop = (lambda: True) if args.once else (lambda: False)
    if args.once:
        live.load()
        ctx.config = live.value
        with env.ledger.locked(timeout=COMMAND_WAIT):
            loop.cycle(ctx)
            env.ledger.save()
        return 0
    try:
        loop.run(ctx, live, sleep=time.sleep, stop=stop)
    except KeyboardInterrupt:
        ctx.log.line("relay-dispatch 종료")
    return 0


def elapsed_text(started, now):
    minutes = int((now - parse(started)).total_seconds() // 60)
    return f"{minutes // 60}시간 {minutes % 60}분" if minutes >= 60 else f"{minutes}분"


def selection_text(entry):
    model = entry.get("model")
    text = f"host {entry['host']} model {model if model is not None else '호스트 기본(모델 옵션 없음)'}"
    selection = entry.get("selection")
    if selection:
        text += f" [host 출처 {selection['host_scope']}, model 출처 {selection['model_scope'] or '-'}]"
        if selection["model_state"] == "host-mismatch":
            text += (f" 요청 모델 {selection['requested_model']} 무시: 출처 host {selection['model_host']}"
                     f" != 최종 host {entry['host']}")
        elif selection["model_state"] == "cleared":
            text += " (null: 상속 해제)"
    return text


def cmd_status(args, env):
    check_wrapper(env)
    config = configuration.read(env.registry)
    env.ledger.load()
    data, now = env.ledger.data, env.clock()
    env.out(f"로그인: {data.get('login') or '(run을 아직 실행하지 않음)'}  설정: {configuration.path()}  paused: {config['paused']}")
    env.out(f"launcher: {config['launcher']}  host: {config.get('host', 'claude')}  poll: {config['poll_seconds']}s  auto: {', '.join(config['auto'])}  gated: {', '.join(config['gated']) or '-'}")
    for warning in configuration.legacy_warnings(config):
        env.out("경고: " + warning)
    env.out("모델 표시는 디스패처가 전달하는 선택이며 호스트 내부 모델의 실시간 조회가 아니다.")
    env.out("전역 기본: " + selection_text(configuration.session_settings(config, {}, None)))
    for stage in sorted(config.get("skills", {})):
        env.out(f"  전역 스킬 {stage}: " + selection_text(configuration.session_settings(config, {}, stage)))
    launcher = env.launcher_for(config["launcher"], env.home)
    env.out("저장소:")
    for entry, found, error in registered(env, config):
        if error:
            env.out(f"  {entry['path']}: 건너뜀 ({error})")
            continue
        key = configuration.slug_key(found["host"], found["slug"])
        settings = configuration.settings(config, entry)
        items = data["watched"].get(key, [])
        env.out(f"  {found['slug']} ({entry['path']}) host {settings['host']} gated {', '.join(settings['gated']) or '-'} 커서 {data['cursors'].get(key) or '(없음, 현재 시각부터)'}")
        env.out("    레포 기본: " + selection_text(configuration.session_settings(config, entry, None)))
        for stage in sorted(set(config.get("skills", {})) | set(entry.get("skills", {}))):
            env.out(f"    스킬 {stage}: " + selection_text(configuration.session_settings(config, entry, stage)))
        for item in items:
            env.out(f"    #{item['number']} {item['kind']} {logs.clip(item.get('title'))!r} {item['status']}")
    sessions = env.ledger.open_sessions()
    env.out("열린 세션:" if sessions else "열린 세션: 없음")
    for item, session in sessions.items():
        env.out(f"  {item} {session['stage']} 저장 선택 ({selection_text(session)}) 시작 후 {elapsed_text(session['started_at'], now)} "
                f"session {session['session_id']} | relay-dispatch go {item} --resume")
    env.out("게이트 대기:" if data["pending"] else "게이트 대기: 없음")
    for item, entry in data["pending"].items():
        env.out(f"  {item} {entry['stage']} ({entry.get('gate')}) 저장 선택 ({selection_text(entry)}) | relay-dispatch go {item}")
        env.out(f"    {shell_text(launcher.session_argv(entry))}")
    last = data.get("last_cycle")
    if last:
        env.out(f"마지막 주기: {last['at']} 감시 {last['watched']}개 오류 {len(last['errors'])}건")
        for error in last["errors"]:
            env.out(f"  {error}")
    else:
        env.out("마지막 주기: 없음")
    return 0


def cmd_go(args, env):
    config = configuration.read(env.registry)
    slug, number = parse_target(args.target)
    if args.repo:
        if slug and slug.lower() != args.repo.lower():
            raise RelayError("input", "target slug and --repo disagree")
        slug = args.repo
    with env.ledger.locked(timeout=COMMAND_WAIT):
        data = env.ledger.data
        if slug is None:
            here = cwd_repository(env, config)
            candidates = [k for k in list(data["pending"]) + list(env.ledger.open_sessions())
                          if k.rsplit("#", 1)[1] == str(number)]
            candidates = list(dict.fromkeys(candidates))
            if here:
                item = item_key(here["slug"], number)
                if item not in candidates:
                    others = [c for c in candidates if c != item]
                    raise RelayError("input", f"{item} 에는 대기 중인 명령이 없다." +
                                     (" 다른 저장소의 후보: " + ", ".join(others) + " (relay-dispatch go <슬러그#번호>)" if others else ""))
            elif len(candidates) == 1:
                item = candidates[0]
            elif candidates:
                raise RelayError("input", "여러 저장소에 같은 번호가 있다. 하나를 고르라: " + ", ".join(f"relay-dispatch go {c}" for c in candidates))
            else:
                raise RelayError("input", f"#{number} 에 대기 중인 명령이나 세션이 없다.")
        else:
            item = item_key(slug, number)
        ctx = context(env, config)
        if args.resume:
            session = data["sessions"].get(item)
            if not session:
                raise RelayError("input", f"{item} 에 기록된 세션이 없다.")
            outcome = loop.resume(ctx, item, session)
            env.ledger.save()
            if not outcome["ok"]:
                raise RelayError("run", "런처 실패: " + outcome["error"] + " | " + outcome["command"])
            env.out(f"{item} {session['stage']} 세션 재개: {outcome['command']} ({resume_hint(session['host'])})")
            return 0
        entry = data["pending"].get(item)
        if not entry:
            raise RelayError("input", f"{item} 에 대기 중인 명령이 없다.")
        ok = loop.launch(ctx, item, dict(entry, reason="go"))
        env.ledger.save()
        return 0 if ok else 1


def cmd_pause(args, env):
    config = configuration.read(env.registry)
    config["paused"] = args.command == "pause"
    configuration.save(config, env.registry)
    env.out("일시정지" if config["paused"] else "재개: 대기 중이던 자동 시작은 다음 주기에 순서대로 실행된다")
    return 0


def cmd_watch(args, env):
    found = configuration.identity(env.cwd)
    gh = env.remote_for(found)
    if args.command == "watch":
        record = watch.apply(gh, args.number, gh.viewer())
        env.out(f"{found['slug']}#{args.number}: {record['label']} 라벨, assignee {', '.join(record['assignees'])}"
                + (f" (경고: {record['warning']})" if record.get("warning") else ""))
    else:
        gh.remove_label(args.number, watch.LABEL)
        env.out(f"{found['slug']}#{args.number}: {watch.LABEL} 라벨 제거 (assignee는 그대로)")
    return 0


def cmd_repo(args, env):
    config = configuration.read(env.registry)
    if args.action == "list":
        for entry, found, error in registered(env, config):
            env.out(f"{entry['path']}: " + (found["slug"] if found else "건너뜀 (" + error + ")"))
        if not config["repos"]:
            env.out("등록된 저장소 없음")
        return 0
    if args.action == "add":
        found = configuration.add_repo(config, args.path, env.registry)
        key = configuration.slug_key(found["host"], found["slug"])
        with env.ledger.locked(timeout=COMMAND_WAIT):
            kept = env.ledger.cursor(key)
            use_kept = False
            if kept and env.stdin_interactive:
                answer = input(f"{found['slug']} 의 이전 커서 {kept} 를 쓸까? [y/N] ")
                use_kept = answer.strip().lower() in ("y", "yes")
            if not use_kept:
                env.ledger.set_cursor(key, env.clock())
            env.ledger.save()
        configuration.save(config, env.registry)
        env.out(f"등록: {found['slug']} ({config['repos'][-1]['path']}) 커서 {env.ledger.cursor(key)}")
        return 0
    removed = configuration.remove_repo(config, args.path)
    configuration.save(config, env.registry)
    env.out(f"해제: {removed['path']} (커서와 처리 이력은 보존)")
    return 0


def cmd_install_shim(args, env):
    explicit = args.host is not None or args.path is not None
    if args.plugin and not args.host:
        raise RelayError("input", "--plugin은 --host와 함께 사용하라")
    if explicit:
        chosen = new_selection(args)
        resolved = boot.resolve(chosen)
    elif (env.home / "package.json").exists():
        if env.package_error:
            raise env.package_error
        chosen, resolved = env.selected, env.resolved
    else:
        chosen = infer_selection()
        resolved = boot.resolve(chosen)
        if Path(resolved["root"]).resolve() != ROOT:
            raise RelayError("input", "설치 기록이 실행 루트와 다르다. --host 또는 --path를 지정하라")
    if explicit or not (env.home / "package.json").exists():
        write_json(env.home / "package.json", chosen)
    bin_dir = env.home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, template in (("relay-dispatch.cmd", CMD_WRAPPER), ("relay-dispatch", SH_WRAPPER)):
        target = bin_dir / name
        if target.exists():
            env.out(f"기존 {target}:\n" + target.read_text(encoding="utf-8").rstrip())
        target.write_text(template, encoding="utf-8", newline="")
        env.out(f"작성: {target}")
    target = bin_dir / "relay_dispatch_boot.py"
    if target.exists():
        env.out(f"기존 {target}:\n" + target.read_text(encoding="utf-8").rstrip())
    target.write_bytes((ROOT / "dispatcher/boot.py").read_bytes())
    env.out(f"작성: {target}")
    try:
        (bin_dir / "relay-dispatch").chmod(0o755)
    except OSError:
        pass
    env.out(f"실행 패키지: {boot.describe(chosen)} {resolved['version']} ({resolved['root']})")
    env.out(f"PATH에 {bin_dir} 를 추가하라. PowerShell: [Environment]::SetEnvironmentVariable('Path', \"$env:Path;{bin_dir}\", 'User')  "
            f"sh: export PATH=\"$PATH:{bin_dir}\"")
    return 0


def new_selection(args):
    if args.path is not None:
        if args.plugin:
            raise RelayError("input", "--plugin은 호스트 선택에서만 사용하라")
        return boot.validate({"schema": 1, "mode": "pinned", "root": str(Path(args.path).resolve())})
    if args.host is None:
        raise RelayError("input", "claude/codex 또는 --path를 지정하라")
    return boot.validate({"schema": 1, "mode": "host", "host": args.host, "plugin": args.plugin or "relay@relay"})


def infer_selection():
    candidates = []
    file = boot.host_home("claude", os.environ) / "plugins/installed_plugins.json"
    if file.exists():
        record = boot.read_record(file)
        if record.get("version") != 2 or not isinstance(record.get("plugins"), dict):
            raise RelayError("input", "Claude 기록을 확인할 수 없다. --host 또는 --path를 지정하라")
        for key, entries in record["plugins"].items():
            if key.startswith("relay@") and isinstance(entries, list) and any(
                    isinstance(e, dict) and e.get("scope") == "user" and isinstance(e.get("installPath"), str)
                    and Path(e["installPath"]).resolve() == ROOT for e in entries):
                candidates.append({"schema": 1, "mode": "host", "host": "claude", "plugin": key})
    cache = boot.host_home("codex", os.environ) / "plugins/cache"
    if ROOT.is_relative_to(cache) and len(ROOT.relative_to(cache).parts) == 3:
        marketplace, name, _ = ROOT.relative_to(cache).parts
        candidates.append({"schema": 1, "mode": "host", "host": "codex", "plugin": f"{name}@{marketplace}"})
    if len(candidates) != 1:
        raise RelayError("input", "실행 패키지 선택을 유도할 수 없다. --host 또는 --path를 지정하라")
    return candidates[0]


def cmd_package(args, env):
    if args.action == "use":
        chosen = new_selection(args)
        resolved = boot.resolve(chosen)
        write_json(env.home / "package.json", chosen)
        env.out(f"실행 패키지: {boot.describe(chosen)} {resolved['version']} ({resolved['root']})")
        env.out("다음 실행부터 적용, 실행 중인 run은 재시작")
        return 0
    env.out(f"선택: {boot.describe(env.selected or {})}")
    env.out(f"실행 루트: {ROOT}\nrelay_core 루트: {Path(relay_core.__file__).resolve().parent}")
    check_wrapper(env)
    if env.package_error:
        return 1
    resolved = env.resolved
    env.out(f"기록: {resolved['source']}\n찾은 루트: {resolved['root']}\n버전: {resolved['version']}")
    env.out(f"enabled: {resolved['enabled']}" if resolved.get("enabled") is not None else "활성화 여부 미확인")
    last = env.selected.get("last")
    env.out(f"마지막 실행: {json.dumps(last, ensure_ascii=False)}")
    if env.selected["mode"] == "host" and (boot.version_tuple(resolved["version"]) is None
            or (last and boot.version_tuple(last["version"]) is None)):
        env.out("하향 버전 비교 불가")
    return 0


def parser():
    top = argparse.ArgumentParser(prog="relay-dispatch", description="Open the next Relay session for watched issues and PRs.")
    sub = top.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="poll and open sessions in this tab")
    run.add_argument("--once", action="store_true", help="run one cycle and exit")
    sub.add_parser("status", help="show login, repositories, sessions and pending commands")
    go = sub.add_parser("go", help="run a pending command now, or resume a recorded session")
    go.add_argument("target", help="number or owner/repo#number")
    go.add_argument("--repo", help="owner/repo when the number is ambiguous")
    go.add_argument("--resume", action="store_true", help="reopen the recorded session instead")
    sub.add_parser("pause", help="queue automatic starts instead of opening them")
    sub.add_parser("resume", help="start queued automatic starts again")
    for name in ("watch", "unwatch"):
        item = sub.add_parser(name, help=f"{name} an issue or PR of the cwd repository")
        item.add_argument("number", type=int)
    repo = sub.add_parser("repo", help="register clones to poll")
    repo_sub = repo.add_subparsers(dest="action", required=True)
    repo_sub.add_parser("add").add_argument("path")
    repo_sub.add_parser("rm").add_argument("path", help="clone path or owner/repo")
    repo_sub.add_parser("list")
    shim = sub.add_parser("install-shim", help="write relay-dispatch wrappers under RELAY_DISPATCH_HOME/bin")
    choice = shim.add_mutually_exclusive_group()
    choice.add_argument("--host", choices=("claude", "codex"))
    choice.add_argument("--path")
    shim.add_argument("--plugin")
    package = sub.add_parser("package", help="show or choose the execution package")
    package_sub = package.add_subparsers(dest="action", required=True)
    package_sub.add_parser("show")
    use = package_sub.add_parser("use")
    choice = use.add_mutually_exclusive_group(required=True)
    choice.add_argument("host", nargs="?", choices=("claude", "codex"))
    choice.add_argument("--path")
    use.add_argument("--plugin")
    return top


COMMANDS = {"run": cmd_run, "status": cmd_status, "go": cmd_go, "pause": cmd_pause, "resume": cmd_pause,
            "watch": cmd_watch, "unwatch": cmd_watch, "repo": cmd_repo, "install-shim": cmd_install_shim, "package": cmd_package}


def main(argv=None, *, out=None, cwd=None, remote_for=None, launcher_for=None, clock=None, registry=None,
         stdin_interactive=None):
    args = parser().parse_args(argv)
    output = out or (lambda text: print(text, flush=True))
    try:
        if not Path(relay_core.__file__).resolve().is_relative_to(ROOT / "scripts"):
            raise RelayError("run", f"relay_core가 다른 패키지에서 로드되었다: {relay_core.__file__}; 실행 루트 {ROOT}")
        env = Env(out=output, cwd=cwd or Path.cwd(),
              registry=registry or read_json(REGISTRY_PATH)["skills"], remote_for=remote_for or github_for,
              launcher_for=launcher_for or launchers.Launcher, clock=clock or utc_now,
              stdin_interactive=sys.stdin.isatty() if stdin_interactive is None else stdin_interactive)
        changing = args.command == "install-shim" or (args.command == "package" and args.action == "use")
        if changing and (getattr(args, "host", None) or getattr(args, "path", None)):
            env.selected, env.resolved, env.package_error = None, None, None
        else:
            start_package(env, record_last=not changing)
        return COMMANDS[args.command](args, env)
    except RelayError as exc:
        output(f"오류 ({exc.code}): {exc}")
        return 1
    except boot.BootError as exc:
        # Package lookup failures from install-shim/package use; other exceptions keep their traceback.
        output(f"오류: {exc}")
        return 1
