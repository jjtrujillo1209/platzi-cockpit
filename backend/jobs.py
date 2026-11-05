"""Gestor de jobs: lanza los motores existentes como subprocesos y permite
seguir su progreso en vivo (tail del JSONL de eventos) por WebSocket.
"""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import signal
import itertools
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

import sendgrid_stats
from config import (
    COOKIES_PATH,
    DEFAULT_BCC,
    DEFAULT_FROM_EMAIL,
    DEFAULT_FROM_NAME,
    DEFAULT_REPLY_TO,
    EMAIL_SENDER_PY,
    ENV_FILE,
    FOLLOWUP_RUNNER_PY,
    PYTHON,
    RESULTS_DIR,
    RUNNER_PY,
    TEMPLATES_DIR,
)
import store
from runs import read_jsonl

load_dotenv(ENV_FILE)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
log = logging.getLogger("jobs")


_seq = itertools.count(1)
_seq_lock = threading.Lock()


def _now_id() -> str:
    """Id de corrida único, con sufijo.

    Antes era solo la marca de tiempo al segundo. Con un job por asiento —que se
    lanzan en el mismo bucle— dos asientos caían en el MISMO run_id, y como el
    id nombra los archivos, el segundo job sobreescribía la lista de URLs del
    primero: un perfil terminaba visitando los contactos del otro y los eventos
    de los dos se mezclaban en un solo JSONL, sin forma de saber quién hizo qué.
    Pasó de verdad (2026-08-25) y por eso el sufijo no es decorativo.
    """
    with _seq_lock:
        n = next(_seq)
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{n:03d}"


def _cdp_de(opts: dict) -> str:
    """Endpoint CDP para este job.

    `PLATZI_LINKEDIN_CDP` es global y apunta a UN Chrome. Servía cuando había un
    solo perfil; con varios asientos es una trampa: un asiento que se conecta
    por cookies heredaría el Chrome de otro y el mensaje saldría firmado por
    quien no era. Por eso, si el job viene de un asiento (`opts["seat"]`), vale
    solo lo que traiga ese asiento — nunca el global.
    """
    propio = (opts.get("cdp_endpoint") or "").strip()
    if opts.get("seat"):
        return propio
    return propio or os.environ.get("PLATZI_LINKEDIN_CDP", "").strip()


def _compose_note(c: dict, max_chars: int = 280) -> str:
    nombre = (c.get("nombre") or "").split(" ")[0].title()
    cargo = (c.get("cargo") or "").strip()
    empresa = (c.get("empresa") or "").strip().title()
    saludo = f"Hola {nombre}," if nombre else "Hola,"
    if cargo and empresa:
        ctx = f" vi tu rol como {cargo} en {empresa} y"
    elif empresa:
        ctx = f" vi que trabajas en {empresa} y"
    else:
        ctx = ""
    msg = (saludo + ctx + " me encantaría conectar para generar impacto juntos.")
    return msg[:max_chars]


