"""Console output for the demos. Standard library only.

Two jobs:
  say(text)        print one line without tearing the live status line
  status(message)  show a spinner and elapsed time while something slow runs

Several statuses can be active at once, because the harness runs tasks in parallel.
They share one line at the bottom of the output. When output is not a terminal
(a file, a pipe, CI), there is no spinner and each status prints plain lines.
"""
from __future__ import annotations

import contextlib
import io
import itertools
import shutil
import sys
import threading
import time
from typing import Callable, Iterator

_lock = threading.RLock()
_active: dict[int, "Status"] = {}
_ids = itertools.count(1)
_thread: threading.Thread | None = None
_drawn = False
FRAMES = "|/-\\"                      # plain ASCII, safe in every Windows console


def _tty() -> bool:
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


class Status:
    def __init__(self, message: str, detail: Callable[[], str] | None = None):
        self.message, self.detail, self.started = message, detail, time.time()

    def update(self, message: str) -> None:
        """Change the text shown next to the spinner."""
        self.message = message

    def elapsed(self) -> float:
        return time.time() - self.started

    def text(self) -> str:
        extra = ""
        if self.detail:
            try:
                extra = self.detail()
            except Exception:
                extra = ""
        return f"{self.message} {extra}".strip()


def _clear() -> None:
    global _drawn
    if _drawn:
        width = shutil.get_terminal_size((100, 20)).columns - 1
        sys.stdout.write("\r" + " " * width + "\r")
        _drawn = False


def _draw(frame: str) -> None:
    global _drawn
    if not _active:
        return
    items = list(_active.values())
    oldest = max(s.elapsed() for s in items)
    body = items[0].text() if len(items) == 1 else f"{len(items)} running: " + ", ".join(s.message.split(":")[0] for s in items)
    width = shutil.get_terminal_size((100, 20)).columns - 1
    tail = f"  {oldest:.0f}s"
    line = f"  {frame} {body}"
    if len(line) + len(tail) > width:
        line = line[: max(0, width - len(tail) - 3)] + "..."
    sys.stdout.write("\r" + (line + tail).ljust(width))
    sys.stdout.flush()
    _drawn = True


def _spin() -> None:
    for frame in itertools.cycle(FRAMES):
        time.sleep(0.12)
        with _lock:
            if not _active:
                _clear()
                sys.stdout.flush()
                return
            _draw(frame)


def say(text: str = "") -> None:
    """Print a line. Safe to call from any thread while a status is showing."""
    with _lock:
        _clear()
        print(text, flush=True)


def ask(prompt: str) -> str:
    """Prompt for input. The status line pauses until the user answers."""
    with _lock:
        _clear()
        return input(prompt)


@contextlib.contextmanager
def status(message: str, *, detail: Callable[[], str] | None = None, report: bool = True,
           hide_noise: bool = False) -> Iterator[Status]:
    """Show `message` with a spinner until the block ends.

    detail       a function returning live extra text, for example bytes downloaded so far
    report       print a 'done' line with the elapsed time when the block ends
    hide_noise   swallow progress bars and warnings that libraries write to stderr.
                 If the block raises, the swallowed text is printed so nothing is lost.
    """
    global _thread
    item, key, tty = Status(message, detail), next(_ids), _tty()
    captured = io.StringIO()
    with _lock:
        _active[key] = item
        if tty and (_thread is None or not _thread.is_alive()):
            _thread = threading.Thread(target=_spin, daemon=True)
            _thread.start()
    if not tty and report:
        say(f"  ...            {message}")
    try:
        if hide_noise:
            with contextlib.redirect_stderr(captured):
                yield item
        else:
            yield item
    except BaseException:
        with _lock:
            _active.pop(key, None)
        say(f"  stopped        {item.message}  ({item.elapsed():.1f} s)")
        if captured.getvalue().strip():
            say(captured.getvalue().strip())
        raise
    with _lock:
        _active.pop(key, None)
    if report:
        say(f"  ok             {item.text()}  ({item.elapsed():.1f} s)")
    else:
        with _lock:
            _clear()
            sys.stdout.flush()


# Plain-language hints for the failures a newcomer is most likely to hit.
HINTS = [
    (("authentication_error", "invalid x-api-key", "401"),
     "The API key was rejected. Check ANTHROPIC_API_KEY in .env for a typo or stray quote."),
    (("credit balance", "billing"),
     "The account has no credit. Add credit in the Claude Console under Billing."),
    (("rate_limit", "429"),
     "Rate limit reached. Wait a minute, or lower parallelism with --workers 2."),
    (("overloaded", "529"),
     "The model service is busy. Run again with --resume to continue where it stopped."),
    (("maximum token limit", "max_tokens"),
     "A model answer ran past the output limit. Raise MAX_TOKENS in .env, then use --resume."),
    (("not_found_error", "model:"),
     "The model name was not found. Check MODEL_FAST, MODEL_BALANCED and MODEL_DEEP in .env."),
    (("connection", "getaddrinfo", "timed out", "ssl"),
     "Network problem. Check the connection, or add --offline to replay saved outputs."),
    (("no fixture",),
     "Offline mode only replays tasks that have a saved output under fixtures/."),
]


def hint(error: str) -> str:
    """Return one line of advice for a known error text, or an empty string."""
    low = error.lower()
    for needles, advice in HINTS:
        if any(n in low for n in needles):
            return advice
    return ""
