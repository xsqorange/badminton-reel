"""
局部判据的主球场归属(E4 / C5)—— 检验「这一击属于主球场吗」能不能用局部基线判。

背景:全局分位已被证伪(Chief 实测 work/chief_gate_diag.py:负样本的 ROI 运动均值 8.77
反而高于真回合 R2 7.60 / R8 7.42 ⇒ 全局分位对真假回合几乎无区分度)。
E4 改用**局部**对照:候选时刻 t 的 ROI 运动峰值 vs 该区间 ±2s 的局部中位基线。

用法:
    python tools/local_gate.py --motion work/analysis/c3.motion.csv \
        --index work/sheets/burst/index.json
输出:各阈值系数 k × 窗口 w 下的「真回合候选通过率 / 负样本候选通过率」对照表。
"""
import argparse
import bisect
import json
import re

NEG_IDS = {"L02", "L03", "L05", "L07", "L19"}


def read_motion(path):
    pts, val = [], []
    pt = re.compile(r"pts_time:([0-9.]+)")
    pv = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")
    cur = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = pt.search(line)
            if m:
                cur = float(m.group(1))
                continue
            m = pv.search(line)
            if m and cur is not None:
                pts.append(cur)
                val.append(float(m.group(1)))
                cur = None
    return pts, val


def main():
    ap = argparse.ArgumentParser(description="局部判据:主球场归属能否分离")
    ap.add_argument("--motion", required=True)
    ap.add_argument("--index", required=True)
    ap.add_argument("--base-window", type=float, default=2.0)
    args = ap.parse_args()

    pts, val = read_motion(args.motion)
    if not pts:
        raise SystemExit("运动 CSV 无数据")
    idx = json.load(open(args.index, encoding="utf-8"))

    def gate(t, w, k):
        lo = bisect.bisect_left(pts, t - w)
        hi = bisect.bisect_right(pts, t + w)
        if hi <= lo:
            return None, 0.0, 0.0
        peak = max(val[lo:hi])
        blo = bisect.bisect_left(pts, t - args.base_window)
        bhi = bisect.bisect_right(pts, t + args.base_window)
        base = sorted(val[blo:bhi])[len(val[blo:bhi]) // 2] if bhi > blo else 0.0
        return (base <= 0) or (peak >= base * (1 + k)), peak, base

    print(f"运动样本 {len(pts)} 个;局部基线窗 ±{args.base_window}s")
    print(f"{'w':>5s} {'k':>5s} | {'真回合通过率':>12s} {'n':>5s} | "
          f"{'负样本通过率':>12s} {'n':>5s} | 区分度")
    for w in (0.15, 0.3):
        for k in (0.2, 0.35, 0.5, 0.75, 1.0, 1.5, 2.0):
            pos = [0, 0]
            neg = [0, 0]
            for rid, iv in idx.items():
                tgt = neg if rid in NEG_IDS else pos
                for t, _n in iv["candidates"]:
                    ok, _p, _b = gate(t, w, k)
                    if ok is None:
                        continue
                    tgt[1] += 1
                    tgt[0] += 1 if ok else 0
            pr = pos[0] / pos[1] if pos[1] else 0.0
            nr = neg[0] / neg[1] if neg[1] else 0.0
            print(f"{w:5.2f} {k:5.2f} | {pr:12.3f} {pos[1]:5d} | "
                  f"{nr:12.3f} {neg[1]:5d} | {pr - nr:+.3f}")
    print()
    print("判读:真回合通过率应当明显高于负样本通过率(正区分度)。"
          "负样本通过率低 = 负样本的瞬态被挡掉;真回合通过率低 = 真击球也被挡掉(误杀)。")


if __name__ == "__main__":
    main()
