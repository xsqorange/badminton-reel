"""
探测 sendmessage 对 context_token 的要求。
用空 item_list(不发送任何可见内容),看服务端返回什么错误码。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import ilink_client as ic

token = ic.bot_token()
chat_id = ic.chat_id()

# 空 item_list + 空 context_token:不产生可见消息,只看校验反馈
for label, ct in (("empty-ctx", ""), ("null-ctx", None)):
    items = []
    body = {
        "msg": {
            "from_user_id": "",
            "to_user_id": chat_id,
            "client_id": f"probe-{ic.secrets.randbelow(10**9)}",
            "message_type": 2,
            "message_state": 2,
            "item_list": items,
        },
        "base_info": {"channel_version": ic.CHANNEL_VERSION, "bot_agent": ic.BOT_AGENT},
    }
    if ct is not None:
        body["msg"]["context_token"] = ct
    st, resp = ic.api_call("ilink/bot/sendmessage", body, token)
    print(f"[{label}] -> {st}  {resp[:300]}")
    print()
