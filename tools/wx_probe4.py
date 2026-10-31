import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import ilink_client as ic

token = ic.bot_token()
chat_id = ic.chat_id()

# getconfig 的完整响应(可能含 context_token / send 权限)
body = {"ilink_user_id": chat_id,
        "base_info": {"channel_version": ic.CHANNEL_VERSION, "bot_agent": ic.BOT_AGENT}}
st, resp = ic.api_call("ilink/bot/getconfig", body, token)
print("getconfig:", st)
try:
    d = json.loads(resp)
    for k, v in d.items():
        print(f"  {k} = {str(v)[:120]}")
except Exception:
    print(resp[:800])

# 再看 sendmessage 对无效 context_token 的报错差异
print("\n--- sendmessage with bogus ctx (text item) ---")
body = {
    "msg": {
        "from_user_id": "", "to_user_id": chat_id,
        "client_id": "probe-bogus-ctx-1",
        "message_type": 2, "message_state": 2,
        "item_list": [{"type": 1, "text_item": {"text": "probe"}}],
        "context_token": "BOGUS_CONTEXT_TOKEN_VALUE",
    },
    "base_info": {"channel_version": ic.CHANNEL_VERSION, "bot_agent": ic.BOT_AGENT},
}
st, resp = ic.api_call("ilink/bot/sendmessage", body, token)
print(st, resp[:400])
