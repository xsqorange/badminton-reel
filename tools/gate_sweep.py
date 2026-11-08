"""
门控分位扫描 + 回合合并前后对照 —— 给 D3 调参提供数据(不新增依赖)。

用法:
    python tools/gate_sweep.py --video <src.mp4> \
        --motion work/analysis/c3main.motion.csv \
        --profile club-2026-11-07 --percentiles 40,50,60,70,80,90,95

输出两段:
  1) 各分位下 —— 9 条真长回合 与 5 条负样本区间各自的 shots(门控后 / 门控前)
     判据:真长回合一个都不能掉(<30 就不合格),负样本全部压到 30 以下
  2) 回合合并前后对照 —— 段数 / 最长段 shots / 哪些段是合并来的(merged_from)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

import detect_hits as dh  # noqa: E402
from reelconf import add_config_args, config_from_args  # noqa: E402

# Chief 裁定的段编号(成片 16 段,按源时间排序):#1~#5 是非回合,已剔除;
# #0/#6/#7/#8/#10/#12/#13/#14/#15 是 9 条 >=30 拍的真长回合;
# #9(1726.8)与 #11(2480.7)是真回合但只有 26/25 拍,算精彩片段,不算长回合。
LONG_RALLIES = [
    ("#0", 29.1, 67.2), ("#6", 1212.2, 1251.5), ("#7", 1346.3, 1373.9),
    ("#8", 1423.1, 1454.3), ("#10", 1972.6, 1998.9), ("#12", 3019.4, 3037.6),
    ("#13", 3100.9, 3128.7), ("#14", 3456.5, 3482.2), ("#15", 3484.0, 3511.1),
]
NEGATIVES = [
    ("L02", 446.9, 483.4), ("L07", 493.4, 520.2), ("L19", 563.9, 584.6),
    ("L05", 1067.3, 1099.2), ("L03", 1102.2, 1130.9),
]


def shots_in(hits, t0, t1):
    return sum(1 for t in hits if t0 - 0.05 <= t <= t1 + 0.05)


def main():
    ap = argparse.ArgumentParser(description="门控分位扫描 + 合并对照")
    ap.add_argument("--video", required=True)
    ap.add_argument("--motion", required=True)
    ap.add_argument("--percentiles", default="40,50,60,70,80,90,95")
    ap.add_argument("--window", type=float, default=0.3)
    ap.add_argument("--merge-gap", type=float, default=None,
                    help="合并间隔(默认取 profile 的 analysis.rally_merge_gap)")
    add_config_args(ap)
    args = ap.parse_args()

    cfg = config_from_args(args)
    a = cfg.section("analysis")
    merge_gap = args.merge_gap if args.merge_gap is not None else float(
        a.get("rally_merge_gap") or 0.0)

    pcts = [float(p) for p in args.percentiles.split(",")]

    x, sr = dh.extract_audio_raw(args.video, sr=a["audio_sample_rate"])
    c_thresh = float(a.get("count_hit_thresh_db", a["hit_thresh_db"]))
    c_off = float(a.get("count_hit_median_offset_db", a["hit_median_offset_db"]))
    c_rise = float(a.get("count_hit_rise_db", a["hit_rise_db"]))
    chits = dh.detect_hits(
        x, sr, thresh_db=c_thresh, min_gap=a["hit_min_gap"],
        env_ms=a["hit_env_window_ms"], median_window=a["hit_median_window"],
        median_offset_db=c_off, rise_db=c_rise)
    print(f"count hits = {len(chits)} (thresh={c_thresh} off={c_off} rise={c_rise})")

    # ---- 1) 分位扫描 ----
    print("\n=== 门控分位扫描(每格 = 门控后 shots / 门控前 shots)===")
    hdr = "  ".join(f"{n:>10s}" for n, _, _ in LONG_RALLIES)
    print(f"{'pct':>5s}  {hdr}")
    neg_hdr = "  ".join(f"{n:>10s}" for n, _, _ in NEGATIVES)
    print(f"{'':>5s}  ---- 负样本 ----")
    print(f"{'':>5s}  {neg_hdr}")
    for pct in pcts:
        active, info = dh.load_motion_gate(args.motion, pct, args.window)
        if active is None:
            print(f"{pct:5.0f}  运动 CSV 无数据")
            continue
        kept = [t for t in chits if active(t)]
        long_cells = "  ".join(f"{shots_in(kept, s, e):5d}/{shots_in(chits, s, e):<4d}"
                                for _, s, e in LONG_RALLIES)
        neg_cells = "  ".join(f"{shots_in(kept, s, e):5d}/{shots_in(chits, s, e):<4d}"
                              for _, s, e in NEGATIVES)
        print(f"{pct:5.0f}  {long_cells}")
        print(f"{'':>5s}  {neg_cells}   thr={info['threshold']:.3f} kept={len(kept)}/{len(chits)}")

    # ---- 2) 合并前后对照(多档间隔一次跑完)----
    print("\n=== 回合合并前后对照 ===")
    dhits = dh.detect_hits(
        x, sr, thresh_db=a["hit_thresh_db"], min_gap=a["hit_min_gap"],
        env_ms=a["hit_env_window_ms"], median_window=a["hit_median_window"],
        median_offset_db=a["hit_median_offset_db"], rise_db=a["hit_rise_db"])
    base = dh.cluster_rallies(dhits, max_gap=a["rally_max_gap"],
                              min_rally=a["hit_min_rally_shots"])
    print(f"聚类口径(rally_max_gap={a['rally_max_gap']}): 段数 {len(base)}  "
          f"最长 {max((r['shots'] for r in base), default=0)} 拍")
    for g in sorted({0.0, 1.0, merge_gap}):
        merged = dh.merge_rallies(base, g) if g else list(base)
        n_merged = sum(1 for r in merged if r.get("merged_from"))
        longest = max((r["shots"] for r in merged), default=0)
        over60 = sum(1 for r in merged if r["shots"] >= 60)
        print(f"\n-- rally_merge_gap = {g} --")
        print(f"   段数 {len(base)} -> {len(merged)}(其中 {n_merged} 段是合并来的)  "
              f"最长 {longest} 拍  >=60 拍的段: {over60}")
        cross = 0
        for name, s, e in LONG_RALLIES:
            hit = [r for r in merged if r["end"] >= s and r["start"] <= e]
            if not hit:
                print(f"    {name:>4s} {s:7.1f}-{e:7.1f}  未检出")
                continue
            r = max(hit, key=lambda z: z["shots"])
            mf = r.get("merged_from") or []
            if mf:
                cross += 1
            print(f"    {name:>4s} {s:7.1f}-{e:7.1f}  shots={r['shots']:3d}  "
                  f"跨度 {r['end'] - r['start']:6.1f}s  合并自 {len(mf)} 段 {mf if mf else ''}")
        print(f"   → 9 条长回合里有 {cross} 条被合并成了跨更长区间的段"
              f"({'跨过了真实回合间隙' if cross else '没有跨真实间隙'})")


if __name__ == "__main__":
    main()
