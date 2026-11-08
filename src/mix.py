"""
给成片混音(视频流 copy,不重编码)。
用法: python mix.py <video> <bgm.mp3> <out.mp4> [--config ...] [--profile ...]
音量/淡入淡出/限幅来自 config/base.yaml 的 music 段。
"""
import argparse, os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reelconf import add_config_args, config_from_args


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout, r.stderr


def probe(p):
    rc, out, _ = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=nw=1:nk=1", p])
    return float(out.strip().strip('"'))


def main():
    ap = argparse.ArgumentParser(description="配乐混音(视频流 copy)")
    ap.add_argument("video", help="无音轨的成片")
    ap.add_argument("bgm", help="配乐床 mp3")
    ap.add_argument("out", help="输出 mp4")
    add_config_args(ap)
    args = ap.parse_args()

    cfg = config_from_args(args)
    m = cfg.section("music")
    mx = cfg.section("mix")
    enc = cfg.section("encode")

    video, bgm, out = args.video, args.bgm, args.out
    dur = probe(video)
    if os.path.exists(out):
        os.remove(out)
    fc = (f"[1:a]afade=t=in:st=0:d={m['fade_in']},"
          f"afade=t=out:st={max(0, dur - m['fade_out']):.2f}:d={m['fade_out']},"
          f"volume={m['volume']},alimiter=limit={m['limiter']}[aout]")
    # 配乐床比成片短时要**循环铺满**(-stream_loop -1),否则配合 -shortest
    # 会把成片截断到配乐床的长度 —— 成片 440s / 配乐床 360s 时,片尾 80s 会凭空消失。
    # (这个坑之前没暴露:那几批成片都在 360s 以内。)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-i", video, "-stream_loop", "-1", "-i", bgm,
           "-filter_complex", fc, "-map", "0:v", "-map", "[aout]",
           "-c:v", "copy", "-c:a", mx["audio_codec"], "-b:a", mx["audio_bitrate"],
           "-ar", mx["audio_sample_rate"],
           "-shortest", "-movflags", enc["movflags"], out]
    rc, _, err = run(cmd)
    if rc != 0:
        print("FAIL:", err[:600])
        raise SystemExit("[FAIL] 混音失败")
    out_dur = probe(out)
    # 成片时长必须与输入一致 —— 少了就是被截断了(配乐床不够长 / 流拷贝丢帧)。
    if abs(out_dur - dur) > 0.5:
        raise SystemExit(
            f"[FAIL] 混音后时长不符:输入 {dur:.1f}s → 输出 {out_dur:.1f}s"
            f"(差 {out_dur - dur:+.1f}s)。成片被截断了,不能交付。")
    print(f"DONE {out}  dur={out_dur:.1f}s  {os.path.getsize(out)/1024/1024:.1f}MB")


if __name__ == "__main__":
    main()
