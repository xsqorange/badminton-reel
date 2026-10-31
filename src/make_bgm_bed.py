"""
把多段 BGM 拼成一条长配乐(段间交叉淡化),再循环到目标时长。
分两步,逻辑简单可靠:
  1) 顺序拼接(acrossfade)
  2) 循环扩展(aloop 拼接,或直接 repeat)
"""
import subprocess, sys, os

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout, r.stderr

def probe(p):
    rc, out, _ = run(["ffprobe","-v","error","-show_entries","format=duration",
                      "-of","default=nw=1:nk=1",p])
    return float(out.strip())

def concat_parts(parts, out, xf=1.2):
    durs = [probe(p) for p in parts]
    inputs = []
    for p in parts: inputs += ["-i", p]
    chains = []
    for i, d in enumerate(durs):
        chains.append(f"[{i}:a]atrim=0:{d:.3f},asetpts=N/SR/TB[a{i}]")
    cur = "a0"
    for i in range(1, len(parts)):
        chains.append(f"[{cur}][a{i}]acrossfade=d={xf}:c1=tri:c2=tri[x{i}]")
        cur = f"x{i}"
    fc = ";".join(chains) + f";[{cur}]alimiter=limit=0.95[out]"
    cmd = ["ffmpeg","-hide_banner","-loglevel","error","-y"] + inputs + \
          ["-filter_complex", fc, "-map","[out]","-c:a","libmp3lame","-b:a","256k", out]
    rc, _, err = run(cmd)
    return rc == 0, err, sum(durs) - xf*(len(parts)-1)

def loop_to(src, target, out, xf=1.5):
    """循环 src 到 target,每次接缝处交叉淡化"""
    sd = probe(src)
    n = int(target / max(sd - xf, 0.5)) + 1
    inputs = []
    for _ in range(n): inputs += ["-i", src]
    chains = []
    for i in range(n):
        chains.append(f"[{i}:a]atrim=0:{sd:.3f},asetpts=N/SR/TB[a{i}]")
    cur = "a0"
    for i in range(1, n):
        chains.append(f"[{cur}][a{i}]acrossfade=d={xf}:c1=tri:c2=tri[x{i}]")
        cur = f"x{i}"
    fc = ";".join(chains) + \
         f";[{cur}]apad,atrim=0:{target:.3f},asetpts=N/SR/TB," \
         f"afade=t=in:st=0:d=1.5,afade=t=out:st={max(0,target-2.5):.2f}:d=2.5,alimiter=limit=0.95[out]"
    cmd = ["ffmpeg","-hide_banner","-loglevel","error","-y"] + inputs + \
          ["-filter_complex", fc, "-map","[out]","-c:a","libmp3lame","-b:a","256k", out]
    rc, _, err = run(cmd)
    return rc == 0, err

def main():
    out = sys.argv[1]
    target = float(sys.argv[2])
    parts = sys.argv[3:]
    tmp = out + ".cat.mp3"
    ok, err, base = concat_parts(parts, tmp)
    if not ok:
        print("CONCAT FAIL:", err[:600]); return
    print(f"  base bed: {base:.1f}s")
    ok, err = loop_to(tmp, target, out)
    if not ok:
        print("LOOP FAIL:", err[:600]); return
    print(f"BGM BED {out}  {probe(out):.1f}s")

if __name__ == "__main__":
    main()
