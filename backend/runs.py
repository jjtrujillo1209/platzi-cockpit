"""Lectura del historial de corridas en bulk_connect/results/.

Convenciones de nombres existentes:
  <run_id>-events.jsonl        -> conexiones LinkedIn (runner.py)
  <run_id>-followup-events.jsonl -> follow-ups LinkedIn (followup_runner.py)
  <run_id>-mail-events.jsonl   -> emails (email_sender.py)
  <run_id>-urls.txt / -messages.json / -source.csv / -mail-recipients.csv
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from config import RESULTS_DIR

RUN_ID_RE = re.compile(r"^(?P<run_id>.+?)-(?P<kind>followup-events|mail-events|events)\.jsonl$")

KIND_CHANNEL = {
    "events": "linkedin",
    "followup-events": "followup",
    "mail-events": "email",
}

# Archivos de eventos que NO son canales de outreach (validación de perfiles,
# calibración, etc.) y que comparten el sufijo -events.jsonl. Se excluyen.
NON_OUTREACH = ("validacion", "revalidacion", "faltantes", "calib")


def _is_outreach_events(name: str) -> bool:
    return not any(tag in name for tag in NON_OUTREACH)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _summarize(events: list[dict]) -> dict:
    counts: dict[str, int] = {}
    last_ts = ""
    for e in events:
        st = e.get("status", "")
        if e.get("idx", 1) == 0:
            continue  # fila de arranque
        counts[st] = counts.get(st, 0) + 1
        ts = e.get("ts", "")
        if ts > last_ts:
            last_ts = ts
    total = sum(counts.values())
    return {"total": total, "by_status": counts, "last_ts": last_ts}


def _parse_dt(run_id: str) -> str:
    m = re.match(r"(\d{8})-(\d{6})", run_id)
    if m:
        try:
            return datetime.strptime(m.group(0), "%Y%m%d-%H%M%S").isoformat()
        except ValueError:
            pass
    return ""


def list_runs() -> list[dict]:
    """Una entrada por archivo de eventos encontrado, más reciente primero."""
    if not RESULTS_DIR.exists():
        return []
    runs: list[dict] = []
    for f in RESULTS_DIR.glob("*-events.jsonl"):
        if not _is_outreach_events(f.name):
            continue
        m = RUN_ID_RE.match(f.name)
        if not m:
            continue
        run_id = m.group("run_id")
        kind = m.group("kind")
        channel = KIND_CHANNEL.get(kind, kind)
        events = read_jsonl(f)
        summary = _summarize(events)
        runs.append({
            "run_id": run_id,
            "channel": channel,
            "file": f.name,
            "created_at": _parse_dt(run_id) or summary["last_ts"],
            "mtime": f.stat().st_mtime,
            **summary,
        })
    runs.sort(key=lambda r: (r.get("created_at") or "", r["mtime"]), reverse=True)
    return runs


def run_detail(run_id: str, channel: str) -> dict:
    suffix = {
        "linkedin": "-events.jsonl",
        "followup": "-followup-events.jsonl",
        "email": "-mail-events.jsonl",
    }.get(channel, "-events.jsonl")
    f = RESULTS_DIR / f"{run_id}{suffix}"
    events = [e for e in read_jsonl(f) if e.get("idx", 1) != 0]
    messages = {}
    msg_file = RESULTS_DIR / f"{run_id}-messages.json"
    if msg_file.exists():
        try:
            messages = json.loads(msg_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            messages = {}
    return {
        "run_id": run_id,
        "channel": channel,
        "events": events,
        "summary": _summarize(events),
        "messages": messages,
    }


def all_email_events() -> list[dict]:
    """Todos los eventos de envío de email (para cruzar con webhooks)."""
    out: list[dict] = []
    if not RESULTS_DIR.exists():
        return out
    for f in RESULTS_DIR.glob("*-mail-events.jsonl"):
        run_id = f.name.replace("-mail-events.jsonl", "")
        for e in read_jsonl(f):
            if e.get("idx", 1) == 0:
                continue
            e["run_id"] = run_id
            out.append(e)
    return out


def all_linkedin_events() -> list[dict]:
    out: list[dict] = []
    if not RESULTS_DIR.exists():
        return out
    for f in RESULTS_DIR.glob("*-events.jsonl"):
        if f.name.endswith("-mail-events.jsonl") or not _is_outreach_events(f.name):
            continue
        channel = "followup" if f.name.endswith("-followup-events.jsonl") else "linkedin"
        run_id = RUN_ID_RE.match(f.name)
        run_id = run_id.group("run_id") if run_id else f.stem
        for e in read_jsonl(f):
            if e.get("idx", 1) == 0:
                continue
            e["run_id"] = run_id
            e["channel"] = channel
            out.append(e)
    return out
