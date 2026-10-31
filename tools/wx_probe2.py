"""
只读探测:验证 bot token 是否有效、CDN 上传链路是否通。
不发任何消息(不调用 sendmessage)。
"""
import sys, os, secrets
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import ilink_client as ic

token = ic.bot_token()
chat_id = ic.chat_id()
print("token prefix:", (token or "")[:12], "| chat:", chat_id)

# 1) 探测 getconfig(只读)
body = {
    "ilink_user_id": chat_id,
    "base_info": {"channel_version": ic.CHANNEL_VERSION, "bot_agent": ic.BOT_AGENT},
}
st, resp = ic.api_call("ilink/bot/getconfig", body, token)
print(f"\ngetconfig -> {st}")
print(resp[:600])

# 2) 探测 getuploadurl(不上传,只看是否授权成功)
probe = b"\x00" * 1024
filekey = secrets.token_bytes(16).hex()
aeskey = secrets.token_bytes(16)
st, resp = ic.get_upload_url(token, chat_id, probe, ic.UploadMediaType.VIDEO if hasattr(ic,'UploadMediaType') else 2,
                             filekey, aeskey.hex())
print(f"\ngetuploadurl -> {st}")
print(resp[:600])
