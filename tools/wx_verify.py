"""
验证投递:用 CDN 参数反查视频能否被取回并解密为合法 MP4。
"""
import sys, re, json, base64, urllib.request, urllib.parse, subprocess, tempfile, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import ilink_client as ic

CFG = r"C:\Users\Admin\.minimax\wechat-channel.yaml"
token = re.search(r"botToken:\s*(\S+)", open(CFG, encoding="utf-8").read()).group(1).strip()
d = json.load(open(r"D:\work\minimax-bot\analysis\last_send.json", encoding="utf-8"))
param = d["download_param"]
aes_b64 = d["aes_key_b64"]
filekey = d.get("filekey", "")

urls = [
    f"{ic.CDN_BASE}/download?encrypted_query_param={urllib.parse.quote(param)}&filekey={urllib.parse.quote(filekey)}",
    f"{ic.CDN_BASE}/download?encrypted_query_param={urllib.parse.quote(param)}&filekey={filekey}",
    f"{ic.CDN_BASE}/download?encrypted_query_param={urllib.parse.quote(param)}",
]
enc = None
for url in urls:
    req = urllib.request.Request(url, headers={
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": ic._uin(),
        "Authorization": f"Bearer {token}",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            enc = r.read()
        print("CDN download OK:", len(enc), "bytes")
        print("  url:", url[:120])
        break
    except Exception as e:
        print("  fail:", repr(e)[:110], "|", url[-60:])

if enc is None:
    print("RESULT: 无法回查(可能需要 filekey 或下载端点不同)")
    raise SystemExit(0)

key = base64.b64decode(aes_b64)
try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    pt = dec.update(enc) + dec.finalize()
except ImportError:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        f.write(enc); tmp = f.name
    outp = tmp + ".dec"
    subprocess.run(["openssl", "enc", "-d", "-aes-128-ecb", "-K", key.hex(),
                    "-nosalt", "-in", tmp, "-out", outp], check=True, capture_output=True)
    pt = open(outp, "rb").read()

head = pt[:32]
print("decrypted head:", head[:20])
if b"ftyp" in head[:16]:
    print("RESULT: 合法 MP4 (ftyp) -> 媒体已在微信 CDN 媒体库")
elif pt[:3] == b"\xff\xd8\xff":
    print("RESULT: JPEG")
else:
    print("RESULT: 头部异常,前32字节 =", head.hex()[:64])
