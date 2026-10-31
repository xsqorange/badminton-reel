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
"""
import json, subprocess, sys, os

# 编码参数:可由环境变量 REEL_CQ / REEL_MAXRATE / REEL_BUFSIZE 覆盖
# (pipeline.py 从 config.yaml 注入)
ENC_OPTS = {
    "cq": os.environ.get("REEL_CQ", "22"),
    "maxrate": os.environ.get("REEL_MAXRATE", "12000k"),
    "bufsize": os.environ.get("REEL_BUFSIZE", "24000k"),
}

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout, r.stderr

def has_nvenc():
    rc, out, _ = run(["ffmpeg","-hide_banner","-encoders"])
    return "h264_nvenc" in out

def cut_stage(video, segs, tmpdir, fps=60, crf="22", use_nv=True,
              maxrate="12000k", bufsize="24000k"):
    parts = []
    for i, s in enumerate(segs):
        sp = s.get("speed") or 1.0
        st = max(0.0, s["start"] - 0.12)
        en = s["end"] + 0.12
        p = os.path.join(tmpdir, f"c{i:03d}.mp4")
        if os.path.exists(p): os.remove(p)
        vf = (f"fps={fps}:round=near:start_time=0,scale=1920:1080:flags=lanczos")
        if abs(sp-1.0) > 0.001:
            vf += f",setpts=PTS/{sp:.6f}"
        if use_nv:
            # -cq 单独用会让 VBR 码率失控(实测 39Mbps > 源 12Mbps),
            # 必须同时给 -maxrate/-bufsize 兜底。
            vargs = ["-c:v","h264_nvenc","-preset","p5","-tune","hq","-rc","vbr",
                     "-cq",crf,"-b:v","0",
                     "-maxrate",maxrate,"-bufsize",bufsize,
                     "-pix_fmt","yuv420p","-g",str(fps*2)]
        else:
            vargs = ["-c:v","libx264","-preset","veryfast","-crf",crf,"-pix_fmt","yuv420p",
                     "-g",str(fps*2)]
        cmd = ["ffmpeg","-hide_banner","-loglevel","error","-y",
               "-ss", f"{st:.3f}", "-i", video,
               "-t", f"{(en-st)/sp:.3f}",
               "-vf", vf, "-an", "-vsync","cfr","-r",str(fps),
               *vargs, p]
        rc, _, err = run(cmd)
        if rc != 0:
            print(f"  cut FAIL {i}: {err[:200]}"); continue
        parts.append(p)
        print(f"  cut {i:3d} {s['start']:8.1f}-{s['end']:8.1f} sp={sp:.2f} "
              f"-> {(en-st)/sp:5.1f}s", flush=True)
    return parts

def concat_stage(parts, out, fps=60, crf="22", use_nv=True,
                 maxrate="12000k", bufsize="24000k"):
    """用 concat filter 拼接,保证时间轴连续"""
    n = len(parts)
    inputs = []
    for p in parts: inputs += ["-i", p]
    labels = "".join(f"[{i}:v]" for i in range(n))
    fc = f"{labels}concat=n={n}:v=1:a=0[cat];[cat]fps=fps={fps}:round=near:start_time=0[outv]"
    if use_nv:
        # 与阶段1 相同的参数约束,避免二次编码时码率再放大
        vargs = ["-c:v","h264_nvenc","-preset","p5","-tune","hq","-rc","vbr",
                 "-cq",crf,"-b:v","0",
                 "-maxrate",maxrate,"-bufsize",bufsize,
                 "-pix_fmt","yuv420p","-g",str(fps*2)]
    else:
        vargs = ["-c:v","libx264","-preset","veryfast","-crf",crf,"-pix_fmt","yuv420p",
                 "-g",str(fps*2)]
    if os.path.exists(out): os.remove(out)
    cmd = ["ffmpeg","-hide_banner","-loglevel","error","-y"] + inputs + \
          ["-filter_complex", fc, "-map","[outv]","-an","-vsync","cfr","-r",str(fps),
           *vargs,"-movflags","+faststart", out]
    return run(cmd)

def main():
    video, v2json, out = sys.argv[1], sys.argv[2], sys.argv[3]
    base_speed = float(sys.argv[4]) if len(sys.argv) > 4 else 1.2
    target = float(sys.argv[5]) if len(sys.argv) > 5 else 330.0
    fps = int(sys.argv[6]) if len(sys.argv) > 6 else 60
    d = json.load(open(v2json, encoding="utf-8"))
    segs = d["segments"]
    # 变速分配
    for s in segs:
        s["speed"] = pick_speed(s, base_speed)
    segs.sort(key=lambda s: -s["score"])
    chosen, used = [], 0.0
    for s in segs:
        dur = (s["end"]-s["start"])/s["speed"]
        if used + dur > target: continue
        if any(abs(s["start"]-x["end"]) < 1.2 for x in chosen): continue
        chosen.append(s); used += dur
    chosen.sort(key=lambda s: s["start"])
    print(f"selected {len(chosen)}  -> {used:.1f}s")
    tmpdir = os.path.join(os.path.dirname(out), "_v4_" +
                          os.path.splitext(os.path.basename(out))[0])
    os.makedirs(tmpdir, exist_ok=True)
    nv = has_nvenc()
    parts = cut_stage(video, chosen, tmpdir, fps=fps, use_nv=nv,
                      crf=ENC_OPTS.get("cq","22"),
                      maxrate=ENC_OPTS.get("maxrate","12000k"),
                      bufsize=ENC_OPTS.get("bufsize","24000k"))
    if not parts: print("NO PARTS"); return
    (rc, so, se) = concat_stage(parts, out, fps=fps, use_nv=nv,
                                crf=ENC_OPTS.get("cq","22"),
                                maxrate=ENC_OPTS.get("maxrate","12000k"),
                                bufsize=ENC_OPTS.get("bufsize","24000k"))
    if rc != 0:
        print("CONCAT FAIL:", se[:1200]); return
    dur = float(run(["ffprobe","-v","error","-show_entries","format=duration",
                     "-of","default=nw=1:nk=1",out])[1].strip())
    print(f"DONE {out}  dur={dur:.1f}s  {os.path.getsize(out)/1024/1024:.1f}MB  nvenc={nv}")

def pick_speed(seg, base=1.1):
    """统一变速:所有片段固定 base 倍速(用户要求 v5 统一 1.1x)"""
    return base

if __name__ == "__main__":
    main()
