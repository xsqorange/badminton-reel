"""
参数扫描 —— 用标注集直接给检测参数打分,决定"改哪个参数能提升识别质量"。

为什么需要它:
  拍数阈值、上升沿判据、自适应阈值偏移…… 每一个都直接影响回合识别。
  没有标注集时只能"凭感觉调";有了标注集,就能把每个候选参数组合的
  precision / recall / F1 / 完整回合率 / 长回合口径一次算出来,**用数据选参数**。

用法:
    # 用缓存音频(快,不碰源视频)。先备好缓存:
    #   ffmpeg -i <src> -vn -ac 1 -ar 48000 -f f32le work/<key>.f32le
    python tools/sweep_params.py --audio work/c3.f32le \
        --gt eval/ground_truth/c3_1200-2400.json --out eval/reports/sweep_c3.json

    # 或直接读源视频(慢,但不需要预建缓存)
    python tools/sweep_params.py --video <src.mp4> --gt eval/ground_truth/...

只读操作:不写任何流水线产物,只写一份扫描报告。
"""
import argparse, json, os, subprocess, sys
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from detect_hits import detect_hits, cluster_rallies
from eval_rounds import match_ratios, prf, load_gt


def load_audio(path, sr):
    """从 f32le 缓存或源视频取单声道音频。"""
    if path.endswith(".f32le"):
        return np.fromfile(path, dtype=np.float32)
    cache = path + ".f32le"
    if not os.path.exists(cache):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
                        "-f", "f32le", cache], check=True)
    return np.fromfile(cache, dtype=np.float32)


# 候选参数组:(名字, hit_thresh_db, hit_median_offset_db, hit_rise_db, hit_min_gap, hit_median_window)
VARIANTS = [
    ("base        ", -32.0, 8.0, 6.0, 0.16, 51),
    ("rise5/off6  ", -34.0, 6.0, 5.0, 0.16, 51),
    ("rise4/off6  ", -34.0, 6.0, 4.0, 0.16, 51),
    ("rise5/off7  ", -33.0, 7.0, 5.0, 0.16, 51),
    ("rise4/off7  ", -33.0, 7.0, 4.0, 0.16, 51),
    ("rise6/off6  ", -34.0, 6.0, 6.0, 0.16, 51),
    ("rise5/off6/g13", -34.0, 6.0, 5.0, 0.13, 51),
    ("rise5/off6/win31", -34.0, 6.0, 5.0, 0.16, 31),
]


def main():
    ap = argparse.ArgumentParser(description="检测参数扫描(对照标注集)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--audio", help="f32le 缓存音频")
    src.add_argument("--video", help="源视频(会先建缓存)")
    ap.add_argument("--gt", required=True, help="标注文件或目录")
    ap.add_argument("--out", help="扫描报告输出路径")
    ap.add_argument("--long-threshold", type=int, default=30, help="长回合拍数门槛")
    args = ap.parse_args()

    sr = 48000
    x = load_audio(args.audio or args.video, sr)
    dur = len(x) / sr

    gt_paths = ([args.gt] if os.path.isfile(args.gt)
                else sorted(__import__("glob").glob(os.path.join(args.gt, "*.json"))))
    gts, _ = load_gt(gt_paths)
    gts = [g for g in gts if g["scope"] == "detection"]
    lo = min(g["window"][0] for g in gts)
    hi = max(g["window"][1] for g in gts)
    print(f"音频 {dur:.0f}s  标注 {len(gts)} 个回合  窗口 {lo}-{hi}")

    rows = []
    print(f"\n{'变体':18s} {'命中':>6s} {'回合':>5s} {'最长':>4s} {'≥N':>4s} "
          f"{'P':>6s} {'R':>6s} {'F1':>6s}")
    for name, thr, off, rise, gap, win in VARIANTS:
        hs = detect_hits(x, sr, thresh_db=thr, min_gap=gap, env_ms=10,
                         median_window=win, median_offset_db=off, rise_db=rise)
        rs = cluster_rallies(hs, max_gap=1.6, min_rally=2)
        preds = [{"start": r["start"], "end": r["end"], "shots": r["shots"]}
                 for r in rs if r["end"] >= lo and r["start"] <= hi]
        pairs = match_ratios(preds, gts, 0.5)
        m = prf(len(pairs), len(preds) - len(pairs), len(gts) - len(pairs))
        longs = [r for r in rs if r["shots"] >= args.long_threshold]
        rows.append({"variant": name.strip(), "hit_thresh_db": thr,
                     "hit_median_offset_db": off, "hit_rise_db": rise,
                     "hit_min_gap": gap, "hit_median_window": win,
                     "hits": len(hs), "rallies": len(rs),
                     "max_shots": max(r["shots"] for r in rs),
                     "long_rallies": len(longs),
                     "long_seconds": round(sum(r["end"] - r["start"] for r in longs), 1),
                     **m})
        print(f"{name:18s} {len(hs):6d} {len(rs):5d} {max(r['shots'] for r in rs):4d} "
              f"{len(longs):4d} {m['precision']:6.3f} {m['recall']:6.3f} {m['f1']:6.3f}")

    best = max(rows, key=lambda r: r["f1"])
    print(f"\nF1 最优: {best['variant']}  F1={best['f1']:.3f}")
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"audio_seconds": round(dur, 1),
                       "gt_files": [os.path.basename(p) for p in gt_paths],
                       "gt_rallies": len(gts), "window": [lo, hi],
                       "long_threshold": args.long_threshold,
                       "variants": rows, "best": best["variant"]},
                      f, ensure_ascii=False, indent=2)
        print("报告 ->", args.out)


if __name__ == "__main__":
    main()
