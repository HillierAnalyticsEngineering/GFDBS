"""A file that several agents edit at the same time, without deadlock.

The rule that prevents deadlock: nobody holds the lock while waiting on a model.
An agent reads a snapshot, thinks for as long as it likes, then proposes its terms.
The commit takes the lock for a few milliseconds, merges against whatever the file
holds now, and writes a new version in one atomic step. Two agents can never wait
on each other, because neither waits while holding anything.

Consensus rule for the shared dictionary: the first agent to define a term owns its
unit. A later agent that uses the same name with the same unit adopts it. A later
agent that uses the same name with a different unit is refused, and told what the
existing definition is. It then conforms or picks another name.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


_SAME = {"boolean": "bool", "true/false": "bool", "flag": "bool", "dimensionless": "ratio", "fraction": "ratio", "none": "ratio",
         "-": "ratio", "": "ratio", "1": "ratio", "unitless": "ratio", "c": "degc", "celsius": "degc", "deg c": "degc",
         "degrees celsius": "degc", "years": "yr", "year": "yr", "minutes": "min", "seconds": "s", "sec": "s", "percent": "%"}


def _norm(unit: str) -> str:
    """Two spellings of one unit must not read as a clash: 'boolean' and 'bool', 'W/m^2' and 'W/m2', '°C' and 'degC'."""
    text = " ".join(str(unit).lower().replace("(", " ").replace(")", " ").split())
    text = text.replace("^", "").replace("**", "").replace("\u00b0", "deg").replace("\u00b2", "2").replace("\u00b3", "3").replace("\u00b7", "*")
    return _SAME.get(text, text)


class SharedDictionary:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        with _guard:                                   # one lock per file, shared by every task thread
            self._lock = _locks.setdefault(str(self.path.resolve()), threading.Lock())

    def read(self) -> dict:
        """A snapshot: {'version': n, 'terms': {name: {unit, description, owner, users}}}. Takes no lock."""
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {"version": 0, "terms": {}}

    def propose(self, owner: str, terms: list[dict]) -> dict:
        """Merge `terms` into the file. All or nothing. Returns what happened.

        The lock covers read, merge, and write only. It is never held across a model call.
        """
        with self._lock:
            data = self.read()
            before, conflicts, added, adopted = data["version"], [], [], []
            for term in terms:
                name, existing = term["name"], data["terms"].get(term["name"])
                if existing is None:
                    added.append(name)
                elif _norm(existing["unit"]) == _norm(term["unit"]) or existing["owner"] == owner:
                    adopted.append(name)
                else:
                    conflicts.append({"name": name, "yours": term["unit"], "existing": existing["unit"],
                                      "owner": existing["owner"], "description": existing["description"]})
            if conflicts:
                return {"ok": False, "version": before, "conflicts": conflicts, "added": [], "adopted": []}
            for term in terms:
                name = term["name"]
                if name in added or data["terms"][name]["owner"] == owner:
                    users = data["terms"].get(name, {}).get("users", [])
                    data["terms"][name] = {"unit": term["unit"], "description": term["description"],
                                           "owner": owner, "users": sorted(set(users) | {owner})}
                else:
                    data["terms"][name]["users"] = sorted(set(data["terms"][name]["users"]) | {owner})
            data["version"] = before + 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(f".{threading.get_ident()}.tmp")
            temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(temp, self.path)                 # atomic: readers see the old file or the new one
            return {"ok": True, "version": data["version"], "from_version": before, "conflicts": [],
                    "added": added, "adopted": adopted}

    def withdraw(self, owner: str) -> None:
        """Remove an owner's terms before it rebuilds. Terms other capabilities use stay, with a new owner."""
        with self._lock:
            data = self.read()
            for name in list(data["terms"]):
                entry = data["terms"][name]
                entry["users"] = [u for u in entry["users"] if u != owner]
                if not entry["users"]:
                    del data["terms"][name]
                elif entry["owner"] == owner:
                    entry["owner"] = entry["users"][0]
            data["version"] += 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
