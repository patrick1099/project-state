"""Optional read-only session-sweep cache hints; never status authority."""
import json
from pathlib import Path


def hints(path, warnings):
    if not path:
        return {}
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(state, dict):
            raise ValueError("state must be an object")
        sessions = state.get("sessions", {})
        if not isinstance(sessions, dict):
            raise ValueError("sessions must be an object")
        closures = state.get("closures", {})
        result = {}
        for sid, entry in sessions.items():
            if not isinstance(entry, dict):
                continue
            verdict = entry.get("verdict") or {}
            summary = entry.get("summary") or {}
            if not isinstance(verdict, dict) or not isinstance(summary, dict):
                continue
            hint = {key: verdict.get(key) for key in ("title", "stopped_at", "version", "owner", "status")}
            hint["gone"] = bool(entry.get("gone"))
            hint["stale"] = verdict.get("version") != summary.get("version")
            hint["closed"] = bool(isinstance(closures, (dict, list)) and "%s@%s" % (sid, summary.get("version")) in closures)
            hint["authority"] = "hint_only"
            result[str(sid)] = hint
        return result
    except (OSError, ValueError, UnicodeError) as exc:
        warnings.append({"kind": "sweep_unreadable", "path": str(path), "reason": str(exc)})
        return {}