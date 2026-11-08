"""
等价性核验 —— 证明"参数搬到配置"这件事没有改变任何产出。

做法:用当前代码重跑分析阶段(特征提取 → 击球检测 → 选段),
      与仓库里已归档的基线 config/select/*.json 逐字段比对。

基线来自重构前的那次真实运行(旧代码 + 当时的硬编码参数)。
若本脚本全绿,说明重构前后选段结果完全一致。

用法:
    python tools/verify_equivalence.py                # 全流程核验(含重跑特征提取,较慢)
    python tools/verify_equivalence.py --reuse-csv    # 复用已有 CSV,只重跑检测与选段
    python tools/verify_equivalence.py --no-redetect  # 直接用归档 hits.json,只核验选段
    python tools/verify_equivalence.py --only m3

--no-redetect 的意义:选段是"算法改动"最常动的地方,而源素材可能已被归档/移走。
  用归档的 hits.json 直接核验选段,就不依赖源文件,回归检查随时能跑。
"""
import argparse, json, os, shutil, subprocess, sys, time

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
                # 基线没有、结果有 = **新增字段**(schema 演进),不参与等价判定;
                # 「基线有、结果缺」才是行为变化,仍判失败。
                out.append(f"{path}.{k}: 新增字段(不参与等价判定)")
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
    # 新增字段(基线没有、结果有)只是 schema 演进,不算行为变化;
    # 「基线有、结果缺」仍然判失败。新增字段要打印出来,让人看得见。
    added = [d for d in diffs if "新增字段(不参与等价判定)" in d]
    diffs = [d for d in diffs if d not in added]
    if not diffs:
        extra = f"(新增字段 {len(added)} 处,不参与等价判定)" if added else ""
        print(f"  [PASS] {label}: 与基线完全一致{extra}")
        for d in added[:6]:
            print(f"         {d}")
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
    ap.add_argument("--no-redetect", action="store_true",
                    help="不重跑击球检测,直接用归档 hits.json(源素材不在时也能核验选段)")
    ap.add_argument("--stamp-params", action="store_true",
                    help="把当前生效的 analysis/select 参数写进 config/select/baseline_params.json"
                         "(重生成归档基线后必须重新打戳)")
    add_config_args(ap)
    args = ap.parse_args()

    cfg = config_from_args(args)
    work = resolve_path(cfg.at("output.work_dir"), ROOT)
    analysis_dir = os.path.join(work, cfg.at("output.analysis_subdir"))
    os.makedirs(analysis_dir, exist_ok=True)

    # ---- 归档基线的参数版本(按批次记)----
    # 归档的 hits.json 是**用某一组检测参数**跑出来的;选段又是用某一组选段参数跑的。
    # 如果改了检测参数却用 --no-redetect 复用旧 hits,核验就变成"新逻辑配旧数据",
    # 门禁会给出一个假的绿灯。所以按批次把参数版本记下来:
    #   版本一致 → 照常做字节级比对(这才是有意义的 PASS);
    #   版本不同 → **说明差异来源**,并如实标成"跳过(未核验)",不能算 PASS。
    #   源素材还在 → 可以按新参数重生成基线后重新核验;
    #   源素材已删 → 该批次从此失去字节级核验能力(要如实说,不能装作通过)。
    cur_params = {
        "analysis": {k: cfg.at("analysis." + k) for k in
                     ("hit_thresh_db", "hit_median_offset_db", "hit_rise_db",
                      "hit_min_gap", "hit_env_window_ms", "hit_median_window",
                      "rally_max_gap", "hit_min_rally_shots", "min_shots")},
        "select": {k: cfg.at("select." + k) for k in
                   ("target_seconds", "speed", "min_lead",
                    "min_tail", "max_lead", "max_tail", "min_gap_between",
                    "long_rally_shots", "long_rally_force",
                    "long_rally_exempt_score_evidence")},
    }
    stamp_path = os.path.join(BASELINE_DIR, "baseline_params.json")
    if args.stamp_params:
        stamp = json.load(open(stamp_path, encoding="utf-8")) \
            if os.path.exists(stamp_path) else {}
        if not isinstance(stamp.get("batches"), dict):
            stamp = {"batches": stamp}
        for clip in cfg["clips"]:
            stamp["batches"][clip["key"]] = cur_params
        with open(stamp_path, "w", encoding="utf-8") as f:
            json.dump(stamp, f, ensure_ascii=False, indent=2)
        print(f"参数版本已打戳 -> {stamp_path}")
        return
    stamps = {}
    if os.path.exists(stamp_path):
        raw = json.load(open(stamp_path, encoding="utf-8"))
        stamps = raw.get("batches", raw) if isinstance(raw, dict) else {}
    else:
        print("[WARN] config/select/baseline_params.json 不存在:"
              "无法确认归档基线适用哪组参数(建议打戳)")

    def param_diff(batch):
        """该批次的归档参数版本与当前参数的差异(空 = 一致)"""
        st = stamps.get(batch)
        if not st:
            return None
        diff = []
        for sec in ("analysis", "select"):
            for k, v in st.get(sec, {}).items():
                cur = cur_params[sec].get(k)
                if cur != v:
                    diff.append(f"{sec}.{k}: 基线={v} 当前={cur}")
        return diff

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
        if args.no_redetect:
            hits_base = os.path.join(BASELINE_DIR, f"{key}.hits.json")
            if not os.path.exists(hits_base):
                raise SystemExit(f"[FAIL] 无归档 hits.json 可复用: {hits_base}")
            shutil.copy2(hits_base, hits)
            print(f">>> [{key}] 复用归档 hits.json")
        else:
            run_stage("detect_hits.py", [src, hits], f"[{key}] 重跑击球检测")

        sel = os.path.join(analysis_dir, f"{key}.v5.json")
        run_stage("select_v5.py", [hits, mcsv, acsv, sel], f"[{key}] 重跑选段")

        # 该批次的归档基线是用哪组参数跑的?不一致就不能拿它验当前逻辑。
        diff = param_diff(key)
        if diff:
            src_ok = os.path.exists(src)
            print(f"[SKIP] {key}: 归档基线与当前生效参数不一致 —— 该批次**未核验**(不是通过)")
            for d in diff:
                print("   -", d)
            if src_ok:
                print(f"   源素材在({src})⇒ 可以按当前参数重生成该批次的归档基线后重新核验:")
                print(f"      python tools/verify_equivalence.py --only {key} --reuse-csv")
                print(f"      python tools/verify_equivalence.py --stamp-params")
            else:
                print(f"   源素材已不在({src})⇒ 该批次从此**失去字节级核验能力**;")
                print("      这不是回归,但也不能再声称它被核验过。")
            results.append(None)
            results.append(None)
            continue

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
    if ok and not skipped:
        print("结论: 重构前后产出完全一致 ✅")
    elif ok:
        print(f"结论: 已核验部分完全一致 ✅;另有 {len(skipped)//2} 个批次因参数版本不同未核验(见上)")


if __name__ == "__main__":
    main()
