"""
微信 iLink 通道诊断 —— 逐端点探测,定位"发不出去"到底卡在哪一环。

排查「视频发不出去 / 显示播放失败 / sendmessage 报 prepare failed」时先跑这个,
别盲目重试上传。结论分三种:

  1) 全绿        -> 通道正常,问题在媒体内容(看 docs/微信通道.md 坑 4/5/6)
  2) sendmessage 挂、其余全绿
                 -> 服务端对该 bot 的出站消息管线不可用,与本次发送内容无关。
                    重新扫码绑定微信通道才能恢复。(坑 7)
  3) getuploadurl / CDN 挂
                 -> 上传链路问题(鉴权、CDN 地址、文件体积)

用法:
    python tools\\wx_channel_diag.py
    python tools\\wx_channel_diag.py --no-send   # 只读探测,绝不发送任何消息

设计约束:全程只发极小负载(text item / 16 字节探针),
不会产生用户可见的垃圾消息,也不会上传大文件。
"""
import argparse, json, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import ilink_client as ic


GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def _ret_ok(body):
    """响应里 ret/errcode 都没有非零值就算成功(成功响应常常连 ret 都没有)。"""
    try:
        d = json.loads(body)
    except Exception:
        return False
    return d.get("ret") in (0, None) and d.get("errcode") in (0, None)


def _brief(body, n=150):
    return (body or "").replace("\n", " ")[:n]


def main():
    ap = argparse.ArgumentParser(description="微信 iLink 通道诊断")
    ap.add_argument("--no-send", action="store_true",
                    help="只读探测:不发送任何消息(含 text 探活)")
    args = ap.parse_args()

    token = ic.bot_token()
    chat_id = ic.chat_id()
    base_info = {"channel_version": ic.CHANNEL_VERSION, "bot_agent": ic.BOT_AGENT}

    results = []

    # --- 1. getuploadurl:上传授权 ---
    print(f"{DIM}probing getuploadurl ...{RESET}")
    probe = b"probe" * 4
    st, body = ic.get_upload_url(token, chat_id, probe, ic.UploadMediaType.FILE,
                                 "diag-%d" % int(time.time()), "0" * 32)
    ok = st == 200 and _ret_ok(body)
    results.append(("getuploadurl", ok))
    print(f"  {'OK ' if ok else 'FAIL'} HTTP {st} {_brief(body)}")

    upload_full_url = upload_param = ""
    if ok:
        d = json.loads(body)
        upload_full_url = (d.get("upload_full_url") or "").strip()
        upload_param = d.get("upload_param") or ""

    # --- 2. CDN upload:密文上传 ---
    print(f"{DIM}probing CDN upload ...{RESET}")
    try:
        cipher = ic.aes_ecb_encrypt(probe, bytes(16))
        dp = ic.cdn_upload(cipher, upload_param, upload_full_url,
                           "diag-%d" % int(time.time()), token)
        ok = bool(dp)
        results.append(("cdn_upload", ok))
        print(f"  {'OK ' if ok else 'FAIL'} x-encrypted-param len={len(dp) if dp else 0}")
    except Exception as e:
        results.append(("cdn_upload", False))
        print(f"  FAIL {e!r}")

    # --- 3. getupdates:入站长轮询 ---
    print(f"{DIM}probing getupdates ...{RESET}")
    st, body = ic.api_call("ilink/bot/getupdates",
                           {"get_updates_buf": "", "base_info": base_info},
                           token, timeout=25)
    ok = st == 200 and _ret_ok(body)
    results.append(("getupdates", ok))
    print(f"  {'OK ' if ok else 'FAIL'} HTTP {st} {_brief(body)}")

    # --- 4. getconfig:通道状态 / typing_ticket ---
    print(f"{DIM}probing getconfig ...{RESET}")
    st, body = ic.api_call("ilink/bot/getconfig",
                           {"ilink_user_id": chat_id, "context_token": "",
                            "base_info": base_info}, token)
    ticket = ""
    try:
        ticket = json.loads(body).get("typing_ticket") or ""
    except Exception:
        pass
    ok = st == 200 and _ret_ok(body)
    results.append(("getconfig", ok))
    print(f"  {'OK ' if ok else 'FAIL'} HTTP {st} typing_ticket={'yes' if ticket else 'no'}")

    # --- 5. sendtyping:服务端写入能力 ---
    print(f"{DIM}probing sendtyping ...{RESET}")
    if ticket:
        st, body = ic.api_call("ilink/bot/sendtyping",
                               {"ilink_user_id": chat_id, "typing_ticket": ticket,
                                "status": 1, "base_info": base_info}, token)
        ok = st == 200 and _ret_ok(body)
        results.append(("sendtyping", ok))
        print(f"  {'OK ' if ok else 'FAIL'} HTTP {st} {_brief(body)}")
    else:
        results.append(("sendtyping", False))
        print("  SKIP (没有 typing_ticket)")

    # --- 6. sendmessage:出站消息(唯一会被用户看到的探测) ---
    if args.no_send:
        results.append(("sendmessage", None))
        print(f"{DIM}probing sendmessage ... SKIPPED (--no-send){RESET}")
    else:
        print(f"{DIM}probing sendmessage (会往微信发一条短文本 'diag') ...{RESET}")
        ctx = os.environ.get("WX_CTX") or f"mcode-{int(time.time()*1000)}"
        st, body = ic.send_message(token, chat_id, ctx,
                                   [{"type": 1, "text_item": {"text": "diag"}}])
        ok = st == 200 and _ret_ok(body) and "message_id" in (body or "")
        results.append(("sendmessage", ok))
        print(f"  {'OK ' if ok else 'FAIL'} HTTP {st} {_brief(body)}")

    # --- 汇总 ---
    print("\n" + "=" * 52)
    failed = [n for n, ok in results if ok is False]
    skipped = [n for n, ok in results if ok is None]
    if not failed:
        print(f"{GREEN}通道全绿{RESET} —— 通道正常。若视频仍打不开,查媒体形态"
              f"(docs/微信通道.md 坑 5/6)。")
    elif failed == ["sendmessage"]:
        print(f"{RED}sendmessage 被拒,其余端点全绿{RESET}")
        print("  => 服务端对该 bot 的出站消息管线不可用,与本次发送内容无关。")
        print("  => 在 MiniMax Code 里重新扫码绑定微信通道,重置 bot 会话。")
        print("  => 不要反复重试上传。")
    else:
        print(f"{RED}以下端点失败: {', '.join(failed)}{RESET}")
        print("  => 上传链路有问题(鉴权 / CDN 地址 / 文件体积),查 docs/微信通道.md 坑 5。")
    if skipped:
        print(f"{YELLOW}跳过: {', '.join(skipped)}{RESET}")
    print("=" * 52)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
