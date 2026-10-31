"""
验证变速是否真正生效:
- 从成片抽一个已知片段,测量其画面内容与源片段的对应关系
- 更直接:对比成片总时长与选中片段源时长之和的比例
"""
import json, sys, subprocess

for name, v5, out in (
    ("m3", r"D:\work\minimax-bot\analysis\m3.v5.json",
     r"D:\work\minimax-bot\analysis\out\v5\3号场_龙泽_羽毛球集锦_配乐.mp4"),
    ("m2", r"D:\work\minimax-bot\analysis\m2.v5.json",
     r"D:\work\minimax-bot\analysis\out\v5\2号场_龙泽_羽毛球集锦_配乐.mp4"),
):
    d = json.load(open(v5, encoding="utf-8"))
    segs = d["segments"]
    src_total = sum(s["end"]-s["start"] for s in segs)
    dur = float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
                                "-of","default=nw=1:nk=1",out],
                               capture_output=True,text=True,
                               encoding="utf-8",errors="replace").stdout.strip())
    # 成片里还有 concat 边界余量(每段前后各 0.12s)
    pad = len(segs)*0.24
    expect = src_total/1.1 + pad
    print(f"=== {name}")
    print(f"  选中 {len(segs)} 段,源时长合计 {src_total:.1f}s")
    print(f"  理论输出 {src_total:.1f}/1.1 + {pad:.1f} 边界余量 = {expect:.1f}s")
    print(f"  实际成片 {dur:.1f}s   偏差 {dur-expect:+.1f}s")
    print(f"  实测变速比 ≈ {src_total/dur:.3f} (期望 1.100)")
    sp = {}
    for s in segs:
        sp[round(s["end"]-s["start"],1)] = sp.get(round(s["end"]-s["start"],1),0)+1
    print(f"  片段时长分布: {sorted(sp.items())[:8]}")
