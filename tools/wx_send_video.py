"""
通过 MiniMax iLink 微信通道发送视频。

流程(逆向自 app.asar 的 SDK):
  1. getuploadurl -> upload_full_url / upload_param
  2. AES-128-ECB(PKCS7) 加密 -> POST CDN /upload -> 响应头 x-encrypted-param
  3. sendmessage, item_list 里放 video_item:
       media: { encrypt_query_param, aes_key(hex->base64), encrypt_type:1 }
       video_size: 密文长度
"""
import sys, os, re, json, base64, secrets, hashlib, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import ilink_client as ic

CFG = r"C:\Users\Admin\.minimax\wechat-channel.yaml"
token = re.search(r"botToken:\s*(\S+)", open(CFG, encoding="utf-8").read()).group(1).strip()
chat_id = "<REDACTED-CHAT-ID>"

VIDEO = sys.argv[1] if len(sys.argv) > 1 else r"D:\work\minimax-bot\3号场_龙泽_羽毛球集锦.mp4"
CAPTION = sys.argv[2] if len(sys.argv) > 2 else \
    "3号场 龙泽羽毛球集锦 · 5分39秒 · 41个完整回合 · 统一1.1倍速 · 1080p60"

if not os.path.exists(VIDEO):
    sys.exit(f"missing {VIDEO}")

buf = open(VIDEO, "rb").read()
print(f"file: {len(buf)/1024/1024:.1f} MB")

# --- 1. getuploadurl ---
filekey = secrets.token_bytes(16).hex()
aeskey = secrets.token_bytes(16)
st, resp = ic.get_upload_url(token, chat_id, buf, ic.UploadMediaType.VIDEO, filekey, aeskey.hex())
print("getuploadurl ->", st)
up = json.loads(resp)
if up.get("ret") not in (0, None) or up.get("errcode") not in (0, None):
    sys.exit(f"getuploadurl failed: {resp[:300]}")
upload_full_url = (up.get("upload_full_url") or "").strip()
upload_param = up.get("upload_param") or ""
print("upload_full_url:", (upload_full_url[:90] + "...") if upload_full_url else "(none)")

# --- 2. 加密 + 上传 CDN ---
print("encrypting + uploading to CDN ...")
t0 = time.time()
cipher = ic.aes_ecb_encrypt(buf, aeskey)
print(f"ciphertext {len(cipher)/1024/1024:.1f} MB in {time.time()-t0:.1f}s")
download_param = ic.cdn_upload(cipher, upload_param, upload_full_url, filekey, token)
print("x-encrypted-param ok, len =", len(download_param))
json.dump({"download_param": download_param,
           "aes_key_b64": base64.b64encode(aeskey).decode(),
           "filekey": filekey},
          open(r"D:\work\minimax-bot\analysis\last_send.json","w",encoding="utf-8"),
          ensure_ascii=False, indent=2)

# --- 3. sendmessage ---
# SDK 的关键行为:每个 item 单独发一条消息(text 一条、video 一条),
# 而不是合并进同一个 item_list。合并会被服务端判 invalid arguments。
def send_one(item, label):
    ctx = os.environ.get("WX_CTX") or f"mcode-{int(time.time()*1000)}-{secrets.randbelow(10**6)}"
    st, resp = ic.send_message(token, chat_id, ctx, [item])
    print(f"  [{label}] -> {st} {resp[:200]}")
    try:
        d = json.loads(resp)
        # 成功判据:有 message_id 且没有 ret/errcode 错误码
        bad = (d.get("ret") not in (0, None)) or (d.get("errcode") not in (0, None))
        return (not bad) and bool(d.get("message_id")), d.get("message_id")
    except Exception:
        return False, None

video_item = {
    "type": 5,  # MessageItemType.VIDEO
    "video_item": {
        "media": {
            "encrypt_query_param": download_param,
            "aes_key": base64.b64encode(aeskey).decode(),
            "encrypt_type": 1,
        },
        "video_size": len(cipher),
    },
}
text_item = {"type": 1, "text_item": {"text": CAPTION}}

print("sending text item ...")
ok_t, mid_t = send_one(text_item, "text")
print("sending video item ...")
ok_v, mid_v = send_one(video_item, "video")

if ok_v:
    print(f"\nSUCCESS video message_id={mid_v}  (text message_id={mid_t})")
elif ok_t:
    print(f"\nTEXT OK (message_id={mid_t}) but VIDEO FAILED")
else:
    print("\nBOTH FAILED")
