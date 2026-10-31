"""
成片构建 v4 —— 两阶段:精确切片 + 单次 concat filter 拼接。

v3 的问题:一个 filter_complex 里放 31 个 trim,ffmpeg 需要从头解码
          整个 3688s 源文件,极慢(15分钟+)。而且 trim 分布在整个时间轴上,
          ffmpeg 仍要顺序解码,无法跳过。

v4 方案:
  阶段1:逐段用 -ss(输入端定位,秒级跳转)+ 精确 trim + fps 重采样 + 变速
         -> 输出 31 个已规范化的片段(各自时间轴从 0 开始,60fps CFR)
         关键:每段都做 fps=60 重采样,消除源 VFR 抖动
  阶段2:一次 ffmpeg 用 concat filter 拼接 31 个片段
         -> concat filter 保证时间轴连续,无 pts 跳变

这样既快(输入端 seek),又保证时间轴连续和帧率统一。

用法: python build_v4.py <video> <sel.json> <out.mp4> [--config ...] [--profile ...]
所有编码/选段参数来自配置文件。
"""
import argparse, json, os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reelconf import add_config_args, config_from_args


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout, r.stderr


def has_nvenc():
    rc, out, _ = run(["ffmpeg", "-hide_banner", "-encoders"])
    return "h264_nvenc" in out


def video_args(enc, fps, use_nv):
    """按是否支持 NVENC 拼出视频编码参数。"""
    if use_nv:
        # -cq 单独用会让 VBR 码率失控(实测 39Mbps > 源 12Mbps),
        # 必须同时给 -maxrate/-bufsize 兜底。
        return ["-c:v", "h264_nvenc", "-preset", enc["preset"], "-tune", enc["tune"],
                "-rc", enc["rc"], "-cq", str(enc["cq"]), "-b:v", enc["b_v"],
                "-maxrate", enc["maxrate"], "-bufsize", enc["bufsize"],
                "-pix_fmt", enc["pix_fmt"], "-g", str(enc["gop"])]
    return ["-c:v", "libx264", "-preset", enc["x264_preset"], "-crf", str(enc["x264_crf"]),
            "-pix_fmt", enc["pix_fmt"], "-g", str(enc["gop"])]


def cut_stage(video, segs, tmpdir, enc, padding, use_nv):
    parts = []
    fps = enc["fps"]
    for i, s in enumerate(segs):
        sp = s.get("speed") or 1.0
        st = max(0.0, s["start"] - padding)
        en = s["end"] + padding
        p = os.path.join(tmpdir, f"c{i:03d}.mp4")
        if os.path.exists(p):
            os.remove(p)
        vf = (f"fps={fps}:round={enc['fps_round']}:start_time={enc['fps_start_time']},"
              f"scale={enc['scale']}:flags={enc['scale_flags']}")
        if abs(sp - 1.0) > 0.001:
            vf += f",setpts=PTS/{sp:.6f}"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-ss", f"{st:.3f}", "-i", video,
               "-t", f"{(en - st) / sp:.3f}",
               "-vf", vf, "-an", "-vsync", enc["vsync"], "-r", str(fps),
               *video_args(enc, fps, use_nv), p]
        rc, _, err = run(cmd)
        if rc != 0:
            print(f"  cut FAIL {i}: {err[:200]}")
            continue
        parts.append(p)
        print(f"  cut {i:3d} {s['start']:8.1f}-{s['end']:8.1f} sp={sp:.2f} "
              f"-> {(en - st) / sp:5.1f}s", flush=True)
    return parts


def concat_stage(parts, out, enc, use_nv):
    """用 concat filter 拼接,保证时间轴连续"""
    n = len(parts)
    fps = enc["fps"]
    inputs = []
    for p in parts:
        inputs += ["-i", p]
    labels = "".join(f"[{i}:v]" for i in range(n))
    fc = (f"{labels}concat=n={n}:v=1:a=0[cat];"
          f"[cat]fps=fps={fps}:round={enc['fps_round']}:start_time={enc['fps_start_time']}[outv]")
    if os.path.exists(out):
        os.remove(out)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + inputs + \
          ["-filter_complex", fc, "-map", "[outv]", "-an",
           "-vsync", enc["vsync"], "-r", str(fps),
           *video_args(enc, fps, use_nv), "-movflags", enc["movflags"], out]
    return run(cmd)


def pick_speed(seg, base):
    """统一变速:所有片段固定 base 倍速(用户要求 v5 统一 1.1x)"""
    return base


def main():
    ap = argparse.ArgumentParser(description="切片 + concat 拼接")
    ap.add_argument("video", help="源视频")
    ap.add_argument("sel", help="select_v5.py 的输出 JSON")
    ap.add_argument("out", help="输出 mp4")
    add_config_args(ap)
    args = ap.parse_args()

    cfg = config_from_args(args)
    enc = cfg.section("encode")
    sel = cfg.section("select")
    bld = cfg.section("build")

    video, v2json, out = args.video, args.sel, args.out
    base_speed = float(sel["speed"])
    target = float(sel["target_seconds"])

    d = json.load(open(v2json, encoding="utf-8"))
    segs = d["segments"]
    # 变速分配
    for s in segs:
        s["speed"] = pick_speed(s, base_speed)
    segs.sort(key=lambda s: -s["score"])
    chosen, used = [], 0.0
    for s in segs:
        dur = (s["end"] - s["start"]) / s["speed"]
        if used + dur > target:
            continue
        if any(abs(s["start"] - x["end"]) < bld["min_gap_between"] for x in chosen):
            continue
        chosen.append(s)
        used += dur
    chosen.sort(key=lambda s: s["start"])
    print(f"selected {len(chosen)}  -> {used:.1f}s")
    tmpdir = os.path.join(os.path.dirname(out), "_v4_" +
                          os.path.splitext(os.path.basename(out))[0])
    os.makedirs(tmpdir, exist_ok=True)
    nv = has_nvenc()
    parts = cut_stage(video, chosen, tmpdir, enc, bld["cut_padding"], use_nv=nv)
    if not parts:
        print("NO PARTS")
        raise SystemExit("[FAIL] 没有可用切片")
    (rc, so, se) = concat_stage(parts, out, enc, use_nv=nv)
    if rc != 0:
        print("CONCAT FAIL:", se[:1200])
        raise SystemExit("[FAIL] 拼接失败")
    dur = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=nw=1:nk=1", out])[1].strip())
    print(f"DONE {out}  dur={dur:.1f}s  {os.path.getsize(out)/1024/1024:.1f}MB  nvenc={nv}")


if __name__ == "__main__":
    main()
