"""
击球时刻核对表 —— 验证"拍数"这个数是不是真的。

为什么需要:ground truth 的拍数决定一个回合算不算"长回合"(≥20/≥30 拍),
拍数错了,R4 的口径就错了。但拍数没法靠 6 帧的联系表数清楚。

做法:把检测到的每一次击球时刻,各抽 3 帧(击球前 0.1s / 击球瞬间 / 击球后 0.1s),
拼成一张表。人眼逐格看"球拍有没有真的碰到球" ——
有接触 = 这一拍是真的;那一格没人打球 = 这一拍是误检(捡球/脚步/衣物摩擦)。

注意:这张表只用来**核对击球时刻**,不用来定回合边界 ——
边界仍然按 docs/标注规范.md 第二节,只看画面、容差 ±0.5s。

用法:
    python tools/hit_sheet.py --video <src.mp4> --hits work/analysis/c3.hits.json \
        --rally 560.5-582.8 --out work/sheets/hits/c3_560.png
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from reelconf import add_config_args, config_from_args, enable_utf8_safe_output  # noqa: E402

enable_utf8_safe_output()


def load_font(path, size):
    if path and os.path.exists(path):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def extract(video, start, dur, fps, outdir, w, h):
    os.makedirs(outdir, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{start:.3f}", "-i", video, "-t", f"{dur:.3f}",
           "-vf", f"fps={fps},scale={w}:{h}", "-q:v", "3",
           "-f", "image2", os.path.join(outdir, "f%06d.jpg")]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("FFMPEG ERROR:", r.stderr[:400])
        return 0
    return len([f for f in os.listdir(outdir) if f.endswith(".jpg")])


def build_sheet(cells, out, fw, fh, cols, font, bar_h):
    rows = (len(cells) + cols - 1) // cols
    head_h = bar_h + 4
    sheet = Image.new("RGB", (fw * cols, rows * (fh + head_h)), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(cells):
        r, c = divmod(i, cols)
        y = r * (fh + head_h)
        d.text((c * fw + 3, y + 2), label, fill=(120, 255, 120), font=font)
        sheet.paste(im, (c * fw, y + head_h))
    sheet.save(out)
    return out


def main():
    ap = argparse.ArgumentParser(description="击球时刻核对表")
    ap.add_argument("--video", required=True)
    ap.add_argument("--hits", required=True)
    ap.add_argument("--rally", required=True, help="回合源时间 start-end(秒)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cols", type=int, default=12)
    ap.add_argument("--frame-width", type=int, default=240)
    ap.add_argument("--frame-height", type=int, default=135)
    ap.add_argument("--sample-fps", type=float, default=20.0)
    ap.add_argument("--lead", type=float, default=0.5, help="回合前后多抽多少秒")
    ap.add_argument("--around", type=float, default=0.1, help="每拍前后各抽多少秒")
    add_config_args(ap)
    args = ap.parse_args()

    cfg = config_from_args(args)
    an = cfg.section("annotation")
    font = load_font(an["font"], int(an["font_size"]))
    small = load_font(an["font"], max(10, int(an["font_size"]) - 3))

    rs, _, re = args.rally.partition("-")
    r0, r1 = float(rs), float(re)
    d = json.load(open(args.hits, encoding="utf-8"))
    cand = [r for r in d["rallies"] if r["end"] >= r0 and r["start"] <= r1]
    if not cand:
        raise SystemExit(f"没有找到重叠回合: {r0}-{r1}")
    r = max(cand, key=lambda x: x["shots"])
    hits = r["hits"]
    print(f"回合 {r['start']:.1f}-{r['end']:.1f}s  shots={r['shots']}  hits={len(hits)}")

    fw, fh = args.frame_width, args.frame_height
    fps = args.sample_fps
    outdir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(outdir, exist_ok=True)
    ex_start = r["start"] - args.lead
    ex_dur = (r["end"] + args.lead) - ex_start
    tmp = tempfile.mkdtemp(prefix="hitsheet_")
    try:
        n = extract(args.video, ex_start, ex_dur, fps, tmp, fw, fh)
        if not n:
            raise SystemExit("抽帧失败")
        cells = []
        for i, t in enumerate(hits):
            for k, dt in enumerate((-args.around, 0.0, args.around)):
                tt = t + dt
                idx = int(round((tt - ex_start) * fps)) + 1
                p = os.path.join(tmp, "f%06d.jpg" % idx)
                if not (1 <= idx <= n and os.path.exists(p)):
                    continue
                im = Image.open(p).convert("RGB")
                dr = ImageDraw.Draw(im)
                dr.rectangle([0, im.size[1] - 13, im.size[0], im.size[1]], fill=(0, 0, 0))
                dr.text((3, im.size[1] - 12), f"#{i:02d} {tt:.2f}", fill=(255, 255, 0), font=small)
                cells.append((f"hit{i:02d} k{k}", im))
        out = build_sheet(cells, args.out, fw, fh, args.cols, font, 18)
        print(f"SHEET {out}  cells={len(cells)}  ({r['shots']} 拍)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
