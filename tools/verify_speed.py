"""
验证变速是否真正生效:
- 从成片抽一个已知片段,测量其画面内容与源片段的对应关系
- 更直接:对比成片总时长与选中片段源时长之和的比例

用法: python tools/verify_speed.py [--config ...] [--profile ...]
路径与期望倍率都来自配置文件。
"""
import argparse, json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from reelconf import add_config_args, config_from_args, resolve_path


def main():
    ap = argparse.ArgumentParser(description="变速倍率核验")
    add_config_args(ap)
    args = ap.parse_args()

    cfg = config_from_args(args)
    speed = cfg.at("select.speed")
    pad_each = cfg.at("build.cut_padding")
    work = resolve_path(cfg.at("output.work_dir"), ROOT)
    analysis_dir = os.path.join(work, cfg.at("output.analysis_subdir"))
    master_dir = resolve_path(cfg.at("output.master_dir"), ROOT)

    for clip in cfg["clips"]:
        name, key = clip["name"], clip["key"]
        v5 = os.path.join(analysis_dir, f"{key}.v5.json")
        out = os.path.join(master_dir, f"{name}.mp4")
        if not (os.path.exists(v5) and os.path.exists(out)):
            print(f"=== {key} 跳过(缺少 {v5 if not os.path.exists(v5) else out})")
            continue

        d = json.load(open(v5, encoding="utf-8"))
        segs = d["segments"]
        src_total = sum(s["end"] - s["start"] for s in segs)
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "default=nw=1:nk=1", out],
                                   capture_output=True, text=True,
                                   encoding="utf-8", errors="replace").stdout.strip())
        # 成片里还有 concat 边界余量(每段前后各 cut_padding 秒)
        pad = len(segs) * pad_each * 2
        expect = src_total / speed + pad
        print(f"=== {key} {name}")
        print(f"  选中 {len(segs)} 段,源时长合计 {src_total:.1f}s")
        print(f"  理论输出 {src_total:.1f}/{speed} + {pad:.1f} 边界余量 = {expect:.1f}s")
        print(f"  实际成片 {dur:.1f}s   偏差 {dur-expect:+.1f}s")
        print(f"  实测变速比 ≈ {src_total/dur:.3f} (期望 {speed:.3f})")
        sp = {}
        for s in segs:
            sp[round(s["end"] - s["start"], 1)] = sp.get(round(s["end"] - s["start"], 1), 0) + 1
        print(f"  片段时长分布: {sorted(sp.items())[:8]}")


if __name__ == "__main__":
    main()
