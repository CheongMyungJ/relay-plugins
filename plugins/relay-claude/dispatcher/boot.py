"""Standalone package resolver, also copied verbatim beside the home wrappers."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


class BootError(Exception):
    def __init__(self, category, reason, selected=None, source=None):
        self.category = category
        self.reason = reason
        self.selected = selected
        self.source = source
        super().__init__(reason)

    def __str__(self):
        chosen = self.selected or {}
        host = chosen.get("host", "claude")
        plugin = chosen.get("plugin", "relay@relay")
        install = f"codex plugin add {plugin}" if host == "codex" else f"claude plugin install {plugin} --scope user"
        if chosen.get("mode") == "pinned":
            # A pinned root is not a host installation, so recovery is a valid root rather than a plugin install.
            recovery = ('복구: 고정 루트를 바로잡거나 다른 설치를 고른다. 설치된 패키지의 엔트리로\n'
                        '  python "<설치 루트>/dispatcher/relay_dispatch.py" package use --path "<설치 루트>"\n'
                        '  python "<설치 루트>/dispatcher/relay_dispatch.py" install-shim --path "<설치 루트>"')
        else:
            recovery = (f"복구: {install}; 설치된 패키지의 엔트리로\n"
                        f'  python "<설치 루트>/dispatcher/relay_dispatch.py" package use {host}\n'
                        '  python "<설치 루트>/dispatcher/relay_dispatch.py" install-shim --path "<설치 루트>"')
        return (f"선택: {describe(chosen)} ({home() / 'package.json'})\n"
                f"기록: {self.source or home() / 'package.json'}\n"
                f"원인: {self.category} — {self.reason}\n" + recovery)


def home():
    return Path(os.environ.get("RELAY_DISPATCH_HOME") or Path.home() / ".relay-dispatch").resolve()


def host_home(host, env):
    key = "CLAUDE_CONFIG_DIR" if host == "claude" else "CODEX_HOME"
    return Path(env.get(key) or Path.home() / ("." + host)).resolve()


def describe(chosen):
    if chosen.get("mode") == "pinned":
        return "pinned " + chosen["root"]
    return f"{chosen.get('host', '(없음)')} {chosen.get('plugin', '')}".strip()


def validate(chosen):
    def invalid(reason):
        raise BootError("조회 실패", reason)
    if not isinstance(chosen, dict) or type(chosen.get("schema")) is not int or chosen["schema"] != 1:
        invalid("package.json schema는 1이어야 한다")
    if chosen.get("mode") == "host":
        if chosen.get("host") not in ("claude", "codex"):
            invalid("host는 claude 또는 codex여야 한다")
        plugin = chosen.get("plugin")
        if not isinstance(plugin, str) or len(plugin.split("@")) != 2 or any(
                not part or part in (".", "..") or any(c in part for c in '/\\\x00:') for part in plugin.split("@")):
            invalid("plugin은 경로 구분자 없는 이름@마켓플레이스여야 한다")
    elif chosen.get("mode") == "pinned":
        root = chosen.get("root")
        if not isinstance(root, str) or not Path(root).is_absolute():
            invalid("pinned root는 절대 경로여야 한다")
    else:
        invalid("mode는 host 또는 pinned여야 한다")
    if "last" in chosen and (not isinstance(chosen["last"], dict) or any(
            not isinstance(chosen["last"].get(k), str) for k in ("root", "version", "at"))):
        invalid("last는 root, version, at 문자열을 포함해야 한다")
    return chosen


def read_selection(directory):
    file = Path(directory) / "package.json"
    try:
        raw = file.read_bytes()
    except FileNotFoundError:
        raise BootError("선택 없음", "install-shim --host 또는 --path로 선택하라", source=str(file)) from None
    except OSError as exc:
        raise BootError("조회 실패", str(exc), source=str(file)) from exc
    try:
        chosen = validate(json.loads(raw.decode("utf-8-sig")))
    except (ValueError, UnicodeError) as exc:
        raise BootError("조회 실패", str(exc), source=str(file)) from exc
    return chosen, hashlib.sha256(raw).hexdigest()


def selection(directory):
    return read_selection(directory)[0]


def read_record(file, category="조회 실패"):
    try:
        value = json.loads(Path(file).read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("JSON object가 필요하다")
        return value
    except (OSError, ValueError, UnicodeError) as exc:
        raise BootError(category, f"{file}: {exc}", source=str(file)) from exc


def check_root(root):
    root = Path(root)
    for name in ("relay.json", "dispatcher/relay_dispatch.py", "dispatcher/cli.py", "dispatcher/boot.py",
                 "scripts/relay_core/__init__.py"):
        if not (root / name).is_file():
            raise BootError("설치 손상·제거", f"설치본 없음 또는 필수 파일 없음: {root / name}")
    version = read_record(root / "relay.json", "설치 손상·제거").get("version")
    if not isinstance(version, str) or not version:
        raise BootError("설치 손상·제거", f"{root / 'relay.json'}: version 없음")
    return version


def version_tuple(version):
    if isinstance(version, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        return tuple(map(int, version.split(".")))
    return None


def resolve(chosen, env=None, run=subprocess.run):
    chosen = validate(chosen)
    env = os.environ if env is None else env
    source = str(home() / "package.json")
    enabled = None
    try:
        if chosen["mode"] == "pinned":
            root = Path(chosen["root"]).resolve()
            version = check_root(root)
        elif chosen["host"] == "claude":
            file = host_home("claude", env) / "plugins/installed_plugins.json"
            source = str(file)
            if not file.exists():
                raise BootError("호스트 기록 없음", str(file))
            record = read_record(file)
            if record.get("version") != 2 or not isinstance(record.get("plugins"), dict):
                raise BootError("조회 실패", "installed_plugins.json schema 2가 필요하다")
            entries = record["plugins"].get(chosen["plugin"], [])
            if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
                raise BootError("조회 실패", "설치 항목 목록이 잘못되었다")
            users = [e for e in entries if e.get("scope") == "user"]
            if not users:
                keys = [k for k in record["plugins"] if k.startswith("relay@")]
                raise BootError("플러그인 미설치", f"user 설치 없음; 참고 키: {keys}; 다른 범위: {entries}")
            if len(users) != 1:
                raise BootError("조회 실패", f"user 설치가 여러 개다: {users}")
            entry = users[0]
            if not isinstance(entry.get("installPath"), str) or not Path(entry["installPath"]).is_absolute():
                raise BootError("설치 손상·제거", "절대 installPath 없음")
            root, version = Path(entry["installPath"]).resolve(), entry.get("version")
            if not isinstance(version, str) or not version:
                raise BootError("조회 실패", "설치 기록 version 없음")
        else:
            name, marketplace = chosen["plugin"].split("@")
            argv = ["codex", "plugin", "list", "--json", "-m", marketplace]
            source = " ".join(argv)
            # Windows npm installations expose codex.cmd rather than an executable.
            executable = shutil.which("codex", path=env.get("PATH"))
            if executable:
                argv[0] = executable
            try:
                result = run(argv, env=dict(env), capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=60)
            except (OSError, subprocess.SubprocessError) as exc:
                raise BootError("조회 실패", str(exc)) from exc
            if result.returncode:
                raise BootError("조회 실패", f"명령 종료 {result.returncode}: {result.stderr[:1000]}")
            try:
                record = json.loads(result.stdout)
                entries = record["installed"]
                if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
                    raise ValueError("installed 목록이 필요하다")
            except (ValueError, KeyError, TypeError) as exc:
                raise BootError("조회 실패", f"명령 JSON: {exc}") from exc
            entries = [e for e in entries if e.get("pluginId") == chosen["plugin"] and e.get("installed") is True]
            if not entries:
                raise BootError("플러그인 미설치", chosen["plugin"])
            if len(entries) != 1:
                raise BootError("조회 실패", "설치 항목이 여러 개다")
            entry = entries[0]
            checkout = entry.get("source", {})
            checkout = checkout.get("path") if isinstance(checkout, dict) else None
            if not isinstance(checkout, str) or not Path(checkout).is_absolute():
                raise BootError("조회 실패", "source.path 없음 또는 절대 경로 아님")
            manifest = Path(checkout) / ".codex-plugin/plugin.json"
            source += f" → {manifest}"
            version = read_record(manifest).get("version")
            if not isinstance(version, str) or not version or version in (".", "..") or any(c in version for c in '/\\\x00:'):
                raise BootError("조회 실패", "체크아웃 version 없음 또는 잘못된 버전")
            source += f" 버전 {version}"
            cache = host_home("codex", env) / "plugins/cache"
            if not cache.is_dir():
                raise BootError("호스트 기록 없음", str(cache))
            root = cache / marketplace / name / version
            enabled = entry.get("enabled")
        if chosen["mode"] == "host":
            previous = version_tuple(chosen.get("last", {}).get("version"))
            current = version_tuple(version)
            if previous is not None and current is not None and current < previous:
                raise BootError("하향 감지", f"{chosen['last']['version']} → {version}; 의도한 하향이면 package use {chosen['host']}")
            actual = check_root(root)
            if actual != version:
                raise BootError("설치 손상·제거", f"{root / 'relay.json'} 버전 {actual} != 기록 {version}")
        return {"root": str(root), "version": version, "source": source, "enabled": enabled}
    except BootError as exc:
        exc.selected, exc.source = chosen, source
        raise
    except (OSError, ValueError) as exc:
        raise BootError("조회 실패", str(exc), chosen, source) from exc


def main(argv=None):
    try:
        chosen, selection_hash = read_selection(home())
        resolved = resolve(chosen)
        env = dict(os.environ, RELAY_DISPATCH_RESOLVED=json.dumps(dict(resolved, selection_hash=selection_hash)))
        command = [sys.executable, str(Path(resolved["root"]) / "dispatcher/relay_dispatch.py"),
                   *(sys.argv[1:] if argv is None else argv)]
        if os.name != "nt":
            os.execve(sys.executable, command, env)
        child = subprocess.Popen(command, env=env)
        while True:
            try:
                return child.wait()
            except KeyboardInterrupt:
                continue
    except (BootError, OSError) as exc:
        print(f"relay-dispatch 부트스트랩: 선택된 패키지를 실행할 수 없다.\n{exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
