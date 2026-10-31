"""
候选池联系表:每个候选抽 1 帧(取片段后 1/3 处,通常是得分瞬间),
拼成大图供视觉挑选。比逐帧自动检测更可靠。

用法: python pool_sheet.py <video> <highlights.json> <out.jpg> [topN]
"""
import json, subprocess, sys, os, math

def grab(video, t, out, w=320, h=180):
    subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",
                    "-ss",f"{t:.3f}","-i",video,"-frames:v","1",
                    "-vf",f"scale={w}:{h}",out], check=False)

def main():
    video, hj, out = sys.argv[1], sys.argv[2], sys.argv[3]
    topn = int(sys.argv[4]) if len(sys.argv) > 4 else 40
    d = json.load(open(hj, encoding="utf-8"))
    cands = d.get("all_candidates") or d["segments"]
    cands = sorted(cands, key=lambda c: -c["score"])[:topn]
    tmpdir = out + ".p"
    os.makedirs(tmpdir, exist_ok=True)
    frames = []
    for i, c in enumerate(cands):
        dur = c["end"] - c["start"]
        t = c["start"] + dur*0.66
        p = os.path.join(tmpdir, f"c{i:03d}.jpg")
        grab(video, t, p)
        if os.path.exists(p): frames.append((i, c, t, p))
    if not frames:
        print("NO_FRAMES"); return
    inputs = []
    for _,_,_,p in frames: inputs += ["-i", p]
    cols = 5
    rows = math.ceil(len(frames)/cols)
    fc = "".join(f"[{i}:v]" for i in range(len(frames)))
    fc += (f"concat=n={len(frames)}:v=1:a=0[cat];"
           f"[cat]tile={cols}x{rows}:padding=4:color=black[out]")
    cmd = ["ffmpeg","-hide_banner","-loglevel","error","-y"] + inputs + \
          ["-filter_complex", fc, "-map","[out]","-frames:v","1", out]
    subprocess.run(cmd, check=False)
    print("SHEET", out, "n=", len(frames), "cols=", cols, "rows=", rows)
    for i, c, t, p in frames:
        print(f"  pool#{i:3d} score={c['score']:.3f} t={t:8.1f} ({c['start']:.1f}-{c['end']:.1f})")

if __name__ == "__main__":
    main()
