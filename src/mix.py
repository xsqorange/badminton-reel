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
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-i", video, "-i", bgm,
           "-filter_complex", fc, "-map", "0:v", "-map", "[aout]",
           "-c:v", "copy", "-c:a", mx["audio_codec"], "-b:a", mx["audio_bitrate"],
           "-ar", mx["audio_sample_rate"],
           "-shortest", "-movflags", enc["movflags"], out]
    rc, _, err = run(cmd)
    if rc != 0:
        print("FAIL:", err[:600])
        raise SystemExit("[FAIL] 混音失败")
    print(f"DONE {out}  dur={probe(out):.1f}s  {os.path.getsize(out)/1024/1024:.1f}MB")


if __name__ == "__main__":
    main()
