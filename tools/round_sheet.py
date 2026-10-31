"""
回合完整性验证表:每行一个回合的 3 帧(首/中/末)。
用 PIL 直接拼接,避开 ffmpeg concat demuxer 的多输入限制。
"""
import json, subprocess, sys, os
from PIL import Image

def grab(video, t, out, w=260, h=146):
    subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",
                    "-ss",f"{t:.3f}","-i",video,"-frames:v","1",
                    "-vf",f"scale={w}:{h}",out], check=False)

def main():
    video, v5json, out = sys.argv[1], sys.argv[2], sys.argv[3]
    limit = int(sys.argv[4]) if len(sys.argv) > 4 else 24
    d = json.load(open(v5json, encoding="utf-8"))
    segs = d["segments"][:limit]
    tmpdir = out + ".rr"
    os.makedirs(tmpdir, exist_ok=True)
    rows = []
    for i, s in enumerate(segs):
        dur = s["end"]-s["start"]
        imgs = []
        for j, f in enumerate((0.02, 0.5, 0.98)):
            p = os.path.join(tmpdir, f"r{i:03d}_{j}.jpg")
            grab(video, s["start"]+dur*f, p)
            if os.path.exists(p):
                imgs.append(Image.open(p).convert("RGB"))
        if len(imgs) == 3:
            row = Image.new("RGB", (260*3, 146), (0,0,0))
            for k, im in enumerate(imgs):
                row.paste(im, (k*260, 0))
            rows.append(row)
    if not rows:
        print("NO ROWS"); return
    W, H = rows[0].size
    per = 8
    chunks = []
    for k in range(0, len(rows), per):
        chunk = rows[k:k+per]
        sheet = Image.new("RGB", (W, H*len(chunk)), (0,0,0))
        for idx, r in enumerate(chunk):
            sheet.paste(r, (0, idx*H))
        chunks.append(sheet)
    total = Image.new("RGB", (W, sum(c.size[1] for c in chunks)), (0,0,0))
    y = 0
    for c in chunks:
        total.paste(c, (0, y)); y += c.size[1]
    total.save(out, quality=88)
    print("SHEET", out, "rows", len(rows))
    for i, s in enumerate(segs):
        print(f"  #{i:2d} {s['start']:8.1f}-{s['end']:8.1f} ({s['end']-s['start']:5.1f}s) shots={s['shots']}")

if __name__ == "__main__":
    main()
