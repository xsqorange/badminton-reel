"""
回合识别评测 —— 对照标注 ground truth 算 precision / recall / F1。

回答四个问题:
  R2 回合识别准不准   → 检测层 P/R/F1(预测回合 vs 标注回合,按时间重叠匹配)
  R3 片段是否完整     → 选段的"完整回合率"(片段覆盖标注回合时长的比例)
  R4 长回合有没有进片 → 长回合召回(检测层 + 在片层)+ 每个长回合在成片里的位置/时长
  R5 能否一键回归     → --compare 基线报告,指标下降超容差即退出码非 0

匹配判据(配置项 eval.match_overlap_ratio):
  重叠时长 / 标注回合时长 ≥ 该值 算命中(一对一贪心匹配,按重叠比例降序)

用法:
    python tools/eval_rounds.py --gt eval/ground_truth/c3_1200-2400.json \
        --hits work/analysis/c3.hits.json \
        --segments work/analysis/c3.v5.json \
        --manifest work/build/c3.silent.mp4.segments.json \
        --out eval/reports/c3.json [--profile club-2026-11-07]

    # 一键回归对比(改动算法后跑这个)
    python tools/eval_rounds.py ... --compare eval/baseline/baseline.json
"""
import argparse, glob, hashlib, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def file_sha256(path):
    """GT 文件哈希:基线里记一份;GT 变了就提示「基线过期」而不是报回归。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
sys.path.insert(0, os.path.join(ROOT, "src"))
from reelconf import add_config_args, config_from_args, resolve_path


def overlap(a, b):
    """两个区间 [start,end) 的重叠时长。"""
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def match_ratios(preds, gts, ratio):
    """一对一贪心匹配。返回 (pred_idx, gt_idx, ratio) 列表。"""
    cand = []
    for i, p in enumerate(preds):
        pg = p["start"], p["end"]
        for j, g in enumerate(gts):
            gg = g["start"], g["end"]
            r = overlap(pg, gg) / max(gg[1] - gg[0], 1e-6)
            if r >= ratio:
                cand.append((r, i, j))
    cand.sort(key=lambda x: (-x[0], x[1], x[2]))
    up, ug, out = set(), set(), []
    for r, i, j in cand:
        if i in up or j in ug:
            continue
        up.add(i)
        ug.add(j)
        out.append((i, j, r))
    return out


def prf(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4)}


def load_gt(paths):
    """读标注文件。

    scope="detection"(默认):完整标注窗口,算 P/R/F1。
    scope="recall":只标了某一类对象(例如长回合),只算召回 —— 这类集合是候选驱动的,
    算精确率没有意义(未标注的回合会被误判成误检)。
    """
    gts = []
    scopes = []
    for p in paths:
        d = json.load(open(p, encoding="utf-8"))
        if "rallies" not in d:
            # 目录里可能混着非标注文件(如 c3_shot_ruling.json)—— 跳过并提示,
            # 别让整个评测崩掉。裁定表已移到 eval/tables/,这里只是兜底防呆。
            print(f"[SKIP] {os.path.basename(p)}: 没有 rallies 字段,不是标注文件,跳过")
            continue
        scope = d.get("scope")
        if scope is None:
            # detection 窗口 GT 本来就没有 scope 字段(默认即 detection)—— 不该误报;
            # 只有**候选型**真值(带 long_rally_candidate)缺 scope 才需要告警。
            if any(r.get("long_rally_candidate") for r in d.get("rallies", [])):
                print(f"[WARN] {os.path.basename(p)} 是候选型真值却没有 scope 字段 —— "
                      f"必须显式写 scope: \"recall\",否则会被当成完整标注窗口,"
                      f"跑出无意义的 P/R/F1。")
            scope = "detection"
        win = d.get("window", {})
        lo = float(win.get("start", 0.0))
        hi = float(win.get("end", 1e12))
        for r in d["rallies"]:
            if r.get("deprecated"):
                continue          # 墓碑条目:保留 id,但不再作为真值(口径 F)
            rid = r.get("id", "?")
            gts.append({"id": rid, "key": f"{os.path.basename(p)}::{rid}",
                        "start": float(r["start"]),
                        "end": float(r["end"]), "shots": int(r.get("shots", 0)),
                        "shots_uncertain": bool(r.get("shots_uncertain", False)),
                        "is_rally": bool(r.get("is_rally", True)),
                        "long_rally_candidate": bool(r.get("long_rally_candidate", False)),
                        "segment_no": r.get("segment_no"),
                        # 「拍数只认人工逐拍」:带逐帧复核标记的才算人工拍数
                        "human": (r.get("annotation_method") == "human_frame_review"
                                  and bool(r.get("shots_source"))),
                        "note": r.get("note", ""),
                        "file": os.path.basename(p), "window": (lo, hi),
                        "scope": scope})
        scopes.append((os.path.basename(p), scope))
    gts.sort(key=lambda g: g["start"])
    return gts, scopes


def in_window(items, lo, hi):
    return [x for x in items if x["end"] >= lo and x["start"] <= hi]


def main():
    ap = argparse.ArgumentParser(description="回合识别评测")
    ap.add_argument("--gt", required=True, help="标注文件或目录")
    ap.add_argument("--hits", required=True, help="detect_hits.py 输出")
    ap.add_argument("--segments", help="select_v5.py 输出(选段层评测)")
    ap.add_argument("--manifest", help="build_v4.py 的 .segments.json(成片实际内容)")
    ap.add_argument("--out", help="评测报告输出路径")
    ap.add_argument("--compare", help="基线报告路径,做回归对比")
    ap.add_argument("--save-baseline", help="把本次结果存成基线")
    ap.add_argument("--scope", help="只评测某个 scope 的标注集(detection / recall)")
    add_config_args(ap)
    args = ap.parse_args()

    cfg = config_from_args(args)
    ev = cfg.section("eval")
    ratio = float(ev["match_overlap_ratio"])
    complete_ratio = float(ev["complete_ratio"])
    long_shots = int(ev["long_rally_shots"])
    tol = float(ev["regression_tolerance"])

    gt_paths = ([args.gt] if os.path.isfile(args.gt)
                else sorted(glob.glob(os.path.join(args.gt, "*.json"))))
    if not gt_paths:
        raise SystemExit(f"找不到标注文件: {args.gt}")
    if args.scope:
        # 只加载指定 scope 的标注文件。
        # 同一批里常有不同用途的标注集:detection(完整窗口,算 P/R/F1)
        # 与 recall(候选驱动的长回合专集,只算召回)。
        # 混在一起算会让"精确率"失真,所以这里可以显式分开跑。
        keep = []
        for p in gt_paths:
            sc = json.load(open(p, encoding="utf-8")).get("scope", "detection")
            if sc == args.scope:
                keep.append(p)
            else:
                print(f"[SKIP] {os.path.basename(p)}: scope={sc}(不是 {args.scope})")
        gt_paths = keep
        if not gt_paths:
            raise SystemExit(f"没有 scope={args.scope} 的标注文件")
    gts, scopes = load_gt(gt_paths)

    hits = json.load(open(args.hits, encoding="utf-8"))
    preds = [{"start": r["start"], "end": r["end"], "shots": r["shots"]}
             for r in hits["rallies"]]

    # scope=recall 的标注集是候选驱动的(只标了长回合),不能混进 P/F1 ——
    # 未标注的回合会被算成误检,精确率没有意义。它们单独走召回评测。
    det_gts = [g for g in gts if g["scope"] == "detection"]
    rec_gts = [g for g in gts if g["scope"] == "recall"]

    # 标注窗口:可能有多段,评测按窗口分别做,再汇总
    windows = []
    for g in det_gts:
        w = tuple(g["window"])
        if w not in windows:
            windows.append(w)

    # ---------- 检测层 ----------
    det_tp = det_fp = det_fn = 0
    long_gt, long_det_hit = [], []
    shot_err = []
    shot_uncertain = 0
    for lo, hi in windows:
        gp_idx = [i for i, g in enumerate(det_gts) if g["window"] == (lo, hi)]
        gp = [det_gts[i] for i in gp_idx]
        pp = in_window(preds, lo, hi)
        pairs = match_ratios(pp, gp, ratio)
        det_tp += len(pairs)
        det_fp += len(pp) - len(pairs)
        det_fn += len(gp) - len(pairs)
        for i, j, r in pairs:
            # 拍数误差只在标注员**确认过拍数**的回合上统计。
            # 6 帧联系表数不清击球次数,标注员数不准的回合(GT 标 shots_uncertain)
            # 其拍数多半沿用检测器 —— 把它们算进 MAE 等于拿检测器验检测器。
            if gp[j].get("shots_uncertain"):
                shot_uncertain += 1
            else:
                shot_err.append(abs(pp[i]["shots"] - gp[j]["shots"]))
        for j, gi in enumerate(gp_idx):
            g = det_gts[gi]
            if g["shots"] >= long_shots:
                long_gt.append(g)
                hit = [p for p in pairs if p[1] == j]
                if hit:
                    long_det_hit.append((g, pp[hit[0][0]]))

    detection = prf(det_tp, det_fp, det_fn)

    # 长回合专集(scope=recall)里的每个回合:是否被检出
    # 只把**独立计数 ≥ 门槛**的回合算进长回合 —— 专集里也收录了 20~29 拍的
    # 候选(它们在检测器口径下可能够 30 拍),但按验收口径它们不是长回合。
    # 长回合专集(scope=recall):这里的拍数是**人工逐拍**判定的。
    #   is_rally=false 的条目是**负样本**(人工判定该区间不是回合):
    #   不计入长回合召回,只用来检查检测器有没有把非回合报成长回合(口径 E)。
    neg_report = []
    shot_err_rec, shot_err_neg = [], []
    for g in rec_gts:
        pp = in_window(preds, g["window"][0], g["window"][1])
        pairs = match_ratios(pp, [g], ratio)
        hit = pp[pairs[0][0]] if pairs else None
        det_shots = hit["shots"] if hit else 0
        if not g["is_rally"]:
            neg_report.append({"id": g["id"], "src_start": g["start"],
                               "src_end": g["end"], "gt_shots": g["shots"],
                               "det_shots": det_shots, "detected": bool(hit),
                               "false_long_rally": bool(det_shots >= long_shots)})
            if hit and not g["shots_uncertain"]:
                shot_err_neg.append(abs(det_shots - g["shots"]))
            continue
        if g["shots"] >= long_shots or g.get("long_rally_candidate"):
            long_gt.append(g)
            if hit:
                long_det_hit.append((g, hit))
        if hit and not g["shots_uncertain"]:
            shot_err_rec.append(abs(det_shots - g["shots"]))

    # ---- 长回合集合:按区间去重,拍数只认**人工逐拍**那一份 ----
    # 检测层窗口 GT 里可能还留着同一区间的旧口径条目(检测器自己拍的数、没有逐帧复核标记),
    # 那是第 1 棒要摆脱的自证循环 —— 同一区间只保留人工逐拍的那条。
    _by_iv = {}
    for g in long_gt:
        k = (round(g["start"], 1), round(g["end"], 1))
        cur = _by_iv.get(k)
        if cur is None or (g["human"] and not cur["human"]):
            _by_iv[k] = g
    long_gt = list(_by_iv.values())
    # 去重后重算命中(长回合专集那部分也要按去重后的集合)
    long_det_hit = []
    for g in long_gt:
        pp = in_window(preds, g["window"][0], g["window"][1])
        pairs = match_ratios(pp, [g], ratio)
        if pairs:
            long_det_hit.append((g, pp[pairs[0][0]]))

    # ---------- 选段层 ----------
    segs, manifest = [], []
    if args.segments and os.path.exists(args.segments):
        sd = json.load(open(args.segments, encoding="utf-8"))
        segs = sd["segments"]
    if args.manifest and os.path.exists(args.manifest):
        md = json.load(open(args.manifest, encoding="utf-8"))
        manifest = md["segments"]

    seg_metrics = {}
    if segs:
        seg_tp = seg_fp = 0
        complete = 0
        matched_segs = 0
        covered = []
        for lo, hi in windows:
            gp = [g for g in det_gts if g["window"] == (lo, hi)]
            ss = in_window(segs, lo, hi)
            pairs = match_ratios(ss, gp, ratio)
            seg_tp += len(pairs)
            seg_fp += len(ss) - len(pairs)
            matched_segs += len(pairs)
            for i, j, r in pairs:
                if r >= complete_ratio:
                    complete += 1
                covered.append(ss[i])
        seg_metrics = prf(seg_tp, seg_fp, 0)
        seg_metrics["matched"] = matched_segs
        seg_metrics["total"] = len(segs)
        seg_metrics["complete_rally_rate"] = round(
            complete / matched_segs, 4) if matched_segs else 0.0
        # 分母覆盖**全部成片段**(裁定 C7):未被 GT 覆盖的片段单独列出来 ——
        # 它们既不能算完整也不能算不完整,不许从分母里悄悄消失。
        seg_metrics["complete_rally_rate_all"] = (round(complete / len(segs), 4)
                                                  if segs else 0.0)
        seg_metrics["uncovered_segments"] = [
            {"start": s["start"], "end": s["end"], "shots": s.get("shots"),
             "reason": "未被任何标注回合覆盖(GT 未标注或边界不匹配)"}
            for s in segs if not any(s == c for c in covered)]

    # ---------- 长回合在片证据 ----------
    # 成片清单(build manifest)用 src_start/src_end,选段 JSON 用 start/end —— 两者都支持
    reel = manifest if manifest else segs

    def seg_span(s):
        if "src_start" in s:
            return (s["src_start"], s["src_end"])
        return (s["start"], s["end"])

    long_rallies = []
    long_in_reel = 0
    for g in long_gt:
        item = {"id": g["id"], "key": g["key"], "file": g["file"],
                "src_start": g["start"], "src_end": g["end"],
                "shots": g["shots"], "human": g["human"],
                "segment_no": g.get("segment_no"), "in_reel": False}
        best = None
        for s in reel:
            a, b = seg_span(s)
            ov = overlap((g["start"], g["end"]), (a, b))
            gd = max(g["end"] - g["start"], 1e-6)
            if ov / gd >= ratio and (best is None or ov > best[0]):
                best = (ov, s)
        if best:
            item["in_reel"] = True
            item["reel_start"] = best[1].get("reel_start")
            item["reel_duration"] = best[1].get("reel_duration")
            item["coverage"] = round(best[0] / max(g["end"] - g["start"], 1e-6), 3)
            long_in_reel += 1
        long_rallies.append(item)

    long_recall_det = round(len(long_det_hit) / len(long_gt), 4) if long_gt else 0.0
    long_recall_reel = round(long_in_reel / len(long_gt), 4) if long_gt else 0.0

    report = {
        "ground_truth_files": [os.path.basename(p) for p in gt_paths],
        "ground_truth_hashes": {os.path.basename(p): file_sha256(p) for p in gt_paths},
        "gt_rallies": len(gts),
        "windows": [list(w) for w in windows],
        "detection": detection,
        "shot_count_mae": round(sum(shot_err) / len(shot_err), 3) if shot_err else None,
        "shot_count_mae_n": len(shot_err),
        "shot_count_uncertain": shot_uncertain,
        "shot_count_mae_recall": round(sum(shot_err_rec) / len(shot_err_rec), 3) if shot_err_rec else None,
        "shot_count_mae_recall_n": len(shot_err_rec),
        "shot_count_mae_negative": round(sum(shot_err_neg) / len(shot_err_neg), 3) if shot_err_neg else None,
        "shot_count_mae_negative_n": len(shot_err_neg),
        "shot_count_mae_all": (round(sum(shot_err + shot_err_rec + shot_err_neg)
                                     / len(shot_err + shot_err_rec + shot_err_neg), 3)
                               if (shot_err or shot_err_rec or shot_err_neg) else None),
        "shot_count_mae_all_n": len(shot_err) + len(shot_err_rec) + len(shot_err_neg),
        # 逐帧复核口径(人工逐拍专集 + 负样本):本项目的主口径
        "shot_count_mae_frame_review": (round(sum(shot_err_rec + shot_err_neg)
                                              / len(shot_err_rec + shot_err_neg), 3)
                                        if (shot_err_rec or shot_err_neg) else None),
        "shot_count_mae_frame_review_n": len(shot_err_rec) + len(shot_err_neg),
        "negative_samples": neg_report,
        "negative_false_long_rally": sum(1 for x in neg_report if x["false_long_rally"]),
        "segment": seg_metrics,
        "long_rally": {
            "threshold_shots": long_shots,
            "gt_count": len(long_gt),
            "gt_count_ge30": sum(1 for g in long_gt if g["shots"] >= long_shots),
            "detected": len(long_det_hit),
            "recall_detection": long_recall_det,
            "in_reel": long_in_reel,
            "recall_reel": long_recall_reel,
            "list": long_rallies,
        },
    }

    # ---------- 输出 ----------
    print("=" * 62)
    print(" 回合识别评测")
    print("=" * 62)
    print(f"标注: {len(gts)} 个回合 / 窗口 {report['windows']} / 文件 {report['ground_truth_files']}")
    d = detection
    print(f"\n[R2] 检测层  TP={d['tp']} FP={d['fp']} FN={d['fn']}  "
          f"P={d['precision']:.3f} R={d['recall']:.3f} F1={d['f1']:.3f}")
    if shot_err:
        print(f"     拍数平均绝对误差 MAE={report['shot_count_mae']} "
              f"(基于 {len(shot_err)} 个拍数已确认的回合;另有 {shot_uncertain} 个拍数不确定,未计入)")
    if shot_err_rec:
        print(f"     拍数 MAE(人工逐拍专集)={report['shot_count_mae_recall']} "
              f"n={len(shot_err_rec)} | 负样本 MAE={report['shot_count_mae_negative']} "
              f"n={len(shot_err_neg)} | 合计 MAE={report['shot_count_mae_all']} "
              f"n={report['shot_count_mae_all_n']}")
    if report["shot_count_mae_frame_review_n"]:
        print(f"     ★拍数 MAE(逐帧复核口径,主口径)="
              f"{report['shot_count_mae_frame_review']} "
              f"n={report['shot_count_mae_frame_review_n']}")
    if neg_report:
        bad = sum(1 for x in neg_report if x["false_long_rally"])
        print(f"[负样本] {len(neg_report)} 条非回合;被误报成 ≥{long_shots} 拍的有 {bad} 条")
        for x in neg_report:
            print(f"     #{x['id']} {x['src_start']:.1f}-{x['src_end']:.1f}s "
                  f"人工 {x['gt_shots']} 拍 vs 检测 {x['det_shots']} 拍"
                  f"{'  ← 误报长回合' if x['false_long_rally'] else ''}")
    if seg_metrics:
        s = seg_metrics
        print(f"[R3] 选段层  命中 {s['matched']}/{s['total']}  "
              f"完整回合率={s['complete_rally_rate']:.3f}  P={s['precision']:.3f}")
    lr = report["long_rally"]
    print(f"[R4] 长回合  标注 {lr['gt_count']} 个(其中 >=30 拍门槛口径样本 "
          f"{lr['gt_count_ge30']} 个)  检出召回={lr['recall_detection']:.3f}  "
          f"在片召回={lr['recall_reel']:.3f}")
    if lr["gt_count_ge30"] == 0:
        print(f"     ⚠ ≥{long_shots} 拍门槛口径下本批没有样本 ⇒ R4 对本批的 ≥30 拍样本数待 C3 裁定表;"
              f"上表是「人工核定的长回合段」内容口径。")
    for it in lr["list"]:
        if it["in_reel"]:
            rs = it.get("reel_start")
            rd = it.get("reel_duration")
            pos = (f"成片 {rs:.1f}s 起 / {rd:.1f}s" if rs is not None and rd is not None
                   else "已入选(无成片清单,位置未知)")
            print(f"     ✓ #{it['id']} 源 {it['src_start']:.1f}-{it['src_end']:.1f}s "
                  f"({it['shots']}拍) → {pos}")
        else:
            print(f"     ✗ #{it['id']} 源 {it['src_start']:.1f}-{it['src_end']:.1f}s "
                  f"({it['shots']}拍) → 未进片")

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n报告 -> {args.out}")
    if args.save_baseline:
        os.makedirs(os.path.dirname(os.path.abspath(args.save_baseline)), exist_ok=True)
        with open(args.save_baseline, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"基线 -> {args.save_baseline}")

    # ---------- 回归对比 ----------
    if args.compare:
        base = json.load(open(args.compare, encoding="utf-8"))
        print("\n" + "=" * 62)
        print(f" 回归对比 vs {args.compare}")
        print("=" * 62)
        keys = [
            ("detection.f1", lambda r: r["detection"]["f1"], True),
            ("detection.precision", lambda r: r["detection"]["precision"], True),
            ("detection.recall", lambda r: r["detection"]["recall"], True),
            ("long_rally.recall_detection", lambda r: r["long_rally"]["recall_detection"], True),
            ("long_rally.recall_reel", lambda r: r["long_rally"]["recall_reel"], True),
            ("segment.complete_rally_rate", lambda r: r.get("segment", {}).get("complete_rally_rate", 0.0), True),
        ]
        regressed = []
        bh, ch = base.get("ground_truth_hashes"), report.get("ground_truth_hashes")
        if bh and bh != ch:
            print("\n⚠ 基线过期:GT 文件已变更(哈希不同) —— 本次不判定回归。")
            for k in sorted(set(bh) | set(ch or {})):
                print(f"   {k}: 基线 {bh.get(k, '-')}  当前 {ch.get(k, '-')}")
            # E7 退出码语义:0 = 通过且基线有效;1 = 回归;2 = 基线过期/不可比
            # (须重生成 baseline 才能放行)。"过期即 0" 已取消 —— 那等于放行开关
            # 可以被绕过:GT 一改,门就自动放行。
            raise SystemExit(2)
        for name, get, higher_better in keys:
            try:
                b = get(base)
            except Exception:
                b = None
            n = get(report)
            if b is None:
                print(f"  {name:34s} 基线无此项  当前 {n}")
                continue
            delta = n - b
            bad = (delta < -tol) if higher_better else (delta > tol)
            flag = "REGRESS" if bad else ("+" if delta > 0 else ("-" if delta < 0 else "="))
            print(f"  {name:34s} {b:8.4f} → {n:8.4f}  Δ{delta:+.4f}  {flag}")
            if bad:
                regressed.append((name, b, n))
        if regressed:
            print("\n结论: 存在回归 ❌")
            for name, b, n in regressed:
                print(f"   {name}: {b} → {n}")
            raise SystemExit(1)
        print("\n结论: 无回归 ✅")


if __name__ == "__main__":
    main()
