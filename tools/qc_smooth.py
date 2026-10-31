"""
流畅度质检 —— 量化"卡不卡"。

核心指标:
  1. 帧间隔标准差:越小越流畅(CFR 应该是 0)
  2. 最大帧间隔:超过 1.5 帧 = 观众能感知的顿挫
  3. 重复帧检测:连续相同内容的帧 = 补帧造成的"卡住不动"
  4. 冻结帧(freezedetect):画面静止超过阈值

对比基准:
  - 源视频
  - 旧版成片(v2 方案:逐段编码 + concat demuxer copy)
  - 新版成片(v3 方案:单次 concat filter)
"""
import subprocess, sys, os, re, json, collections
import numpy as np

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout, r.stderr

def frame_times(path):
    cmd = ["ffprobe","-v","error","-select_streams","v","-show_entries","frame=pts_time",
           "-of","csv=p=0",path]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = []
    for line in r.stdout.splitlines():
        line = line.strip().rstrip(",")
        if not line or line=="N/A": continue
        try: out.append(float(line))
        except ValueError: pass
    return out

def smoothness(path, label):
    pts = frame_times(path)
    if len(pts) < 10:
        print(f"{label}: too few frames"); return
    d = np.diff(np.array(pts))
    med = np.median(d)
    std = np.std(d)
    mx = d.max()
    # 相对中位数的偏差
    rel = d/med
    # 顿挫:间隔 > 1.5x 中位数
    stutter = (rel > 1.5).sum()
    # 过密:间隔 < 0.5x 中位数(补帧/重复)
    dense = (rel < 0.5).sum()
    print(f"=== {label}")
    print(f"  frames={len(pts)}  median_gap={med*1000:.3f}ms  std={std*1000:.3f}ms  max={mx*1000:.2f}ms")
    print(f"  gap/median: std={np.std(rel):.4f}  p99={np.percentile(rel,99):.3f}  max={rel.max():.3f}")
    print(f"  顿挫帧(>1.5x): {stutter} ({100*stutter/len(d):.3f}%)")
    print(f"  过密帧(<0.5x): {dense} ({100*dense/len(d):.3f}%)")
    # 冻结帧
    rc, _, err = run(["ffmpeg","-hide_banner","-v","info","-i",path,
                      "-vf","freezedetect=n=-60dB:d=0.5","-an","-f","null","-"])
    fz = re.findall(r"freeze_start: ([0-9.]+)", err)
    print(f"  冻结帧起点: {len(fz)} {fz[:6]}")
    return {"std": std, "max": mx, "stutter": stutter, "dense": dense,
            "rel_std": float(np.std(rel)), "freeze": len(fz)}

if __name__ == "__main__":
    for p, lab in zip(sys.argv[1::2], sys.argv[2::2]):
        if os.path.exists(p):
            smoothness(p, lab)
        else:
            print(f"=== {lab}: MISSING {p}")
