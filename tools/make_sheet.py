"""
从高光片段中抽帧,拼成联系表 (contact sheet) 用于视觉核验。
用法: python make_sheet.py <video> <highlights.json> <out.jpg> [frames_per_seg]
"""
import json, subprocess, sys, os, math

def ffprobe_duration(path):
    out = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
                          "-of","default=nw=1:nk=1",path],
                         capture_output=True,text=True,encoding="utf-8",errors="replace").stdout.strip()
    try: return float(out)
    except: return 0.0

def grab(video, t, out):
    subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",
                    "-ss",f"{t:.3f}","-i",video,"-frames:v","1",
                    "-vf","scale=320:180",out], check=False)

def main():
    video = sys.argv[1]
    hj = sys.argv[2]
    out = sys.argv[3]
    per = int(sys.argv[4]) if len(sys.argv) > 4 else 2
    d = json.load(open(hj, encoding="utf-8"))
    segs = d["segments"]
    tmpdir = out + ".frames"
    os.makedirs(tmpdir, exist_ok=True)
    frames = []
    for si, s in enumerate(segs):
        dur = s["end"] - s["start"]
        # 取片段中段与后段(后段通常是得分瞬间)
        for fi in range(per):
            frac = 0.35 + 0.45 * (fi / max(per - 1, 1)) if per > 1 else 0.5
            t = s["start"] + dur * frac
            p = os.path.join(tmpdir, f"s{si:02d}_f{fi}.jpg")
            grab(video, t, p)
            if os.path.exists(p):
                frames.append((si, s, t, p))
    if not frames:
        print("NO_FRAMES"); return
    # 用 ffmpeg tile 拼图
    inputs = []
    for _, _, _, p in frames:
        inputs += ["-i", p]
    fc = "".join(f"[{i}:v]" for i in range(len(frames)))
    fc += f"concat=n={len(frames)}:v=1:a=0[cat];[cat]tile=4x{math.ceil(len(frames)/4)}[out]"
    cmd = ["ffmpeg","-hide_banner","-loglevel","error","-y"] + inputs + \
          ["-filter_complex", fc, "-map","[out]","-frames:v","1", out]
    subprocess.run(cmd, check=False)
    print("SHEET", out, "frames", len(frames))
    for si, s, t, p in frames:
        print(f"  seg{si:02d} t={t:8.2f}s (seg {s['start']:.1f}-{s['end']:.1f}) {p}")

if __name__ == "__main__":
    main()
