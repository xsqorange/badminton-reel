"""
编码参数对照实验:同一段素材,不同参数组合,测体积与画质。

目标:找到比 "-cq 17 -b:v 0" 体积合理、画质不明显下降的参数。
关键约束:
  - 必须保持 1080p60(源是 60fps,掉帧会伤观感)
  - 需要 maxrate/bufsize 兜底,防止 VBR 爆码率
"""
import subprocess, os, sys, json, time

SRC = r"D:\羽毛球视频\俱乐部日常活动\10月31日\20261031_龙泽_3号场_7点.mp4"
OUT = r"D:\work\minimax-bot\analysis\enc_test"
os.makedirs(OUT, exist_ok=True)

# 取一段 20 秒的高运动内容(羽球对抗)
CLIP = ["-ss", "935.0", "-t", "20"]

TESTS = [
    ("A_current_cq17_bv0",  ["-c:v","h264_nvenc","-preset","p5","-tune","hq","-rc","vbr",
                             "-cq","17","-b:v","0"]),
    ("B_cq23_bv0",          ["-c:v","h264_nvenc","-preset","p5","-tune","hq","-rc","vbr",
                             "-cq","23","-b:v","0"]),
    ("C_cq20_maxrate12M",   ["-c:v","h264_nvenc","-preset","p5","-tune","hq","-rc","vbr",
                             "-cq","20","-b:v","0","-maxrate","12000k","-bufsize","24000k"]),
    ("D_cq22_maxrate16M",   ["-c:v","h264_nvenc","-preset","p5","-tune","hq","-rc","vbr",
                             "-cq","22","-b:v","0","-maxrate","16000k","-bufsize","32000k"]),
    ("E_cq24_maxrate12M",   ["-c:v","h264_nvenc","-preset","p5","-tune","hq","-rc","vbr",
                             "-cq","24","-b:v","0","-maxrate","12000k","-bufsize","24000k"]),
    ("F_cq26_maxrate10M",   ["-c:v","h264_nvenc","-preset","p5","-tune","hq","-rc","vbr",
                             "-cq","26","-b:v","0","-maxrate","10000k","-bufsize","20000k"]),
    ("G_cq28_maxrate8M",    ["-c:v","h264_nvenc","-preset","p5","-tune","hq","-rc","vbr",
                             "-cq","28","-b:v","0","-maxrate","8000k","-bufsize","16000k"]),
    ("H_crf23_x264",        ["-c:v","libx264","-preset","medium","-crf","23"]),
]

def probe(path):
    b = subprocess.run(["ffprobe","-v","error","-select_streams","v",
                        "-show_entries","stream=bit_rate","-of","default=nw=1:nk=1",path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    s = os.path.getsize(path)
    dur = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
                          "-of","default=nw=1:nk=1",path], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout.strip()
    try: br = float(b)
    except: br = 0.0
    return br, s, float(dur)

def ssim(a, b):
    r = subprocess.run(["ffmpeg","-hide_banner","-v","error","-i",a,"-i",b,
                        "-lavfi","ssim","-f","null","-"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    import re
    m = re.search(r"All:([0-9.]+)", r.stderr)
    return float(m.group(1)) if m else 0.0

ref = os.path.join(OUT, "ref_lossless.mkv")
if not os.path.exists(ref):
    subprocess.run(["ffmpeg","-hide_banner","-v","error","-y", *CLIP, "-i", SRC,
                    "-c:v","libx264","-preset","veryslow","-crf","12","-pix_fmt","yuv420p", ref],
                   check=True)
ref_br, ref_sz, ref_dur = probe(ref)
print(f"reference(近无损 CRF12): {ref_br/1e6:.2f} Mbps  {ref_sz/1024/1024:.2f} MB  {ref_dur:.1f}s\n")

results = []
for name, vargs in TESTS:
    p = os.path.join(OUT, f"{name}.mp4")
    t0 = time.time()
    cmd = ["ffmpeg","-hide_banner","-v","error","-y", *CLIP, "-i", SRC,
           "-an", "-r","60", "-vsync","cfr", *vargs, "-pix_fmt","yuv420p","-g","120", p]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"{name:24s} FAIL {r.stderr[:160]}"); continue
    br, sz, dur = probe(p)
    s = ssim(ref, p)
    rel = sz/ref_sz
    results.append((name, br, sz, rel, s, time.time()-t0))
    print(f"{name:24s} {br/1e6:6.2f} Mbps  {sz/1024/1024:7.2f} MB  "
          f"体积x{rel:.3f}  SSIM {s:.5f}  ({time.time()-t0:.0f}s)")

print("\n=== 汇总(目标:6分钟成片 <600MB,SSIM>0.98) ===")
print(f"{'方案':24s} {'Mbps':>8s} {'6分钟估MB':>10s} {'SSIM':>9s}")
for name, br, sz, rel, s, el in results:
    est = br * 340 / 8 / 1024 / 1024
    print(f"{name:24s} {br/1e6:8.2f} {est:10.0f} {s:9.5f}")
