"""
成片质量检测:
- 黑帧 / 冻结帧检测
- 音频静音段检测
- 音频峰值(削波)
- 整体音量
"""
import subprocess, sys, os, re, json

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout, r.stderr

def probe(p):
    rc, out, _ = run(["ffprobe","-v","error","-show_entries","format=duration",
                      "-of","default=nw=1:nk=1",p])
    return float(out.strip())

def check(path):
    print(f"=== {os.path.basename(path)}  {probe(path):.1f}s")
    # 黑帧检测
    rc, out, err = run(["ffmpeg","-hide_banner","-v","info","-i",path,
                         "-vf","blackdetect=d=0.5:pix_th=0.10","-an","-f","null","-"])
    blacks = re.findall(r"black_start:([0-9.]+) black_end:([0-9.]+)", err)
    print(f"  black segments (>0.5s): {len(blacks)}")
    for b in blacks[:8]:
        print(f"     {b[0]}s -> {b[1]}s")
    # 冻结帧检测
    rc, out, err = run(["ffmpeg","-hide_banner","-v","info","-i",path,
                         "-vf","freezedetect=n=-60dB:d=1.5","-an","-f","null","-"])
    frz = re.findall(r"freeze_start: ([0-9.]+)", err)
    print(f"  freeze starts (>1.5s): {len(frz)}", frz[:8])
    # 静音检测
    rc, out, err = run(["ffmpeg","-hide_banner","-v","info","-i",path,
                         "-af","silencedetect=n=-50dB:d=2.0","-vn","-f","null","-"])
    sil = re.findall(r"silence_start: ([0-9.]+).*?silence_duration: ([0-9.]+)", err)
    print(f"  silence segments (>2s): {len(sil)}")
    for s in sil[:8]:
        print(f"     {s[0]}s ({s[1]}s)")
    # 音量统计
    rc, out, err = run(["ffmpeg","-hide_banner","-v","info","-i",path,
                         "-af","volumedetect","-vn","-f","null","-"])
    for k in ("mean_volume","max_volume"):
        m = re.search(k + r": (-?[0-9.]+) dB", err)
        if m: print(f"  {k}: {m.group(1)} dB")

if __name__ == "__main__":
    for p in sys.argv[1:]:
        check(p)
