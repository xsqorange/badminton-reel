"""第 3 棒收尾:聚合逐拍判定 -> C3 裁定表 -> 写进执行报告 -> 提交。"""
import json
import os
import subprocess
import sys

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BURST = os.path.join(R, "work", "sheets", "burst")

missing = [b for b in "ABC" if not os.path.exists(os.path.join(BURST, f"judgments_{b}.json"))]
if missing:
    print("判定未齐,缺批次:", missing)
    sys.exit(3)

subprocess.run([sys.executable, os.path.join(R, "tools", "aggregate_judgments.py")], check=True)

ruling = json.load(open(os.path.join(R, "eval", "ground_truth", "c3_shot_ruling.json"),
                       encoding="utf-8"))["ruling"]
tl = json.load(open(os.path.join(R, "eval", "ground_truth", "c3_shot_timeline.json"),
                    encoding="utf-8"))
shots_total = sum(len(v) for v in tl["per_interval"].values())
ge30 = [r for r in ruling if r["ge30"]]
covered = sum(r["end"] - r["start"] for r in ruling)

rows = ["\n\n## 十、≥30 拍裁定表(C3 · 第 3 棒)\n",
        "\n判据:裁定 E1 三通道 —— ①音频只给候选时刻(独立包络法,与 detect_hits 无共享参数);"
        "②每个候选用 **t±0.15s 三帧帧串**判是否主球场击球(禁止单帧);"
        "③6fps 无先验连续联系表补音频漏掉的击球。\n",
        "\n| 候选 | 源区间 | 音频瞬态上限 | **逐拍真值** | ≥30 拍? | 不确定带 | 证据 |",
        "|---|---|---|---|---|---|---|"]
for r in ruling:
    rows.append("| {} | {:.1f}–{:.1f} | {} | **{}** | {} | ±{} | `{}` |".format(
        r["id"], r["start"], r["end"], r["audio_upper"], r["shots_truth"],
        "**是**" if r["ge30"] else "否", r["shots_uncertain_band"], r["evidence"]))
rows.append("\n")
rows.append("### 10.1 结论\n")
rows.append("- 逐拍真值合计 **{} 拍**(28 个候选区间);≥30 拍的候选区间 **{} 条**。\n".format(
    shots_total, len(ge30)))
if ge30:
    rows.append("- **本批存在 ≥30 拍长回合**:{} ⇒ 这些回合必须全部进片(C4)。\n".format(
        "、".join("{} {:.1f}–{:.1f}s({} 拍)".format(r["id"], r["start"], r["end"], r["shots_truth"])
                  for r in ge30)))
else:
    rows.append("- **本批不存在 ≥30 拍长回合(在逐拍帧串口径下)** —— 覆盖度论证见 10.2。\n")
rows.append(
    "- ⚠ 与上一版的差异:上一版用**单帧判定**得 6~18 拍/段,已知有系统性漏拍偏差(裁定 F2/F3);"
    "本版是帧串判定,逐拍时刻与证据见 `eval/ground_truth/c3_shot_timeline.json`。\n")
rows.append("### 10.2 覆盖度论证\n")
rows.append(
    "- 候选集怎么来的:①上一轮长回合候选表里的区间(音频强瞬态 ≥20 拍);"
    "②三个 detection 窗口里 `shots>=20` 的条目;按物理区间去重后共 **{} 个区间**。\n".format(len(ruling)))
rows.append(
    "- 覆盖了全片多少:这 {} 个区间合计 {:.0f}s,占 4833.3s 素材的 **{:.1f}%**;"
    "逐拍判定**只在这 {} 个区间上做**。\n".format(
        len(ruling), covered, 100.0 * covered / 4833.3, len(ruling)))
rows.append(
    "- 未覆盖的区如何排除:未被选入候选集的区间,其**音频瞬态上限 < 20 拍**(这是候选集的生成规则);"
    "通道三的无先验补漏只在候选区间内执行 ⇒ 对这些区间,即使漏检,也只有在漏掉一半以上击球时才可能凑到 30 拍,"
    "而这需要该区间存在大量音频无响应的击球 —— 与「该区间被音频瞬态筛出」的前提自相矛盾。\n")
rows.append(
    "- ⚠ 残余不确定性:①判据本身仍有主观性(UNC 候选单列为不确定带);"
    "②通道三的无先验扫描按 Chief 的建议只在候选区间内做,不是全片 6fps 普查 —— "
    "**因此「不存在 ≥30 拍」的结论只在上述候选集内成立**,全片级别的普查留作下一棒。\n")

p = os.path.join(R, "docs", "执行报告.md")
s = open(p, encoding="utf-8").read()
if "## 十、≥30 拍裁定表" not in s:
    open(p, "w", encoding="utf-8", newline="\n").write(s + "".join(rows))
    print("C3 裁定表 -> docs/执行报告.md 第十节")

subprocess.run(["git", "-C", R, "add", "-A"], check=True)
subprocess.run(["git", "-C", R, "commit", "-q", "-m",
                "feat(第3棒): 逐拍帧串真值 + GT 唯一性 + >=30 拍裁定表\n\n"
                "- eval/ground_truth/c3_shot_timeline.json: 逐拍时刻序列(每拍 {id,t,evidence,method}),"
                "method 标 audio+burst / no-prior-scan\n"
                "- eval/ground_truth/c3_shot_ruling.json: >=30 拍裁定表(音频上限/逐拍真值/是否>=30/证据)\n"
                "- GT 统一:同一物理区间在 5 个 GT 文件里的 start/end/shots 统一为帧串真值;"
                "冲突消解表进 docs/标注规范.md 附录 A\n"
                "- 执行报告新增第十节:C3 裁定表 + 覆盖度论证"], check=True)
print("committed")
print(subprocess.run(["git", "-C", R, "log", "--oneline", "-1"],
                     capture_output=True, text=True).stdout.strip())
print("git status:", repr(subprocess.run(["git", "-C", R, "status", "--porcelain"],
                                         capture_output=True, text=True).stdout))
print("\n=== C3 裁定表(逐拍真值 >=30 的区间) ===")
for r in ruling:
    print("  {:>8s} {:.1f}-{:.1f}s  音频上限 {:3d}  逐拍真值 {:3d}  >=30: {}".format(
        r["id"], r["start"], r["end"], r["audio_upper"], r["shots_truth"], r["ge30"]))
