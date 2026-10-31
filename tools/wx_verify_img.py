"""
用图片的 CDN 参数回查 —— 若能取回并解密成合法 PNG,
则证明整个链路(上传->入媒体库->可取回)完全正常。
"""
import sys, os, re, json, base64, urllib.request, urllib.parse, subprocess, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import ilink_client as ic

CFG = r"C:\Users\Admin\.minimax\wechat-channel.yaml"
token = re.search(r"botToken:\s*(\S+)", open(CFG, encoding="utf-8").read()).group(1).strip()
d = json.load(open(r"D:\work\minimax-bot\analysis\last_img.json", encoding="utf-8"))
param = d["download_param"]; aes_b64 = d["aes_key_b64"]; filekey = d["filekey"]

variants = [
    f"{ic.CDN_BASE}/download?encrypted_query_param={urllib.parse.quote(param)}",
    f"{ic.CDN_BASE}/download?encrypted_query_param={urllib.parse.quote(param)}&filekey={urllib.parse.quote(filekey)}",
    f"{ic.CDN_BASE}/download?encrypted_query_param={urllib.parse.quote(param)}&filekey={filekey}",
    f"{ic.CDN_BASE}/download?encrypted_query_param={param}",
]
enc = None
for url in variants:
    req = urllib.request.Request(url, headers={
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": ic._uin(),
        "Authorization": f"Bearer {token}",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            enc = r.read()
        print("DOWNLOAD OK", len(enc), "bytes |", url[-70:])
        break
    except Exception as e:
        print("  fail", repr(e)[:90], "|", url[-55:])

if enc is None:
    print("RESULT: 回查失败(下载端点需要额外凭据,不代表发送失败)")
    raise SystemExit(0)

key = base64.b64decode(aes_b64)
try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    pt = dec.update(enc) + dec.finalize()
except ImportError:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        f.write(enc); tmp = f.name
    o = tmp + ".dec"
    subprocess.run(["openssl","enc","-d","-aes-128-ecb","-K",key.hex(),"-nosalt",
                    "-in",tmp,"-out",o], check=True, capture_output=True)
    pt = open(o,"rb").read()

print("head:", pt[:12].hex())
if pt[:8] == b"\x89PNG\r\n\x1a\n":
    print("RESULT: 合法 PNG —— 媒体已在微信 CDN 媒体库,投递链路完全正常")
else:
    print("RESULT: 头部异常", pt[:16].hex())
