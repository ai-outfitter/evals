"""Private, loopback-only evidence browser. All model/source text is escaped."""
from functools import partial
from html import escape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import time
from pathlib import Path
from urllib.parse import quote
from .resident import failure_state


def page(title, content):
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>{escape(title)}</title><style>body{{max-width:1100px;margin:3rem auto;padding:0 1rem;"
            "font:16px/1.55 system-ui;background:#161616;color:#eee}a{color:#a8d3ff}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#242424;padding:1rem;border-radius:8px}"
            "details{margin:1rem 0}small{color:#bbb}table{width:100%;border-collapse:collapse}"
            "td,th{text-align:left;padding:.7rem;border-bottom:1px solid #444;overflow-wrap:anywhere}</style>"
            f"<h1>{escape(title)}</h1>{content}")


def render(root: Path):
    rows = []
    for outcome_file in sorted(root.glob("runs/**/outcome.json")):
        outcome = json.loads(outcome_file.read_text())
        relative = outcome_file.parent.relative_to(root)
        report = quote((relative / ("report.md" if (outcome_file.parent/"report.md").exists() else "outcome.json")).as_posix())
        status = outcome['status']
        if status == 'blocked':
            status = failure_state(outcome.get('error', {}).get('message', ''))
        progress_file = outcome_file.parent / "progress.json"
        if status == "running" and progress_file.exists():
            progress = json.loads(progress_file.read_text())
            status = "stalled" if time.time() > progress.get("deadline", float("inf")) else progress.get("state", status)
        if status == "running" and not progress_file.exists() and time.time() - outcome_file.stat().st_mtime > 660:
            status = "stalled (legacy run)"
        reason = outcome.get("error", {}).get("message", "")
        rows.append(f"<tr><td>{escape(str(relative))}</td><td>{escape(status)}</td>"
                    f"<td>{escape(reason)}<br><a href='{report}'>Run status/report</a> · <a href='{quote((relative/'steps').as_posix())}/'>Inputs and steps</a></td></tr>")
    artifacts = []
    for transcript_file in sorted(root.glob("adapter/**/runtime/transcripts.json")):
        runtime = transcript_file.parent
        conversation = []
        for session in json.loads(transcript_file.read_text()):
            for line in session["content"].splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue  # A live checkpoint can end during the last append.
                message = entry.get("message")
                if not message:
                    continue
                role = message.get("role", "event")
                content = message.get("content", "")
                if isinstance(content, list):
                    content = [{k:v for k,v in block.items() if k != "thinking"} if block.get("type") == "thinking" else block for block in content]
                text = content if isinstance(content, str) else json.dumps(content, indent=2, ensure_ascii=False)
                conversation.append(f"<details {'open' if role != 'system' else ''}><summary>{escape(role)} "
                    f"{escape(message.get('toolName',''))}</summary><pre>{escape(text)}</pre></details>")
        (runtime / "conversation.html").write_text(page("Resident task conversation", "".join(conversation)))
        observations_file = runtime / "provider-observations.json"
        if observations_file.exists():
            observations = json.loads(observations_file.read_text())
            content = "".join(f"<details><summary>Provider request {index+1}</summary>"
                f"<pre>{escape(json.dumps(item,indent=2,ensure_ascii=False))}</pre></details>"
                for index,item in enumerate(observations))
            (runtime / "system-prompts.html").write_text(page("Exact provider system prompts", content))
        relative = runtime.relative_to(root)
        links = []
        for name, label in [("conversation.html","Conversation"),("system-prompts.html","System prompts"),
                            ("accepted-envelope.json","Accepted input"),("result.json","Output / status"),
                            ("transcripts.json","Raw transcript"),("mcp-audit.json","MCP activity")]:
            if (runtime/name).exists():
                links.append(f"<a href='{quote((relative/name).as_posix())}'>{escape(label)}</a>")
        artifacts.append(f"<li><small>{escape(runtime.parent.name)}</small><br>{' · '.join(links)}</li>")
    datasets = "".join(f"<li><a href='{quote(path.name)}'>{escape(path.name)}</a></li>"
                       for path in sorted(root.glob("*.json")) if "config" not in path.name)
    content = ("<p>Local evaluation evidence. Candidate queues are separate from registered resident tasks. "
               "Saved drafts do not imply approval or adoption.</p><h2>Runs</h2><table>"
               "<tr><th>Run</th><th>Status</th><th>Evidence</th></tr>" + "".join(rows) + "</table>"
               "<h2>Task inputs and outputs</h2><ul>" + "".join(artifacts) + "</ul>"
               "<h2>Captured graph and schemas</h2><ul>" + datasets + "</ul>")
    (root/"index.html").write_text(page("Executive document evaluations",content))


def serve(root: Path, port: int):
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("Evidence directory does not exist")
    render(root)
    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                render(root)
            super().do_GET()
    server = ThreadingHTTPServer(("127.0.0.1",port),partial(Handler,directory=str(root)))
    print(f"Private evidence browser: http://127.0.0.1:{server.server_port}/",flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
