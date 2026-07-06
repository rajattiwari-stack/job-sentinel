"""Static HTML dashboard (docs/index.html) generated from tracker.xlsx.

Published free via GitHub Pages (Settings → Pages → main branch → /docs), so you
get a private-ish job board URL you can open on your phone: newest jobs first,
search box, applied/pending badges, direct apply links. Read-only view — the
Excel file remains the single source of truth for the Applied? state.
"""
from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("report")


def write_dashboard(tracker_path: Path, out_path: Path) -> None:
    from .tracker import _read_existing  # reuse the tolerant reader
    rows = _read_existing(Path(tracker_path))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload = [
        {
            "d": r.get("Date Found", ""), "c": r.get("Company", ""),
            "t": r.get("Position", ""), "l": r.get("Location", ""),
            "e": r.get("Experience", ""), "k": r.get("Matched Keywords", ""),
            "a": r.get("Applied?", ""), "u": r.get("Link", ""),
        }
        for r in rows
    ]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    doc = _TEMPLATE.replace("__DATA__", html.escape(json.dumps(payload), quote=False)) \
                   .replace("__STAMP__", stamp).replace("__COUNT__", str(len(rows)))
    out_path.write_text(doc, "utf-8")
    log.info("Dashboard written: %s (%d jobs)", out_path, len(rows))


_TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Job Sentinel</title>
<style>
:root{--bg:#0f1720;--card:#182430;--tx:#e7eef5;--mut:#8fa3b5;--acc:#4cc2ff;--ok:#2fbf71;--warn:#f4b942}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--tx);padding:16px}
h1{font-size:20px;margin:0 0 2px}.sub{color:var(--mut);font-size:12px;margin-bottom:14px}
input{width:100%;padding:10px 12px;border-radius:10px;border:1px solid #2a3a4a;background:var(--card);color:var(--tx);font-size:15px;margin-bottom:12px}
.card{background:var(--card);border:1px solid #223243;border-radius:12px;padding:12px 14px;margin-bottom:10px}
.card a{color:var(--acc);text-decoration:none;font-weight:600;font-size:16px}
.meta{color:var(--mut);font-size:13px;margin-top:4px}
.badge{display:inline-block;font-size:11px;padding:2px 8px;border-radius:20px;margin-left:6px;vertical-align:2px}
.yes{background:rgba(47,191,113,.15);color:var(--ok)}.no{background:rgba(244,185,66,.15);color:var(--warn)}
.kw{font-size:12px;color:var(--acc);margin-top:4px}
</style></head><body>
<h1>🛡️ Job Sentinel</h1>
<div class="sub">__COUNT__ jobs tracked · updated __STAMP__ · mark “Applied” in tracker.xlsx</div>
<input id="q" placeholder="Search company, title, location, keyword…" oninput="draw()">
<div id="list"></div>
<script>
const jobs = __DATA__;
function draw(){
  const q=(document.getElementById('q').value||'').toLowerCase();
  document.getElementById('list').innerHTML = jobs.filter(j=>
    !q || (j.c+' '+j.t+' '+j.l+' '+j.k).toLowerCase().includes(q)
  ).map(j=>`<div class="card">
    <a href="${j.u}" target="_blank" rel="noopener">${esc(j.t)}</a>
    <span class="badge ${j.a==='Yes'?'yes':'no'}">${j.a==='Yes'?'Applied ✓':'Not applied'}</span>
    <div class="meta">🏢 ${esc(j.c)} · 📍 ${esc(j.l||'—')} · ⏳ ${esc(j.e||'—')} · 🗓 ${esc(j.d)}</div>
    <div class="kw">${esc(j.k)}</div>
  </div>`).join('') || '<div class="sub">No matches.</div>';
}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
draw();
</script></body></html>
"""
