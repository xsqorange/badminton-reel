#!/usr/bin/env python
"""
羽毛球集锦剪辑 · 单一入口

用法:
    python pipeline.py --config config.yaml
    python pipeline.py --config config.yaml --only m3          # 只处理某一场
    python pipeline.py --config config.yaml --skip analyze     # 跳过耗时分析,复用已有选段
    python pipeline.py --config config.yaml --from build       # 从某步开始

流程:
    analyze  ① 运动能量+音频RMS  ② 击球瞬态+回合聚类  ③ 选段(剔除不确定回合)
    build    ④ 切片+拼接         ⑤ 混音                ⑥ 微信版压制
"""
import argparse, json, os, shutil, subprocess, sys, time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

# ---------- 极简 YAML 读取(避免额外依赖) ----------
def load_yaml(path):
    """只支持本项目用到的 YAML 子集:嵌套 dict、列表、标量、# 注释"""
    import re
    def parse_scalar(s):
        s = s.strip()
        if s.startswith(('"', "'")): return s[1:-1]
        if s.lower() in ("true","false"): return s.lower()=="true"
        if s.lower()=="null": return None
        if re.fullmatch(r"-?\d+", s): return int(s)
        if re.fullmatch(r"-?\d*\.\d+", s): return float(s)
        return s
    root = {}
    stack = [(-1, root)]
    for raw in open(path, encoding="utf-8"):
        line = raw.split("#")[0].rstrip()
        if not line.strip(): continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()
        while stack and indent <= stack[-1][0]: stack.pop()
        parent = stack[-1][1]
        if body.startswith("- "):
            item = body[2:].strip()
            if ":" in item:
                k, v = item.split(":", 1)
                obj = {k.strip(): parse_scalar(v)}
                parent.setdefault("__list__", []).append(obj)
                stack.append((indent, obj))
            else:
                lst = parent.setdefault("__list__", []) if isinstance(parent, dict) else parent
                lst.append(parse_scalar(item))
        else:
            k, v = body.split(":", 1)
            k = k.strip(); v = v.strip()
            if v == "":
                d = {}
                if isinstance(parent, dict): parent[k] = d
                else: parent.append(d)
                stack.append((indent, d))
            else:
                if isinstance(parent, dict): parent[k] = parse_scalar(v)
    # 把 __list__ 还原成正常列表
    def fix(o):
        if isinstance(o, dict):
            if "__list__" in o and len(o)==1: return [fix(x) for x in o["__list__"]]
            return {k: fix(v) for k, v in o.items()}
        if isinstance(o, list): return [fix(x) for x in o]
        return o
    return fix(root)

def parse_list(text):
    return [parse_scalar_line(x) for x in text.split() if x.strip()]

def parse_scalar_line(s):
    s = s.strip()
    try: return int(s)
    except ValueError: return s

def resolve(p, base):
    if not p: return p
    if os.path.isabs(p): return p
    return os.path.normpath(os.path.join(base, p))