class Job:
    def __init__(self, job_id: str, channel: str, events_file: Path,
                 proc: subprocess.Popen, total: int, params: dict,
                 stderr_file: Optional[Path] = None):
        self.job_id = job_id
        self.channel = channel
        self.events_file = events_file
        self.stderr_file = stderr_file
        self.proc = proc
        self.total = total
        self.params = params
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self._status = "running"

    def _crash_reason(self) -> str:
        """Motivo legible de un subproceso que murió sin escribir eventos.
        Traduce las fallas típicas (intérprete sin deps, cookies faltantes) a
        algo accionable; si no, devuelve la última línea del stderr."""
        tail = ""
        if self.stderr_file and self.stderr_file.exists():
            try:
                lines = [l for l in self.stderr_file.read_text(
                    encoding="utf-8", errors="replace").splitlines() if l.strip()]
                tail = lines[-1] if lines else ""
            except OSError:
                tail = ""
        low = tail.lower()
        if "no module named" in low:
            return ("El intérprete de Python no tiene las dependencias del motor "
                    "(playwright/pandas). Configura PLATZI_PYTHON a un venv con ellas.")
        if "cookies" in low or "sesion-invalida" in low or "sesión" in low:
            return ("No hay sesión de LinkedIn válida (cookies). Inicia sesión y "
                    "guarda las cookies antes de lanzar conexiones/follow-ups.")
        return tail or "El motor terminó con error y no produjo eventos."

    def _emit_synthetic_error(self, reason: str) -> None:
        """Escribe un evento de error en el JSONL para que la UI muestre el
        motivo en vez de quedarse en 'Esperando primeros eventos…'."""
        try:
            with self.events_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(
                    {"idx": 1, "status": "error", "url": "—",
                     "detail": reason}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    @property
    def status(self) -> str:
        if self._status == "running" and self.proc.poll() is not None:
            self._status = "done" if self.proc.returncode == 0 else "error"
            if self._status == "error":
                # ¿murió sin producir un solo evento útil? Entonces el JSONL está
                # vacío y la UI se quedaría colgada esperando: inyecta el motivo.
                real = [e for e in read_jsonl(self.events_file) if e.get("idx", 0) != 0]
                if not real:
                    self._emit_synthetic_error(self._crash_reason())
            try:
                store.finish_job(self.job_id, self._status)
            except Exception:
                log.exception("no se pudo cerrar el job %s en store", self.job_id)
        return self._status

    def stop(self) -> None:
        if self.proc.poll() is None:
            try:
                self.proc.send_signal(signal.SIGINT)
            except Exception:
                self.proc.kill()
        self._status = "stopped"
        try:
            store.finish_job(self.job_id, "stopped")
        except Exception:
            log.exception("no se pudo marcar detenido el job %s", self.job_id)

    def progress(self) -> dict:
        events = [e for e in read_jsonl(self.events_file) if e.get("idx", 0) != 0]
        done = len(events)
        return {
            "job_id": self.job_id,
            "channel": self.channel,
            "status": self.status,
            "total": self.total,
            "done": done,
            "started_at": self.started_at,
            "events_file": self.events_file.name,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def _spawn(self, channel: str, run_id: str, events_file: Path,
              cmd: list[str], total: int, params: dict) -> Job:
        env = dict(os.environ)
        # stderr a un archivo (antes: DEVNULL). Si el motor muere al arrancar
        # —intérprete sin deps, cookies faltantes— aquí queda el motivo, que
        # Job._crash_reason() traduce a un evento de error visible en la UI.
        stderr_file = events_file.with_suffix(".stderr.log")
        stderr_fh = stderr_file.open("wb")
        proc = subprocess.Popen(
            cmd, cwd=str(events_file.parent.parent),
            stdout=subprocess.DEVNULL, stderr=stderr_fh, env=env,
        )
        stderr_fh.close()  # el hijo ya heredó su propio fd; evita fuga en el padre
        job = Job(run_id, channel, events_file, proc, total, params, stderr_file)
        with self._lock:
            self._jobs[run_id] = job
        try:
            store.log_job(run_id, channel, total, params, events_file.name)
        except Exception:
            log.exception("no se pudo registrar el job %s en store", run_id)
        return job

    # ---- LinkedIn connections -------------------------------------------
    def launch_linkedin(self, urls: list[str], opts: dict,
                        contacts: Optional[list[dict]] = None,
                        messages: Optional[dict[str, str]] = None) -> Job:
        """`messages` (url→nota) tiene prioridad sobre la nota autogenerada;
        lo usa la cola de aprobación del copiloto con drafts ya aprobados."""
        run_id = _now_id()
        urls_file = RESULTS_DIR / f"{run_id}-urls.txt"
        events_file = RESULTS_DIR / f"{run_id}-events.jsonl"
        urls_file.write_text("\n".join(urls), encoding="utf-8")

        cmd = [PYTHON, str(RUNNER_PY),
               "--urls-file", str(urls_file),
               "--events-file", str(events_file),
               "--cookies", str(opts.get("cookies") or COOKIES_PATH),
               "--daily-cap", str(opts.get("daily_cap", 20)),
               "--min-delay", str(opts.get("min_delay", 8.0)),
               "--max-delay", str(opts.get("max_delay", 18.0))]
        # Si hay un Chrome con remote debugging (el del asiento, o
        # PLATZI_LINKEDIN_CDP para un job manual), el runner se conecta a esa
        # sesión real en vez de inyectar cookies.
        cdp = _cdp_de(opts)
        if cdp:
            cmd += ["--cdp-endpoint", cdp]
        if opts.get("headless"):
            cmd.append("--headless")
        if opts.get("sales_nav"):
            cmd.append("--sales-nav")
        if messages is None and opts.get("with_notes") and contacts:
            messages = {c["linkedin"]: _compose_note(c) for c in contacts if c.get("linkedin")}
        if messages:
            msg_file = RESULTS_DIR / f"{run_id}-messages.json"
            msg_file.write_text(json.dumps(messages, ensure_ascii=False, indent=2),
                                encoding="utf-8")
            cmd += ["--messages-file", str(msg_file)]
        return self._spawn("linkedin", run_id, events_file, cmd, len(urls), opts)

    # ---- LinkedIn follow-ups --------------------------------------------
    def launch_followup(self, urls: list[str], opts: dict,
                       contacts: Optional[list[dict]] = None,
                       messages: Optional[dict[str, str]] = None) -> Job:
        run_id = _now_id()
        urls_file = RESULTS_DIR / f"{run_id}-followup-urls.txt"
        events_file = RESULTS_DIR / f"{run_id}-followup-events.jsonl"
        urls_file.write_text("\n".join(urls), encoding="utf-8")

        msg_file = RESULTS_DIR / f"{run_id}-followup-messages.json"
        text = opts.get("message", "").strip()
        messages = dict(messages) if messages else {}
        if not messages and contacts:
            for c in contacts:
                if not c.get("linkedin"):
                    continue
                nombre = (c.get("nombre") or "").split(" ")[0].title()
                m = text.replace("{nombre}", nombre).replace("{empresa}", c.get("empresa", ""))
                messages[c["linkedin"]] = m or text
        msg_file.write_text(json.dumps(messages, ensure_ascii=False, indent=2),
                            encoding="utf-8")

        cmd = [PYTHON, str(FOLLOWUP_RUNNER_PY),
               "--urls-file", str(urls_file),
               "--events-file", str(events_file),
               "--messages-file", str(msg_file),
               "--cookies", str(opts.get("cookies") or COOKIES_PATH),
               "--daily-cap", str(opts.get("daily_cap", 30)),
               "--min-delay", str(opts.get("min_delay", 6.0)),
               "--max-delay", str(opts.get("max_delay", 14.0))]
        # Mismo criterio que las invitaciones: si el asiento tiene su Chrome
        # abierto, el mensaje sale de esa sesión y no de cookies inyectadas.
        cdp = _cdp_de(opts)
        if cdp:
            cmd += ["--cdp-endpoint", cdp]
        if opts.get("default_message"):
            cmd += ["--default-message", opts["default_message"]]
        if opts.get("headless"):
            cmd.append("--headless")
        return self._spawn("followup", run_id, events_file, cmd, len(urls), opts)

    # ---- Email -----------------------------------------------------------
    def launch_email(self, recipients: list[dict], opts: dict) -> Job:
        run_id = _now_id()
        rec_file = RESULTS_DIR / f"{run_id}-mail-recipients.csv"
        events_file = RESULTS_DIR / f"{run_id}-mail-events.jsonl"

        cols = ["email", "nombre", "nombre_completo", "empresa", "cargo",
                "ciudad", "linkedin", "contact_id", "custom_subject", "custom_body"]
        with rec_file.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in recipients:
                w.writerow({
                    "email": r.get("email", ""),
                    "nombre": (r.get("nombre", "") or "").split(" ")[0].title(),
                    "nombre_completo": r.get("nombre", ""),
                    "empresa": r.get("empresa", ""),
                    "cargo": r.get("cargo", ""),
                    "ciudad": r.get("ciudad", ""),
                    "linkedin": r.get("linkedin", ""),
                    "contact_id": r.get("email", ""),  # contact_id = email para atribución
                    "custom_subject": r.get("custom_subject", ""),
                    "custom_body": r.get("custom_body", ""),
                })

        cmd = [PYTHON, str(EMAIL_SENDER_PY),
               "--recipients-file", str(rec_file),
               "--events-file", str(events_file),
               "--from-email", opts.get("from_email") or os.environ.get("SENDGRID_FROM_EMAIL") or DEFAULT_FROM_EMAIL,
               "--from-name", opts.get("from_name") or os.environ.get("SENDGRID_FROM_NAME") or DEFAULT_FROM_NAME,
               "--reply-to", opts.get("reply_to") or os.environ.get("SENDGRID_REPLY_TO") or DEFAULT_REPLY_TO,
               "--min-delay", str(opts.get("min_delay", 2.0)),
               "--max-delay", str(opts.get("max_delay", 5.0)),
               # explícito (no depender del default de email_sender.py): TODO
               # correo del cockpit —manual o del copiloto— debe quedar bajo
               # esta única categoría en SendGrid, para no mezclarse con otros
               # sistemas que comparten la misma cuenta de SendGrid. Misma
               # constante que usan las stats (sendgrid_stats.CATEGORY): si se
               # cambia por env var, envío y lectura de stats quedan en sync.
               "--category", sendgrid_stats.CATEGORY]
        if opts.get("template"):
            cmd += ["--template", str(TEMPLATES_DIR / opts["template"])]
        if opts.get("subject"):
            cmd += ["--subject", opts["subject"]]
        # Sin BCC configurado no se pasa la bandera: un "--bcc ''" hace que el
        # envío falle con una dirección vacía en vez de simplemente no copiar.
        for b in (opts.get("bcc") or [os.environ.get("SENDGRID_BCC") or DEFAULT_BCC]):
            if (b or "").strip():
                cmd += ["--bcc", b]
        if opts.get("dry_run"):
            cmd.append("--dry-run")
        else:
            cmd.append("--track")
            cmd += ["--campaign-id", opts.get("campaign", run_id), "--run-id", run_id]
        return self._spawn("email", run_id, events_file, cmd, len(recipients), opts)

    # ---- introspection ---------------------------------------------------
    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list(self) -> list[dict]:
        return [j.progress() for j in self._jobs.values()]


manager = JobManager()


async def tail_events(job: Job, send) -> None:
    """Envía eventos nuevos por WS hasta que el job termina y se agota el archivo."""
    sent = 0
    while True:
        events = read_jsonl(job.events_file)
        if len(events) > sent:
            for e in events[sent:]:
                await send(json.dumps({"type": "event", "data": e}, ensure_ascii=False))
            sent = len(events)
        prog = job.progress()
        await send(json.dumps({"type": "progress", "data": prog}, ensure_ascii=False))
        if prog["status"] != "running":
            # vuelco final por si quedó algo en buffer
            events = read_jsonl(job.events_file)
            for e in events[sent:]:
                await send(json.dumps({"type": "event", "data": e}, ensure_ascii=False))
            await send(json.dumps({"type": "done", "data": prog}, ensure_ascii=False))
            return
        await asyncio.sleep(1.0)
