"""relay-dispatch commands: run, status, go, pause, resume, watch, unwatch, repo, install-shim."""
import argparse
import datetime
import json
import sys
import time
from pathlib import Path

from relay_core import RelayError, watch
from relay_core.github import GitHub
from relay_core.state import read_json
from . import config as configuration, launcher as launchers, log as logs, loop
from .ledger import Ledger, iso, item_key, parse
from .launcher import resume_argv, resume_hint, shell_text

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "dispatcher" / "relay_dispatch.py"
REGISTRY_PATH = ROOT / "relay.json"
COMMAND_WAIT = 90.0  # seconds a one-shot command waits for `run` to finish its cycle before giving up

CMD_WRAPPER = '@echo off\r\npython "{entry}" %*\r\n'
SH_WRAPPER = '#!/bin/sh\nexec python "{entry}" "$@"\n'


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


def check_shim(env):
    wrapper = env.home / "bin" / ("relay-dispatch.cmd" if sys.platform == "win32" else "relay-dispatch")
    if not wrapper.exists():
        return
    text = wrapper.read_text(encoding="utf-8")
    if str(ENTRY) not in text:
        env.out(f"경고: {wrapper} 가 다른 엔트리를 가리킨다 (현재 {ENTRY}). `relay-dispatch install-shim`을 다시 실행하라.")


def cmd_run(args, env):
    live = configuration.Config(env.registry)
    live.load()
    if live.error:
        raise RelayError("input", "config.json: " + live.error)
    config = live.value
    if not config["repos"]:
        env.out("감시할 저장소가 없다. `relay-dispatch repo add <clone-path>` 먼저 실행하라.")
    check_shim(env)
    hosts = [found["host"] for _, found, _ in registered(env, config) if found] or ["github.com"]
    login = env.remote_for({"slug": "", "host": hosts[0]}).viewer()
    with env.ledger.locked(timeout=COMMAND_WAIT):
        env.ledger.data["login"] = login
        env.ledger.save()
    ctx = context(env, config)
    ctx.login = login
    ctx.log.line(f"relay-dispatch 시작: 로그인 {login}, 저장소 {len(config['repos'])}개, launcher {config['launcher']}, "
                 f"host {config['host']}, poll {config['poll_seconds']}s, 설정 {live.file}")
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


def cmd_status(args, env):
    config = configuration.read(env.registry)
    env.ledger.load()
    data, now = env.ledger.data, env.clock()
    env.out(f"로그인: {data.get('login') or '(run을 아직 실행하지 않음)'}  설정: {configuration.path()}  paused: {config['paused']}")
    env.out(f"launcher: {config['launcher']}  host: {config['host']}  poll: {config['poll_seconds']}s  auto: {', '.join(config['auto'])}  gated: {', '.join(config['gated']) or '-'}")
    env.out("저장소:")
    for entry, found, error in registered(env, config):
        if error:
            env.out(f"  {entry['path']}: 건너뜀 ({error})")
            continue
        key = configuration.slug_key(found["host"], found["slug"])
        settings = configuration.settings(config, entry)
        items = data["watched"].get(key, [])
        env.out(f"  {found['slug']} ({entry['path']}) host {settings['host']} gated {', '.join(settings['gated']) or '-'} 커서 {data['cursors'].get(key) or '(없음, 현재 시각부터)'}")
        for item in items:
            env.out(f"    #{item['number']} {item['kind']} {logs.clip(item.get('title'))!r} {item['status']}")
    sessions = env.ledger.open_sessions()
    env.out("열린 세션:" if sessions else "열린 세션: 없음")
    for item, session in sessions.items():
        env.out(f"  {item} {session['stage']} ({session['host']}) 시작 후 {elapsed_text(session['started_at'], now)} "
                f"session {session['session_id']} | relay-dispatch go {item} --resume")
    env.out("게이트 대기:" if data["pending"] else "게이트 대기: 없음")
    for item, entry in data["pending"].items():
        env.out(f"  {item} {entry['stage']} ({entry.get('gate')}) | relay-dispatch go {item}")
        env.out(f"    {entry['prompt']}")
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
            argv = resume_argv(session["host"], session["session_id"])
            outcome = ctx.launcher.launch(session["cwd"], argv, session["name"], session["session_id"])
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
    bin_dir = env.home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, template in (("relay-dispatch.cmd", CMD_WRAPPER), ("relay-dispatch", SH_WRAPPER)):
        target = bin_dir / name
        if target.exists():
            env.out(f"기존 {target}:\n" + target.read_text(encoding="utf-8").rstrip())
        target.write_text(template.format(entry=ENTRY), encoding="utf-8", newline="")
        env.out(f"작성: {target}")
    try:
        (bin_dir / "relay-dispatch").chmod(0o755)
    except OSError:
        pass
    env.out(f"PATH에 {bin_dir} 를 추가하라. PowerShell: [Environment]::SetEnvironmentVariable('Path', \"$env:Path;{bin_dir}\", 'User')  "
            f"sh: export PATH=\"$PATH:{bin_dir}\"")
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
    sub.add_parser("install-shim", help="write relay-dispatch wrappers under RELAY_DISPATCH_HOME/bin")
    return top


COMMANDS = {"run": cmd_run, "status": cmd_status, "go": cmd_go, "pause": cmd_pause, "resume": cmd_pause,
            "watch": cmd_watch, "unwatch": cmd_watch, "repo": cmd_repo, "install-shim": cmd_install_shim}


def main(argv=None, *, out=None, cwd=None, remote_for=None, launcher_for=None, clock=None, registry=None,
         stdin_interactive=None):
    args = parser().parse_args(argv)
    env = Env(out=out or (lambda text: print(text, flush=True)), cwd=cwd or Path.cwd(),
              registry=registry or read_json(REGISTRY_PATH)["skills"], remote_for=remote_for or github_for,
              launcher_for=launcher_for or launchers.Launcher, clock=clock or utc_now,
              stdin_interactive=sys.stdin.isatty() if stdin_interactive is None else stdin_interactive)
    try:
        return COMMANDS[args.command](args, env)
    except RelayError as exc:
        env.out(f"오류 ({exc.code}): {exc}")
        return 1