# ---------- 步骤封装 ----------
class Runner:
    def __init__(self, cfg, root, work):
        self.cfg = cfg
        self.root = root
        self.work = work
        self.analysis_dir = os.path.join(work, "analysis")
        self.build_dir = os.path.join(work, "build")
        for d in (self.analysis_dir, self.build_dir):
            os.makedirs(d, exist_ok=True)

    def run(self, script, args, desc):
        path = os.path.join(ROOT, "src", script)
        cmd = [sys.executable, path] + [str(a) for a in args]
        print(f"\n>>> {desc}")
        print(f"    $ {' '.join(os.path.basename(cmd[0]) if i==0 else str(a) for i,a in enumerate(cmd))}")
        t0 = time.time()
        # 注入编码参数(config.yaml -> 环境变量 -> 脚本)
        env = dict(os.environ)
        enc = self.cfg["encode"]
        env["REEL_CQ"] = str(enc["cq"])
        env["REEL_MAXRATE"] = str(enc["maxrate"])
        env["REEL_BUFSIZE"] = str(enc["bufsize"])
        r = subprocess.run(cmd, cwd=self.root, env=env)
        if r.returncode != 0:
            raise SystemExit(f"[FAIL] {desc} (exit {r.returncode})")
        print(f"    done in {time.time()-t0:.0f}s")

    # ---------- 各阶段 ----------
    def analyze(self, clip):
        key = clip["key"]
        src = resolve(os.path.join(self.cfg["source_dir"], clip["file"]), self.root)
        if not os.path.exists(src):
            raise SystemExit(f"素材不存在: {src}")
        # ① 运动能量 + 音频 RMS
        # probe_highlights.py 输出 <prefix>.motion.csv / <prefix>.audio.csv
        prefix = os.path.join(self.analysis_dir, key)
        self.run("probe_highlights.py", [src, prefix], f"[{key}] ① 提取运动能量与音频RMS")
        motion = prefix + ".motion.csv"
        audio = prefix + ".audio.csv"

        # ② 击球瞬态 + 回合聚类
        hits = os.path.join(self.analysis_dir, f"{key}.hits.json")
        self.run("detect_hits.py", [src, hits], f"[{key}] ② 击球瞬态检测与回合聚类")

        # ③ 选段
        sel = os.path.join(self.analysis_dir, f"{key}.v5.json")
        self.run("select_v5.py", [hits, motion, audio, sel,
                                  self.cfg["select"]["target_seconds"],
                                  self.cfg["analysis"]["min_shots"],
                                  self.cfg["select"]["speed"]],
                 f"[{key}] ③ 选段(剔除不确定回合)")
        return sel

    def build(self, clip, sel_path):
        key = clip["key"]
        src = resolve(os.path.join(self.cfg["source_dir"], clip["file"]), self.root)
        enc = self.cfg["encode"]
        master_dir = resolve(self.cfg["output"]["master_dir"], self.root)
        os.makedirs(master_dir, exist_ok=True)

        # ④ 切片 + 拼接(两阶段)
        silent = os.path.join(self.build_dir, f"{key}.silent.mp4")
        self.run("build_v4.py", [src, sel_path, silent,
                                 self.cfg["select"]["speed"],
                                 self.cfg["select"]["target_seconds"],
                                 enc["fps"]],
                 f"[{key}] ④ 切片 + concat 拼接")
        # ⑤ 混音
        bed = resolve(self.cfg["music"]["bed_file"], self.root)
        final = os.path.join(master_dir, f"{clip['name']}.mp4")
        self.run("mix.py", [silent, bed, final], f"[{key}] ⑤ 配乐混音")
        return final

    def wechat(self, clip, master):
        if not self.cfg["wechat"]["enabled"]: return None
        w = self.cfg["wechat"]
        wd = resolve(self.cfg["output"]["wechat_dir"], self.root)
        os.makedirs(wd, exist_ok=True)
        out = os.path.join(wd, os.path.basename(master))
        cmd = ["ffmpeg","-hide_banner","-v","error","-y","-i",master,
               "-c:v","h264_nvenc","-preset","p5","-tune","hq","-rc","vbr",
               "-cq",str(w["cq"]),"-b:v","0",
               "-maxrate",w["maxrate"],"-bufsize",w["bufsize"],
               "-pix_fmt","yuv420p","-g","120","-c:a","copy",
               "-movflags","+faststart", out]
        print(f"\n>>> [{clip['key']}] ⑥ 微信版压制")
        r = subprocess.run(cmd)
        if r.returncode != 0: raise SystemExit("[FAIL] 微信版压制")
        print(f"    done -> {os.path.getsize(out)/1024/1024:.1f} MB")
        return out

# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser(description="羽毛球集锦剪辑管线")
    ap.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    ap.add_argument("--only", help="只处理某个场次 key")
    ap.add_argument("--skip", action="append", default=[],
                    help="跳过阶段:analyze / build / wechat")
    ap.add_argument("--from", dest="stage", default="analyze",
                    choices=["analyze","build","wechat"])
    args = ap.parse_args()

    cfg = load_yaml(args.config)
    root = ROOT
    work = resolve(cfg["output"]["work_dir"], root)
    os.makedirs(work, exist_ok=True)
    r = Runner(cfg, root, work)

    clips = cfg["clips"]
    if args.only:
        clips = [c for c in clips if c["key"] == args.only]
        if not clips: raise SystemExit(f"没有匹配的场次: {args.only}")

    stages = ["analyze", "build", "wechat"]
    start = stages.index(args.stage)
    active = set(stages[start:]) - set(args.skip)

    t0 = time.time()
    for clip in clips:
        key = clip["key"]
        print(f"\n{'='*60}\n  {clip['name']}  ({key})\n{'='*60}")
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
            master = os.path.join(resolve(cfg["output"]["master_dir"], root),
                                  f"{clip['name']}.mp4")
            if os.path.exists(master):
                r.wechat(clip, master)
            else:
                print(f"    跳过微信版(母版不存在): {master}")

    print(f"\n{'='*60}\n全部完成,总耗时 {time.time()-t0:.0f}s\n{'='*60}")

if __name__ == "__main__":
    main()
