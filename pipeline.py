#!/usr/bin/env python
"""
羽毛球集锦剪辑 · 单一入口

用法:
    python pipeline.py                                  # 用 config/base.yaml
    python pipeline.py --profile short-60s              # 叠加一个 profile
    python pipeline.py --only m3                        # 只处理某一场
    python pipeline.py --skip analyze                   # 跳过耗时分析,复用已有选段
    python pipeline.py --from build                     # 从某步开始
    python pipeline.py --set encode.cq=24               # 临时覆盖单个参数

配置生效顺序:
    config/base.yaml  <  --profile <名字>(可重复)  <  --set key=value(可重复)

流程:
    analyze  ① 运动能量+音频RMS  ② 击球瞬态+回合聚类  ③ 选段(剔除不确定回合)
    build    ④ 切片+拼接         ⑤ 混音                ⑥ 微信版压制
"""
import argparse, json, os, shutil, subprocess, sys, time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

from reelconf import add_config_args, load_config, resolve_path


# ---------- 步骤封装 ----------
class Runner:
    def __init__(self, cfg, root, work, cfg_path):
        self.cfg = cfg
        self.root = root
        self.work = work
        # 已解析的配置快照:所有阶段共用同一份,避免各阶段解析出不同结果
        self.cfg_path = cfg_path
        out_cfg = cfg.section("output")
        self.analysis_dir = os.path.join(work, out_cfg["analysis_subdir"])
        self.build_dir = os.path.join(work, out_cfg["build_subdir"])
        for d in (self.analysis_dir, self.build_dir):
            os.makedirs(d, exist_ok=True)

    def run(self, script, args, desc):
        path = os.path.join(ROOT, "src", script)
        cmd = [sys.executable, path] + [str(a) for a in args] + ["--config", self.cfg_path]
        print(f"\n>>> {desc}")
        print(f"    $ {' '.join(os.path.basename(cmd[0]) if i == 0 else str(a) for i, a in enumerate(cmd))}")
        t0 = time.time()
        r = subprocess.run(cmd, cwd=self.root)
        if r.returncode != 0:
            raise SystemExit(f"[FAIL] {desc} (exit {r.returncode})")
        print(f"    done in {time.time() - t0:.0f}s")

    def source_of(self, clip):
        src = resolve_path(os.path.join(self.cfg["source_dir"], clip["file"]), self.root)
        if not os.path.exists(src):
            raise SystemExit(f"素材不存在: {src}")
        return src

    # ---------- 各阶段 ----------
    def analyze(self, clip):
        key = clip["key"]
        src = self.source_of(clip)
        # ① 运动能量 + 音频 RMS
        # probe_highlights.py 输出 <prefix>.motion.csv / <prefix>.audio.csv
        prefix = os.path.join(self.analysis_dir, key)
        self.run("probe_highlights.py", [src, prefix], f"[{key}] ① 提取运动能量与音频RMS")

        # ② 击球瞬态 + 回合聚类
        # profile 开了画面-音频共现门控(analysis.shot_gate_percentile > 0)时,
        # 必须把 ROI 运动能量 CSV 传进去 —— 否则门控代码永远不会被执行。
        hits = os.path.join(self.analysis_dir, f"{key}.hits.json")
        gate = float(self.cfg.section("analysis").get("shot_gate_percentile") or 0.0)
        hargs = [src, hits] + (["--motion", prefix + ".motion.csv"] if gate > 0 else [])
        self.run("detect_hits.py", hargs, f"[{key}] ② 击球瞬态检测与回合聚类")

        # ③ 选段
        sel = os.path.join(self.analysis_dir, f"{key}.v5.json")
        self.run("select_v5.py", [hits, prefix + ".motion.csv", prefix + ".audio.csv", sel],
                 f"[{key}] ③ 选段(剔除不确定回合)")
        return sel

    def build(self, clip, sel_path):
        key = clip["key"]
        src = self.source_of(clip)
        master_dir = resolve_path(self.cfg["output"]["master_dir"], self.root)
        os.makedirs(master_dir, exist_ok=True)

        # ④ 切片 + 拼接(两阶段)
        silent = os.path.join(self.build_dir, f"{key}.silent.mp4")
        self.run("build_v4.py", [src, sel_path, silent],
                 f"[{key}] ④ 切片 + concat 拼接")
        # ⑤ 混音
        bed = resolve_path(self.cfg["music"]["bed_file"], self.root)
        final = os.path.join(master_dir, f"{clip['name']}.mp4")
        self.run("mix.py", [silent, bed, final], f"[{key}] ⑤ 配乐混音")
        return final

    def wechat(self, clip, master):
        if not self.cfg["wechat"]["enabled"]:
            return None
        key = clip["key"]
        wd = resolve_path(self.cfg["output"]["wechat_dir"], self.root)
        os.makedirs(wd, exist_ok=True)
        out = os.path.join(wd, os.path.basename(master))
        self.run("press_wechat.py", [master, out], f"[{key}] ⑥ 微信版压制")
        return out


# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser(description="羽毛球集锦剪辑管线")
    add_config_args(ap)
    ap.add_argument("--only", help="只处理某个场次 key")
    ap.add_argument("--skip", action="append", default=[],
                    help="跳过阶段:analyze / build / wechat")
    ap.add_argument("--from", dest="stage", default="analyze",
                    choices=["analyze", "build", "wechat"])
    args = ap.parse_args()

    if not os.path.exists(args.config):
        legacy = os.path.join(ROOT, "config.yaml")
        if os.path.exists(legacy):
            raise SystemExit(
                f"配置文件不存在: {args.config}\n"
                f"提示:旧版 config.yaml 已拆分为 config/base.yaml + config/profiles/。\n"
                f"     可改用: --config {legacy}")
        raise SystemExit(f"配置文件不存在: {args.config}")

    cfg = load_config(args.config, profiles=args.profile, overrides=args.overrides)
    root = ROOT
    work = resolve_path(cfg["output"]["work_dir"], root)
    os.makedirs(work, exist_ok=True)

    # 落一份已解析的配置快照:各阶段共用同一份,且便于追溯"这次用的什么参数"
    cfg_path = os.path.join(work, "config.resolved.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg.as_dict(), f, ensure_ascii=False, indent=2)

    r = Runner(cfg, root, work, cfg_path)

    clips = cfg["clips"]
    if args.only:
        clips = [c for c in clips if c["key"] == args.only]
        if not clips:
            raise SystemExit(f"没有匹配的场次: {args.only}")

    stages = ["analyze", "build", "wechat"]
    start = stages.index(args.stage)
    active = set(stages[start:]) - set(args.skip)

    t0 = time.time()
    for clip in clips:
        key = clip["key"]
        print(f"\n{'=' * 60}\n  {clip['name']}  ({key})\n{'=' * 60}")
        sel_path = os.path.join(r.analysis_dir, f"{key}.v5.json")

        if "analyze" in active:
            sel_path = r.analyze(clip)
        else:
            # 复用已有选段
            cached = os.path.join(root, "config", "select", f"{key}.v5.json")
            if os.path.exists(cached):
                shutil.copy2(cached, sel_path)
                print(f"\n>>> [{key}] 复用已有选段 {cached}")

        if "build" in active:
            master = r.build(clip, sel_path)
            if "wechat" in active:
                r.wechat(clip, master)
        elif "wechat" in active:
            master = os.path.join(resolve_path(cfg["output"]["master_dir"], root),
                                  f"{clip['name']}.mp4")
            if os.path.exists(master):
                r.wechat(clip, master)
            else:
                print(f"    跳过微信版(母版不存在): {master}")

    print(f"\n{'=' * 60}\n全部完成,总耗时 {time.time() - t0:.0f}s\n{'=' * 60}")


if __name__ == "__main__":
    main()
