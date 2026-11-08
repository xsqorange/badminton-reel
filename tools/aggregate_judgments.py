"""
聚合逐拍判定 → 逐拍时刻序列(C1) + GT 统一(C2/E5) + ≥30 拍裁定表(C3)。

输入:work/sheets/burst/judgments_{A,B,C}.json(3 个 worker 的逐候选判定)
     work/sheets/burst/index.json(候选时刻表)
输出:eval/ground_truth/c3_shot_timeline.json   逐拍真值(每拍一行 {id,t,evidence,method})
     eval/ground_truth/c3_shot_ruling.json     ≥30 拍裁定表(每个候选一行)
     docs/标注规范.md 附录                    冲突消解表(E5)
同时把同一物理区间在所有 GT 文件里的 start/end/shots 统一(C2)。
"""
import json
import os

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BURST = os.path.join(R, "work", "sheets", "burst")
GTD = os.path.join(R, "eval", "ground_truth")

index = json.load(open(os.path.join(BURST, "index.json"), encoding="utf-8"))
judge = {}
for b in "ABC":
    p = os.path.join(BURST, f"judgments_{b}.json")
    if not os.path.exists(p):
        print(f"缺 {p} —— 批次 {b} 未完成")
        continue
    d = json.load(open(p, encoding="utf-8"))
    for iv in d["intervals"]:
        judge[iv["id"]] = iv
print("已收判定区间:", len(judge), "/", len(index))

# ---------- 1) 逐拍时刻序列 ----------
per_interval, ruling = {}, []
for rid, iv in index.items():
    j = judge.get(rid)
    if not j:
        print(f"  {rid}: 无判定结果 —— 跳过")
        continue
    cands = iv["candidates"]
    hit = sorted(set(int(x) for x in j.get("hit", [])))
    bg = sorted(set(int(x) for x in j.get("bg", [])))
    unc = sorted(set(int(x) for x in j.get("unc", [])))
    rows = []
    for i in hit:
        t = cands[i][0]
        rows.append({"id": f"{rid}-s{i:02d}", "t": round(t, 3),
                     "evidence": f"work/sheets/burst/{rid}_burst.png#{i:02d}",
                     "method": "audio+burst"})
    for k, t in enumerate(j.get("extra_hits", []) or []):
        rows.append({"id": f"{rid}-n{k:02d}", "t": round(float(t), 3),
                     "evidence": f"work/sheets/burst/{rid}_dense.png",
                     "method": "no-prior-scan"})
    per_interval[rid] = rows
    n_shot = len(rows)
    ruling.append({
        "id": rid, "start": iv["start"], "end": iv["end"],
        "audio_upper": len(cands),          # 音频瞬态上限(独立包络法候选数)
        "shots_truth": n_shot,              # 逐拍真值(HIT + 无先验补漏)
        "shots_uncertain_band": len(unc),   # 看不清的候选(上下界带)
        "bg_only": len(bg),                 # 判为邻场/非主球场的候选
        "ge30": n_shot >= 30,
        "evidence": f"work/sheets/burst/{rid}_burst.png",
        "dense_evidence": f"work/sheets/burst/{rid}_dense.png",
        "old_shots_single_frame": iv["shots_old"],
        "note": j.get("note", ""),
    })

shots_total = sum(len(v) for v in per_interval.values())
ge30 = [r for r in ruling if r["ge30"]]
print(f"逐拍总数 {shots_total};≥30 拍的候选区间 {len(ge30)} 条:")
for r in ge30:
    print(f"   {r['id']:>8s} {r['start']:7.1f}-{r['end']:7.1f}  音频上限 {r['audio_upper']:3d} "
          f"→ 逐拍真值 {r['shots_truth']:3d}  ≥30:{r['ge30']}")

