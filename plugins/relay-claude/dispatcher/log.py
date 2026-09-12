"""One line per event on the run tab and in a daily-rotated file. Quiet cycles print nothing."""
import datetime
import sys
from pathlib import Path

from .ledger import iso, parse

HEARTBEAT = datetime.timedelta(minutes=5)
ELAPSED = datetime.timedelta(minutes=30)
KEEP_DAYS = 7


def clip(title, limit=80):
    title = (title or "").replace("\n", " ").strip()
    return title if len(title) <= limit else title[:limit - 1] + "…"


class Log:
    def __init__(self, home, write=None, clock=None):
        self.home = Path(home)
        self.write = write or (lambda text: print(text, file=sys.stdout, flush=True))
        self.clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc))
        self.last_output = None
        self.last_heartbeat = self.clock()  # the first heartbeat comes one interval after start
        self.reported = {}
        self.day = self.recorded_day()  # so a restart on a later day still rotates yesterday's file

    def line(self, text):
        now = self.clock()
        full = iso(now) + " " + text
        self.write(full)
        self.append(full, now)
        self.last_output = now

    def event(self, slug, number, title, artifact, decision, reason, command=None):
        text = f"{slug}#{number} {clip(title)!r} {artifact} → {decision} ({reason})"
        if command:
            text += " | " + command
        self.line(text)

    def heartbeat(self, watched, sessions):
        now = self.clock()
        recent = [t for t in (self.last_output, self.last_heartbeat) if t]
        if recent and now - max(recent) < HEARTBEAT:
            return
        self.last_heartbeat = now
        self.line(f"heartbeat 마지막 폴링 {iso(now)} 감시 {watched}개 열린 세션 {sessions}개")

    def elapsed(self, sessions):
        now = self.clock()
        for item, session in sessions.items():
            started = parse(session["started_at"])
            last = self.reported.get(item, started)
            if now - last >= ELAPSED:
                minutes = int((now - started).total_seconds() // 60)
                self.reported[item] = now
                self.line(f"{item} {session['stage']} 세션 열린 지 {minutes}분 (게시 없음)")

    def append(self, text, now):
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            self.rotate(now)
            with (self.home / "dispatch.log").open("a", encoding="utf-8") as stream:
                stream.write(text + "\n")
        except OSError:
            pass  # The tab is the primary display; a failed file write must not stop the loop.

    def recorded_day(self):
        """The date of the last line already in dispatch.log, read from its own timestamp."""
        try:
            with (self.home / "dispatch.log").open("rb") as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 4096))
                tail = stream.read().decode("utf-8", errors="replace").rstrip("\n")
        except OSError:
            return None
        last = tail.rsplit("\n", 1)[-1]
        return last[:10] if len(last) >= 10 and last[4] == "-" and last[7] == "-" else None

    def rotate(self, now):
        today = now.date().isoformat()
        current = self.home / "dispatch.log"
        if self.day and self.day != today and current.exists():
            current.replace(self.home / f"dispatch.{self.day}.log")
            cutoff = (now - datetime.timedelta(days=KEEP_DAYS)).date().isoformat()
            for old in self.home.glob("dispatch.*.log"):
                if old.name[len("dispatch."):-len(".log")] < cutoff:
                    old.unlink(missing_ok=True)
        self.day = today
