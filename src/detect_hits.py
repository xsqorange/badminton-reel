"""
击球瞬态检测 —— 解决"多拍回合"和"击球连贯性"。

原理:
- 羽毛球击球 = 极短促的高频冲击音(球拍击球 + 羽毛球碰撞)
- 一个"回合" = 一串间隔规律的击球瞬态(每拍 0.4-1.2s)
- 音频瞬态用一阶差分 + 局部峰值提取,比 RMS 包络更锐利

输出:
  hits[]  = 击球时刻列表(秒)
  然后按间隔聚类成"回合"(rally),统计每回合的拍数

这比只看运动能量强得多:
  - 运动能量高可能是"换人/走动/穿帮"
  - 击球瞬态是"真的在打球",且拍数直接反映回合激烈程度
"""
import subprocess, sys, json, re, math
import numpy as np

def extract_audio_raw(path, sr=48000):
    """解码成单声道 float32"""
    cmd = ["ffmpeg","-hide_banner","-loglevel","error","-i",path,"-vn",
           "-ac","1","-ar",str(sr),"-f","f32le","-"]
    r = subprocess.run(cmd, capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.float32), sr

def detect_hits(x, sr, thresh_db=-32.0, min_gap=0.16):
    """
    瞬态检测:
    1. 短时能量包络(10ms 窗)
    2. 对包络做一阶差分,取正半轴显著上升沿 = 冲击起点
    3. 局部峰值 + 阈值 + 最小间隔去重
    """
    win = int(sr*0.01)
    n = len(x)//win
    if n < 10: return []
    env = np.array([np.sqrt(np.mean(x[i*win:(i+1)*win]**2)) for i in range(n)])
    db = 20*np.log10(np.maximum(env, 1e-9))
    # 自适应阈值:用局部中位数 + 固定偏移(适应场地嘈杂)
    k = 51
    pad = np.pad(db, (k//2, k//2), mode="edge")
    local_med = np.array([np.median(pad[i:i+k]) for i in range(n)])
    thr = np.maximum(local_med + 8.0, thresh_db)
    # 一阶差分
    d = np.diff(db, prepend=db[0])
    # 上升沿候选:差分大 且 当前值超阈值
    cand = []
    for i in range(1, n):
        if d[i] > 6.0 and db[i] > thr[i] and db[i] > db[i-1]:
            cand.append(i)
    # 最小间隔合并(保留更大的那个)
    hits = []
    min_gap_frames = int(min_gap/0.01)
    for i in cand:
        t = i*0.01
        if hits and (t - hits[-1]) < min_gap:
            # 保留能量更高的
            if db[i] > db[int(hits[-1]/0.01)]:
                hits[-1] = t
            continue
        hits.append(t)
    return hits

def cluster_rallies(hits, max_gap=1.6, min_rally=2):
    """
    把击球聚成回合:相邻击球间隔 < max_gap 视为同一回合
    返回 [{start, end, shots, intervals, regularity}]
    """
    if not hits: return []
    rallies = []
    cur = [hits[0]]
    for h in hits[1:]:
        if h - cur[-1] <= max_gap:
            cur.append(h)
        else:
            rallies.append(cur); cur = [h]
    rallies.append(cur)
    out = []
    for r in rallies:
        if len(r) < min_rally: continue
        iv = [r[i+1]-r[i] for i in range(len(r)-1)]
        # 连贯性:击球间隔的标准差越小越连贯
        regularity = 1.0/(1.0 + (np.std(iv) if iv else 0.5))
        out.append({
            "start": r[0], "end": r[-1],
            "shots": len(r),
            "duration": r[-1]-r[0],
            "mean_interval": float(np.mean(iv)) if iv else 0,
            "std_interval": float(np.std(iv)) if iv else 0,
            "regularity": float(regularity),
            "hits": [round(x,3) for x in r],
        })
    return out

def main():
    path = sys.argv[1]
    outp = sys.argv[2]
    x, sr = extract_audio_raw(path)
    print(f"audio {len(x)/sr:.1f}s")
    hits = detect_hits(x, sr)
    print(f"hits: {len(hits)}  ({len(hits)/(len(x)/sr):.2f}/s)")
    rallies = cluster_rallies(hits)
    print(f"rallies (>=2 shots): {len(rallies)}")
    multi = [r for r in rallies if r["shots"] >= 4]
    print(f"rallies with >=4 shots: {len(multi)}")
    best = sorted(rallies, key=lambda r: -(r["shots"]*r["regularity"]))[:15]
    print("top rallies by shots*regularity:")
    for r in best:
        print(f"  {r['start']:8.2f}-{r['end']:8.2f} shots={r['shots']:3d} "
              f"dur={r['duration']:5.2f} mean_iv={r['mean_interval']:.2f} "
              f"std={r['std_interval']:.2f} reg={r['regularity']:.3f}")
    json.dump({"hits": hits, "rallies": rallies},
              open(outp,"w",encoding="utf-8"), ensure_ascii=False)
    print("saved", outp)

if __name__ == "__main__":
    main()
