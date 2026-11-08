"""
标注工具 —— 为"回合 ground truth"生成人工核验联系表(contact sheet)。

为什么要它:
  评估集必须是**真**标注。这套工具把"看视频找回合"变成
  "看联系表逐个判定",判定过程可复现、可复查,不靠拍脑袋。

产出(每个窗口一个子目录 w<起>-<止>/):
  w<起>-<止>/frames/f%06d.jpg  窗口内统一抽帧(一次 ffmpeg 调用,按 frame_rate)
  w<起>-<止>/cand_NN.png       候选回合联系表:每行一个候选,行内均匀抽 frames_per_rally 帧
  w<起>-<止>/gap_NN.png        空档扫描:候选之间没被覆盖的区间(用来抓漏检)
  w<起>-<止>/candidates.json   候选清单(标注时对着 #编号 写结论)

标注规范见 docs/标注规范.md。

用法:
    python tools/annotate_rounds.py --video <src.mp4> --hits <hits.json> \
        --out-dir work/sheets/ann [--window 1200:2400] [--profile club-2026-11-07]

    # 一次跑完配置里声明的全部窗口(窗口列表见 config 的 annotation.window)
    python tools/annotate_rounds.py --video <src.mp4> --hits <hits.json> \
        --out-dir work/sheets/ann --profile club-2026-11-07

注:窗口内的一次抽帧是整条流程里唯一一次全量解码,因此先抽帧再拼图,
    不要每帧单独调用 ffmpeg(逐帧 -ss 抽 800 帧要十几分钟)。
"""
import argparse, json, os, subprocess, sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from reelconf import add_config_args, config_from_args


def extract_frames(video, start, dur, fps, outdir, w, h):
    """窗口内统一抽帧到 outdir/f%06d.jpg(1-based)。返回帧数。"""
    os.makedirs(outdir, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{start:.3f}", "-i", video, "-t", f"{dur:.3f}",
           "-vf", f"fps={fps},scale={w}:{h}", "-q:v", "3",
           "-f", "image2", os.path.join(outdir, "f%06d.jpg")]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("FFMPEG ERROR:", r.stderr[:400])
        return 0
    return len([f for f in os.listdir(outdir) if f.endswith(".jpg")])


def load_font(path, size):
    if path and os.path.exists(path):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def frame_path(framedir, idx):
    return os.path.join(framedir, f"f{idx:06d}.jpg")


def stamp(im, text, font, bar_h):
    """在帧底部画一条黑边 + 白字(时间戳)。"""
    d = ImageDraw.Draw(im)
    w = im.size[0]
    d.rectangle([0, im.size[1] - bar_h, w, im.size[1]], fill=(0, 0, 0))
    d.text((3, im.size[1] - bar_h + 2), text, fill=(255, 255, 0), font=font)
    return im


def build_sheet(rows, out, frame_w, frame_h, bar_h, font, cols_of_row):
    """把若干行拼成一张 PNG。每行 = 一个表头条 + len(cols_of_row) 个帧。"""
    n = len(rows)
    if not n:
        return None
    head_h = bar_h + 4
    sheet = Image.new("RGB", (frame_w * cols_of_row, n * (frame_h + head_h)), (20, 20, 20))
    for r, (header, imgs) in enumerate(rows):
        y = r * (frame_h + head_h)
        d = ImageDraw.Draw(sheet)
        d.text((4, y + 2), header, fill=(120, 255, 120), font=font)
        for c, im in enumerate(imgs):
            sheet.paste(im, (c * frame_w, y + head_h))
    sheet.save(out)
    return out


def extract_audio(path, t0, dur, sr):
    """解一段音频成单声道 float32。"""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t0:.3f}",
           "-i", path, "-t", f"{dur:.3f}", "-vn", "-ac", "1", "-ar", str(sr),
           "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.float32)


def envelope(x, sr, win_ms):
    """短时 RMS(dB)包络,窗长 win_ms 毫秒。"""
    win = max(1, int(sr * win_ms / 1000.0))
    n = len(x) // win
    if n < 1:
        return np.array([])
    e = np.sqrt((x[:n * win].reshape(n, win) ** 2).mean(axis=1))
    return 20 * np.log10(np.maximum(e, 1e-9))


