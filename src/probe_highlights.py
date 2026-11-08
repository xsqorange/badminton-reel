"""
羽毛球素材高光点分析
输出每个采样点的:时间戳 / 运动能量(帧差 YAVG) / 音频 RMS(dB)
用于后续识别回合(rally)与得分高潮。

用法: python probe_highlights.py <video.mp4> <out_prefix> [--config ...] [--profile ...]
产物: <out_prefix>.motion.csv / <out_prefix>.audio.csv

所有参数来自配置文件(config/base.yaml + profile),本文件不再含魔数。
"""
import argparse, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reelconf import add_config_args, config_from_args


def ffprobe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def _filter_file_arg(csv_path):
    """为 ffmpeg 的 `file=` 参数准备 (工作目录, 文件名)。

    ffmpeg 用 ':' 分隔滤镜选项,Windows 盘符里的冒号会被当成选项分隔符,
    报 `No option name near '/work/...'`。
    解法:把 ffmpeg 的工作目录设成目标目录,`file=` 里只写文件名。
    """
    d = os.path.dirname(os.path.abspath(csv_path))
    return d, os.path.basename(csv_path)


def extract_motion(path, csv_path, fps_sample, scale, roi=None):
    """逐帧差分求运动能量。tblend=difference + signalstats.YAVG

    roi:可选的 ffmpeg crop 滤镜参数(如 "crop=1280:620:0:460"),
         **只统计主球场区域**。不设时是全画面 —— 全画面会把背景 4~5 片场地的
         运动也算进来,配合混合音频就量成了"主球场+背景场"的混合瞬态密度。
    """
    cwd, fname = _filter_file_arg(csv_path)
    vf = (f"fps={float(fps_sample)},{roi}," if roi else f"fps={float(fps_sample)},")
    vf += f"scale={scale},tblend=all_mode=difference,"\
           f"signalstats,metadata=print:key=lavfi.signalstats.YAVG:file={fname}"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-i", os.path.abspath(path), "-an", "-vf", vf, "-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=cwd)
    if p.returncode != 0:
        print("MOTION_FFMPEG_ERROR", p.stderr[:500], flush=True)
        return False
    return True


def extract_audio(path, csv_path, reset):
    """音频 RMS,每 reset 帧重置统计"""
    cwd, fname = _filter_file_arg(csv_path)
    af = ("astats=metadata=1:reset=%d,ametadata=print:"
          "key=lavfi.astats.Overall.RMS_level:file=%s" % (reset, fname))
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-i", os.path.abspath(path), "-vn", "-af", af, "-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=cwd)
    if p.returncode != 0:
        print("AUDIO_FFMPEG_ERROR", p.stderr[:500], flush=True)
        return False
    return True


def parse_motion(csv_path):
    """解析 metadata=print 输出: frame:N pts_time:T ... lavfi.signalstats.YAVG=V"""
    pts, val = [], []
    pat_t = re.compile(r"pts_time:([0-9.]+)")
    pat_v = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")
    cur_t = None
    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = pat_t.search(line)
            if m:
                cur_t = float(m.group(1))
                continue
            m = pat_v.search(line)
            if m and cur_t is not None:
                pts.append(cur_t)
                val.append(float(m.group(1)))
                cur_t = None
    return pts, val


def parse_audio(csv_path):
    pts, val = [], []
    pat_t = re.compile(r"pts_time:([0-9.]+)")
    pat_v = re.compile(r"lavfi\.astats\.Overall.RMS_level=(-?[0-9.]+|-inf)")
    cur_t = None
    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = pat_t.search(line)
            if m:
                cur_t = float(m.group(1))
                continue
            m = pat_v.search(line)
            if m and cur_t is not None:
                raw = m.group(1)
                v = -120.0 if raw in ("-inf", "-inf ") else float(raw)
                pts.append(cur_t)
                val.append(float(v))
                cur_t = None
    return pts, val


def main():
    ap = argparse.ArgumentParser(description="提取运动能量与音频RMS")
    ap.add_argument("video", help="源视频")
    ap.add_argument("out_prefix", help="输出前缀,产物为 <prefix>.motion.csv / <prefix>.audio.csv")
    add_config_args(ap)
    args = ap.parse_args()

    cfg = config_from_args(args)
    a = cfg.section("analysis")

    path, out = args.video, args.out_prefix
    dur = ffprobe_duration(path)
    print(f"FILE={os.path.basename(path)} DURATION={dur:.1f}s", flush=True)
    mcsv = out + ".motion.csv"
    acsv = out + ".audio.csv"
    ok_m = extract_motion(path, mcsv, a["motion_sample_fps"], a["motion_scale"],
                          roi=a.get("motion_roi") or None)
    print(f"motion_ok={ok_m} roi={a.get('motion_roi')}", flush=True)
    ok_a = extract_audio(path, acsv, a["audio_stats_reset"])
    print(f"audio_ok={ok_a}", flush=True)
    if not (ok_m and ok_a):
        raise SystemExit("[FAIL] 特征提取失败")
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
