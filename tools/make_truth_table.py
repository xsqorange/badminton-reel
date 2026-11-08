"""
拍数真值定案表(第 3 棒)—— 一次性解决「同一区间在不同 GT 文件里拍数打架」。

三套口径都列出来,各自标清输入文件 / 筛选字段 / 生成命令 / 生成时刻:
  A 独立计数口径(上界):work/independent_corrected.json
      raw_strong_transients − double_hit_pairs(逐帧复核过的「拍+球」双响对数)
  B 单帧判定口径(下界):eval/ground_truth/c3_long_rallies.json 的 shots
  C 检测器口径:work/analysis/c3.hits.json 的 shots(门控前后)

输出:
  eval/tables/c3_shot_truth_table.json       结构化定案表(裁定表,放 eval/tables 不放 GT 目录)
  docs/执行报告.md 第十一节                     人读版表格 + 三口径摘要 + 裁定
不重渲、不改 base、不提交 git(第 3 棒纪律)。
"""
import json
import os
import subprocess

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GTD = os.path.join(R, "eval", "ground_truth")
TBL = os.path.join(R, "eval", "tables")


def mtime(p):
    try:
        import datetime
        return datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return "?"


indep = json.load(open(os.path.join(R, "work", "independent_corrected.json"),
                       encoding="utf-8"))["results"]
gt_long = json.load(open(os.path.join(GTD, "c3_long_rallies.json"), encoding="utf-8"))["rallies"]
gt_neg = json.load(open(os.path.join(GTD, "c3_negative_rallies.json"), encoding="utf-8"))["rallies"]
gt_live = {r["id"]: r for r in gt_long if not r.get("deprecated")}
gt_negmap = {r["id"]: r for r in gt_neg}
hits = json.load(open(os.path.join(R, "work", "analysis", "c3.hits.json"),
                      encoding="utf-8"))["rallies"]
burst = {}
bp = os.path.join(R, "work", "sheets", "burst", "index.json")
if os.path.exists(bp):
    burst = json.load(open(bp, encoding="utf-8"))
# 帧串判定(E1)结果:worker 的 judgments_*.json
judge = {}
for b in "ABC":
    q = os.path.join(R, "work", "sheets", "burst", f"judgments_{b}.json")
    if os.path.exists(q):
        for iv in json.load(open(q, encoding="utf-8"))["intervals"]:
            judge[iv["id"]] = iv


def det_hit(rid, s, e):
    """检测器口径:与该区间重叠最多的检测段(门控后 shots)"""
    ov = [r for r in hits if r["end"] > s and r["start"] < e]
    if not ov:
        return None, None
    best = max(ov, key=lambda r: min(r["end"], e) - max(r["start"], s))
    return best["shots"], best


rows = []
for r in indep:
    rid = r["id"]
    s, e = r["start"], r["end"]
    g = gt_live.get(rid) or gt_negmap.get(rid)
    single = g.get("shots") if g else None
    is_rally = bool(g.get("is_rally", True)) if g else True
    d_shots, d_seg = det_hit(rid, s, e)
    j = judge.get(rid)
    e1 = None
    if j:
        e1 = len(j.get("hit", [])) + len(j.get("extra_hits", []) or [])
    b = burst.get(rid)
    rows.append({
        "id": rid, "start": s, "end": e,
        "audio_candidates_raw": r["raw_strong_transients"],
        "double_hit_pairs_verified": r["double_hit_pairs"],
        "shots_A_independent": r["corrected_shots"],
        "shots_B_single_frame": single,
        "shots_C_detector": d_shots,
        "shots_E1_burst": e1,
        "cadence_median_s": r.get("cadence_median_s"),
        "is_rally": is_rally,
        "ge30_A": r["corrected_shots"] >= 30,
        "ge30_B": (single >= 30) if single is not None else None,
        "ge30_E1": (e1 >= 30) if e1 is not None else None,
        "evidence": {
            "A_逐帧复核双响对": "work/sheets/indep/verify_*.jpg",
            "B_单帧复核表": f"work/sheets/gtreview/{rid}.png",
            "E1_帧串表": f"work/sheets/burst/{rid}_burst.png",
            "E1_无先验联系表": f"work/sheets/burst/{rid}_dense.png",
        },
        "candidate_count_pre_merged": len(b["candidates"]) if b else None,
    })

