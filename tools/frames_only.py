"""
逐候选单帧小图(已验证的 pool_sheet 方式),输出到独立目录,
供视觉模型按文件名顺序逐张核验,判定近景遮挡。
"""
import json, subprocess, sys, os

def grab(video, t, out, w=480, h=270):
    subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",
                    "-ss",f"{t:.3f}","-i",video,"-frames:v","1",
                    "-vf",f"scale={w}:{h}",out], check=False)

def main():
    video, hj, outdir = sys.argv[1], sys.argv[2], sys.argv[3]
    topn = int(sys.argv[4]) if len(sys.argv) > 4 else 45
    d = json.load(open(hj, encoding="utf-8"))
    cands = sorted(d.get("all_candidates") or d["segments"], key=lambda c: -c["score"])[:topn]
    os.makedirs(outdir, exist_ok=True)
    manifest = []
    for i, c in enumerate(cands):
        dur = c["end"] - c["start"]
        p = os.path.join(outdir, f"c{i:03d}.jpg")
        grab(video, c["start"] + dur*0.66, p)
        manifest.append({"i": i, "file": os.path.basename(p),
                         "start": round(c["start"],1), "end": round(c["end"],1),
                         "score": round(c["score"],3)})
    json.dump(manifest, open(os.path.join(outdir,"manifest.json"),"w",encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"OK {len(manifest)} frames -> {outdir}")

if __name__ == "__main__":
    main()
