# 羽毛球集锦剪辑 · 项目结构说明

```
badminton-reel/
├── README.md                  # 方案总纲:原理、效果、参数取舍
├── pipeline.py                # 单一入口:一条命令跑完整条流程
├── config.yaml                # 唯一配置源(素材路径/参数/输出)
├── requirements.txt           # Python 依赖
├── .gitignore                 # 排除中间产物与成片
│
├── src/                       # 管线核心(按执行顺序)
│   ├── probe_highlights.py    # ① 运动能量 + 音频 RMS 提取
│   ├── detect_hits.py         # ② 击球瞬态检测 → 回合聚类
│   ├── select_v5.py           # ③ 回合选段 + 不确定回合剔除
│   ├── build_v4.py            # ④ 切片 + concat 拼接(两阶段)
│   ├── make_bgm_bed.py        # ⑤ 多段 BGM 拼接 + 循环铺满
│   ├── mix.py                 # ⑥ 混音
│   └── ilink_client.py        # ⑦ iLink 微信通道协议客户端
│
├── tools/                     # 辅助工具(非主干,按需调用)
│   ├── qc.py                  # 画质质检:黑帧/冻结/静音/音量
│   ├── qc_smooth.py           # 时间戳质检:帧间隔统计
│   ├── exp_stutter.py         # 流畅度质检:静止帧/运动突变点
│   ├── verify_speed.py        # 变速倍率核验
│   ├── verify_reel.py         # 成片抽帧联系表(视觉核验)
│   ├── round_sheet.py         # 回合首/中/末三帧联系表(完整性核验)
│   ├── pool_sheet.py          # 候选池联系表
│   ├── frames_only.py         # 批量抽帧
│   ├── enc_test.py            # 编码参数对照实验
│   ├── enc_cmp2.py            # 编码画质并排对比
│   └── wx_send_video.py       # 微信通道发送(薄封装)
│
├── docs/                      # 文档
│   ├── 操作流程.md            # 完整流程 + 参数理由 + 踩坑
│   ├── 微信通道.md            # iLink 协议逆向笔记
│   └── 踩坑记录.md            # 全部失败尝试(含被否决的方案)
│
├── config/                    # 配置文件
│   ├── music/                 # BGM 生成 prompt
│   │   ├── batch1.json
│   │   ├── batch2.json
│   │   └── batch3.json
│   └── select/                # 各版本选段结果(供追溯)
│       ├── m2.v5.json
│       └── m3.v5.json
│
├── assets/                    # 音频资产(小文件,进 git)
│   └── bgm_bed.mp3            # 最终配乐床 360s
│
├── output/                    # 成片产物(进 .gitignore)
│   ├── master/                # 母版 ~495MB
│   └── wechat/                # 微信版 ~82MB
│
└── work/                      # 中间产物(进 .gitignore,可全删重跑)
    ├── analysis/              # CSV 曲线 / 击球数据 / 候选帧
    ├── sheets/                # 联系表图片
    └── build/                 # 切片临时文件
```

## 三类文件的区别

| 类别 | 目录 | 进 git? | 能否重生成 |
|---|---|---|---|
| **代码** | `src/` `tools/` | ✅ | — |
| **配置/数据** | `config/` `assets/` `docs/` | ✅ | 配置可手改,资产是成品 |
| **产物** | `output/` | ❌ | ✅ 重跑 pipeline |
| **中间件** | `work/` | ❌ | ✅ 重跑 pipeline 自动生成 |

> 中间产物一度累积到 **28 GB**(切片临时文件占绝大部分)。
> 全部可由 `pipeline.py` 从原始素材重新生成,因此不入版本库。

## 迁移方式

整个目录自包含,不含绝对路径依赖(路径全在 `config.yaml`):

```bash
# 拷贝
cp -r badminton-reel /new/path/

# 改配置
vim config.yaml   # 改 source_dir / 输出目录

# 重跑
python pipeline.py --config config.yaml
```

## 版本

当前 **v1** —— 完整跑通两场素材,输出两条 ≤6 分钟集锦:
- 3号场:5分39秒 / 41 个完整回合 / 495MB
- 2号场:5分36秒 / 31 个完整回合 / 491MB

质量基线:静止帧 ≤4、时间戳 std=0.000ms、0 黑帧、0 冻结、0 静音。
