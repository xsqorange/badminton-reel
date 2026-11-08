"""
标注碎片合并器 —— 把人工核验碎片合成 ground truth 文件。

为什么要碎片:一张联系表 8 行,一个窗口十几张表;分开看更不容易看漏,
所以按联系表分片判定,最后合并。合并这一步是**唯一**写 GT 的入口,
所有格式/完整性检查都在这里做,不在人脑里做。

碎片格式(每个碎片一个 json):
{
  "window": [559.0, 1200.0],
  "candidates": [                       # 候选回合的判定
    {"id": 0, "verdict": "rally",         # rally | not_rally | uncertain
     "start": 39.5, "end": 48.0, "shots": 8,
     "shots_uncertain": false, "note": ""},
    {"id": 1, "verdict": "not_rally", "note": "捡球"},
    {"id": 2, "verdict": "rally", "start": 50.0, "end": 58.0, "shots": 6,
     "merge_with": 3, "note": "与候选 #003 连续(检测器切开)"}
  ],
  "missed": [                           # 空档扫描发现的漏检回合
    {"start": 150.0, "end": 156.5, "shots": 5, "note": "检测器漏检"}
  ]
}

merge_with:声明"这两个候选其实是同一个真实回合"(检测器把它切开了)。
合并时区间取并集、拍数相加 —— 按标注规范第一节,标注记的是整段回合,不是切开的碎片。

输出(eval/ground_truth/<clip>_<起>-<止>.json),格式见 docs/标注规范.md 第六节。

用法:
    python tools/merge_gt.py --fragments work/ann_gt/w*.json \
        --candidates work/sheets/ann/w559.0-1200.0/candidates.json \
        --clip c3 --source "20261107_龙泽_3号场.mp4" \
        --out eval/ground_truth/c3_559-1200.json
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from reelconf import enable_utf8_safe_output  # noqa: E402

enable_utf8_safe_output()

# 边界允许越出窗口一点(窗口边缘的回合,发球/得分可能在窗口外一点点)
EDGE_TOLERANCE = 2.0
# 两个标注回合之间的最小间隔(秒);小于它说明两块标注重叠了
MIN_GAP = 0.5


def load_fragments(paths):
    cands, missed, windows = [], [], set()
    for p in paths:
        d = json.load(open(p, encoding="utf-8"))
        w = tuple(float(x) for x in d["window"])
        windows.add(w)
        for c in d.get("candidates", []):
            c = dict(c)
            c["_src"] = os.path.basename(p)
            cands.append(c)
        for m in d.get("missed", []):
            m = dict(m)
            m["_src"] = os.path.basename(p)
            missed.append(m)
    return cands, missed, windows


def main():
    ap = argparse.ArgumentParser(description="合并标注碎片成 ground truth")
    ap.add_argument("--fragments", nargs="+", required=True, help="碎片 json(可多个)")
    ap.add_argument("--candidates", required=True,
                    help="该窗口的 candidates.json(用于校验候选 id 是否判全)")
    ap.add_argument("--clip", required=True)
    ap.add_argument("--source", required=True, help="源视频文件名")
    ap.add_argument("--annotator", default="agent:视频剪辑执行员")
    ap.add_argument("--method", default="contact-sheet visual review (tools/annotate_rounds.py)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cands, missed, windows = load_fragments(args.fragments)
    if len(windows) != 1:
        raise SystemExit(f"碎片里的窗口不一致: {windows}")
    win = list(windows.pop())
    lo, hi = win

    cd = json.load(open(args.candidates, encoding="utf-8"))
    expected = {c["id"] for c in cd["candidates"]}
    seen = {}
    for c in cands:
        if c["id"] in seen:
            raise SystemExit(f"候选 #{c['id']} 判了两次 "
                             f"({seen[c['id']]} / {c['_src']})")
        seen[c["id"]] = c["_src"]
    missing = sorted(expected - set(seen))
    extra = sorted(set(seen) - expected)
    if missing:
        raise SystemExit(f"有 {len(missing)} 个候选没判定: {missing[:20]}")
    if extra:
        raise SystemExit(f"碎片里有不在候选表里的 id: {extra[:20]}")

    errors = []
    rallies = []
    for c in cands:
        v = c.get("verdict")
        if v == "rally":
            s, e = float(c["start"]), float(c["end"])
            if not (s < e):
                errors.append(f"#{c['id']} 区间非法: {s}-{e}")
                continue
            if s < lo - EDGE_TOLERANCE or e > hi + EDGE_TOLERANCE:
                errors.append(f"#{c['id']} 超出窗口 [{lo},{hi}]: {s}-{e}")
            shots = int(c.get("shots") or 0)
            if shots < 1:
                errors.append(f"#{c['id']} 拍数非法: {shots}")
            rallies.append({"start": s, "end": e, "shots": shots,
                            "shots_uncertain": bool(c.get("shots_uncertain")),
                            "note": c.get("note") or "",
                            "_ids": [c["id"]],
                            "_merge_with": c.get("merge_with")})
        elif v == "not_rally":
            continue
        elif v == "uncertain":
            errors.append(f"#{c['id']} 判为 uncertain,必须先复核再合并")
        else:
            errors.append(f"#{c['id']} 缺 verdict(需 rally/not_rally/uncertain)")

    for m in missed:
        s, e = float(m["start"]), float(m["end"])
        if not (s < e):
            errors.append(f"漏检回合区间非法: {s}-{e}")
            continue
        if s < lo - EDGE_TOLERANCE or e > hi + EDGE_TOLERANCE:
            errors.append(f"漏检回合超出窗口: {s}-{e}")
        rallies.append({"start": s, "end": e, "shots": int(m.get("shots") or 0),
                        "shots_uncertain": bool(m.get("shots_uncertain", True)),
                        "note": m.get("note") or "",
                        "_ids": ["gap:%.1f" % s],
                        "_merge_with": None})

    # ---- 合并被检测器切开的同一回合 ----
    # 检测器会把一个真实回合切成相邻的几段。按标注规范第一节,
    # "一个回合 = 从发球到得分" —— 标注要记整段,不是记切开的碎片。
    # 标注员在碎片里用 merge_with 声明"这两个候选是同一个回合"。
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    by_id = {}
    for r in rallies:
        for cid in r["_ids"]:
            parent.setdefault(cid, cid)
            by_id[cid] = r
    for r in rallies:
        mw = r.get("_merge_with")
        if mw is None:
            continue
        targets = mw if isinstance(mw, (list, tuple)) else [mw]
        for t in targets:
            t = int(t)
            if t not in by_id:
                errors.append(f"#{r['_ids'][0]} 的 merge_with 指向不存在的候选 #{t}")
                continue
            union(r["_ids"][0], t)
    groups = {}
    for r in rallies:
        groups.setdefault(find(r["_ids"][0]), []).append(r)

    # ---- 自动合并:区间重叠或首尾相接的两条,是同一个真实回合 ----
    # 常见来源:空档扫描补录的漏检回合,与候选回合其实是同一次对打
    # (标注员看到"已经在进行中",起点取了空档边界)。
    # 羽毛球两个回合之间必然有捡球的停顿(≥1s),所以"贴在一起"只能是同一个回合。
    by_start = sorted(groups.items(), key=lambda kv: min(m["start"] for m in kv[1]))
    merged_groups = []
    for _, members in by_start:
        if merged_groups:
            prev = merged_groups[-1]
            prev_end = max(m["end"] for m in prev)
            this_start = min(m["start"] for m in members)
            if this_start <= prev_end:            # 重叠或相接
                prev.extend(members)
                continue
        merged_groups.append(list(members))

    merged = []
    for members in merged_groups:
        if len(members) == 1:
            m = members[0]
            merged.append({"start": m["start"], "end": m["end"], "shots": m["shots"],
                           "shots_uncertain": m["shots_uncertain"], "note": m["note"]})
            continue
        ids = sorted((str(i) for m in members for i in m["_ids"]),
                     key=lambda x: (x.startswith("gap:"), x))
        explicit = all(m.get("_merge_with") is not None for m in members)
        if explicit:
            # 显式声明的合并 = 检测器把回合切成几段,拍数相加
            shots = sum(m["shots"] for m in members)
            uncertain = any(m["shots_uncertain"] for m in members)
        else:
            # 重叠/相接 = 同一个回合被两处各标了一段,拍数取大值并标不确定
            shots = max(m["shots"] for m in members)
            uncertain = True
        notes = [m["note"] for m in members if m["note"]]
        merged.append({
            "start": min(m["start"] for m in members),
            "end": max(m["end"] for m in members),
            "shots": shots,
            "shots_uncertain": uncertain,
            "note": ("%d 处标注合并为同一回合(%s);%s"
                     % (len(members), "/".join(str(i) for i in ids),
                        "; ".join(notes)))[:400],
        })
    rallies = merged
    print(f"  合并检测器切开的回合: {len(groups)} 组 -> {len(rallies)} 个回合")

    rallies.sort(key=lambda r: r["start"])
    for i in range(len(rallies) - 1):
        gap = rallies[i + 1]["start"] - rallies[i]["end"]
        if gap < MIN_GAP:
            errors.append(f"标注区间重叠: #{i} 结束 {rallies[i]['end']:.1f} "
                          f"与 #{i+1} 开始 {rallies[i+1]['start']:.1f} (间隔 {gap:.1f}s)")

    if errors:
        print(f"[FAIL] {len(errors)} 处问题:")
        for e in errors[:40]:
            print("   -", e)
        raise SystemExit(1)

    out = {
        "source": args.source,
        "clip": args.clip,
        "window": {"start": win[0], "end": win[1]},
        "annotator": args.annotator,
        "method": args.method,
        "tolerance_seconds": 0.5,
        "rallies": [
            {"id": f"r{i:03d}", "start": round(r["start"], 1), "end": round(r["end"], 1),
             "shots": r["shots"], "shots_uncertain": r["shots_uncertain"],
             "note": r["note"]}
            for i, r in enumerate(rallies)
        ],
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    n_cand = len([c for c in cands if c.get("verdict") == "rally"])
    print(f"候选 {len(cands)} 判为真回合 {n_cand}  漏检补录 {len(missed)}  "
          f"-> 标注 {len(rallies)} 个")
    print(f"窗口 [{win[0]:.1f}, {win[1]:.1f}]  ->  {args.out}")


if __name__ == "__main__":
    main()
