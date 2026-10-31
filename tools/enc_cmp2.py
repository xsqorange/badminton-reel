"""
画质对比:源与各编码版本按【同一源时间点】抽帧并排。
编码版本是从 935s 起的 20s 片段,所以源时间 947s 对应片段内 12s。

用法: python tools/enc_cmp2.py [--config ...] [--profile ...]
素材路径与输出目录来自配置文件。
"""
import argparse, os, subprocess, sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from reelconf import add_config_args, config_from_args, resolve_path

SRC_T = 947.0      # 源时间轴上的取帧点
SEG_START = 935.0  # 编码片段的起点


def grab(src, t, path, scale):
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", src,
                    "-frames:v", "1", "-vf", f"scale={scale}", "-pix_fmt", "rgb24", path],
                   check=False)
    return os.path.exists(path)


def main():
    ap = argparse.ArgumentParser(description="编码画质并排对比")
    add_config_args(ap)
    args = ap.parse_args()

    cfg = config_from_args(args)
    enc = cfg.section("encode")
    clip = cfg["clips"][0]
    SRC = resolve_path(os.path.join(cfg.at("source_dir"), clip["file"]), ROOT)
    OUT = os.path.join(resolve_path(cfg.at("output.work_dir"), ROOT), "enc_test2")
    TMP = os.path.join(OUT, "cmp")
    os.makedirs(TMP, exist_ok=True)

    versions = [
        ("源原生", None),
        ("A_cq17_现方案", os.path.join(OUT, "A_cq17_bv0.mp4")),
        ("B_cq23", os.path.join(OUT, "B_cq23_bv0.mp4")),
        ("E_cq24_12M", os.path.join(OUT, "E_cq24_max12M.mp4")),
        ("F_cq26_10M", os.path.join(OUT, "F_cq26_max10M.mp4")),
    ]

    rows = []
    for name, p in versions:
        out = os.path.join(TMP, name + ".png")
        if p is None:
            ok = grab(SRC, SRC_T, out, enc["scale"])
        else:
            ok = grab(p, SRC_T - SEG_START, out, enc["scale"])
        if ok:
            rows.append((name, out))
        else:
            print("miss", name)

    print("rows:", [n for n, _ in rows])

    # 全幅
    W, H = 900, 506
    sheet = Image.new("RGB", (W, H * len(rows)), (0, 0, 0))
    for i, (n, p) in enumerate(rows):
        sheet.paste(Image.open(p).convert("RGB").resize((W, H), Image.LANCZOS), (0, i * H))
    sheet.save(os.path.join(OUT, "cmp_full.jpg"), quality=93)

    # 100% 裁切(球衣/文字区)
    box = (980, 560, 1380, 726)
    cw, ch = box[2] - box[0], box[3] - box[1]
    sheet = Image.new("RGB", (cw, ch * len(rows)), (0, 0, 0))
    for i, (n, p) in enumerate(rows):
        sheet.paste(Image.open(p).convert("RGB").crop(box), (0, i * ch))
    sheet.save(os.path.join(OUT, "cmp_crop100.jpg"), quality=96)
    print("saved cmp_full.jpg / cmp_crop100.jpg")


if __name__ == "__main__":
    main()
