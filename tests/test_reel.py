"""
自检断言 —— 标准库 unittest,不新增依赖。

    python tests/test_reel.py            # 直接跑
    python -m unittest discover -s tests # CI 方式跑

覆盖:
  T1 回合合并:间隔 <= merge_gap 合并 / > merge_gap 不合并 / merge_gap=0 等价于不合并
  T2 人工闸门可审计:profile 的 select.exclude_intervals 必须与负样本 GT 的区间逐条一致
  T3 长回合强制入选:人工核定的长回合段与 >=30 拍(门控前上界)回合必须都进选段;
     选段里不得出现被人工排除的区间
  T4 manifest 段时长累加 == 成片实测时长
"""
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import yaml  # noqa: E402  (reelconf 也用它,不是新依赖)

from detect_hits import merge_rallies  # noqa: E402

PROFILE = os.path.join(ROOT, "config", "profiles", "club-2026-11-07.yaml")
NEG_GT = os.path.join(ROOT, "eval", "ground_truth", "c3_negative_rallies.json")
LONG_GT = os.path.join(ROOT, "eval", "ground_truth", "c3_long_rallies.json")
SEL = os.path.join(ROOT, "work", "analysis", "c3.v5.json")
HITS = os.path.join(ROOT, "work", "analysis", "c3.hits.json")
MANIFEST = os.path.join(ROOT, "work", "build", "c3.silent.mp4.segments.json")
MASTER = os.path.join(ROOT, "output", "master")


def _profile():
    with open(PROFILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _exclude_intervals():
    return [(float(a), float(b)) for a, b in
            (_profile().get("select", {}).get("exclude_intervals") or [])]


def _overlap(a, b):
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


class TestRallyMerge(unittest.TestCase):
    """T1 回合合并逻辑"""

    def test_merge_logic(self):
        a = {"start": 0.0, "end": 5.0, "shots": 6, "hits": [0.0, 1, 2, 3, 4, 5],
             "duration": 5.0, "mean_interval": 1.0, "std_interval": 0.0,
             "regularity": 1.0}
        b = {"start": 6.0, "end": 9.0, "shots": 3, "hits": [6.0, 7, 8],
             "duration": 3.0, "mean_interval": 1.0, "std_interval": 0.0,
             "regularity": 1.0}

        m = merge_rallies([dict(a), dict(b)], 2.5)      # 间隔 1.0s <= 2.5 -> 合并
        self.assertEqual(len(m), 1, m)
        self.assertEqual(m[0]["shots"], 9)
        self.assertEqual((m[0]["start"], m[0]["end"]), (0.0, 9.0))
        self.assertTrue(m[0].get("merged_from"), "合并出来的段必须留 merged_from 痕迹")

        z = merge_rallies([dict(a), dict(b)], 0)         # merge_gap=0 -> 不合并
        self.assertEqual(len(z), 2, z)
        self.assertEqual([r["shots"] for r in z], [6, 3])

        c = dict(b)
        c["start"], c["end"], c["hits"] = 8.0, 11.0, [8.0, 9, 10]   # 间隔 3.0s > 2.5
        m2 = merge_rallies([dict(a), dict(c)], 2.5)
        self.assertEqual(len(m2), 2, m2)


class TestManualGate(unittest.TestCase):
    """T2 人工闸门与负样本 GT 一致 / T3 长回合强制入选"""

    def test_exclude_matches_negative_gt(self):
        prof = set(_exclude_intervals())
        with open(NEG_GT, encoding="utf-8") as f:
            gt = json.load(f)
        gts = set((float(r["start"]), float(r["end"])) for r in gt["rallies"]
                  if not r.get("is_rally", True))
        self.assertTrue(gts, "负样本集不能为空")
        self.assertEqual(prof, gts,
                         f"人工闸门区间与负样本 GT 不一致:\n profile={sorted(prof)}\n gt={sorted(gts)}")

    def test_long_rally_in_selection(self):
        if not (os.path.exists(SEL) and os.path.exists(HITS)):
            self.skipTest("选段/检测产物缺失")
        with open(SEL, encoding="utf-8") as f:
            segs = json.load(f)["segments"]
        ex = _exclude_intervals()

        for s in segs:                       # 人工闸门生效:选段里不得有被排除的区间
            for x0, x1 in ex:
                self.assertFalse(s["start"] < x1 and s["end"] > x0,
                                 f"选段 {s['start']}-{s['end']} 落进了人工排除区间 {x0}-{x1}")

        with open(LONG_GT, encoding="utf-8") as f:   # 人工核定的长回合段必须在片
            gt = json.load(f)
        cands = [g for g in gt["rallies"] if g.get("long_rally_candidate")]
        self.assertTrue(cands, "真值里没有 long_rally_candidate 条目")
        for g in cands:
            dur = max(g["end"] - g["start"], 1e-6)
            best = max((_overlap((g["start"], g["end"]), (s["start"], s["end"])) / dur
                        for s in segs), default=0.0)
            self.assertGreaterEqual(best, 0.5,
                                    f"长回合段 {g['id']}({g.get('segment_no')}) "
                                    f"{g['start']:.1f}-{g['end']:.1f} 未进选段(覆盖 {best:.2f})")

        with open(HITS, encoding="utf-8") as f:        # >=30 拍(门控前上界)也必须进片
            rallies = json.load(f)["rallies"]
        long_shots = int(_profile()["select"]["long_rally_shots"])
        for r in rallies:
            if max(r["shots"], r.get("shots_ungated", 0)) < long_shots:
                continue
            if any(r["end"] > x0 and r["start"] < x1 for x0, x1 in ex):
                continue
            dur = max(r["end"] - r["start"], 1e-6)
            best = max((_overlap((r["start"], r["end"]), (s["start"], s["end"])) / dur
                        for s in segs), default=0.0)
            self.assertGreaterEqual(best, 0.5,
                                    f">={long_shots} 拍回合 {r['start']:.1f}-{r['end']:.1f} "
                                    f"未进选段(覆盖率 {best:.2f})")


class TestManifestDuration(unittest.TestCase):
    """T4 manifest 段时长累加 == 成片实测时长"""

    def test_manifest_equals_film(self):
        if not os.path.exists(MANIFEST):
            self.skipTest("manifest 缺失")
        with open(MANIFEST, encoding="utf-8") as f:
            man = json.load(f)
        total = sum(float(s.get("reel_duration", 0.0)) for s in man["segments"])
        name = _profile()["clips"][0]["name"] + ".mp4"
        mp4 = os.path.join(MASTER, name)
        self.assertTrue(os.path.exists(mp4), f"成片不存在: {mp4}")
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", mp4],
                             capture_output=True, text=True, encoding="utf-8")
        dur = float(out.stdout.strip())
        self.assertLessEqual(abs(total - dur), 1.0,
                             f"manifest 段时长累加 {total:.2f}s != 成片实测 {dur:.2f}s ({mp4})")


if __name__ == "__main__":
    unittest.main(verbosity=2)
