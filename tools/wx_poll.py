"""
用刚拿到的游标持续轮询,等待一条新消息以获取 context_token。
注意:桌面端 monitor 也持有自己的游标,两边各自消费。
"""
import sys, os, json, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import ilink_client as ic

token = ic.bot_token()
work_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "work")
os.makedirs(work_dir, exist_ok=True)

buf = sys.argv[1] if len(sys.argv) > 1 else ""
rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 3

for i in range(rounds):
    body = {
        "get_updates_buf": buf,
        "base_info": {"channel_version": ic.CHANNEL_VERSION, "bot_agent": ic.BOT_AGENT},
    }
    st, resp = ic.api_call("ilink/bot/getupdates", body, token, timeout=40)
    try:
        d = json.loads(resp)
    except Exception:
        print("bad resp", resp[:300]); break
    msgs = d.get("msgs") or []
    buf = d.get("get_updates_buf") or buf
    print(f"round {i}: msgs={len(msgs)} buf={(buf or '')[:40]}")
    for m in msgs:
        ct = m.get("context_token")
        print(f"   from={m.get('from_user_id')} to={m.get('to_user_id')} "
              f"ctx_len={len(ct) if ct else 0} type={m.get('message_type')} state={m.get('message_state')}")
        if ct:
            json.dump({"from": m.get("from_user_id"), "to": m.get("to_user_id"), "ctx": ct},
                      open(os.path.join(work_dir, "ctx_token.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            print("   >>> SAVED context_token")
            sys.exit(0)
    if not msgs:
        time.sleep(1)
print("no message with context_token yet; last buf:", buf[:60])