ge30 = {}
for key, label in (("ge30_A", "A 独立计数"), ("ge30_B", "B 单帧判定"), ("ge30_E1", "E1 帧串判定")):
    # 「未判定」与「判定为否」是两回事:E1 全行都是 null(判定未回填)时,
    # 不能报成「≥30 拍 0 条」—— 那会被读成 E1 已跑完且结论为否。
    judged = [r for r in rows if r.get(key) is not None]
    hits_ = [r["id"] for r in judged if r.get(key) and r["is_rally"]]
    ge30[key] = {"label": label, "n": len(hits_) if judged else None, "ids": hits_,
                 "judged_intervals": len(judged),
                 "status": "已判定" if judged else "未判定(E1 判定未回填)"}

table = {
    "clip": "c3", "source": "20261107_龙泽_3号场.mp4",
    "calibers": {
        "A_独立计数(上界)": {"input": "work/independent_corrected.json",
                            "field": "corrected_shots = raw_strong_transients − double_hit_pairs",
                            "mtime": mtime(os.path.join(R, "work", "independent_corrected.json")),
                            "note": "双响对数逐帧复核过(work/sheets/indep/verify_*.jpg);但计数含邻场击球 ⇒ 上界"},
        "B_单帧判定(下界)": {"input": "eval/ground_truth/c3_long_rallies.json",
                             "field": "shots", "mtime": mtime(os.path.join(GTD, "c3_long_rallies.json")),
                             "note": "每个候选只抽 1 帧;击球相位常不落在该帧 ⇒ 系统性漏拍(裁定 F2/F3)"},
        "C_检测器口径": {"input": "work/analysis/c3.hits.json",
                         "field": "rallies[].shots(门控后)", "mtime": mtime(os.path.join(R, "work/analysis/c3.hits.json")),
                         "note": "主球场 ROI 门控后的计数;门控只改拍数不改回合区间"},
        "E1_帧串判定": {"input": "work/sheets/burst/<id>_burst.png + index.json",
                        "field": "hit 候选数 + extra_hits",
                        "mtime": mtime(os.path.join(R, "work/sheets/burst/index.json")) if burst else "?",
                        "note": "裁定 E1 通道二(t±0.15s 三帧帧串)+ 通道三(6fps 无先验扫描)"},
    },
    "ge30_summary": ge30,
    "rows": rows,
}
os.makedirs(TBL, exist_ok=True)
json.dump(table, open(os.path.join(TBL, "c3_shot_truth_table.json"), "w", encoding="utf-8",
                      newline="\n"), ensure_ascii=False, indent=1)

# ---------------- 报告第十一节 ----------------
L = ["\n\n## 十一、拍数真值定案表(第 3 棒)\n",
     "\n同一物理区间在三套口径下给出不同的拍数 —— 本节把它们并排列出,并给出各口径的"
     "输入文件 / 筛选字段 / 生成时刻。**不做取舍式合并**:A 是上界(含邻场击球),"
     "B 是下界(单帧漏拍),E1 是帧串判定。\n",
     "\n### 11.1 三套口径的来源\n",
     "\n| 口径 | 输入文件 | 字段/筛选 | 生成时刻 | 性质 |\n|---|---|---|---|---|"]
for k, v in table["calibers"].items():
    L.append("| {} | `{}` | {} | {} | {} |\n".format(k, v["input"], v["field"], v["mtime"], v["note"]))
L.append("\n### 11.2 逐条定案表(26 条)\n")
L.append("\n| id | 源区间 | 候选(原始瞬态) | **复核过的双响对** | A 独立计数 | B 单帧 | E1 帧串 | 判定 | ≥30(A/B/E1) | 证据 |\n")
L.append("|---|---|---|---|---|---|---|---|---|---|\n")
for r in rows:
    g30 = "{}/{}/{}".format("是" if r["ge30_A"] else "否",
                            "是" if r["ge30_B"] else "否",
                            ("是" if r["ge30_E1"] else "否") if r["ge30_E1"] is not None else "—")
    L.append("| {} | {:.1f}–{:.1f} | {} | {} | **{}** | {} | {} | {} | {} | `{}` |\n".format(
        r["id"], r["start"], r["end"], r["audio_candidates_raw"], r["double_hit_pairs_verified"],
        r["shots_A_independent"], r["shots_B_single_frame"],
        r["shots_E1_burst"] if r["shots_E1_burst"] is not None else "待判",
        "回合" if r["is_rally"] else "**非回合**", g30, r["evidence"]["E1_帧串表"]))
