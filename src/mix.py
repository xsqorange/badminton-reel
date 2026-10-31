"""
给 v4 成片混音(视频流 copy,不重编码)。
用法: python mix.py <video> <bgm.mp3> <out.mp4>
"""
import subprocess, sys, os

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout, r.stderr

def probe(p):
    rc, out, _ = run(["ffprobe","-v","error","-show_entries","format=duration",
                      "-of","default=nw=1:nk=1",p])
    return float(out.strip().strip('"'))

def main():
    video, bgm, out = sys.argv[1], sys.argv[2], sys.argv[3]
    dur = probe(video)
    if os.path.exists(out): os.remove(out)
    fc = (f"[1:a]afade=t=in:st=0:d=2,afade=t=out:st={max(0,dur-3):.2f}:d=3,"
          "volume=0.8,alimiter=limit=0.95[aout]")
    cmd = ["ffmpeg","-hide_banner","-loglevel","error","-y",
           "-i", video, "-i", bgm,
           "-filter_complex", fc, "-map","0:v","-map","[aout]",
           "-c:v","copy","-c:a","aac","-b:a","256k","-ar","48000",
           "-shortest","-movflags","+faststart", out]
    rc, _, err = run(cmd)
    if rc != 0:
        print("FAIL:", err[:600]); return
    print(f"DONE {out}  dur={probe(out):.1f}s  {os.path.getsize(out)/1024/1024:.1f}MB")

if __name__ == "__main__":
    main()
