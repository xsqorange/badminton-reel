"""
等价性核验 —— 证明"参数搬到配置"这件事没有改变任何产出。

做法:用当前代码重跑分析阶段(特征提取 → 击球检测 → 选段),
      与仓库里已归档的基线 config/select/*.json 逐字段比对。

基线来自重构前的那次真实运行(旧代码 + 当时的硬编码参数)。
若本脚本全绿,说明重构前后选段结果完全一致。

用法:
    python tools/verify_equivalence.py                # 全流程核验(含重跑特征提取,较慢)
    python tools/verify_equivalence.py --reuse-csv    # 复用已有 CSV,只重跑检测与选段
    python tools/verify_equivalence.py --only m3
"""
import argparse, json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from reelconf import add_config_args, config_from_args, resolve_path

BASELINE_DIR = os.path.join(ROOT, "config", "select")


def run_stage(script, args, desc):
    cmd = [sys.executable, os.path.join(ROOT, "src", script)] + [str(a) for a in args]
    print(f"\n>>> {desc}")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit(f"[FAIL] {desc}")
    print(f"    done in {time.time() - t0:.0f}s")


def diff_json(a, b, path="", out=None, limit=12):
    """递归比较两个 JSON,返回差异描述列表。"""
    if out is None:
        out = []
    if len(out) >= limit:
        return out
    if type(a) is not type(b):
        out.append(f"{path or '<root>'}: 类型不同 {type(a).__name__} vs {type(b).__name__}")
        return out
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: 基线缺失")
            elif k not in b:
                out.append(f"{path}.{k}: 结果缺失")
            else:
                diff_json(a[k], b[k], f"{path}.{k}", out, limit)
            if len(out) >= limit:
                break
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: 长度不同 {len(a)} vs {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                diff_json(x, y, f"{path}[{i}]", out, limit)
                if len(out) >= limit:
                    break
    else:
        if a != b:
            out.append(f"{path}: {a!r} != {b!r}")
    return out


def compare(label, new_path, base_path):
    if not os.path.exists(base_path):
        print(f"  [SKIP] {label}: 无基线 {base_path}")
        return None
    if not os.path.exists(new_path):
        print(f"  [FAIL] {label}: 产物缺失 {new_path}")
        return False
    with open(new_path, encoding="utf-8") as f:
        new = json.load(f)
    with open(base_path, encoding="utf-8") as f:
        base = json.load(f)
    diffs = diff_json(base, new)
    if not diffs:
        print(f"  [PASS] {label}: 与基线完全一致")
        return True
    print(f"  [FAIL] {label}: {len(diffs)} 处差异")
    for d in diffs[:12]:
        print(f"         {d}")
    return False


def main():
    ap = argparse.ArgumentParser(description="重构等价性核验")
    ap.add_argument("--only", help="只核验某个场次 key")
    ap.add_argument("--reuse-csv", action="store_true",
                    help="复用已有 motion/audio CSV,不重跑特征提取")
    add_config_args(ap)
    args = ap.parse_args()

    cfg = config_from_args(args)
    work = resolve_path(cfg.at("output.work_dir"), ROOT)
    analysis_dir = os.path.join(work, cfg.at("output.analysis_subdir"))
    os.makedirs(analysis_dir, exist_ok=True)

    results = []
    for clip in cfg["clips"]:
        key = clip["key"]
        if args.only and key != args.only:
            continue
        src = resolve_path(os.path.join(cfg.at("source_dir"), clip["file"]), ROOT)
        prefix = os.path.join(analysis_dir, key)
        mcsv, acsv = prefix + ".motion.csv", prefix + ".audio.csv"

        print(f"\n{'=' * 60}\n  核验 {key}  {clip['name']}\n{'=' * 60}")

        if not (args.reuse_csv and os.path.exists(mcsv) and os.path.exists(acsv)):
            run_stage("probe_highlights.py", [src, prefix], f"[{key}] 重跑特征提取")
        else:
            print(f">>> [{key}] 复用已有 CSV")

        hits = os.path.join(analysis_dir, f"{key}.hits.json")
        run_stage("detect_hits.py", [src, hits], f"[{key}] 重跑击球检测")

        sel = os.path.join(analysis_dir, f"{key}.v5.json")
        run_stage("select_v5.py", [hits, mcsv, acsv, sel], f"[{key}] 重跑选段")

        results.append(compare(f"{key} 击球检测 hits.json", hits,
                                os.path.join(BASELINE_DIR, f"{key}.hits.json")))
        results.append(compare(f"{key} 选段 v5.json", sel,
                                os.path.join(BASELINE_DIR, f"{key}.v5.json")))

    ok = [r for r in results if r is True]
    bad = [r for r in results if r is False]
    skipped = [r for r in results if r is None]
    print(f"\n{'=' * 60}")
    print(f"等价性核验: 通过 {len(ok)} / 失败 {len(bad)} / 跳过 {len(skipped)}")
    if bad:
        print("结论: 存在差异,重构未做到行为等价")
        raise SystemExit(1)
    if ok:
        print("结论: 重构前后产出完全一致 ✅")


if __name__ == "__main__":
    main()
