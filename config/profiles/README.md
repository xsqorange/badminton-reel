# profiles —— 场景配置档案

一个 profile 是一份"只写差异"的配置片段,叠在 `config/base.yaml` 之上。

## 用法

```bash
python pipeline.py --profile short-60s              # 用一个 profile
python pipeline.py --profile club-2026-11-07 --profile short-60s   # 叠加多个(按顺序)
python pipeline.py --profile short-60s --set encode.cq=24         # 再临时改一个值
```

生效顺序:`base.yaml` → profile(按命令行出现顺序) → `--set`

## 已有档案

| 文件 | 用途 |
|---|---|
| `short-60s.yaml` | 60 秒竖屏短视频(1080x1920 / 30fps / 1.0x) |
| `rehearsal.yaml` | 快速试跑(640x360 / 24fps),几分钟出片,不可发布 |

## 新增一个档案

1. 在本目录新建 `<名字>.yaml`
2. 只写要覆盖的项,其余自动继承 `base.yaml`
3. 运行 `python pipeline.py --profile <名字>`

```yaml
# config/profiles/club-2026-11-07.yaml
source_dir: "D:/羽毛球视频/俱乐部日常活动/11月7日"

clips:
  - key: f1
    name: "1号场_11月7日_集锦"
    file: "20261107_1号场.mp4"

select:
  target_seconds: 300
```

## 规则

- **dict 深合并**:只写要改的键,同级的其他键保持不变
- **list 整体替换**:`clips` 这类列表不做元素级合并,写了就整份换掉
- **路径相对项目根**:也可以写绝对路径
- profile 之间可以互相独立,组合使用时后面的覆盖前面的
