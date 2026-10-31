"""
微信发送版压制 —— 母版转小体积 mp3(视频流重编码,音频直拷)。

体积目标 ~82MB:iLink CDN 对大文件不友好。
码率参数来自 config/base.yaml 的 wechat 段;
未列出的项(preset/tune/rc/pix_fmt/gop 等)继承 encode 段。

用法: python press_wechat.py <master.mp4> <out.mp4> [--config ...] [--profile ...]
"""
import argparse, os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reelconf import add_config_args, config_from_args


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout, r.stderr


def has_nvenc():
    rc, out, _ = run(["ffmpeg", "-hide_banner", "-encoders"])
    return "h264_nvenc" in out


def main():
    ap = argparse.ArgumentParser(description="微信版压制")
    ap.add_argument("master", help="母版 mp4")
    ap.add_argument("out", help="输出 mp4")
    add_config_args(ap)
    args = ap.parse_args()

    cfg = config_from_args(args)
    w = cfg.section("wechat")
    enc = cfg.section("encode")

    if not w.get("enabled", True):
        print("wechat.enabled=false,跳过")
        return

    # wechat 段未显式给出的编码项,继承 encode 段
    preset = w.get("preset", enc["preset"])
    tune = w.get("tune", enc["tune"])
    rc_mode = w.get("rc", enc["rc"])
    b_v = w.get("b_v", enc["b_v"])
    pix_fmt = w.get("pix_fmt", enc["pix_fmt"])
    gop = w.get("gop", enc["gop"])
    movflags = w.get("movflags", enc["movflags"])

    if has_nvenc():
        vargs = ["-c:v", "h264_nvenc", "-preset", preset, "-tune", tune, "-rc", rc_mode,
                 "-cq", str(w["cq"]), "-b:v", b_v,
                 "-maxrate", w["maxrate"], "-bufsize", w["bufsize"],
                 "-pix_fmt", pix_fmt, "-g", str(gop)]
    else:
        vargs = ["-c:v", "libx264", "-preset", enc["x264_preset"],
                 "-crf", str(w["cq"]), "-pix_fmt", pix_fmt, "-g", str(gop)]

    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", args.master,
           *vargs, "-c:a", "copy", "-movflags", movflags, args.out]
    rc, _, err = run(cmd)
    if rc != 0:
        print("FAIL:", err[:600])
        raise SystemExit("[FAIL] 微信版压制失败")
    print(f"DONE {args.out}  {os.path.getsize(args.out)/1024/1024:.1f} MB")


if __name__ == "__main__":
    main()
