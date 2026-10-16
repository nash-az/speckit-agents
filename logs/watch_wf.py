"""Follow a workflow transcript dir: one line per journal event, plus refused or failed tool calls inside agents."""
import glob
import json
import os
import sys
import time

wf, main_stream = sys.argv[1], sys.argv[2]
seen_journal = 0
offsets = {}
labels = {}
started, finished = {}, set()
last_beat = time.time()
if "--from-now" in sys.argv:
    journal0 = os.path.join(wf, "journal.jsonl")
    if os.path.exists(journal0):
        for line in open(journal0).read().splitlines():
            ev = json.loads(line)
            if ev.get("agentId") and ev.get("label"):
                labels[ev["agentId"]] = ev["label"]
            if ev.get("type") == "started":
                started[ev.get("agentId")] = 1
            elif ev.get("type") in ("result", "failed"):
                finished.add(ev.get("agentId"))
            seen_journal += 1
    for path in glob.glob(os.path.join(wf, "agent-*.jsonl")):
        offsets[path] = os.path.getsize(path)


def short(text, n=220):
    text = text.replace("\n", " ")
    return text if len(text) <= n else text[:n] + "…"


while True:
    journal = os.path.join(wf, "journal.jsonl")
    if os.path.exists(journal):
        lines = open(journal).read().splitlines()
        for line in lines[seen_journal:]:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("agentId") and ev.get("label"):
                labels[ev["agentId"]] = ev["label"]
            kind = ev.get("type")
            if kind == "started":
                started[ev.get("agentId")] = 1
            elif kind in ("result", "failed"):
                finished.add(ev.get("agentId"))
            label = ev.get("label") or labels.get(ev.get("agentId"), "")
            if kind == "started":
                print(f"{time.strftime('%H:%M')} start {label}", flush=True)
            elif kind != "launched":
                rest = {k: v for k, v in ev.items() if k not in ("type", "key", "agentId", "label", "phase")}
                print(f"{time.strftime('%H:%M')} {kind} {label} {short(json.dumps(rest), 300)}", flush=True)
        seen_journal = len(lines)
    for path in glob.glob(os.path.join(wf, "agent-*.jsonl")):
        agent = os.path.basename(path)[6:-6]
        with open(path) as fh:
            fh.seek(offsets.get(path, 0))
            for line in fh:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = ev.get("message", {}).get("content")
                for block in content if isinstance(content, list) else []:
                    if block.get("type") != "tool_result":
                        continue
                    body = block.get("content")
                    if isinstance(body, list):
                        body = " ".join(b.get("text", "") for b in body if isinstance(b, dict))
                    body = body or ""
                    if body.startswith(("Permission", "<tool_use_error>")) or "denied" in body[:200] or "guard" in body[:200].lower():
                        print(f"{time.strftime('%H:%M')} ✗ {labels.get(agent, agent)}: {short(body)}", flush=True)
            offsets[path] = fh.tell()
    if os.path.exists(main_stream) and '"type":"result"' in open(main_stream).read()[-200000:]:
        print("main loop finished", flush=True)
        break
    now = time.time()
    if now - last_beat >= 600:
        last_beat = now
        current = next((labels.get(k, k) for k in reversed(list(started)) if k not in finished), "none")
        agents = glob.glob(os.path.join(wf, "agent-*.jsonl"))
        newest = max((os.path.getmtime(a) for a in agents), default=now)
        alive = os.system("pgrep -f 'claude -p /sdd-run' >/dev/null") == 0
        print(f"{time.strftime('%H:%M')} ♥ on {current}; last agent activity {int((now - newest) / 60)} min ago; run process {'alive' if alive else 'GONE'}", flush=True)
    time.sleep(15)
