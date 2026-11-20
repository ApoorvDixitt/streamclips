#!/usr/bin/env python3
"""Optional local dashboard for a campaign folder (read-only, stdlib only). Start it when the user wants to watch progress.

  dashboard.py --campaign <dir> [--port 8787] [--open]

Shows: live watcher status + hot moments, candidates per source, today's clips with thumbnails and review results, index.csv.
Refreshes every 15 s. Binds to 127.0.0.1 only.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from common import find_campaign, load_yaml, main_guard, read_json

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="15">
<title>streamclips · {name}</title>
<style>body{{font:14px -apple-system,Segoe UI,Roboto,sans-serif;margin:24px;background:#111;color:#eee}}
h1,h2{{font-weight:600}} table{{border-collapse:collapse;width:100%}} td,th{{border-bottom:1px solid #333;padding:6px 8px;text-align:left;vertical-align:top}}
.ok{{color:#5fd38d}} .bad{{color:#ff6b6b}} .card{{display:inline-block;width:220px;margin:8px;vertical-align:top;background:#1b1b1b;border-radius:8px;padding:8px}}
img{{max-width:100%;border-radius:6px}} code{{color:#9cf}} small{{color:#999}}</style></head><body>
<h1>{name} <small>{folder}</small></h1>
<h2>Live watchers</h2>{live}
<h2>Clips ({n_clips})</h2>{clips}
<h2>Candidates</h2>{cands}
<h2>index.csv</h2>{index}
</body></html>"""


def render(folder: Path) -> str:
    cfg = load_yaml(folder / "campaign.yaml")
    live = []
    for st in sorted((folder / "live").glob("*/status.json")):
        s = read_json(st, {})
        hot = read_json(st.parent / "hot.json", {}).get("hot", [])[:5]
        live.append(f"<p><b>{html.escape(str(s.get('url')))}</b> · {s.get('state')} · segments {s.get('segments')} · buffer {s.get('buffer_minutes')} min · chat {s.get('chat_messages')}<br>"
                    + "".join(f"<code>t={h['t']}</code> ×{h['ratio']} {html.escape(' | '.join(h['sample'][:3]))}<br>" for h in hot) + "</p>")
    clips = []
    for day in sorted((p for p in folder.iterdir() if p.is_dir() and p.name[:4].isdigit()), reverse=True):
        for c in sorted(p for p in day.iterdir() if p.is_dir()):
            spec, rev, meta = read_json(c / "spec.json", {}), read_json(c / "review.json", {}), read_json(c / "metadata.json", {})
            rel = c.relative_to(folder)
            status = meta.get("status") or ("reviewed " + ("ok" if rev.get("ok") else "FAIL") if rev else "rendered" if (c / "clip.mp4").exists() else "spec")
            cls = "ok" if rev.get("ok") else "bad" if rev else ""
            img = f'<a href="/{rel}/clip.mp4"><img src="/{rel}/thumb.jpg"></a>' if (c / "thumb.jpg").exists() else ""
            clips.append(f'<div class="card">{img}<b>{html.escape(spec.get("hook", c.name))}</b><br><small>{rel}</small><br><span class="{cls}">{status}</span>'
                         f'<br><small>{meta.get("post_at") or ""} {meta.get("audience") or ""}</small></div>')
    cands = []
    for cj in sorted((folder / "sources").glob("*/candidates.json")):
        d = read_json(cj, {})
        rows = "".join(f"<tr><td>{c['id']}</td><td>{c['score']}</td><td>{json.dumps(c['components'])}</td><td>{c.get('chat_label','')}</td>"
                       f"<td>{html.escape((c.get('transcript') or '')[:120])}</td><td>{'<a href=/'+c['frames']+'>frames</a>' if c.get('frames') else ''}</td>"
                       f"<td>{html.escape(json.dumps(c.get('agent',''))[:80])}</td></tr>" for c in d.get("candidates", [])[:15])
        cands.append(f"<h3>{cj.parent.name} <small>updated {d.get('updated','')}</small></h3><table><tr><th>id</th><th>score</th><th>components</th><th>chat</th><th>transcript</th><th></th><th>agent</th></tr>{rows}</table>")
    idx = ""
    if (folder / "index.csv").exists():
        rows = list(csv.DictReader(open(folder / "index.csv", encoding="utf-8")))
        cols = ["clip_id", "hook", "duration_s", "score", "status", "post_count", "views_tiktok", "views_reels", "views_shorts", "approved", "payout_usd"]
        idx = "<table><tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>" + "".join("<tr>" + "".join(f"<td>{html.escape(str(r.get(c,'')))}</td>" for c in cols) + "</tr>" for r in rows) + "</table>"
    return PAGE.format(name=html.escape(cfg.get("name", "")), folder=html.escape(str(folder)), live="".join(live) or "<p>none</p>",
                       n_clips=len(clips), clips="".join(clips) or "<p>none yet</p>", cands="".join(cands) or "<p>none yet</p>", index=idx)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campaign")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    folder = find_campaign(a.campaign)

    class H(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kw):
            super().__init__(*args, directory=str(folder), **kw)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                body = render(folder).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                super().do_GET()

        def log_message(self, *a):  # quiet
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    url = f"http://127.0.0.1:{a.port}/"
    print(json.dumps({"ok": True, "url": url, "campaign": str(folder)}), flush=True)
    if a.open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main_guard(main)
