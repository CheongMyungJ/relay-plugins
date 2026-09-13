"""Session commands per host and terminal backends. Processes start from argument arrays."""
import base64
import json
import shlex
import shutil
import subprocess
from pathlib import Path

from relay_core import RelayError


# Sessions the dispatcher opens run without per-tool permission prompts. Codex's option also lifts its sandbox.
# Relay's own draft review and exact publication approval stay inside the skills; this only covers host tool prompts.
PERMISSION_OPTIONS = {"claude": "--dangerously-skip-permissions", "codex": "--dangerously-bypass-approvals-and-sandbox"}


def session_argv(host, prompt, name, session_id, claude_options=("--session-id", "--name")):
    if host == "claude":
        argv = ["claude", PERMISSION_OPTIONS["claude"]]
        if "--session-id" in claude_options:
            argv += ["--session-id", session_id]
        if "--name" in claude_options:
            argv += ["--name", name]
        return argv + [prompt]
    if host == "codex":
        return ["codex", PERMISSION_OPTIONS["codex"], prompt]
    if host == "opencode":
        return ["opencode", "--prompt", prompt]
    raise RelayError("input", "unknown host: " + str(host))


def resume_argv(host, session_id):
    """How to reopen a recorded session with the same permission policy; Codex and opencode choose from their own pickers."""
    if host == "claude":
        return ["claude", PERMISSION_OPTIONS["claude"], "--resume", session_id]
    if host == "codex":
        return ["codex", "resume", PERMISSION_OPTIONS["codex"]]
    if host == "opencode":
        return ["opencode"]
    raise RelayError("input", "unknown host: " + str(host))


def resume_hint(host):
    return {"claude": "기록된 세션 ID로 다시 연다.",
            "codex": "Codex는 세션 ID를 고정할 수 없어 resume 선택 화면을 연다.",
            "opencode": "opencode는 실행 시 세션 ID를 정할 수 없어 세션 목록(/sessions)에서 고른다."}[host]


def ps_quote(value):
    return "'" + value.replace("'", "''") + "'"


def powershell_command(argv):
    return "& " + " ".join(ps_quote(a) for a in argv)


def encoded(script):
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def powershell_executable():
    """pwsh when PowerShell 7 is installed, otherwise Windows PowerShell; -EncodedCommand works on both."""
    return "pwsh" if shutil.which("pwsh") else "powershell"


def wt_argv(name, cwd, argv, shell="pwsh"):
    # The inner command travels base64-encoded so no quoting layer of wt or the shell can split it.
    return ["wt", "-w", "0", "nt", "--title", name, "-d", str(cwd), shell, "-NoExit",
            "-EncodedCommand", encoded(powershell_command(argv))]


def tmux_argv(name, cwd, argv):
    return ["tmux", "new-window", "-n", name, "-c", str(cwd), shlex.join(argv)]


def shell_text(argv):
    """The command as a person would type it, for logs and gate notices."""
    return shlex.join(argv)


class Launcher:
    def __init__(self, kind, home):
        if kind not in ("wt", "tmux", "dry-run"):
            raise RelayError("input", "unknown launcher: " + str(kind))
        self.kind = kind
        self.home = Path(home)
        self.claude_options = None
        self.shell = None

    def powershell(self):
        if self.shell is None:
            self.shell = powershell_executable()
        return self.shell

    def claude_support(self):
        """Which of --session-id/--name this machine's claude accepts; both when unprobed (dry-run)."""
        if self.kind == "dry-run":
            return ("--session-id", "--name")
        if self.claude_options is None:
            try:
                proc = subprocess.run(["claude", "--help"], capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", timeout=30)
                text = proc.stdout + proc.stderr
            except (OSError, subprocess.TimeoutExpired):
                text = ""
            self.claude_options = tuple(o for o in ("--session-id", "--name") if o in text)
        return self.claude_options

    def backend_argv(self, name, cwd, argv):
        return wt_argv(name, cwd, argv, self.powershell()) if self.kind == "wt" else tmux_argv(name, cwd, argv)

    def launch(self, cwd, argv, name, session_id):
        record = {"cwd": str(cwd), "argv": list(argv), "session_id": session_id, "name": name}
        if self.kind == "dry-run":
            self.home.mkdir(parents=True, exist_ok=True)
            with (self.home / "launches.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            return {"ok": True, "error": None, "command": shell_text(argv)}
        full = self.backend_argv(name, cwd, argv)
        try:
            proc = subprocess.run(full, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "error": f"{self.kind}: {exc}", "command": shell_text(argv)}
        if proc.returncode:
            return {"ok": False, "error": f"{self.kind} exit {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:300]}",
                    "command": shell_text(argv)}
        return {"ok": True, "error": None, "command": shell_text(argv)}
