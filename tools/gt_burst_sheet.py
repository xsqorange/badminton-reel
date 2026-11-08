"""
逐拍真值素材生成 —— 第 3 棒 · 裁定 E1 三通道。

对每个候选区间产出两张证据表:
  1) burst 表:每个**音频候选时刻**配 t-0.15 / t / t+0.15 三帧(裁定 E1 通道二:
     禁止只抽单帧 —— 单帧会系统性漏拍)。
  2) dense 表:同区间 **6fps 连续联系表**(裁定 E1 通道三:无音频先验扫描,
     用来补音频漏掉的击球)。

候选时刻来自独立音频包络法(6ms 窗/2ms 步 -> 局部极大值 -> 相对本段中位数 >= +11dB,
0.30s 内相邻瞬态按「拍+球」双响规则合并),与 src/detect_hits.py 的阈值无共享参数。

用法:
    python tools/gt_burst_sheet.py --video <src.mp4> --out-dir work/sheets/burst
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gt_review_sheet import candidates as audio_candidates  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

GT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "eval", "ground_truth")


def load_intervals():
    """要覆盖的区间:长回合专集 + 负样本 + 三个 detection 窗口里 shots>=20 的条目(按区间去重)。"""
    out, seen = [], set()
    for f in ("c3_long_rallies.json", "c3_negative_rallies.json"):
        d = json.load(open(os.path.join(GT_DIR, f), encoding="utf-8"))
        for r in d["rallies"]:
            if r.get("deprecated"):
                continue
            key = (round(float(r["start"]), 1), round(float(r["end"]), 1))
            if key in seen:
                continue
            seen.add(key)
            out.append({"id": r["id"], "start": key[0], "end": key[1],
                        "scope": "recall", "shots_old": r.get("shots")})
    for f in ("c3_559-1200.json", "c3_1200-2400.json", "c3_2400-3039.json"):
        d = json.load(open(os.path.join(GT_DIR, f), encoding="utf-8"))
        for r in d["rallies"]:
            if int(r.get("shots", 0)) < 20:
                continue
            key = (round(float(r["start"]), 1), round(float(r["end"]), 1))
            if key in seen:
                continue
            seen.add(key)
            out.append({"id": r["id"], "start": key[0], "end": key[1],
                        "scope": "detection", "shots_old": r.get("shots")})
    out.sort(key=lambda x: x["start"])
    return out


def extract(video, t0, dur, fps, roi, w, h, outdir):
    os.makedirs(outdir, exist_ok=True)
    vf = f"fps={fps},{roi},scale={w}:{h}"
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", f"{t0:.3f}",
           "-i", video, "-t", f"{dur:.3f}", "-vf", vf, "-q:v", "3",
           os.path.join(outdir, "f%05d.jpg")]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        print("FFMPEG ERROR:", r.stderr[:200].decode("utf-8", "replace"))
        return 0
    return len([f for f in os.listdir(outdir) if f.endswith(".jpg")])


def main():
    ap = argparse.ArgumentParser(description="逐拍真值证据表(burst + dense)")
    ap.add_argument("--video", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--roi", default="crop=1400:600:280:480")
    ap.add_argument("--fps", type=float, default=15.0, help="抽帧率(burst 用)")
    ap.add_argument("--dense-fps", type=float, default=6.0, help="无先验连续扫描的帧率")
    ap.add_argument("--cell-width", type=int, default=320)
    ap.add_argument("--only", help="只做某个 id(逗号分隔)")
    args = ap.parse_args()

    m = re.search(r"crop=(\d+):(\d+):(\d+):(\d+)", args.roi)
    cw, ch = int(m.group(1)), int(m.group(2))
    cell_h = max(1, round(args.cell_width * ch / cw))
    dense_h = max(1, round(200 * ch / cw))

    os.makedirs(args.out_dir, exist_ok=True)
    font = ImageFont.load_default()
    intervals = load_intervals()
    if args.only:
        keep = set(args.only.split(","))
        intervals = [x for x in intervals if x["id"] in keep]
    index = {}
    print(f"待做区间 {len(intervals)} 个", flush=True)

    for iv in intervals:
        rid = iv["id"]
        cands = audio_candidates(args.video, iv["start"], iv["end"])
        tmp = tempfile.mkdtemp(prefix="burst_")
        try:
            n = extract(args.video, iv["start"], iv["end"] - iv["start"], args.fps,
                        args.roi, args.cell_width, cell_h, tmp)
            if n < 3:
                print(f"[{rid}] 抽帧失败")
                continue
            names = sorted(os.listdir(tmp))
            times = [iv["start"] + (i + 0.5) / args.fps for i in range(n)]

            # ---- burst 表:每个候选 3 帧,两个候选一行(6 格)----
            cells, labels = [], []
            for ci, (ct, cn) in enumerate(cands):
                for off in (-0.15, 0.0, 0.15):
                    k = min(range(n), key=lambda j: abs(times[j] - (ct + off)))
                    im = Image.open(os.path.join(tmp, names[k])).convert("RGB")
                    d = ImageDraw.Draw(im)
                    d.rectangle([0, 0, im.size[0] - 1, 12], fill=(0, 0, 0))
                    d.text((2, 1), f"#{ci:02d} {ct + off:+.2f} n={cn}",
                           fill=(255, 255, 0), font=font)
                    cells.append(im)
                    labels.append(f"#{ci:02d}{off:+.2f}")
            cols = 6
            rows = (len(cells) + cols - 1) // cols
            sheet = Image.new("RGB", (args.cell_width * cols, rows * cell_h), (0, 0, 0))
            for i, im in enumerate(cells):
                sheet.paste(im, ((i % cols) * args.cell_width, (i // cols) * cell_h))
            sheet.save(os.path.join(args.out_dir, f"{rid}_burst.png"))

            # ---- dense 表:6fps 连续联系表(无音频先验)----
            step = max(1, int(round(args.fps / args.dense_fps)))
            dcols, dcell_w = 10, 200
            picks = list(range(0, n, step))
            drows = (len(picks) + dcols - 1) // dcols
            dense = Image.new("RGB", (dcell_w * dcols, drows * dense_h), (0, 0, 0))
            for j, k in enumerate(picks):
                im = Image.open(os.path.join(tmp, names[k])).convert("RGB")
                im = im.resize((dcell_w, dense_h))
                d = ImageDraw.Draw(im)
                d.rectangle([0, 0, dcell_w - 1, 11], fill=(0, 0, 0))
                d.text((2, 0), f"{times[k]:.2f}", fill=(0, 255, 255), font=font)
                dense.paste(im, ((j % dcols) * dcell_w, (j // dcols) * dense_h))
            dense.save(os.path.join(args.out_dir, f"{rid}_dense.png"))

            index[rid] = {"start": iv["start"], "end": iv["end"], "scope": iv["scope"],
                          "shots_old": iv["shots_old"],
                          "candidates": [[round(t, 3), cn] for t, cn in cands],
                          "burst_sheet": f"{rid}_burst.png",
                          "dense_sheet": f"{rid}_dense.png"}
            print(f"[{rid}] {iv['start']:.1f}-{iv['end']:.1f}s 候选 {len(cands)} 个 "
                  f"(旧 shots={iv['shots_old']}) -> {rid}_burst.png / {rid}_dense.png", flush=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    with open(os.path.join(args.out_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    print("索引 ->", os.path.join(args.out_dir, "index.json"))


if __name__ == "__main__":
    main()
