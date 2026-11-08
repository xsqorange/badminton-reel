"""
把单个回合从原始素材里单独导出成一条成片。

用途:文档里报的那些"最长 44 拍 / 28.6 秒"这类极限回合,
直接剪成独立片段,方便单独看、单独听击球声。

边界来源(与主流程完全一致):
  · 若该回合出现在最终选段里,直接复用 select_v5 算好的边界(含发球前置与得分后延)
  · 若不在,则把该回合单独喂给 select_v5 跑一遍,取它算出的边界
  · 兜底:用击球瞬态的原始首末拍时刻

用法:
    python tools/export_rally.py --clip m2 --longest
    python tools/export_rally.py --clip m2 --shots 44
    python tools/export_rally.py --clip m2 --longest --speed 1.1 --with-bgm
"""
import argparse, json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from reelconf import add_config_args, config_from_args, resolve_path

import build_v4  # 复用主流程的编码参数拼装,保证规格一致


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout, r.stderr


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_hits_and_sel(cfg, key):
    """找击球数据与选段结果:优先工作目录(最新),回落到仓库归档的基线。"""
    work = resolve_path(cfg.at("output.work_dir"), ROOT)
    analysis = os.path.join(work, cfg.at("output.analysis_subdir"))
    baseline = os.path.join(ROOT, "config", "select")
    hits = sel = None
    for h, s in ((os.path.join(analysis, f"{key}.hits.json"), os.path.join(analysis, f"{key}.v5.json")),
                 (os.path.join(baseline, f"{key}.hits.json"), os.path.join(baseline, f"{key}.v5.json"))):
        if hits is None and os.path.exists(h):
            hits = h
            sel = s if os.path.exists(s) else None
            break
    return hits, sel


def pick_rally(rallies, args):
    """按条件挑回合。"""
    if args.around is not None:
        t = args.around
        cands = [r for r in rallies if r["start"] - 5 <= t <= r["end"] + 5]
        if not cands:
            raise SystemExit(f"时间点 {t}s 附近没有回合")
        return max(cands, key=lambda r: r["shots"])
    if args.shots:
        cands = [r for r in rallies if r["shots"] == args.shots]
        if not cands:
            raise SystemExit(f"没有 {args.shots} 拍的回合")
        return max(cands, key=lambda r: r["duration"])
    return max(rallies, key=lambda r: r["shots"])


def boundaries_from_select(rally, hits_path, motion, audio, cfg, work_dir):
    """把该回合单独喂给 select_v5,拿它算出的边界(含发球前置 / 得分后延)。"""
    iso_hits = os.path.join(work_dir, "isolate.hits.json")
    with open(iso_hits, "w", encoding="utf-8") as f:
        json.dump({"hits": rally["hits"], "rallies": [rally]}, f, ensure_ascii=False)
    out = os.path.join(work_dir, "isolate.v5.json")
    cmd = [sys.executable, os.path.join(ROOT, "src", "select_v5.py"),
           iso_hits, motion, audio, out,
           "--config", os.path.join(ROOT, "config", "base.yaml")]
    rc, _, err = run(cmd)
    if rc != 0:
        return None
    d = load_json(out)
    segs = d.get("segments") or []
    return (segs[0]["start"], segs[0]["end"]) if segs else None