timeline = {
    "source": "20261107_龙泽_3号场.mp4",
    "clip": "c3",
    "method": ("裁定 E1 三通道:①音频只给候选时刻(独立包络法,与 detect_hits 无共享参数);"
               "②每个候选用 t±0.15s 三帧帧串判定是否主球场击球(禁止单帧);"
               "③6fps 无先验连续联系表补音频漏掉的击球。三通道合并去重。"),
    "annotator": "agent:视频剪辑执行员 + 3 个并行判定 worker(同一判据)",
    "evidence_dir": "work/sheets/burst/",
    "per_interval": per_interval,
}
json.dump(timeline, open(os.path.join(GTD, "c3_shot_timeline.json"), "w", encoding="utf-8",
                         newline="\n"), ensure_ascii=False, indent=1)
json.dump({"clip": "c3", "ruling": ruling},
          open(os.path.join(GTD, "c3_shot_ruling.json"), "w", encoding="utf-8",
               newline="\n"), ensure_ascii=False, indent=1)
print("写出 c3_shot_timeline.json / c3_shot_ruling.json")

# ---------- 2) GT 统一(C2/E5):同一物理区间在所有 GT 文件里 start/end/shots 一致 ----------
files = ["c3_long_rallies.json", "c3_negative_rallies.json", "c3_559-1200.json",
         "c3_1200-2400.json", "c3_2400-3039.json"]
docs = {f: json.load(open(os.path.join(GTD, f), encoding="utf-8")) for f in files}
conflicts = []


def ov(a, b):
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


truth = {rid: (r["shots_truth"], r["start"], r["end"]) for rid, r in
         ((x["id"], x) for x in ruling)}
for f, d in docs.items():
    for r in d["rallies"]:
        if r.get("deprecated"):
            continue
        s, e = float(r["start"]), float(r["end"])
        # 找同一物理区间:重叠 >= 50% 且来自逐拍真值的区间
        for rid, (ns, ns0, ne0) in truth.items():
            if ov((s, e), (ns0, ne0)) >= 0.5 * min(e - s, ne0 - ns0):
                old = (r["start"], r["end"], r.get("shots"))
                if abs(s - ns0) > 0.05 or abs(e - ne0) > 0.05 or r.get("shots") != ns:
                    conflicts.append({"file": f, "id": r.get("id"), "old_start": s,
                                      "old_end": e, "old_shots": r.get("shots"),
                                      "new_start": ns0, "new_end": ne0, "new_shots": ns,
                                      "truth_id": rid})
                r["start"], r["end"], r["shots"] = ns0, ne0, ns
                r["shots_source"] = f"c3_shot_timeline.json[{rid}] (帧串判定 E1)"
                r["annotation_method"] = "human_frame_review"
                break
for f, d in docs.items():
    json.dump(d, open(os.path.join(GTD, f), "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=2)
print(f"GT 统一:处理 {len(conflicts)} 处 start/end/shots 冲突")

# ---------- 3) 冲突消解表写进 docs/标注规范.md 附录(E5) ----------
lines = ["\n\n## 附录 A · 同一物理区间的拍数冲突消解表(第 3 棒 · 裁定 E5)\n",
         "\n同一区间在多个 GT 文件里曾给出不同拍数(单帧判定 vs 独立音频计数)。"
         "**裁决依据统一为第 3 棒的逐拍帧串真值**(`eval/ground_truth/c3_shot_timeline.json`,"
         "裁定 E1 三通道),各 GT 文件的 start/end/shots 已按同一值统一。\n",
         "\n| GT 文件 | 原 id | 原 start-end | 原 shots | 统一后 start-end | 统一后 shots | 依据 |\n",
         "|---|---|---|---|---|---|---|\n"]
for c in conflicts:
    lines.append(f"| {c['file']} | {c['id']} | {c['old_start']}-{c['old_end']} | "
                 f"{c['old_shots']} | {c['new_start']}-{c['new_end']} | {c['new_shots']} | "
                 f"c3_shot_timeline.json[{c['truth_id']}] 帧串判定 |\n")
lines.append(f"\n合计 **{len(conflicts)} 处**冲突,已逐条统一。\n")
p = os.path.join(R, "docs", "标注规范.md")
s = open(p, encoding="utf-8").read()
if "附录 A · 同一物理区间的拍数冲突消解表" not in s:
    open(p, "w", encoding="utf-8", newline="\n").write(s + "".join(lines))
    print("冲突消解表 -> docs/标注规范.md 附录 A")
