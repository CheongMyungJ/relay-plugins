"""Entry point: `python relay_dispatch.py <command>`; the bin/ wrappers call this file."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
sys.path.insert(0, str(HERE.parent))

def main():
    try:
        from dispatcher.cli import main as dispatch
    except ImportError as exc:
        # Only the initial package import is an installation failure; later ImportErrors keep their traceback.
        print(f"relay-dispatch: 설치 손상·제거 — {HERE.parent}에서 {exc.name}을 불러올 수 없다 ({exc})\n"
              f"기록: 직접 엔트리 {__file__}\n"
              "복구: 호스트에서 플러그인을 다시 설치한 뒤 새 설치의 엔트리로 install-shim을 실행하라.",
              file=sys.stderr)
        return 3
    return dispatch()

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
