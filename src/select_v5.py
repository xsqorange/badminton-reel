"""
选段 v5 —— 完整回合边界(局部相对判据)。

v4 失败原因:
  这段录像全程有持续的场地背景噪声(RMS 恒在 -31dB),不存在绝对"静默",
  所以按 floor 阈值找静默期 -> 末拍后永远找不到 -> gap_after 恒为 0。

正确判据 = **相对本地的能量下陷**:
  末拍后常有"球落地 + 欢呼 + 走动"的能量变化,而回合之间(捡球/等发球)
  是一段相对低能量区。用局部中位做基准,检测下陷。

完整回合的判据:
  起点 = 第一拍 往前回溯到"能量回升点"(捡球结束、准备发球)
  终点 = 最后一拍 往后延伸到"能量回落点"或固定的得分后窗口

不确定回合的判定(移除):
  1) 第一拍之前的能量下陷太短  -> 起点可能切在上一回合中间
  2) 末拍之后没有任何能量变化  -> 可能是长回合被切开的一段
  3) 末拍后缺少得分应有的响动(峰值高于回合内均值的 1.25 倍)
"""
import json, re, sys
import numpy as np

BUCKET = 0.2

def read_motion(path):
    pts, val = [], []
    pt = re.compile(r"pts_time:([0-9.]+)")
    pv = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")
    cur = None
    for line in open(path, encoding="utf-8", errors="replace"):
        m = pt.search(line)
        if m: cur = float(m.group(1)); continue
        m = pv.search(line)
        if m and cur is not None:
            pts.append(cur); val.append(float(m.group(1))); cur = None
    return pts, val

def read_audio_raw(path):
    pts, val = [], []
    pt = re.compile(r"pts_time:([0-9.]+)")
    pv = re.compile(r"lavfi\.astats\.Overall.RMS_level=(-?[0-9.]+|-inf)")
    cur = None
    for line in open(path, encoding="utf-8", errors="replace"):
        m = pt.search(line)
        if m: cur = float(m.group(1)); continue
        m = pv.search(line)
        if m and cur is not None:
            raw = m.group(1)
            v = -120.0 if raw.startswith("-inf") else float(raw)
            pts.append(cur); val.append(v); cur = None
    return pts, val

def moving_average(vals, k):
    if k <= 1: return list(vals)
    from collections import deque
    dq = deque(); acc = 0.0; out = []
    for v in vals:
        dq.append(v); acc += v
        if len(dq) > k: acc -= dq.popleft()
        out.append(acc/len(dq))
    return out

def percentile(vals, p):
    if not vals: return 0.0
    s = sorted(vals); return s[min(int(len(s)*p), len(s)-1)]

def normalize(vals):
    lo, hi = min(vals), max(vals)
    if hi-lo < 1e-9: return [0.0]*len(vals)
    return [(v-lo)/(hi-lo) for v in vals]

def to_grid(apt, aval, step=0.2):
    """把 41ms 的音频序列重采样到 0.2s 网格(取窗口内 max,保留瞬态)"""
    nb = int(apt[-1]/step)+1
    buckets = [[] for _ in range(nb)]
    for t, v in zip(apt, aval):
        buckets[min(int(t/step), nb-1)].append(v)
    return [max(b) if b else -120.0 for b in buckets], nb

