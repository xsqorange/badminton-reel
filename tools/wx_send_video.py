"""
通过 MiniMax iLink 微信通道发送视频。

流程(逆向自 app.asar 的 SDK):
  1. getuploadurl -> upload_full_url / upload_param
  2. AES-128-ECB(PKCS7) 加密 -> POST CDN /upload -> 响应头 x-encrypted-param
  3. sendmessage, item_list 里放 video_item:
       media: { encrypt_query_param, aes_key(hex->base64), encrypt_type:1 }
       video_size: 密文长度

用法:
    python tools/wx_send_video.py [video] [caption] [--config ...] [--profile ...]
    python tools/wx_send_video.py --clip f2 --profile club-2026-11-01
    不传 video 时默认取指定场次的微信版成片;
    不传 caption 时按选段结果自动生成(时长 / 回合数 / 倍速 / 帧率)。

版本选择:
    默认发微信版(output/wechat)。iLink CDN 对大文件不友好,
    几百 MB 的母版投不出去 —— 详见 docs/微信通道.md「坑 5:文件体积」。
"""
import argparse, json, os, secrets, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import ilink_client as ic
from reelconf import add_config_args, config_from_args, resolve_path

# 通道凭据与会话 ID 不在项目内(是本机运行时凭据),由 ic.bot_token() / ic.chat_id()
# 从环境变量或通道配置文件解析。


def video_duration(path):
    """成片实际时长(秒)。文案用它,而不是选段内容总长 —— 后者不含边界余量。"""
    if not path or not os.path.exists(path):
        return 0.0
    import subprocess
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def default_caption(cfg, clip, video_path=None):
    """按选段结果自动生成文案,避免手写死数据。"""
    work = resolve_path(cfg.at("output.work_dir"), ROOT)
    v5 = os.path.join(work, cfg.at("output.analysis_subdir"), f"{clip['key']}.v5.json")
    segs = 0
    if os.path.exists(v5):
        d = json.load(open(v5, encoding="utf-8"))
        segs = len(d["segments"])
    total = video_duration(video_path)
    mins = int(total // 60)
    secs = int(total % 60)
    speed = cfg.at("select.speed")
    fps = cfg.at("encode.fps")
    parts = [clip["name"].replace("_", " ")]
    if total:
        parts.append(f"{mins}分{secs}秒")
    if segs:
        parts.append(f"{segs}个完整回合")
    parts.append(f"统一{speed}倍速")
    parts.append(f"1080p{fps}")
    return " · ".join(parts)


def main():
    ap = argparse.ArgumentParser(description="微信通道发送视频")
    ap.add_argument("video", nargs="?", help="要发送的视频(默认取指定场次的对应版本)")
    ap.add_argument("caption", nargs="?", help="文案(默认按选段结果自动生成)")
    ap.add_argument("--clip", help="场次 key(如 f1/f2),决定默认取哪一条")
    ap.add_argument("--variant", choices=["wechat", "master"], default="wechat",
                    help="默认发微信版(CDN 投不动母版);master 为母版")
    add_config_args(ap)
    args = ap.parse_args()

    cfg = config_from_args(args)
    ic.configure(cfg)

    token = ic.bot_token()
    chat_id = ic.chat_id()

    clip = cfg["clips"][0]
    if args.clip:
        clip = next((c for c in cfg["clips"] if c["key"] == args.clip), None)
        if clip is None:
            raise SystemExit(f"没有这个场次: {args.clip}")

    if args.variant == "wechat":
        vdir = resolve_path(cfg.at("output.wechat_dir"), ROOT)
    else:
        vdir = resolve_path(cfg.at("output.master_dir"), ROOT)
    VIDEO = args.video or os.path.join(vdir, f"{clip['name']}.mp4")
    CAPTION = args.caption or default_caption(cfg, clip, VIDEO)

    if not os.path.exists(VIDEO):
        sys.exit(f"missing {VIDEO}")

    buf = open(VIDEO, "rb").read()
    print(f"file: {len(buf)/1024/1024:.1f} MB")
    print(f"caption: {CAPTION}")

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
    work = resolve_path(cfg.at("output.work_dir"), ROOT)
    os.makedirs(work, exist_ok=True)
    json.dump({"download_param": download_param,
               "aes_key_b64": ic.aes_key_base64(aeskey),
               "filekey": filekey},
              open(os.path.join(work, "last_send.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # --- 3. sendmessage ---
    # SDK 的关键行为:每个 item 单独发一条消息(text 一条、video 一条),
    # 而不是合并进同一个 item_list。合并会被服务端判 invalid arguments。
    def send_one(item, label, retries=3):
        """发一条消息。返回 (ok, message_id, err_msg)。

        'prepare failed' 是服务端出站管线的通用前置失败(纯文字也会中),
        多半是通道侧的短时限流/冷却,不是参数问题 —— 所以重试并给出明确诊断。
        """
        ctx = os.environ.get("WX_CTX") or f"mcode-{int(time.time()*1000)}-{secrets.randbelow(10**6)}"
        for attempt in range(1, retries + 1):
            st, resp = ic.send_message(token, chat_id, ctx, [item])
            try:
                d = json.loads(resp)
            except Exception:
                print(f"  [{label}] -> HTTP {st} 非 JSON: {resp[:200]}")
                return False, None, "non-json response"
            # 成功判据:有 message_id 且没有 ret/errcode 错误码
            bad = (d.get("ret") not in (0, None)) or (d.get("errcode") not in (0, None))
            if not bad and d.get("message_id"):
                print(f"  [{label}] -> HTTP {st} message_id={d.get('message_id')}")
                return True, d.get("message_id"), None
            errmsg = d.get("errmsg") or resp[:160]
            print(f"  [{label}] -> HTTP {st} 失败: {errmsg}"
                  + (f"  (第 {attempt}/{retries} 次)" if attempt < retries else ""))
            if "prepare" in str(errmsg).lower() and attempt < retries:
                wait = 30 * attempt
                print(f"    服务端 prepare 失败,等待 {wait}s 后重试(通道限流/冷却)")
                time.sleep(wait)
                continue
            return False, None, str(errmsg)
        return False, None, "retries exhausted"

    video_item = {
        "type": 5,  # MessageItemType.VIDEO
        "video_item": {
            "media": {
                "encrypt_query_param": download_param,
                # 必须用 SDK 的 hex->base64 形态(44 字符),见 ilink_client.aes_key_base64
                "aes_key": ic.aes_key_base64(aeskey),
                "encrypt_type": 1,
            },
            "video_size": len(cipher),
        },
    }
    text_item = {"type": 1, "text_item": {"text": CAPTION}}

    print("sending text item ...")
    ok_t, mid_t, err_t = send_one(text_item, "text")
    print("sending video item ...")
    ok_v, mid_v, err_v = send_one(video_item, "video")

    if ok_v:
        print(f"\nSUCCESS video message_id={mid_v}  (text message_id={mid_t})")
    elif ok_t:
        print(f"\nTEXT OK (message_id={mid_t}) but VIDEO FAILED: {err_v}")
        print("  文字能发、视频不行 -> 通道正常,是媒体 prepare 环节的问题")
    else:
        print(f"\nBOTH FAILED (text: {err_t} / video: {err_v})")
        print("  纯文字也发不出去 -> 不是媒体或参数问题,是通道侧出站管线被卡:")
        print("  1) 刚连续发过若干条,可能触发了通道的短时限流,等几分钟再试")
        print("  2) 若持续失败,在 MiniMax Code 里重新扫码绑定微信通道,重置 bot 会话")
        print("  3) 确认微信客户端没有被退出登录")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
