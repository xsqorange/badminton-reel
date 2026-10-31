"""
卡顿归因实验:对比不同切片/拼接方式产生的"帧间差异序列"。

对比组:
  A. 源文件直接取一段(基准)
  B. 旧方案:逐段 -ss 切片(-r 60 -vsync cfr) + concat demuxer copy
  C. 新方案:逐段 -ss 切片(fps filter 重采样) + concat filter
  D. 源文件无损转码(纯重编码,不切)

看哪个的帧间差分最"平稳"(运动连续),哪个有突变。
运动不连续的判定:diff 序列的变异系数(CV)与自相关骤降。
"""
import subprocess, sys, os, re, numpy as np

def diff_series(path, label, maxsec=None):
    cmd = ["ffmpeg","-hide_banner","-v","error","-i",path]
    if maxsec: cmd += ["-t", str(maxsec)]
    cmd += ["-vf","scale=320:180,tblend=all_mode=difference,signalstats,"
                    "metadata=print:key=lavfi.signalstats.YAVG:file=-",
            "-an","-f","null","-"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    txt = r.stderr + r.stdout
    vals = [float(m.group(1)) for m in re.finditer(r"lavfi\.signalstats\.YAVG=([0-9.]+)", txt)]
    if not vals: return None
    a = np.array(vals)
    # 运动连续性:相邻 diff 的变化幅度
    da = np.diff(a)
    # 突变点:diff 变化超过 3 个标准差
    sd = da.std()
    spikes = (np.abs(da) > 3*sd).sum() if sd > 0 else 0
    print(f"{label}")
    print(f"   n={len(a)} mean_diff={a.mean():.4f} std={a.std():.4f} cv={a.std()/(a.mean()+1e-9):.3f}")
    print(f"   运动突变点(|Δdiff|>3σ): {spikes} ({100*spikes/len(da):.3f}%)")
    print(f"   静止帧(diff<0.01): {(a<0.01).sum()}")
    return a

if __name__ == "__main__":
    for p, lab in zip(sys.argv[1::2], sys.argv[2::2]):
        if os.path.exists(p): diff_series(p, lab)
        else: print(f"{lab}: MISSING {p}")
