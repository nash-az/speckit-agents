"""Condense a `claude -p --output-format stream-json` file: tool calls, errors, denials, result."""
import json
import sys


def short(value, n=160):
    text = value if isinstance(value, str) else json.dumps(value)
    text = text.replace("\n", " ")
    return text if len(text) <= n else text[:n] + "…"


def main(path, verbose=False):
    calls = {}
    for line in open(path):
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "init":
            print(f"init: model={ev.get('model')} mode={ev.get('permissionMode')} cwd={ev.get('cwd')}")
        elif kind == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    calls[block["id"]] = block["name"]
                    inp = block.get("input", {})
                    arg = inp.get("command") or inp.get("file_path") or inp.get("skill") or inp.get("name") or inp
                    print(f"→ {block['name']}: {short(arg)}")
                elif block.get("type") == "text" and verbose:
                    print(f"  say: {short(block['text'], 300)}")
        elif kind == "user":
            content = ev.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if block.get("type") != "tool_result":
                    continue
                body = block.get("content")
                if isinstance(body, list):
                    body = " ".join(b.get("text", "") for b in body if isinstance(b, dict))
                body = body or ""
                flag = block.get("is_error") or body.startswith(("Permission", "Error", "<tool_use_error>"))
                if flag:
                    print(f"  ✗ {calls.get(block.get('tool_use_id'), '?')}: {short(body, 300)}")
        elif kind == "result":
            print(f"result: {ev.get('subtype')} turns={ev.get('num_turns')} cost=${ev.get('total_cost_usd', 0):.2f} "
                  f"secs={ev.get('duration_ms', 0) / 1000:.0f}")
            for d in ev.get("permission_denials", []) or []:
                print(f"  denial: {d.get('tool_name')} {short(d.get('tool_input'))}")
            print("final:", short(ev.get("result", ""), 4000))


if __name__ == "__main__":
    main(sys.argv[1], verbose="-v" in sys.argv)
