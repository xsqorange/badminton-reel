"""
羽毛球素材高光点分析
输出每个采样点的:时间戳 / 运动能量(帧差 YAVG) / 音频 RMS(dB)
用于后续识别回合(rally)与得分高潮。

用法: python probe_highlights.py <video.mp4> <out.csv> [fps]
"""
import subprocess, sys, re, os, math

FPS_SAMPLE = 5.0  # 采样帧率(降采样后计算运动能量)

def ffprobe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0

def extract_motion(path, csv_path, duration):
    """逐帧差分求运动能量。tblend=difference + signalstats.YAVG"""
    vf = (f"fps={FPS_SAMPLE},scale=320:180,tblend=all_mode=difference,"
          "signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=" + csv_path.replace("\\", "/"))
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-i", path, "-an", "-vf", vf, "-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        print("MOTION_FFMPEG_ERROR", p.stderr[:500], flush=True)
        return False
    return True

def extract_audio(path, csv_path, duration):
    """音频 RMS,每 0.1s 重置统计"""
    af = ("astats=metadata=1:reset=10,ametadata=print:"
          "key=lavfi.astats.Overall.RMS_level:file=" + csv_path.replace("\\", "/"))
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-i", path, "-vn", "-af", af, "-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
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
    pat_v = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(-?[0-9.]+|-inf)")
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
                val.append(v)
                cur_t = None
    return pts, val

def main():
    path, out = sys.argv[1], sys.argv[2]
    dur = ffprobe_duration(path)
    print(f"FILE={os.path.basename(path)} DURATION={dur:.1f}s", flush=True)
    mcsv = out + ".motion.csv"
    acsv = out + ".audio.csv"
    ok_m = extract_motion(path, mcsv, dur)
    print(f"motion_ok={ok_m}", flush=True)
    ok_a = extract_audio(path, acsv, dur)
    print(f"audio_ok={ok_a}", flush=True)
    print("DONE", flush=True)

if __name__ == "__main__":
    main()
