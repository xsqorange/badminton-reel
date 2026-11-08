"""
回合高清核对图 —— 人工核验回合的主工具。

为什么需要它:标注联系表(tools/annotate_rounds.py)每帧只有 320×180,
判"是不是真回合"够用,但要确认"主对是不是在打球""边界落在哪一帧"就看不清。
这个工具从**源视频**按候选回合的 6 个时刻抽原生分辨率帧,拼成一张 3×2 的大图,
一图看清一个回合。

和联系表的区别(别混用):
  · 联系表 —— 全窗口批量初筛,帧小但覆盖全
  · 本工具 —— 逐回合细看,帧大但只覆盖一个回合
两者的抽帧时刻完全一致(都是 候选起-1.5s ~ 候选止+1.5s 的 6 等分点),
所以两者可以互相印证。

用法:
    # 生成一批候选的高清核对图
    python tools/rally_montage.py --video <src.mp4> \
        --candidates work/sheets/ann/w559.0-1200.0/candidates.json \
        --ids 0-39 --out-dir work/sheets/hi/w559.0-1200.0

    # 只看密集采样(数击球次数用)
    python tools/rally_montage.py ... --ids 0 --mode dense

    # 放大到前景场地(主角两人所在的那片)
    python tools/rally_montage.py ... --ids 0 --crop 1440:680:380:390
"""
import argparse
import json
import math
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from reelconf import add_config_args, config_from_args, enable_utf8_safe_output  # noqa: E402

enable_utf8_safe_output()


def parse_ids(spec):
    """'0-39' / '0,5,9' / '0-10,20-30' -> [0,5,9,...]"""
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def sample_times(cand, mode, frames, ctx):
    s, e = cand["start"], cand["end"]
    if mode == "dense":
        n = max(4, min(int((e - s) / 0.5) + 1, 24))
        return [s + (e - s) * i / (n - 1) for i in range(n)]
    lo, hi = s - ctx, e + ctx
    return [lo + (hi - lo) * i / (frames - 1) for i in range(frames)]


def main():
    ap = argparse.ArgumentParser(description="回合高清核对图")
    ap.add_argument("--video", required=True)
    ap.add_argument("--candidates", required=True, help="annotate_rounds.py 产的 candidates.json")
    ap.add_argument("--ids", required=True, help="候选 id:0-39 / 0,5,9")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--mode", default="sheet", choices=["sheet", "dense"])
    ap.add_argument("--frames", type=int, default=6, help="每个回合抽几帧")
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--tile-width", type=int, default=640)
    ap.add_argument("--crop", help="ffmpeg crop 参数,如 1440:680:380:390")
    ap.add_argument("--workers", type=int, default=8)
    add_config_args(ap)
    args = ap.parse_args()

    cfg = config_from_args(args)
    an = cfg.section("annotation")
    ctx = float(an["context_seconds"])
    font = load_font = None
    try:
        font = ImageFont.truetype(an["font"], int(an["font_size"]))
    except Exception:
        font = ImageFont.load_default()
    small = ImageFont.truetype(an["font"], max(10, int(an["font_size"]) - 3)) \
        if os.path.exists(an["font"]) else ImageFont.load_default()

    data = json.load(open(args.candidates, encoding="utf-8"))
    cmap = {c["id"]: c for c in data["candidates"]}
    ids = [i for i in parse_ids(args.ids) if i in cmap]
    if not ids:
        raise SystemExit("没有匹配的候选 id")

    outdir = os.path.abspath(args.out_dir)
    rawdir = os.path.join(outdir, "_raw")
    os.makedirs(rawdir, exist_ok=True)

    tw = args.tile_width
    th = int(tw * 9 / 16)
    jobs, plan = [], {}
    for cid in ids:
        c = cmap[cid]
        times = sample_times(c, args.mode, args.frames, ctx)
        plan[cid] = (c, times)
        for k, t in enumerate(times):
            jobs.append((cid, t, k))

    def grab(job):
        cid, t, k = job
        p = os.path.join(rawdir, "c%03d_%02d.jpg" % (cid, k))
        if os.path.exists(p):
            return p
        vf = (args.crop + "," if args.crop else "") + "scale=%d:%d" % (tw, th)
        cmd = ["ffmpeg", "-v", "error", "-ss", "%.3f" % t, "-i", args.video,
               "-frames:v", "1", "-vf", vf, "-q:v", "3", "-y", p]
        subprocess.run(cmd, check=True)
        return p

    print("抽帧 %d 张(源:%s)" % (len(jobs), os.path.basename(args.video)))
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        paths = list(ex.map(grab, jobs))

    by = {}
    for (cid, t, k), p in zip(jobs, paths):
        by.setdefault(cid, []).append((p, t))

    hdr = 26
    for cid in ids:
        c, times = plan[cid]
        items = sorted(by[cid], key=lambda x: x[1])
        rows = math.ceil(len(items) / args.cols)
        im = Image.new("RGB", (args.cols * tw, hdr + rows * (th + 18)), (16, 16, 16))
        d = ImageDraw.Draw(im)
        d.text((6, 7), "#%03d  %.1f-%.1f  shots=%d" % (cid, c["start"], c["end"], c["shots"]),
               fill=(120, 255, 120), font=font)
        for i, (p, t) in enumerate(items):
            r, cc = divmod(i, args.cols)
            x, y = cc * tw, hdr + r * (th + 18)
            im.paste(Image.open(p), (x, y))
            d.text((x + 5, y + th + 3), "t=%.1f" % t, fill=(255, 220, 60), font=small)
        out = os.path.join(outdir, "c%03d.png" % cid)
        im.save(out)
    print("生成 %d 张 -> %s" % (len(ids), outdir))


if __name__ == "__main__":
    main()
