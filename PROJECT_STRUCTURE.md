# 羽毛球集锦剪辑 · 项目结构说明

```
badminton-reel/
├── README.md                  # 方案总纲:原理、效果、参数取舍
├── pipeline.py                # 单一入口:一条命令跑完整条流程
├── requirements.txt           # Python 依赖
├── .gitignore                 # 排除中间产物与成片
│
├── src/                       # 管线核心(按执行顺序)
│   ├── reelconf.py            # 配置层:加载/合并/校验/覆盖(共用模块)
│   ├── probe_highlights.py    # ① 运动能量 + 音频 RMS 提取
│   ├── detect_hits.py         # ② 击球瞬态检测 → 回合聚类
│   ├── select_v5.py           # ③ 回合选段 + 不确定回合剔除
│   ├── build_v4.py            # ④ 切片 + concat 拼接(两阶段)
│   ├── make_bgm_bed.py        # ⑤ 多段 BGM 拼接 + 循环铺满
│   ├── mix.py                 # ⑥ 混音
│   ├── press_wechat.py        # ⑦ 微信版压制
│   └── ilink_client.py        # ⑧ iLink 微信通道协议客户端
│
├── tools/                     # 辅助工具(非主干,按需调用)
│   ├── qc.py                  # 画质质检:黑帧/冻结/静音/音量
│   ├── qc_smooth.py           # 时间戳质检:帧间隔统计
│   ├── exp_stutter.py         # 流畅度质检:静止帧/运动突变点
│   ├── verify_speed.py        # 变速倍率核验
│   ├── verify_equivalence.py  # 等价性核验:重跑分析阶段 vs 归档基线
│   ├── annotate_rounds.py     # 标注工具:生成回合核验联系表(评估集入口,支持多窗口)
│   ├── merge_gt.py            # 标注碎片合并成 ground truth(唯一写 GT 的入口,带校验)
│   ├── hit_sheet.py           # 击球时刻核对表:逐拍验证拍数(长回合阈值核对)
│   ├── eval_rounds.py         # 回合识别评测:P/R/F1 + 长回合召回 + 一键回归
│   ├── export_rally.py        # 导出单个回合为独立成片(最长 N 拍等)
│   ├── make_smoke_fixture.py  # 生成合成素材 + 小 profile(10 秒冒烟测试)
│   ├── sync_deliver.py        # 成片交付到素材目录
│   ├── verify_reel.py         # 成片抽帧联系表(视觉核验)
│   ├── round_sheet.py         # 回合首/中/末三帧联系表(完整性核验)
│   ├── pool_sheet.py          # 候选池联系表
│   ├── frames_only.py         # 批量抽帧
│   ├── enc_test.py            # 编码参数对照实验
│   ├── enc_cmp2.py            # 编码画质并排对比
│   ├── wx_send_video.py       # 微信通道发送(薄封装)
│   └── wx_channel_diag.py     # 微信通道逐端点诊断(定位发送失败的原因)
│
├── eval/                      # 评估集(进 git)
│   ├── README.md              #   怎么跑评测 / 指标含义
│   ├── ground_truth/          #   标注数据(每文件 = 一个标注窗口)
│   │   ├── c3_559-1200.json   #     11月7日 3号场 · 长回合专窗前段
│   │   ├── c3_1200-2400.json  #     11月7日 3号场 · 普通窗口
│   │   └── c3_2400-3039.json  #     11月7日 3号场 · 长回合专窗后段
│   ├── baseline/              #   回归基线报告(改算法前后对比)
│   └── reports/               #   历次评测输出
│
├── docs/                      # 文档
│   ├── 操作流程.md            # 完整流程 + 参数理由 + 踩坑
│   ├── 标注规范.md            # 回合标注规范(评估集怎么标、标注什么)
│   ├── 微信通道.md            # iLink 协议逆向笔记
│   ├── 执行报告.md            # 每批素材的执行报告(改了什么/指标/还差什么)
│   └── 踩坑记录.md            # 全部失败尝试(含被否决的方案)
│
├── config/                    # 配置文件
│   ├── base.yaml              # ★ 参数基线:唯一配置源(素材/阈值/编码/输出)
│   ├── profiles/              # 场景档案(只写差异,叠加在 base 之上)
│   │   ├── README.md          #   怎么新增一个 profile
│   │   ├── short-60s.yaml     #   竖屏 60 秒短视频
│   │   ├── rehearsal.yaml     #   快速试跑
│   │   ├── club-2026-11-01.yaml #  11月1日 龙泽(1/2号场)
│   │   └── club-2026-11-07.yaml #  11月7日 龙泽(3号场,开长回合强制纳入)
│   ├── music/                 # BGM 生成 prompt
│   │   ├── batch1.json
│   │   ├── batch2.json
│   │   └── batch3.json
│   └── select/                # 各版本选段结果(供追溯 / 等价性基线)
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
    ├── analysis/              # CSV 曲线 / 击球数据 / 选段结果
    ├── config.resolved.json   # 本次运行生效的配置快照(可追溯)
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

## 配置体系

参数只有一处来源:`config/base.yaml`。

```
config/base.yaml  <  --profile <名字>  <  --set key=value
```

- `src/reelconf.py` 负责加载、合并、校验、路径解析
- 每个阶段脚本都接受 `--config / --profile / --set`,可单独运行
- `pipeline.py` 解析一次配置并落一份 `work/config.resolved.json` 快照,各阶段共用
- 配置在开工前校验:缺项或类型错会立刻报出具体路径

新增参数只需两步:在 `base.yaml` 加一行 → 代码里 `cfg.at("section.key")` 读它。
换一套输出规格(时长/画幅/帧率/码率)只需加一个 profile,不用改代码。

## 迁移方式

整个目录自包含,不含绝对路径依赖(路径全在 `config/base.yaml`):

```bash
# 拷贝
cp -r badminton-reel /new/path/

# 改配置
vim config/base.yaml   # 改 source_dir / 输出目录

# 重跑
python pipeline.py
```

## 版本

当前 **v1** —— 完整跑通两场素材,输出两条 ≤6 分钟集锦:
- 3号场:5分39秒 / 41 个完整回合 / 495MB
- 2号场:5分36秒 / 31 个完整回合 / 491MB

质量基线:静止帧 ≤4、时间戳 std=0.000ms、0 黑帧、0 冻结、0 静音。