def main():
    ap = argparse.ArgumentParser(description="导出单个回合为独立成片")
    ap.add_argument("--clip", required=True, help="场次 key(如 m2)")
    ap.add_argument("--longest", action="store_true", help="导出拍数最多的回合(默认)")
    ap.add_argument("--shots", type=int, help="导出指定拍数的回合")
    ap.add_argument("--around", type=float, help="导出指定时间点附近(±5s)的回合")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="变速倍率,默认 1.0 原速(便于听清每一下击球)")
    ap.add_argument("--with-bgm", action="store_true", help="混入配乐(默认保留原声)")
    ap.add_argument("--out", help="输出文件路径(默认 output/master 下自动命名)")
    add_config_args(ap)
    args = ap.parse_args()

    cfg = config_from_args(args)
    enc = cfg.section("encode")
    out_cfg = cfg.section("output")

    clip = next((c for c in cfg["clips"] if c["key"] == args.clip), None)
    if clip is None:
        raise SystemExit(f"没有这个场次: {args.clip}")

    hits_path, sel_path = find_hits_and_sel(cfg, args.clip)
    if not hits_path:
        raise SystemExit(f"找不到 {args.clip} 的击球数据,先跑一次 analyze")
    d = load_json(hits_path)
    rallies = d["rallies"]
    rally = pick_rally(rallies, args)

    print(f"场次 {args.clip}  ({clip['name']})")
    print(f"回合: {rally['shots']} 拍  {rally['start']:.2f}s - {rally['end']:.2f}s "
          f"({rally['duration']:.2f}s)")
    print(f"      击球间隔 均值 {rally['mean_interval']:.3f}s / 标准差 {rally['std_interval']:.3f}s "
          f"/ 连贯性 {rally['regularity']:.3f}")

    # ---- 边界 ----
    start, end = None, None
    if sel_path:
        v5 = load_json(sel_path)
        for s in v5.get("segments", []):
            if s["shots"] == rally["shots"] and abs(s["start"] - rally["start"]) < 3:
                start, end = s["start"], s["end"]
                print(f"边界: 复用最终选段 {start:.2f} - {end:.2f} ({end-start:.2f}s)")
                break
    if start is None:
        work = resolve_path(out_cfg["work_dir"], ROOT)
        analysis = os.path.join(work, out_cfg["analysis_subdir"])
        motion = os.path.join(analysis, f"{args.clip}.motion.csv")
        audio = os.path.join(analysis, f"{args.clip}.audio.csv")
        if os.path.exists(motion) and os.path.exists(audio):
            b = boundaries_from_select(rally, hits_path, motion, audio, cfg, work)
            if b:
                start, end = b
                print(f"边界: 由 select_v5 单独计算 {start:.2f} - {end:.2f} ({end-start:.2f}s)")
    if start is None:
        start, end = rally["start"], rally["end"]
        print(f"边界: 回落到击球瞬态原始区间 {start:.2f} - {end:.2f} ({end-start:.2f}s)")

    # ---- 输出路径 ----
    if args.out:
        out_path = os.path.abspath(args.out)
    else:
        master_dir = resolve_path(out_cfg["master_dir"], ROOT)
        out_path = os.path.join(master_dir, f"{clip['name']}_最长回合_{rally['shots']}拍.mp4")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # ---- 切片段 ----
    src = resolve_path(os.path.join(cfg.at("source_dir"), clip["file"]), ROOT)
    if not os.path.exists(src):
        raise SystemExit(f"素材不存在: {src}")
    fps = enc["fps"]
    sp = args.speed
    pad = cfg.at("build.cut_padding")
    st = max(0.0, start - pad)
    en = end + pad
    vf = (f"fps={fps}:round={enc['fps_round']}:start_time={enc['fps_start_time']},"
          f"scale={enc['scale']}:flags={enc['scale_flags']}")
    if abs(sp - 1.0) > 0.001:
        vf += f",setpts=PTS/{sp:.6f}"
    vargs = build_v4.video_args(enc, fps, build_v4.has_nvenc())
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{st:.3f}", "-i", src,
           "-t", f"{(en - st) / sp:.3f}",
           "-vf", vf, "-vsync", enc["vsync"], "-r", str(fps),
           *vargs, "-c:a", "aac", "-b:a", cfg.at("mix.audio_bitrate"),
           "-ar", cfg.at("mix.audio_sample_rate"),
           "-movflags", enc["movflags"], out_path]
    print(f"\n>>> 切片段  {st:.2f} - {en:.2f}  @{sp}x")
    rc, _, err = run(cmd)
    if rc != 0:
        print("FAIL:", err[:800])
        raise SystemExit("[FAIL] 切片失败")

    # ---- 可选混配乐 ----
    if args.with_bgm:
        bed = resolve_path(cfg.at("music.bed_file"), ROOT)
        tmp = out_path + ".tmp.mp4"
        fc = (f"[1:a]afade=t=in:st=0:d={cfg.at('music.fade_in')},"
              f"afade=t=out:st={max(0, (en - st) / sp - cfg.at('music.fade_out')):.2f}:"
              f"d={cfg.at('music.fade_out')},"
              f"volume={cfg.at('music.volume')},"
              f"alimiter=limit={cfg.at('music.limiter')}[aout]")
        rc, _, err = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                          "-i", out_path, "-i", bed, "-filter_complex", fc,
                          "-map", "0:v", "-map", "[aout]", "-c:v", "copy",
                          "-c:a", cfg.at("mix.audio_codec"),
                          "-b:a", cfg.at("mix.audio_bitrate"),
                          "-ar", cfg.at("mix.audio_sample_rate"),
                          "-shortest", "-movflags", enc["movflags"], tmp])
        if rc != 0:
            print("BGM 混音失败(保留无配乐版本):", err[:300])
        else:
            os.replace(tmp, out_path)

    # ---- 结果 ----
    dur = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", out_path])[1].strip()
    nb = run(["ffprobe", "-v", "error", "-count_frames",
              "-select_streams", "v", "-show_entries", "stream=nb_read_frames",
              "-of", "default=nw=1:nk=1", out_path])[1].strip()
    print(f"\n>>> DONE {out_path}")
    print(f"    时长 {float(dur):.2f}s   帧数 {nb}   体积 {os.path.getsize(out_path)/1024/1024:.1f} MB")
    print(f"    击球密度 {rally['shots'] / (float(dur) / sp):.2f} 拍/秒")


if __name__ == "__main__":
    main()
