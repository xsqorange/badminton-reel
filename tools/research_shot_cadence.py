"""
击球节奏 / 聚类间隙(gap)标定 —— 只读研究脚本,不改任何生产代码。

要回答一个问题:回合聚类的 gap 阈值(rally_max_gap)到底该取多少?

判据用**人工核验过的回合边界**(GT 的 start/end 是人眼看画面定的),
不用 GT 的 shots 字段 —— 那个字段是"以检测器合并估计为主"的
(见 eval/ground_truth/c3_1200-2400.json 的 notes),拿它标定检测器就是自证。

两个可复算的量:
  1) 拆分代价:一个真实回合被切成 >=2 段(gap 阈值太小)
  2) 合并代价:两个相邻真实回合被并成一段(gap 阈值太大)
  -> 二者夹出的区间就是 gap 阈值的可行区间

用法:
  python tools/research_shot_cadence.py \
      --audio work/research/c3_559_3040.f32le --offset 559 \
      --gt eval/ground_truth --out work/research/shot_cadence.json
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from detect_hits import detect_hits  # noqa: E402

SR = 48000
# (名字, thresh_db, median_offset_db, rise_db)
VARIANTS = [
    ("base      -32/8/6", -32.0, 8.0, 6.0),
    ("rise5off6 -34/6/5", -34.0, 6.0, 5.0),
    ("rise4off6 -34/6/4", -34.0, 6.0, 4.0),
    ("countG    -34/7/4.5", -34.0, 7.0, 4.5),
]
GAP_CANDIDATES = [1.2, 1.6, 2.0, 2.5, 3.0, 4.0, 6.0]


def load_gt(paths):
    gts = []
    for p in paths:
        d = json.load(open(p, encoding="utf-8"))
        if d.get("scope", "detection") != "detection":
            continue  # recall 类集合是候选驱动的,只算召回
        for r in d.get("rallies", []):
            gts.append({"start": r["start"], "end": r["end"],
                        "shots": r.get("shots"), "file": os.path.basename(p),
                        "id": r.get("id")})
    gts.sort(key=lambda g: g["start"])
    return gts


def pct(xs, q):
    return float(np.percentile(xs, q)) if xs else float("nan")


def analyze(hits, gts, offset):
    """hits: 绝对时间(秒,已加 offset)。"""
    out = {}
    # 每个真实回合内部的命中与间隙
    per_rally = []
    for g in gts:
        inner = [t for t in hits if g["start"] <= t <= g["end"]]
        gaps = [b - a for a, b in zip(inner, inner[1:])]
        per_rally.append({"start": g["start"], "end": g["end"], "shots_gt": g["shots"],
                          "n_hits": len(inner), "gaps": gaps,
                          "max_gap": max(gaps) if gaps else 0.0})
    out["gt_rallies"] = len(per_rally)
    out["rally_with_hits"] = sum(1 for r in per_rally if r["n_hits"] > 0)
    out["hit_coverage"] = round(out["rally_with_hits"] / max(len(per_rally), 1), 4)

    allgaps = [g for r in per_rally for g in r["gaps"]]
    out["interior_gap_count"] = len(allgaps)
    out["interior_gap_pct"] = {f"p{q}": round(pct(allgaps, q), 3)
                               for q in (50, 75, 90, 95, 99)}
    out["interior_gap_max"] = round(max(allgaps), 3) if allgaps else 0.0
    # 节奏直方图:真实击球节奏 vs 漏拍造成的空洞
    hist = {"0.0-0.3": 0, "0.3-0.8": 0, "0.8-1.2": 0, "1.2-1.6": 0,
            "1.6-2.5": 0, "2.5-4.0": 0, "4.0-8.0": 0, ">8.0": 0}
    for g in allgaps:
        if g < 0.3:
            hist["0.0-0.3"] += 1
        elif g < 0.8:
            hist["0.3-0.8"] += 1
        elif g < 1.2:
            hist["0.8-1.2"] += 1
        elif g < 1.6:
            hist["1.2-1.6"] += 1
        elif g < 2.5:
            hist["1.6-2.5"] += 1
        elif g < 4.0:
            hist["2.5-4.0"] += 1
        elif g < 8.0:
            hist["4.0-8.0"] += 1
        else:
            hist[">8.0"] += 1
    out["interior_gap_hist"] = hist

    # 代价曲线:G 太小 -> 真实回合被切碎;G 太大 -> 相邻真实回合被并成一段
    split = {}
    for G in GAP_CANDIDATES:
        n_split = sum(1 for r in per_rally
                      if sum(1 for g in r["gaps"] if g > G) >= 1)
        # 合并:按 gap 阈值把命中串成簇,一个簇若覆盖了 >=2 个真实回合即为误合并
        clusters = []
        cur = []
        for t in hits:
            if cur and t - cur[-1] <= G:
                cur.append(t)
            else:
                if cur:
                    clusters.append(cur)
                cur = [t]
        if cur:
            clusters.append(cur)
        merged = 0
        for c in clusters:
            ids = set()
            for i, r in enumerate(per_rally):
                if any(r["start"] <= t <= r["end"] for t in c):
                    ids.add(i)
            if len(ids) >= 2:
                merged += 1
        split[str(G)] = {"split_rallies": n_split,
                         "split_rate": round(n_split / max(len(per_rally), 1), 4),
                         "merged_clusters": merged,
                         "merge_rate": round(
                             merged / max(len(clusters), 1), 4),
                         "n_clusters": len(clusters)}
    out["split_by_gap"] = split

    # 合并代价:相邻两个真实回合之间,后一回合的首个命中距前一回合末个命中的距离
    inter = []
    for a, b in zip(per_rally, per_rally[1:]):
        if a["n_hits"] and b["n_hits"]:
            d = b["start"] - a["end"]
            if d >= 0:
                inter.append(d)
    out["inter_rally_gap_count"] = len(inter)
    out["inter_rally_gap_pct"] = {f"p{q}": round(pct(inter, q), 3)
                                  for q in (5, 10, 25, 50)} if inter else {}
    # gap 阈值推荐:代价 = 拆分率 + 合并率,取最小的 G
    best_g, best_cost = None, None
    for g, v in split.items():
        c = v["split_rate"] + v["merge_rate"]
        if best_cost is None or c < best_cost:
            best_g, best_cost = g, c
    out["recommended_gap"] = {"gap": best_g, "cost": round(best_cost, 4),
                              "split_rate": split[best_g]["split_rate"],
                              "merge_rate": split[best_g]["merge_rate"]}
    out["note_inter_rally"] = (
        "inter_rally_gap 是 GT 边界间距(含回合间空档),不等于命中流间距;"
        "判定合并风险请用 split_by_gap[*].merge_rate")

    # 拍数(仅诊断:GT 的 shots 本身是估计值,不能当真值)
    pairs = [(r["n_hits"], r["shots_gt"]) for r in per_rally
             if r["shots_gt"] and r["n_hits"]]
    if pairs:
        err = [abs(h - s) for h, s in pairs]
        out["shot_mae_vs_gt_est"] = round(float(np.mean(err)), 3)
        out["shot_bias_ratio"] = round(sum(h for h, _ in pairs) /
                                       max(sum(s for _, s in pairs), 1), 3)
        out["shot_pairs"] = len(pairs)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--offset", type=float, default=0.0,
                    help="音频缓存第一个样本对应的源视频绝对时间(秒)")
    ap.add_argument("--gt", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()

    x = np.fromfile(args.audio, dtype=np.float32)
    print(f"音频 {len(x)/SR:.1f}s  源时间起点 {args.offset:.1f}s  "
          f"覆盖 {args.offset:.1f}-{args.offset+len(x)/SR:.1f}s")

    gt_paths = ([args.gt] if os.path.isfile(args.gt)
                else sorted(glob.glob(os.path.join(args.gt, "*.json"))))
    gts = load_gt(gt_paths)
    print(f"GT(detection 口径) {len(gts)} 个回合,来自 "
          f"{len([p for p in gt_paths if 'long' not in p])} 个文件")

    report = {"audio": os.path.abspath(args.audio), "offset": args.offset,
              "gt_files": [os.path.basename(p) for p in gt_paths],
              "gt_rallies_used": len(gts), "variants": {}}

    for name, thr, off, rise in VARIANTS:
        hs = detect_hits(x, SR, thresh_db=thr, min_gap=0.16, env_ms=10,
                         median_window=51, median_offset_db=off, rise_db=rise)
        hits = [t + args.offset for t in hs]
        res = analyze(hits, gts, args.offset)
        res["hits_total"] = len(hits)
        report["variants"][name] = res
        print(f"\n=== {name} ===  命中 {len(hits)}  回合含命中率 {res['hit_coverage']:.3f}")
        print(f"  内部间隙 n={res['interior_gap_count']}  "
              f"p50={res['interior_gap_pct']['p50']:.2f} "
              f"p90={res['interior_gap_pct']['p90']:.2f} "
              f"p99={res['interior_gap_pct']['p99']:.2f} "
              f"max={res['interior_gap_max']:.2f}")
        print(f"  直方图 {res['interior_gap_hist']}")
        print("  拆分率/合并率(按 gap 阈值): " + "  ".join(
            f"G={g}: 拆{v['split_rate']:.3f} 合{v['merge_rate']:.3f}"
            for g, v in res["split_by_gap"].items()))
        print(f"  相邻回合间距 p5/p10/p25 = "
              f"{res['inter_rally_gap_pct'].get('p5')}/"
              f"{res['inter_rally_gap_pct'].get('p10')}/"
              f"{res['inter_rally_gap_pct'].get('p25')}")
        print(f"  推荐 gap = {res['recommended_gap']['gap']} "
              f"(拆{res['recommended_gap']['split_rate']:.3f} "
              f"合{res['recommended_gap']['merge_rate']:.3f} "
              f"总代价{res['recommended_gap']['cost']:.3f})")
        print(f"  拍数 MAE(vs GT 估计值)={res.get('shot_mae_vs_gt_est')}  "
              f"虚高比={res.get('shot_bias_ratio')}  n={res.get('shot_pairs')}")

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        json.dump(report, open(args.out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print("\n报告 ->", args.out)


if __name__ == "__main__":
    main()
