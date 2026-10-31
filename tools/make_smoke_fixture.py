"""
生成冒烟测试夹具:一段带击球脉冲的合成视频 + 一个小型 profile。

用来在改动管线后,十秒内验证"整条链路还通不通",
不用等真实素材跑完 115 分钟分析。

用法:
    python tools/make_smoke_fixture.py
    python pipeline.py --profile work/smoke-profile.yaml
"""
import os, sys, wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(ROOT, "work", "smoke")


def build_video():
    """合成一段 40 秒视频:背景噪声 + 间隔不等的击球脉冲。"""
    os.makedirs(REF, exist_ok=True)
    sr = 48000
    dur = 40.0
    n = int(dur * sr)
    rng = np.random.default_rng(3)
    x = rng.normal(0, 0.008, n).astype(np.float32)

    t = 0.5
    while t < dur - 1.0:
        i = int(t * sr)
        w = int(0.004 * sr)
        amp = 0.25 + 0.35 * ((i * 7919) % 100) / 100.0
        x[i:i + w] += (amp * np.hanning(w)).astype(np.float32)
        # 一个回合:连续 5-9 拍,拍间隔 0.5s
        shots = 5 + (i * 31) % 5
        t += 0.5
        for _ in range(shots - 1):
            j = int(t * sr)
            w2 = int(0.004 * sr)
            x[j:j + w2] += (amp * 0.9 * np.hanning(w2)).astype(np.float32)
            t += 0.5
        t += 2.5   # 回合之间的安静段

    pcm = np.clip(x, -1, 1)
    wav = os.path.join(REF, "tone.wav")
    w = wave.open(wav, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes((pcm * 32767).astype("<i2").tobytes())
    w.close()

    mp4 = os.path.join(REF, "smoke.mp4")
    rc = os.system(f'ffmpeg -hide_banner -v error -y -f lavfi '
                   f'-i "testsrc2=size=640x360:rate=30:duration={dur}" '
                   f'-i "{wav}" -c:v libx264 -preset ultrafast -pix_fmt yuv420p '
                   f'-c:a aac -shortest "{mp4}"')
    if rc != 0:
        raise SystemExit("[FAIL] 生成测试视频失败")
    return mp4


def build_profile(mp4):
    """生成一个指向测试视频的小 profile(低分辨率低码率,秒级出片)。"""
    profile = os.path.join(ROOT, "work", "smoke-profile.yaml")
    src_dir = os.path.dirname(os.path.abspath(mp4)).replace("\\", "/")
    with open(profile, "w", encoding="utf-8") as f:
        f.write(f'''# 冒烟测试 profile —— 由 tools/make_smoke_fixture.py 生成
source_dir: "{src_dir}"
clips:
  - key: smoke
    name: "冒烟测试片"
    file: "{os.path.basename(mp4)}"

select:
  target_seconds: 8
  speed: 1.0

encode:
  fps: 24
  scale: "320:180"
  gop: 48
  cq: 28
  maxrate: "2000k"
  bufsize: "4000k"

build:
  min_gap_between: 1.2
  cut_padding: 0.12

mix:
  audio_bitrate: "96k"

music:
  bed_seconds: 20

wechat:
  enabled: true
  cq: 30
  maxrate: "1200k"
  bufsize: "2400k"

output:
  master_dir: "work/smoke_out/master"
  wechat_dir: "work/smoke_out/wechat"
  work_dir: "work/smoke_out/work"
''')
    return profile


def main():
    mp4 = build_video()
    profile = build_profile(mp4)
    print(f"视频: {mp4}  ({os.path.getsize(mp4)/1024/1024:.1f} MB)")
    print(f"配置: {profile}")
    print("\n跑一遍完整流程:")
    print(f'    python pipeline.py --profile "{profile}"')


if __name__ == "__main__":
    main()
