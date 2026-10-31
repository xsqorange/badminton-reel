"""
iLink API 客户端 —— 复刻桌面端 SDK 的协议,用于主动向微信发送媒体。

协议要素(全部逆向自 app.asar):
  BASE      = https://ilinkai.weixin.qq.com/
  CDN       = https://novac2c.cdn.weixin.qq.com/c2c
  鉴权头    = Authorization: Bearer <botToken>
              AuthorizationType: ilink_bot_token
              X-WECHAT-UIN: base64(random_uint32)
              iLink-App-Id: bot
              iLink-App-ClientVersion: encode('2.4.6')
  端点      = ilink/bot/getuploadurl, ilink/bot/sendmessage, ilink/bot/getconfig
  上传      = getuploadurl -> AES-128-ECB(PKCS7) 加密 -> POST /upload?encrypted_query_param&filekey
              -> 响应头 x-encrypted-param
  消息体    = {msg:{from_user_id:'', to_user_id, client_id, message_type:2,
                    message_state:2, item_list:[...], context_token}, base_info:{...}}
"""
import json, base64, hashlib, re, secrets, sys, os, time
import urllib.request, urllib.error, urllib.parse

# 通道参数。默认值与 config/base.yaml 的 ilink 段一致,
# configure() / configure_from_file() 可从配置文件覆盖。
BASE = "https://ilinkai.weixin.qq.com/"
CDN_BASE = "https://novac2c.cdn.weixin.qq.com/c2c"
CHANNEL_VERSION = "2.4.6"
APP_ID = "bot"
BOT_AGENT = f"Mavis/{CHANNEL_VERSION}"

def encode_version(v):
    mj, mn, pt = [int(x) for x in v.split(".")]
    return ((mj & 0xff) << 16) | ((mn & 0xff) << 8) | (pt & 0xff)
APP_CLIENT_VERSION = str(encode_version(CHANNEL_VERSION))

def configure(cfg):
    """用配置覆盖通道参数。cfg 可以是 reelconf.Config 或普通 dict。"""
    global BASE, CDN_BASE, CHANNEL_VERSION, APP_ID, BOT_AGENT, APP_CLIENT_VERSION
    il = cfg.section("ilink") if hasattr(cfg, "section") else (cfg.get("ilink") or {})
    BASE = il.get("base_url", BASE)
    CDN_BASE = il.get("cdn_base", CDN_BASE)
    CHANNEL_VERSION = il.get("channel_version", CHANNEL_VERSION)
    APP_ID = il.get("app_id", APP_ID)
    BOT_AGENT = il.get("bot_agent", f"Mavis/{CHANNEL_VERSION}")
    APP_CLIENT_VERSION = str(encode_version(CHANNEL_VERSION))