def envelope_peaks(env, sr, win_ms, base_win_s=1.0, margin_db=6.0):
    """独立于 detect_hits 的简易峰值提取:局部极大 且 高于 1s 滑动中位数 margin_db。

    用途:给长回合做**第二套**拍数估计 —— 两套独立方法对得上,拍数才算站得住。
    """
    if env.size == 0:
        return []
    step = max(1, int(base_win_s * 1000.0 / win_ms))
    k = max(1, int(150.0 / win_ms))          # 局部极大比较半径 ±150ms
    med = np.array([np.median(env[max(0, i - step):i + step + 1])
                    for i in range(len(env))])
    out = []
    for i in range(k, len(env) - k):
        w = env[i - k:i + k + 1]
        if env[i] == w.max() and env[i] - med[i] >= margin_db:
            if not out or (i - out[-1]) * win_ms >= 100:
                out.append(i)
    return out


def build_env_sheet(rallies, env, env_t0, sr, out, width, height, font, hits_of,
                    win_ms=10.0):
    """音频包络条:每个回合一行,画出 dB 包络 + 独立峰值(蓝竖线)+ 检出击球(红竖线)。

    用途:人眼在联系表上点不出"第 37 拍",但能在包络上数出明显的瞬态个数 ——
    这是给长回合做**独立拍数核对**的依据(不能只信检测器自己的计数)。
    另外也能一眼看出"这一段是不是连续的一个长回合"(有没有明显的空档)。
    """
    if env.size == 0 or not rallies:
        return None
    bins_per_sec = 1000.0 / win_ms
    row_h = height
    head_h = 18
    sheet = Image.new("RGB", (width, len(rallies) * (row_h + head_h)), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    peaks = envelope_peaks(env, sr, win_ms)
    lo_db = float(np.percentile(env, 5))
    hi_db = float(np.percentile(env, 99.5))
    rng = max(hi_db - lo_db, 1.0)
    for r, rl in enumerate(rallies):
        y0 = r * (row_h + head_h) + head_h
        a, b = rl["start"], rl["end"]
        d.text((4, y0 - head_h + 2),
               f"#{rl['id']:03d}  {a:8.1f}-{b:8.1f}s  shots(det)={rl['shots']}",
               fill=(120, 255, 120), font=font)
        span = max(b - a + 2.0, 1e-6)
        # 秒格线
        t = int(a) - 1
        while t <= int(b) + 1:
            x = int((t - (a - 1.0)) / span * width)
            if 0 <= x < width:
                d.line([(x, y0), (x, y0 + row_h)], fill=(60, 60, 80))
                d.text((x + 2, y0 + 1), str(t), fill=(90, 90, 110), font=font)
            t += 1
        # 包络
        i0 = max(0, int((a - 1.0 - env_t0) * bins_per_sec))
        i1 = min(len(env), int((b + 1.0 - env_t0) * bins_per_sec))
        if i1 > i0:
            seg = env[i0:i1]
            prev = None
            for k, v in enumerate(seg):
                x = int(k / max(len(seg) - 1, 1) * width)
                y = y0 + row_h - int((v - lo_db) / rng * (row_h - 2))
                y = max(y0, min(y0 + row_h - 1, y))
                if prev is not None:
                    d.line([prev, (x, y)], fill=(80, 110, 170))
                prev = (x, y)
        # 独立峰值(蓝) —— 用来数拍子
        for pi in peaks:
            tt = env_t0 + pi / bins_per_sec
            if not (a - 1.0 <= tt <= b + 1.0):
                continue
            x = int((tt - (a - 1.0)) / span * width)
            if 0 <= x < width:
                d.line([(x, y0), (x, y0 + row_h // 2)], fill=(110, 200, 255))
        # 检出的击球(红)
        for h in hits_of(rl):
            x = int((h - (a - 1.0)) / span * width)
            if 0 <= x < width:
                d.line([(x, y0), (x, y0 + row_h)], fill=(255, 70, 70))
    sheet.save(out)
    return out


def normalize_windows(value):
    """annotation.window 支持两种形状(一个旋钮,两种写法):
         [a, b]            -> 单窗口
         [[a, b], [c, d]]  -> 多窗口
    """
    if not value:
        return []
    if all(isinstance(x, (int, float)) for x in value):
        return [(float(value[0]), float(value[1]))]
    return [(float(w[0]), float(w[1])) for w in value]


def process_window(win_start, win_end, outdir, video, rallies_all, an):
    """处理一个标注窗口:抽帧 → 候选联系表 → 空档扫描。返回候选数。"""
    w, h = int(an["frame_width"]), int(an["frame_height"])
    fps = float(an["frame_rate"])
    nfr = int(an["frames_per_rally"])
    ctx = float(an["context_seconds"])
    rows_per_sheet = int(an["rows_per_sheet"])
    bar_h = max(14, int(h * 0.11))
    font = load_font(an["font"], int(an["font_size"]))
    small = load_font(an["font"], max(10, int(an["font_size"]) - 3))

    framedir = os.path.join(outdir, "frames")
    os.makedirs(framedir, exist_ok=True)

    rallies = [r for r in rallies_all
               if r["end"] >= win_start and r["start"] <= win_end]
    rallies.sort(key=lambda r: r["start"])

    print(f"\nwindow {win_start:.1f}-{win_end:.1f}s  候选 {len(rallies)}")
    print("抽帧中(一次 ffmpeg 调用,全窗口)...")
    n = extract_frames(video, win_start, win_end - win_start, fps, framedir, w, h)
    print(f"  frames={n} @ {fps}fps")

    def idx_at(t):
        """时间 -> 帧号(1-based);超出窗口则夹到边界。"""
        i = int(round((t - win_start) * fps)) + 1
        return max(1, min(n, i))

    # ---- 候选联系表 ----
    rows = []
    cands = []
    for r in rallies:
        lo, hi = r["start"] - ctx, r["end"] + ctx
        picks = [lo + (hi - lo) * k / max(nfr - 1, 1) for k in range(nfr)]
        imgs = []
        for t in picks:
            p = frame_path(framedir, idx_at(t))
            if not os.path.exists(p):
                continue
            im = Image.open(p).convert("RGB")
            stamp(im, f"t={t:8.1f}", small, bar_h)
            imgs.append(im)
        if len(imgs) < nfr:
            continue
        cid = len(cands)
        cands.append({"id": cid, "start": r["start"], "end": r["end"], "shots": r["shots"],
                      "hits": r.get("hits", [])})
        header = (f"#{cid:03d}  {r['start']:8.1f}-{r['end']:8.1f}s  "
                  f"shots={r['shots']:3d}  dur={r['duration']:.1f}s")
        rows.append((header, imgs))

    for k in range(0, len(rows), rows_per_sheet):
        chunk = rows[k:k + rows_per_sheet]
        idx = k // rows_per_sheet
        out = os.path.join(outdir, f"cand_{idx:02d}.png")
        build_sheet(chunk, out, w, h, bar_h, font, nfr)
        print(f"  SHEET {out}  rows {len(chunk)}")

    # ---- 音频包络条:长回合逐拍核对 ----
    # 只给长回合出。目的是拿到**独立于检测器**的拍数证据。
    long_list = [c for c in cands if c["shots"] >= int(an["env_min_shots"])]
    if long_list:
        env_sheets(long_list, video, an, outdir, rows_per_sheet=rows_per_sheet)

    # ---- 空档扫描:候选之间没被覆盖的区间 ----
    cov = [(r["start"], r["end"]) for r in rallies]
    gaps = []
    cur = win_start
    for a, b in cov:
        if a - cur >= float(an["gap_scan_min_seconds"]):
            gaps.append((cur, a))
        cur = max(cur, b)
    if win_end - cur >= float(an["gap_scan_min_seconds"]):
        gaps.append((cur, win_end))

    step = float(an["gap_scan_step_seconds"])
    cols = 8
    gap_rows = []
    for gi, (a, b) in enumerate(gaps):
        times = []
        t = a
        while t < b:
            times.append(t)
            t += step
        for k in range(0, len(times), cols):
            chunk = times[k:k + cols]
            imgs = []
            for t in chunk:
                p = frame_path(framedir, idx_at(t))
                if not os.path.exists(p):
                    continue
                im = Image.open(p).convert("RGB")
                stamp(im, f"t={t:7.1f}", small, bar_h)
                imgs.append(im)
            if imgs:
                gap_rows.append((f"gap{gi} {a:.1f}-{b:.1f}s", imgs))
    for k in range(0, len(gap_rows), rows_per_sheet):
        chunk = gap_rows[k:k + rows_per_sheet]
        out = os.path.join(outdir, f"gap_{k // rows_per_sheet:02d}.png")
        build_sheet(chunk, out, w, h, bar_h, font, cols)
        print(f"  SHEET {out}  rows {len(chunk)}")
    print(f"gaps>= {an['gap_scan_min_seconds']}s: {len(gaps)}  (gap rows {len(gap_rows)})")

    with open(os.path.join(outdir, "candidates.json"), "w", encoding="utf-8") as f:
        json.dump({"window": [win_start, win_end], "frame_rate": fps,
                   "candidates": cands}, f, ensure_ascii=False, indent=2)
    print("candidates ->", os.path.join(outdir, "candidates.json"))
    return len(cands)


def env_sheets(rallies, video, an, outdir, prefix="env", rows_per_sheet=None):
    """给指定回合出音频包络条(只解音频,不抽帧 —— 长回合可能散布在全片)。"""
    if not rallies:
        return []
    sr = int(an["env_sr"])
    win_ms = float(an["env_win_ms"])
    rows_per_sheet = rows_per_sheet or int(an["rows_per_sheet"])
    font = load_font(an["font"], max(10, int(an["font_size"]) - 3))
    lo_a = min(r["start"] for r in rallies) - 2.0
    hi_a = max(r["end"] for r in rallies) + 2.0
    ax = extract_audio(video, lo_a, hi_a - lo_a, sr)
    env = envelope(ax, sr, win_ms)
    outs = []
    for k in range(0, len(rallies), rows_per_sheet):
        chunk = rallies[k:k + rows_per_sheet]
        out = os.path.join(outdir, f"{prefix}_{k // rows_per_sheet:02d}.png")
        build_env_sheet(chunk, env, lo_a, sr, out, int(an["env_width"]),
                        int(an["env_height"]), font,
                        lambda rl: rl.get("hits", []), win_ms=win_ms)
        outs.append(out)
        print(f"  ENVSHEET {out}  rows {len(chunk)}")
    return outs


def main():
    ap = argparse.ArgumentParser(description="生成标注用联系表")
    ap.add_argument("--video", required=True, help="源视频")
    ap.add_argument("--hits", help="detect_hits.py 的输出 JSON")
    ap.add_argument("--out-dir", required=True, help="产物目录")
    ap.add_argument("--window", help="标注窗口 start:end(秒),覆盖配置里的窗口列表")
    ap.add_argument("--rallies", help="只给这些回合出音频包络条(JSON 列表:"
                                     "[{id,start,end,shots,hits}]);不给就出联系表")
    add_config_args(ap)
    args = ap.parse_args()

    cfg = config_from_args(args)
    an = cfg.section("annotation")

    outroot = os.path.abspath(args.out_dir)
    os.makedirs(outroot, exist_ok=True)

    # 只出包络条:用于长回合的独立拍数核对
    if args.rallies:
        rallies = json.load(open(args.rallies, encoding="utf-8"))
        if isinstance(rallies, dict):
            rallies = rallies.get("rallies", [])
        print(f"包络条模式:{len(rallies)} 个回合")
        env_sheets(rallies, args.video, an, outroot)
        return

    if not args.hits:
        raise SystemExit("需要 --hits(或用 --rallies 只出包络条)")

    if args.window:
        ws, _, we = args.window.partition(":")
        windows = [(float(ws), float(we))]
    else:
        windows = normalize_windows(an["window"])
    if not windows:
        raise SystemExit("没有标注窗口:请在配置里给 annotation.window,或用 --window 指定")

    d = json.load(open(args.hits, encoding="utf-8"))
    rallies_all = d["rallies"]

    for win_start, win_end in windows:
        # 每个窗口一个子目录,窗口之间互不覆盖
        sub = f"w{win_start:.1f}-{win_end:.1f}"
        outdir = os.path.join(outroot, sub)
        os.makedirs(outdir, exist_ok=True)
        process_window(win_start, win_end, outdir, args.video, rallies_all, an)


if __name__ == "__main__":
    main()
