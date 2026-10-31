"""
从成片里抽帧,验证拼接结果:
- 画面确实是羽毛球对抗
- 片段之间有切换(不同场景)
- 无穿帮画面
"""
import subprocess, sys, os, math

def grab(video, t, out, w=320, h=180):
    subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",
                    "-ss",f"{t:.3f}","-i",video,"-frames:v","1",
                    "-vf",f"scale={w}:{h}",out], check=False)

def main():
    video, out = sys.argv[1], sys.argv[2]
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 24
    dur = float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
                                "-of","default=nw=1:nk=1",video],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace").stdout.strip())
    tmpdir = out + ".f"
    os.makedirs(tmpdir, exist_ok=True)
    frames = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        p = os.path.join(tmpdir, f"v{i:03d}.jpg")
        grab(video, t, p)
        if os.path.exists(p): frames.append(p)
    cols = 6
    rows = math.ceil(len(frames)/cols)
    inputs = []
    for p in frames: inputs += ["-i", p]
    fc = "".join(f"[{i}:v]" for i in range(len(frames)))
    fc += f"concat=n={len(frames)}:v=1:a=0[cat];[cat]tile={cols}x{rows}:padding=3:color=black[o]"
    subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y"] + inputs +
                   ["-filter_complex", fc, "-map","[o]","-frames:v","1", out], check=False)
    print(f"SHEET {out}  n={len(frames)} of {dur:.0f}s")

if __name__ == "__main__":
    main()
