"""
把成片从 output/master 拷到素材目录旁边的"集锦成片"文件夹。

用法:
    python tools/sync_deliver.py                    # 默认拷到 <source_dir>/集锦成片
    python tools/sync_deliver.py --dest D:\\某目录   # 指定目标目录
"""
import argparse, os, shutil, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from reelconf import add_config_args, config_from_args, resolve_path


def main():
    ap = argparse.ArgumentParser(description="成片交付到素材目录")
    ap.add_argument("--dest", default=None, help="目标目录(默认 <source_dir>/集锦成片)")
    add_config_args(ap)
    args = ap.parse_args()

    cfg = config_from_args(args)
    master_dir = resolve_path(cfg.at("output.master_dir"), ROOT)
    source_dir = resolve_path(cfg.at("source_dir"), ROOT)
    dest = args.dest or os.path.join(source_dir, "集锦成片")
    os.makedirs(dest, exist_ok=True)

    if not os.path.isdir(master_dir):
        print(f"母版目录不存在,跳过: {master_dir}")
        return

    files = sorted(f for f in os.listdir(master_dir) if f.lower().endswith(".mp4"))
    if not files:
        print(f"母版目录里没有成片: {master_dir}")
        return

    for f in files:
        s = os.path.join(master_dir, f)
        d = os.path.join(dest, f)
        shutil.copy2(s, d)
        print(f"synced {f}  {os.path.getsize(d)/1024/1024:.1f} MB")


if __name__ == "__main__":
    main()
