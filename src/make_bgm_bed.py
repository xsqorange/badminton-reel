"""
把多段 BGM 拼成一条长配乐(段间交叉淡化),再循环到目标时长。
分两步,逻辑简单可靠:
  1) 顺序拼接(acrossfade)
  2) 循环扩展(aloop 拼接,或直接 repeat)

用法: python make_bgm_bed.py <out.mp3> <part1.mp3> [part2.mp3 ...] [--config ...]
目标时长来自 config/base.yaml 的 music.bed_seconds。
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
    return float(out.strip())


def concat_parts(parts, out, xf, limiter, bitrate):
    durs = [probe(p) for p in parts]
    inputs = []
    for p in parts:
        inputs += ["-i", p]
    chains = []
    for i, d in enumerate(durs):
        chains.append(f"[{i}:a]atrim=0:{d:.3f},asetpts=N/SR/TB[a{i}]")
    cur = "a0"
    for i in range(1, len(parts)):
        chains.append(f"[{cur}][a{i}]acrossfade=d={xf}:c1=tri:c2=tri[x{i}]")
        cur = f"x{i}"
    fc = ";".join(chains) + f";[{cur}]alimiter=limit={limiter}[out]"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + inputs + \
          ["-filter_complex", fc, "-map", "[out]", "-c:a", "libmp3lame", "-b:a", bitrate, out]
    rc, _, err = run(cmd)
    return rc == 0, err, sum(durs) - xf * (len(parts) - 1)


def loop_to(src, target, out, xf, fade_in, fade_out, limiter, bitrate):
    """循环 src 到 target,每次接缝处交叉淡化"""
    sd = probe(src)
    n = int(target / max(sd - xf, 0.5)) + 1
    inputs = []
    for _ in range(n):
        inputs += ["-i", src]
    chains = []
    for i in range(n):
        chains.append(f"[{i}:a]atrim=0:{sd:.3f},asetpts=N/SR/TB[a{i}]")
    cur = "a0"
    for i in range(1, n):
        chains.append(f"[{cur}][a{i}]acrossfade=d={xf}:c1=tri:c2=tri[x{i}]")
        cur = f"x{i}"
    fc = ";".join(chains) + \
         f";[{cur}]apad,atrim=0:{target:.3f},asetpts=N/SR/TB," \
         f"afade=t=in:st=0:d={fade_in},afade=t=out:st={max(0, target - fade_out):.2f}:d={fade_out}," \
         f"alimiter=limit={limiter}[out]"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + inputs + \
          ["-filter_complex", fc, "-map", "[out]", "-c:a", "libmp3lame", "-b:a", bitrate, out]
    rc, _, err = run(cmd)
    return rc == 0, err


def main():
    ap = argparse.ArgumentParser(description="多段 BGM 拼接 + 循环铺满")
    ap.add_argument("out", help="输出 mp3")
    ap.add_argument("parts", nargs="+", help="BGM 分段 mp3")
    ap.add_argument("--target", type=float, default=None,
                    help="目标时长(秒),默认取配置 music.bed_seconds")
    add_config_args(ap)
    args = ap.parse_args()

    cfg = config_from_args(args)
    m = cfg.section("music")

    out = args.out
    target = args.target if args.target is not None else m["bed_seconds"]
    parts = args.parts
    tmp = out + ".cat.mp3"
    ok, err, base = concat_parts(parts, tmp, m["bed_crossfade"],
                                  m["bed_limiter"], m["bed_bitrate"])
    if not ok:
        print("CONCAT FAIL:", err[:600])
        raise SystemExit("[FAIL] BGM 拼接失败")
    print(f"  base bed: {base:.1f}s")
    ok, err = loop_to(tmp, target, out, m["bed_loop_crossfade"],
                      m["bed_fade_in"], m["bed_fade_out"],
                      m["bed_limiter"], m["bed_bitrate"])
    if not ok:
        print("LOOP FAIL:", err[:600])
        raise SystemExit("[FAIL] BGM 循环失败")
    print(f"BGM BED {out}  {probe(out):.1f}s")


if __name__ == "__main__":
    main()