def main():
    hitsf, mcsv, acsv, outp = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    target = float(sys.argv[5]) if len(sys.argv) > 5 else 330.0
    min_shots = int(sys.argv[6]) if len(sys.argv) > 6 else 4
    speed = float(sys.argv[7]) if len(sys.argv) > 7 else 1.1

    d = json.load(open(hitsf, encoding="utf-8"))
    rallies = d["rallies"]
    mtimes, mval = read_motion(mcsv)
    motion = normalize(moving_average(mval, 5))
    apt, aval = read_audio_raw(acsv)
    agrid, anb = to_grid(apt, aval, BUCKET)
    # 音频能量的局部基线:用 5s 滑动中位(消除慢漂移)
    k = int(5.0/BUCKET)
    base = moving_average(agrid, k)          # 近似滑动均值
    # 相对能量 = 当前 - 局部基线(dB)
    rel = [a - b for a, b in zip(agrid, base)]
    # 动态范围压缩,便于比较
    rel_n = normalize([max(0.0, r) for r in rel])

    def at_idx(t): return int(max(0, min(anb-1, round(t/BUCKET))))

    def local_base(t, half=15):
        i = at_idx(t)
        lo = max(0, i-half); hi = min(anb, i+half+1)
        w = rel[lo:hi]
        return float(np.median(w)) if w else 0.0

    def motion_at(t):
        lo = max(0, int((t-0.4)/BUCKET)); hi = min(len(motion), int((t+0.4)/BUCKET)+1)
        return max(motion[lo:hi]) if hi > lo else 0.0

    scored, dropped = [], []
    for r in rallies:
        if r["shots"] < min_shots: continue
        h = r["hits"]
        t_first, t_last = h[0], h[-1]
        i_first, i_last = at_idx(t_first), at_idx(t_last)

        # ---- 起点判定:第一拍之前的"能量下陷"(捡球/等发球的安静段) ----
        lb = local_base(t_first)
        # 从第一拍往前找,直到相对能量回升到基线以上(表示活动重新开始)
        j = i_first
        lead_frames = 0
        while j > 0 and (rel[j] <= lb + 1.0) and lead_frames < int(2.5/BUCKET):
            j -= 1; lead_frames += 1
        gap_before = lead_frames * BUCKET
        # ---- 终点判定:末拍之后的能量变化 ----
        j = i_last
        tail_frames = 0
        # 末拍后若能量持续高于基线(欢呼/走动),把它纳入
        while j < anb-1 and (rel[j] >= lb - 2.0) and tail_frames < int(2.5/BUCKET):
            j += 1; tail_frames += 1
        gap_after = tail_frames * BUCKET

        # 得分佐证:末拍后 0.2~2.2s 的相对能量峰值
        j0, j1 = at_idx(t_last+0.2), at_idx(t_last+2.2)
        pk = max(rel_n[j0:j1]) if j1 > j0 else 0.0
        # 回合内击球的相对能量中位(作为"常规击球"基准)
        rin = [rel_n[at_idx(t)] for t in h]
        base_hit = float(np.median(rin)) if rin else 0.0

        # --- 不确定判定 ---
        reason = None
        if gap_before < 0.2 and gap_after < 0.2:
            reason = ("boundary_split", f"前 {gap_before:.1f}s / 后 {gap_after:.1f}s 均无明显能量变化")
        elif pk < base_hit * 1.12 and pk < 0.25:
            reason = ("no_score_evidence", f"末拍后峰值 {pk:.2f} 不显著(基准 {base_hit:.2f})")
        if reason:
            dropped.append({"start": r["start"], "end": r["end"], "shots": r["shots"],
                            "reason": reason[0], "detail": reason[1]})
            continue

        # 边界:纳入发球准备与得分后收尾
        # 注意:发球常常太轻,瞬态检测会漏掉 -> 用固定最小前置量覆盖发球动作
        MIN_LEAD = 0.9     # 至少回溯 0.9s,涵盖抛球+发球动作
        MIN_TAIL = 1.2     # 至少延伸到末拍后 1.2s,涵盖得分反应
        lead = max(MIN_LEAD, min(gap_before, 1.4))
        tail = max(MIN_TAIL, min(gap_after, 2.4))
        start = max(0.0, t_first - lead)
        end = t_last + tail
        if end - start < (t_last - t_first) + 1.0:
            end = t_last + 1.0

        ivs = [h[i+1]-h[i] for i in range(len(h)-1)]
        scored.append({
            "start": round(start,2), "end": round(end,2),
            "shots": r["shots"], "regularity": round(r["regularity"],3),
            "duration": round(r["duration"],2),
            "mean_interval": round(r["mean_interval"],3),
            "min_interval": round(min(ivs),3) if ivs else 1.0,
            "max_interval": round(max(ivs),3) if ivs else 1.0,
            "gap_before": round(gap_before,2), "gap_after": round(gap_after,2),
            "score_peak": round(pk,3), "hit_base": round(base_hit,3),
            "max_motion": round(motion_at(t_last),3),
            "hits": h,
        })

    for s in scored:
        shots = s["shots"]; reg = s["regularity"]; dur = s["duration"]
        s_shots = min(shots/12.0, 1.6)
        if dur < 4: s_dur = 0.3
        elif dur <= 18: s_dur = 1.0
        elif dur <= 28: s_dur = 0.75
        else: s_dur = 0.5
        s["score"] = round(0.40*s_shots + 0.25*reg + 0.20*s_dur + 0.15*s["max_motion"], 4)

    scored.sort(key=lambda s: -s["score"])
    chosen, used = [], 0.0
    for s in scored:
        dur = (s["end"]-s["start"])/speed
        if used + dur > target: continue
        if any(abs(s["start"]-x["end"]) < 2.0 for x in chosen): continue
        chosen.append(s); used += dur
    chosen.sort(key=lambda s: s["start"])

    reasons = {}
    for x in dropped: reasons[x["reason"]] = reasons.get(x["reason"],0)+1
    out = {"segments": chosen, "total": round(used,2), "target": target, "speed": speed,
           "candidates": len(scored), "dropped": len(dropped), "drop_reasons": reasons,
           "dropped_detail": dropped}
    json.dump(out, open(outp,"w",encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"chosen {len(chosen)}  total {used:.1f}s @ {speed}x  "
          f"(candidates {len(scored)}, dropped {len(dropped)})")
    print(f"  移除原因: {reasons}")
    for i,s in enumerate(chosen):
        print(f"  {i:2d} {s['start']:8.1f}-{s['end']:8.1f} ({s['end']-s['start']:5.1f}s) "
              f"shots={s['shots']:3d} gap={s['gap_before']:.1f}/{s['gap_after']:.1f} "
              f"pk={s['score_peak']:.2f}/{s['hit_base']:.2f} sc={s['score']:.3f}")

if __name__ == "__main__":
    main()
