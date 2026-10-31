"""
最终验证:用极小的图片消息测试完整投递链路。
小文件便于回查验证(如果能取回,链路 100% 确认)。
"""
import sys, os, json, base64, secrets, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import ilink_client as ic

token = ic.bot_token()
chat_id = ic.chat_id()

# 造一个真实的小 PNG(用 ffmpeg 生成,避免手写字节出错)
import subprocess
png_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "work", "probe.png")
png_path = os.path.abspath(png_path)
os.makedirs(os.path.dirname(png_path), exist_ok=True)
subprocess.run(["ffmpeg","-hide_banner","-v","error","-y","-f","lavfi",
                "-i","color=c=orange:s=64x64:d=1","-frames:v","1",png_path], check=True)
png = open(png_path,"rb").read()
print("probe png bytes:", len(png))

filekey = secrets.token_bytes(16).hex()
aeskey = secrets.token_bytes(16)
st, resp = ic.get_upload_url(token, chat_id, png, ic.UploadMediaType.IMAGE, filekey, aeskey.hex())
up = json.loads(resp)
print("getuploadurl:", st, "ret=", up.get("ret"))
full = (up.get("upload_full_url") or "").strip()
param = up.get("upload_param") or ""

cipher = ic.aes_ecb_encrypt(png, aeskey)
dp = ic.cdn_upload(cipher, param, full, filekey, token)
print("cdn upload ok, x-encrypted-param len =", len(dp))

item = {
    "type": 2,  # IMAGE
    "image_item": {
        "media": {
            "encrypt_query_param": dp,
            "aes_key": base64.b64encode(aeskey).decode(),
            "encrypt_type": 1,
        },
        "mid_size": len(cipher),
    },
}
ctx = f"mcode-{int(time.time()*1000)}"
st, resp = ic.send_message(token, chat_id, ctx, [item])
print("sendmessage(image) ->", st, resp[:300])

# 立刻尝试回查
json.dump({"download_param": dp, "aes_key_b64": base64.b64encode(aeskey).decode(),
           "filekey": filekey}, open(os.path.join(os.path.dirname(png_path), "last_img.json"), "w",
           encoding="utf-8"), ensure_ascii=False, indent=2)
