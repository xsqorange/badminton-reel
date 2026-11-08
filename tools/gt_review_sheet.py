"""
人工逐拍复核表 —— 给每个**候选击球时刻**抽 1 帧(主球场 ROI),供人眼逐拍判定真伪。

候选时刻来自**独立音频包络法**(6ms 窗 / 2ms 步 -> 局部极大值 -> 相对本段中位数 >= +11dB),
再用 0.30s 内的相邻瞬态合并为一次击球候选(「拍+球」双响规则)。
这条链与 src/detect_hits.py 的 rise / offset / thresh **没有任何共享参数** ——
GT 不允许由检测器输出反推(A4 纪律)。

判定口径(人眼逐格看):
  这一格里前景主球场有没有一次**真实击球**?有 -> 记 1 拍;没有 -> 记 0 拍。
  球员站着不动/只有背景场地在动/衣物摩擦 -> 0 拍。

用法:
    python tools/gt_review_sheet.py --video <src.mp4> \
        --candidates cand.json --out-dir work/sheets/gtreview
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SR = 48000
WIN_MS, HOP_MS = 6.0, 2.0
MIN_GAP, PAIR_GAP, THR_DB = 0.12, 0.30, 11.0


def decode_audio(video, t0, dur):
    """解一段音频 -> float32 单声道 48k"""
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{t0:.3f}",
           "-i", video, "-t", f"{dur:.3f}", "-vn", "-ac", "1", "-ar", str(SR),
           "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("音频解码失败: " + r.stderr[:200].decode("utf-8", "replace"))
    return np.frombuffer(r.stdout, dtype=np.float32)


def envelope(x):
    """6ms 窗 / 2ms 步 RMS 包络(dB),cumsum 方窗卷积"""
    win, hop = int(SR * WIN_MS / 1000), int(SR * HOP_MS / 1000)
    n = 1 + (len(x) - win) // hop
    if n < 10:
        return np.array([])
    sq = np.asarray(x, dtype=np.float64) ** 2
    cs = np.concatenate(([0.0], np.cumsum(sq)))
    e = np.sqrt((cs[win:win + n * hop:hop] - cs[0:n * hop:hop]) / win)
    return 20 * np.log10(np.maximum(e, 1e-9))


def candidates(video, t0, t1):
    """返回该区间的候选击球时刻列表[(时刻, 同簇瞬态数), ...]"""
    pad = 1.0
    db = envelope(decode_audio(video, max(0.0, t0 - pad), (t1 - t0) + 2 * pad))
    if len(db) < 50:
        return []
    hop_s = HOP_MS / 1000.0
    base = max(0.0, t0 - pad)
    rel = db - float(np.median(db))
    peaks = []
    for i in range(1, len(rel) - 1):
        if rel[i] >= rel[i - 1] and rel[i] > rel[i + 1] and rel[i] >= THR_DB:
            t = base + i * hop_s
            if peaks and t - peaks[-1][0] < MIN_GAP:
                if rel[i] > peaks[-1][1]:
                    peaks[-1] = (t, rel[i])
                continue
            peaks.append((t, rel[i]))
    # 0.30s 内的相邻瞬态 = 同一次挥拍(拍+球双响) -> 合并成一次击球候选
    clusters = []
    for t, v in peaks:
        if clusters and t - clusters[-1]["t0"] < PAIR_GAP:
            c = clusters[-1]
            c["n"] += 1
            c["t1"] = t
            if v > c["v"]:
                c["v"], c["t"] = v, t
        else:
            clusters.append({"t": t, "t0": t, "t1": t, "n": 1, "v": v})
    return [(c["t"], c["n"]) for c in clusters if t0 - 0.05 <= c["t"] <= t1 + 0.05]


def extract_frames(video, t0, dur, fps, roi, w, h, outdir):
    os.makedirs(outdir, exist_ok=True)
    vf = f"fps={fps},{roi},scale={w}:{h}"
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", f"{t0:.3f}",
           "-i", video, "-t", f"{dur:.3f}", "-vf", vf, "-q:v", "3",
           os.path.join(outdir, "f%05d.jpg")]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        print("FFMPEG ERROR:", r.stderr[:300].decode("utf-8", "replace"))
        return 0
    return len([f for f in os.listdir(outdir) if f.endswith(".jpg")])


def main():
    ap = argparse.ArgumentParser(description="人工逐拍复核表")
    ap.add_argument("--video", required=True)
    ap.add_argument("--candidates", required=True, help="JSON: [{id,start,end}, ...]")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--roi", default="crop=1400:620:170:460", help="主球场 ROI 的 ffmpeg crop")
    ap.add_argument("--cell-width", type=int, default=480)
    ap.add_argument("--sample-fps", type=float, default=4.0)
    ap.add_argument("--cols", type=int, default=6)
    args = ap.parse_args()

    m = re.search(r"crop=(\d+):(\d+):(\d+):(\d+)", args.roi)
    if not m:
        raise SystemExit("--roi 必须是 crop=W:H:X:Y 形式")
    cw, ch = int(m.group(1)), int(m.group(2))
    cell_h = max(1, round(args.cell_width * ch / cw))

    cands = json.load(open(args.candidates, encoding="utf-8"))
    os.makedirs(args.out_dir, exist_ok=True)
    font = ImageFont.load_default()
    summary = {}

    for c in cands:
        rid, t0, t1 = c["id"], float(c["start"]), float(c["end"])
        cl = candidates(args.video, t0, t1)
        summary[rid] = {"start": t0, "end": t1,
                        "candidates": [[round(t, 3), n] for t, n in cl]}
        if not cl:
            print(f"[{rid}] 无候选击球 —— 区间内没有独立音频强瞬态")
            continue
        tmp = tempfile.mkdtemp(prefix="gt_")
        try:
            n = extract_frames(args.video, t0, t1 - t0, args.sample_fps, args.roi,
                               args.cell_width, cell_h, tmp)
            if n < 2:
                print(f"[{rid}] 抽帧失败")
                continue
            frames = sorted(os.listdir(tmp))
            times = [t0 + (i + 0.5) / args.sample_fps for i in range(n)]
            cells = []
            for i, (ct, cn) in enumerate(cl):
                k = min(range(n), key=lambda j: abs(times[j] - ct))
                im = Image.open(os.path.join(tmp, frames[k])).convert("RGB")
                d = ImageDraw.Draw(im)
                d.rectangle([0, 0, im.size[0] - 1, 13], fill=(0, 0, 0))
                d.text((3, 2), f"#{i:02d} t={ct:.2f} n={cn}", fill=(255, 255, 0), font=font)
                cells.append(im)
            cols = args.cols
            rows = (len(cells) + cols - 1) // cols
            sheet = Image.new("RGB", (args.cell_width * cols, rows * cell_h), (0, 0, 0))
            for i, im in enumerate(cells):
                sheet.paste(im, ((i % cols) * args.cell_width, (i // cols) * cell_h))
            out = os.path.join(args.out_dir, f"{rid}.png")
            sheet.save(out)
            print(f"[{rid}] {t0:.1f}-{t1:.1f}s 候选 {len(cl)} 个 -> {out} {sheet.size}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    with open(os.path.join(args.out_dir, "clusters.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print("候选汇总 ->", os.path.join(args.out_dir, "clusters.json"))


if __name__ == "__main__":
    main()