def configure_from_file(config_path=None, profiles=()):
    """从 config/base.yaml(+ profile)读取通道参数。"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from reelconf import load_config
    configure(load_config(config_path, profiles=profiles))


# ---------- 凭据 / 会话 ID 解析 ----------

# 通道凭据是本机运行时凭据,不进版本库。
# 查找顺序:环境变量 > 通道配置文件 > 报错。
DEFAULT_CHANNEL_CFG = os.path.expanduser(r"~\.minimax\wechat-channel.yaml")
DEFAULT_OWNER_CFG = os.path.expanduser(r"~\.minimax\channel-owner.yaml")
ENV_CHANNEL_CFG = "WX_CHANNEL_CFG"
ENV_BOT_TOKEN = "WX_BOT_TOKEN"
ENV_CHAT_ID = "WX_CHAT_ID"


def channel_cfg_path():
    """返回通道配置文件路径(环境变量优先)。"""
    return os.environ.get(ENV_CHANNEL_CFG) or DEFAULT_CHANNEL_CFG


def _candidate_cfg_paths():
    """按优先级列出要扫描的配置文件。"""
    return [channel_cfg_path(), DEFAULT_OWNER_CFG]


def _read_channel_cfg():
    path = channel_cfg_path()
    if not os.path.exists(path):
        return "", path
    with open(path, "r", encoding="utf-8") as f:
        return f.read(), path


def _grep_cfg(keys):
    """在候选配置文件里找第一个匹配项。返回 (值, 来源文件) 或 (None, None)。"""
    for path in _candidate_cfg_paths():
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read()
        for key in keys:
            m = re.search(rf"{key}\s*:\s*(\S+)", txt)
            if m:
                return m.group(1).strip(), path
    return None, None


def bot_token():
    """取 botToken。

    优先级:环境变量 WX_BOT_TOKEN > 通道配置文件里的 botToken。
    找不到时抛出带修复指引的异常,而不是让调用方在别处崩。
    """
    tok = os.environ.get(ENV_BOT_TOKEN)
    if tok:
        return tok.strip()
    val, path = _grep_cfg(("botToken",))
    if val:
        return val
    raise SystemExit(
        f"未找到 botToken。\n"
        f"  方式 1:设置环境变量 {ENV_BOT_TOKEN}\n"
        f"  方式 2:在 {channel_cfg_path()} 里配置 botToken\n"
        f"  方式 3:设置 {ENV_CHANNEL_CFG} 指向别的通道配置文件"
    )


def chat_id():
    """取会话 ID(ownerSenderId / chatId)。

    优先级:环境变量 WX_CHAT_ID > 通道配置文件 / channel-owner.yaml 里的
    ownerSenderId / chatId。找不到时抛出带修复指引的异常。
    """
    cid = os.environ.get(ENV_CHAT_ID)
    if cid:
        return cid.strip()
    val, path = _grep_cfg(("ownerSenderId", "chatId", "chat_id"))
    if val:
        return val
    raise SystemExit(
        f"未找到会话 ID。\n"
        f"  方式 1:设置环境变量 {ENV_CHAT_ID}=<chatId>\n"
        f"  方式 2:在 {channel_cfg_path()} 或 {DEFAULT_OWNER_CFG} 里配置 ownerSenderId / chatId\n"
        f"  (群聊或文件传输助手的 chatId 需要另行获取)"
    )

MessageType_BOT = 2
MessageState_FINISH = 2
ItemType_TEXT, ItemType_IMAGE, ItemType_VOICE, ItemType_FILE, ItemType_VIDEO = 1, 2, 3, 4, 5
class UploadMediaType:
    IMAGE = 1
    VIDEO = 2
    FILE = 3
    VOICE = 4

def _uin():
    return base64.b64encode(str(secrets.randbelow(2**32)).encode()).decode()

def _headers(token, body_len=None):
    h = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _uin(),
        "iLink-App-Id": APP_ID,
        "iLink-App-ClientVersion": APP_CLIENT_VERSION,
    }
    if token:
        h["Authorization"] = f"Bearer {token.strip()}"
    return h

def api_call(endpoint, body, token, timeout=30):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + endpoint, data=data, headers=_headers(token), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, repr(e)

# ---------- AES-128-ECB (纯 python, 避免依赖 cryptography) ----------
def _pkcs7_pad(data, bs=16):
    n = bs - (len(data) % bs)
    return data + bytes([n]) * n

def aes_ecb_encrypt(data, key):
    """AES-128-ECB 加密。优先用 cryptography,否则用 openssl CLI。"""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        pad = _pkcs7_pad(data)
        c = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        return c.update(pad) + c.finalize()
    except ImportError:
        pass
    # 回退: openssl
    import subprocess, tempfile, os as _os
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        f.write(data); tmp_in = f.name
    tmp_out = tmp_in + ".enc"
    try:
        subprocess.run(["openssl","enc","-aes-128-ecb","-K",key.hex(),"-nosalt",
                        "-in",tmp_in,"-out",tmp_out], check=True,
                       capture_output=True)
        return open(tmp_out,"rb").read()
    finally:
        for p in (tmp_in, tmp_out):
            try: _os.remove(p)
            except OSError: pass

def padded_size(n):
    return ((n + 1 + 15) // 16) * 16

def get_upload_url(token, to_user_id, buf, media_type, filekey, aeskey_hex):
    rawsize = len(buf)
    rawfilemd5 = hashlib.md5(buf).hexdigest()
    filesize = padded_size(rawsize)
    body = {
        "filekey": filekey,
        "media_type": media_type,
        "to_user_id": to_user_id,
        "rawsize": rawsize,
        "rawfilemd5": rawfilemd5,
        "filesize": filesize,
        "no_need_thumb": True,
        "aeskey": aeskey_hex,
        "base_info": {"channel_version": CHANNEL_VERSION, "bot_agent": BOT_AGENT},
    }
    return api_call("ilink/bot/getuploadurl", body, token)

def cdn_upload(ciphertext, upload_param, upload_full_url, filekey, token):
    url = upload_full_url or f"{CDN_BASE}/upload?encrypted_query_param={urllib.parse.quote(upload_param or '')}&filekey={urllib.parse.quote(filekey)}"
    req = urllib.request.Request(url, data=ciphertext,
                                 headers={"Content-Type":"application/octet-stream",
                                          "AuthorizationType":"ilink_bot_token",
                                          "X-WECHAT-UIN":_uin(),
                                          "Authorization": f"Bearer {token}"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        dp = r.headers.get("x-encrypted-param")
        if not dp:
            raise RuntimeError("CDN upload response missing x-encrypted-param")
        return dp

def send_message(token, to_user_id, context_token, items):
    body = {
        "msg": {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": f"mcode-{int(time.time()*1000)}-{secrets.randbelow(10**6)}",
            "message_type": MessageType_BOT,
            "message_state": MessageState_FINISH,
            "item_list": items,
            "context_token": context_token,
        },
        "base_info": {"channel_version": CHANNEL_VERSION, "bot_agent": BOT_AGENT},
    }
    return api_call("ilink/bot/sendmessage", body, token)
