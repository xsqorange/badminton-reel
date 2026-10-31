"""
取一个有效的 context_token。

策略:用 getupdates 以"旧游标"拉取,服务端会重放消息(含 context_token)。
桌面端 monitor 用的是推进后的游标,不受影响。
只取 context_token,不使用消息内容。
"""
import sys, os, re, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import ilink_client as ic

CFG = r"C:\Users\Admin\.minimax\wechat-channel.yaml"
txt = open(CFG, encoding="utf-8").read()
token = re.search(r"botToken:\s*(\S+)", txt).group(1).strip()
chat_id = "<REDACTED-CHAT-ID>"

# 空游标 -> 服务端可能重放最近的消息
body = {
    "get_updates_buf": "",
    "base_info": {"channel_version": ic.CHANNEL_VERSION, "bot_agent": ic.BOT_AGENT},
}
st, resp = ic.api_call("ilink/bot/getupdates", body, token, timeout=40)
print("getupdates status:", st)
try:
    data = json.loads(resp)
except Exception:
    print(resp[:800]); raise SystemExit
print("ret:", data.get("ret"), "errcode:", data.get("errcode"), "errmsg:", data.get("errmsg"))
msgs = data.get("msgs") or []
print("msgs:", len(msgs))
found = []
for m in msgs:
    ct = m.get("context_token")
    to = m.get("to_user_id") or m.get("from_user_id")
    print("  msg keys:", list(m.keys())[:14])
    print(f"    from={m.get('from_user_id')} to={m.get('to_user_id')} ctx_len={len(ct) if ct else 0} type={m.get('message_type')}")
    if ct:
        found.append((m.get("from_user_id"), m.get("to_user_id"), ct))
if found:
    print("\nFOUND context tokens:")
    for f, t, ct in found:
        print(f"  from={f} to={t} ctx={ct[:60]}...")
    json.dump([{"from": f, "to": t, "ctx": ct} for f, t, ct in found],
              open(r"D:\work\minimax-bot\analysis\ctx_tokens.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("saved -> ctx_tokens.json")
else:
    print("no context_token in returned messages")
    print(json.dumps(data, ensure_ascii=False)[:1500])