L.append("\n### 11.3 ≥30 拍裁定\n")
for key, v in ge30.items():
    if v["n"] is None:
        L.append("- **{}**:**未判定** —— {}\n".format(v["label"], v["status"]))
    else:
        L.append("- **{}**:真回合里 ≥30 拍的 **{} 条** —— {}\n".format(
            v["label"], v["n"], "、".join(v["ids"]) if v["ids"] else "无"))
L.append(
    "\n**裁定**:\n"
    "- 唯一能交叉验证的两个口径(A 独立计数、B 单帧判定)在 ≥30 这条线上**不一致**,"
    "且区间跨越 30 ⇒ **「本批是否存在 ≥30 拍长回合」在现有证据下无法定论**;"
    "把任一口径单独当结论都不成立(这正是 Chief F1/F2/F3 指出的问题)。\n"
    "- 4 条可能 ≥30 的候选是 **L00 / L01 / L06**(+ L02 已裁定为非回合,剔除)。"
    "三者的拍数区间分别是 L00 [13,32]、L01 [16,35]、L06 [11,30]。\n"
    "- ★**无论哪个口径成立,这 3 条都已在成片里**(成片第 0 / 2 / 3 段,源 "
    "29.1–67.2 / 1211.2–1252.7 / 1345.4–1375.0)⇒ **成片内容不需要重出**,"
    "这与「停止重渲」的指令一致。\n"
    "- 要把 ≥30 这条线定死,缺的是**把邻场击球从计数里摘出去**;"
    "E4 的局部判据实测**分不开**(真回合通过率不高于负样本),"
    "而门控前置治 interval precision 是第 4 棒 ⇒ **这一条挂到第 4 棒**。\n")
L.append("\n### 11.4 口径冲突清单(同文并存但已标时点/来源)\n")
L.append("\n| 数字 | 出处 | 时点 | 现在怎么用 |\n|---|---|---|---|")
L.append("| F1=0.491 / MAE 11.57(n=7) | 报告早期版本 | 15:43 的 hits | 已作废,只作历史 |\n")
L.append("| F1=0.4906 / MAE 13.0(n=7) | 当前树复跑 | hits mtime %s | **现行值** |\n" % table["calibers"]["C_检测器口径"]["mtime"])
L.append("| 检测器 ≥30 拍 16 条 | 旧 hits(15:43) | 15:43 | 已作废 |\n")
L.append("| 检测器 ≥30 拍 0 条 | 当前 hits(门控后) | %s | 现行(门控把拍数压到 30 以下) |\n" % table["calibers"]["C_检测器口径"]["mtime"])
L.append("| 独立真值 ≥30 拍 4 条 | work/independent_corrected.json | %s | 参考(上界口径) |\n" % table["calibers"]["A_独立计数(上界)"]["mtime"])
L.append("| 当前 GT ≥30 拍 0 条 | c3_long_rallies.json | %s | 参考(下界口径) |\n" % table["calibers"]["B_单帧判定(下界)"]["mtime"])
L.append("\n")
p = os.path.join(R, "docs", "执行报告.md")
s = open(p, encoding="utf-8").read()
# 守卫按「章节序号」判重,不看标题文案 —— 标题被后续修订改写后(如「拍数真值定案表」
# 改成「口径对照与冲突清单」),按文案判会再追加一份重复的第十一节。
if not any(l.startswith("## 十一、") for l in s.split("\n")):
    open(p, "w", encoding="utf-8", newline="\n").write(s + "".join(L))
    print("定案表 -> docs/执行报告.md 第十一节")
else:
    print("docs/执行报告.md 已有第十一节 —— 跳过追加(只更新 JSON)")
print("定案表 -> eval/tables/c3_shot_truth_table.json")
for k, v in ge30.items():
    print("  ≥30(真回合)", k, v["n"], v["ids"])
print("\n每条的三口径:")
for r in rows:
    print("  {:>5s} {:7.1f}-{:7.1f} A={:3d} B={:4s} E1={:4s} {}".format(
        r["id"], r["start"], r["end"], r["shots_A_independent"],
        str(r["shots_B_single_frame"]), str(r["shots_E1_burst"]),
        "非回合" if not r["is_rally"] else ""))
print("\ngit status:", repr(subprocess.run(["git", "-C", R, "status", "--porcelain"],
                                         capture_output=True, text=True).stdout))
