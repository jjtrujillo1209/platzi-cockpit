"""Valida que cada contacto con perfil de LinkedIn pertenece a la empresa que
figura en la base (columna FRONTERAS_ATIENDE — EMPRESA_REAL suele ser 'OTROS').

Visita el perfil con la sesión guardada, extrae la empresa actual (top-card +
headline + primera experiencia) y la compara con la empresa de la base por
solape de tokens. Escribe:
  - results/<run_id>-validacion-events.jsonl  (progreso en vivo, idx/total/status)
  - VALIDACION_LINKEDIN.csv                    (tabla final con el veredicto)

Status por fila:
  match           empresa de la base aparece en el perfil
  partial         coincidencia parcial (algún token relevante)
  mismatch        el perfil muestra empresa distinta
  empty_profile   no se pudo leer empresa del perfil
  not_found       perfil no carga / 404 / redirigido
  rate_limited    LinkedIn pidió login/checkpoint  (se detiene)
  error
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

STOP = {"sa", "sas", "esp", "ltda", "s", "a", "de", "del", "la", "el", "y", "cia",
        "co", "com", "colombia", "planta", "zona", "franca", "grupo", "corp",
        "group", "inc", "the", "and", "company", "compania", "compañia", "ind",
        "industrias", "industria", "comercializadora", "internacional"}


def toks(s: str) -> set[str]:
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    return {t for t in s.split() if len(t) > 2 and t not in STOP}


def normalize_url(raw: str) -> str | None:
    raw = (raw or "").strip()
    if raw.startswith("linkedin.com"):
        raw = "https://www." + raw
    if raw.startswith("www.linkedin.com"):
        raw = "https://" + raw
    m = re.match(r"https?://[^/]*linkedin\.com/in/([^/?#]+)", raw, re.I)
    return f"https://www.linkedin.com/in/{m.group(1)}/" if m else None


def load_cookies(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        data = data.get("cookies", [])
    out = []
    for c in data:
        c = {k: v for k, v in c.items() if k in {
            "name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"}}
        if c.get("sameSite") not in {"Strict", "Lax", "None"}:
            c["sameSite"] = "Lax"
        out.append(c)
    return out


@dataclass
class Result:
    idx: int
    total: int
    nombre: str
    empresa_base: str
    empresa_linkedin: str
    headline: str
    status: str
    score: float
    url: str
    ts: str = ""

    def ev(self):
        d = asdict(self)
        d["ts"] = self.ts or datetime.now().isoformat(timespec="seconds")
        return d


SIDEBAR_MARKERS = ["Personas que también", "Gente que quizá", "Personas similares",
                   "People also viewed", "Otras personas", "Anuncio", "Promovido"]


async def extract_profile(page: Page) -> tuple[str, str, str]:
    """Devuelve (empresa_actual, headline, haystack).

    Lee texto real del perfil con tres señales: headline ('X at EMPRESA'),
    empresa del top-card (línea tras 'Información de contacto') y la sección
    Experiencia. Recorta el sidebar 'Personas que también vieron' para no
    contaminar el match con empresas de OTRAS personas.
    """
    # Scroll progresivo para forzar render del top-card y de Experiencia.
    for y in (400, 900, 1600, 2400):
        try:
            await page.mouse.wheel(0, y)
            await asyncio.sleep(0.6)
        except Exception:
            pass
    try:
        data = await page.evaluate(
            """() => {
              const m = document.querySelector('main') || document.body;
              let top = m.innerText || "";
              let exp = "";
              const a = document.getElementById('experience');
              if (a) { const s = a.closest('section'); if (s) exp = s.innerText.slice(0, 1200); }
              return { top: top.slice(0, 1800), exp };
            }"""
        )
    except Exception:
        data = {"top": "", "exp": ""}
    top, exp = data.get("top", ""), data.get("exp", "")

    # Recorta el sidebar de "otras personas" para evitar falsos positivos.
    cut = len(top)
    for mk in SIDEBAR_MARKERS:
        i = top.find(mk)
        if i != -1:
            cut = min(cut, i)
    top = top[:cut]

    lines = [l.strip() for l in top.split("\n") if l.strip() and l.strip() != "·"]

    # Headline: primera línea con ' at '/' en ' (cargo en empresa).
    headline = ""
    for l in lines[1:7]:
        if re.search(r"\b(at|en|@)\b", l, re.I) and len(l) < 140 and not l.startswith("·"):
            headline = l
            break

    # Empresa del top-card: línea inmediatamente después de "Información de contacto".
    top_company = ""
    for i, l in enumerate(lines):
        if "Información de contacto" in l or "Contact info" in l:
            if i + 1 < len(lines):
                top_company = lines[i + 1]
            break

    # Empresa desde el headline (tras 'at'/'en').
    empresa = top_company
    m = re.search(r"\b(?:at|en)\b\s+(.+)$", headline, re.I)
    if m and not empresa:
        empresa = m.group(1).strip()

    haystack = " \n ".join([headline, top_company, exp, top[:600]])
    return empresa, headline, haystack


def verdict(empresa_base: str, empresa_li: str, headline: str, exp_text: str) -> tuple[str, float]:
    base = toks(empresa_base)
    if not base:
        return "empty_profile", 0.0
    hay = " ".join([empresa_li, headline, exp_text])
    found = toks(hay)
    if not found:
        return "empty_profile", 0.0
    inter = base & found
    score = round(len(inter) / max(1, len(base)) * 100, 1)
    if score >= 60:
        return "match", score
    if score > 0:
        return "partial", score
    return "mismatch", 0.0


async def _process_one(page, idx, total, r) -> Result:
    url = normalize_url(r["url"]) or r["url"]
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(random.uniform(1.5, 2.5))
        if any(x in page.url for x in ("login", "checkpoint", "authwall")):
            return Result(idx, total, r["nombre"], r["empresa"], "", "", "rate_limited", 0, url)
        if "/in/" not in page.url:
            return Result(idx, total, r["nombre"], r["empresa"], "", "", "not_found", 0, url)
        emp, head, exp = await extract_profile(page)
        st, score = verdict(r["empresa"], emp, head, exp)
        return Result(idx, total, r["nombre"], r["empresa"], emp, head, st, score, url)
    except Exception:
        return Result(idx, total, r["nombre"], r["empresa"], "", "", "error", 0, url)


async def run(rows, cookies_path, events_path, *, headless, min_delay, max_delay,
              limit, concurrency=1):
    cookies = load_cookies(cookies_path)
    total = min(limit, len(rows)) if limit else len(rows)
    work = list(enumerate(rows[:total], 1))
    results: list[Result] = []
    write_lock = asyncio.Lock()
    stop = asyncio.Event()
    queue: asyncio.Queue = asyncio.Queue()
    for item in work:
        queue.put_nowait(item)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless,
            args=["--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context(viewport={"width": 1366, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        await ctx.add_cookies(cookies)

        # Chequeo de sesión una vez.
        probe = await ctx.new_page()
        try:
            await probe.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=25000)
        except Exception:
            pass
        if any(x in probe.url for x in ("login", "checkpoint", "authwall")):
            _write(events_path, Result(0, total, "", "", "", "", "rate_limited", 0, probe.url))
            await browser.close(); return results
        await probe.close()

        async def worker():
            page = await ctx.new_page()
            while not stop.is_set():
                try:
                    idx, r = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                res = await _process_one(page, idx, total, r)
                async with write_lock:
                    _write(events_path, res)
                    results.append(res)
                if res.status == "rate_limited":
                    stop.set()
                    break
                await asyncio.sleep(random.uniform(min_delay, max_delay))
            await page.close()

        await asyncio.gather(*[worker() for _ in range(max(1, concurrency))])
        await browser.close()
    results.sort(key=lambda x: x.idx)
    return results


def _write(path: Path, res: Result):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(res.ev(), ensure_ascii=False) + "\n")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--cookies", required=True)
    p.add_argument("--out", required=True, help="CSV de resultados")
    p.add_argument("--events-file", required=True)
    p.add_argument("--limit", type=int, default=0, help="0 = todos")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--min-delay", type=float, default=5.0)
    p.add_argument("--max-delay", type=float, default=11.0)
    p.add_argument("--concurrency", type=int, default=1,
                   help="Perfiles en paralelo (pestañas). 3-4 acelera mucho.")
    return p.parse_args()


def main():
    a = parse_args()
    src = list(csv.DictReader(open(a.csv, encoding="utf-8-sig")))
    rows = []
    for r in src:
        url = (r.get("LINKEDIN_PROFILE") or "").strip()
        if "linkedin.com/in/" in url.lower():
            rows.append({"nombre": (r.get("CONTACTO_NOMBRE") or "").strip(),
                         "empresa": (r.get("FRONTERAS_ATIENDE") or "").strip(),
                         "url": url})
    ev = Path(a.events_file); ev.parent.mkdir(parents=True, exist_ok=True); ev.write_text("")
    results = asyncio.run(run(rows, Path(a.cookies), ev,
        headless=a.headless, min_delay=a.min_delay, max_delay=a.max_delay,
        limit=a.limit, concurrency=a.concurrency))
    with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["nombre", "empresa_base", "empresa_linkedin", "headline", "veredicto", "score", "url"])
        for r in results:
            if r.idx == 0:
                continue
            w.writerow([r.nombre, r.empresa_base, r.empresa_linkedin, r.headline, r.status, r.score, r.url])
    from collections import Counter
    c = Counter(r.status for r in results if r.idx)
    print("RESUMEN:", dict(c))
    print("CSV:", a.out)


if __name__ == "__main__":
    main()
