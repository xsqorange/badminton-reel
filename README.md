# 羽毛球长视频集锦剪辑方案

从 **115 分钟 / 8GB** 的活动录像里,自动剪出 **两条 ≤6 分钟** 的高光集锦(带配乐),
全程自动化,人工只做最后的视觉核验。

当前版本 **v1** · 实测产出:3号场 5分39秒/41回合,2号场 5分36秒/31回合。

---

## 一、为什么不能直接用剪辑器剪

8GB / 115 分钟素材拖进剪辑器,逐帧找高光不可行——你得看 6900 秒画面,而且反复拖动试错。

这套方案的核心是**分层**:把"找高光"这件苦活交给信号处理,人只负责判断"这段好不好看"。

| 阶段 | 手段 | 产出 |
|---|---|---|
| 找高光 | 击球瞬态检测 | 427 个真实回合(其中 221 个 ≥4 拍) |
| 筛高光 | 自动规则 + 视觉核验 | 剔除穿帮/捡球/近景遮挡 |
| 切片 | NVENC 硬编 | 秒级精确定位,统一 1080p60 |
| 配乐 | AI 生成 + 交叉淡化 | 6 分钟无缝配乐床 |
| 质检 | 自动指标 | 静止帧/黑帧/静音/时间戳 |

---

## 二、关键技术决策

### 1. 用「击球瞬态」而不是「运动能量」找高光 ⭐

**这是整套方案最核心的一步。**

第一版用运动能量(帧间差分)找高光,结果选出的多是 1-2 拍短促对抗,还混进了换人走动。

原因:运动能量高 ≠ 在打球。可能是有人走过镜头前、换人、捡球。

**击球瞬态**则直接检测"球拍击球"这个物理事件:

```
音频 → 10ms 短时 RMS 包络 → 一阶差分取上升沿
     → 局部峰值 + 自适应阈值(局部中位数 +8dB,适应场地嘈杂)
     → 最小间隔 0.16s 合并 → 按 1.6s 间隔聚类成「回合」
```

一个回合 = 一串间隔规律的击球。于是天然得到:
- **拍数**:直接反映激烈程度(实测最长 44 拍 / 28.6 秒)
- **连贯性**:击球间隔标准差越小,节奏越规整
- **边界**:第一拍 ≈ 发球,最后一拍 ≈ 得分

### 2. VFR 源必须用 `fps` 滤镜,不能用 `-vsync cfr` ⭐

源视频 `r_frame_rate=60/1` 但 `avg_frame_rate=359433383/5988964`,是 **VFR 变帧率**。

用 `-r 60 -vsync cfr` 强转时,ffmpeg 丢弃运动帧后**用复制帧补齐**,
在成片里制造了 **12485 个"画面定住不动"的静止帧** —— 这就是第一版"一卡一卡"的根因。

正确做法:
```bash
-vf "fps=60:round=near:start_time=0"     # 基于时间戳做等距重采样
```
不是丢/补帧,而是按时间戳线性重采样。

配套的还有:**拼接必须用 `concat filter`**,不能用 `concat demuxer + -c copy`
(后者按各文件容器时长算偏移,VFR 的取整误差会累积成时间轴断裂)。

### 3. 码率必须加 `-maxrate`/`-bufsize` ⭐

```bash
-cq 22 -b:v 0 -maxrate 12000k -bufsize 24000k
```

NVENC 的 VBR 模式下 `-b:v 0` 表示**没有码率上限**,`-cq 22` 又要求高质量。
羽毛球是全画面高频运动(球员跑动 + 球拍高速挥动 + 球网细纹),每帧都难压,
码率直接顶到 **39 Mbps**(源素材才 12 Mbps),5分半的片子 **1.5 GB**。

加了 `-maxrate` 后降到 12 Mbps(与源同级),**6 分钟 495 MB**。

画质损失为零 —— 100% 像素裁切对比(球衣衣褶、球拍线、空调纹理)在
cq17 / cq23 / cq24 / cq26 之间**肉眼完全分不出**。那多出来的 3 倍码率是纯浪费。

### 4. 回合完整性:边界用「局部相对能量」定位 ⭐

要"从发球到得分",边界必须落在真实起止点。踩了三次坑:

| 尝试 | 思路 | 为什么失败 |
|---|---|---|
| v3 | 运动能量找「静止→活跃」 | 羽毛球连续对打,回合间隙极短,运动能量几乎不降到静止 → 159 个回合被判起点不明 |
| v4 | 音频找「静默期」 | 这段录像**全程有场地背景噪声**(RMS 恒在 -31dB),不存在绝对静默 → 末拍后永远找不到边界 |
| **v5** | **局部相对能量** | ✅ |

v5 的做法:音频 RMS 减去 5s 滑动均值(消除慢漂移)得到相对能量,
再找「回升到基线以上」= 发球准备、「跌回基线以下」= 得分后收尾。

**不确定的回合直接移除**(用户明确要求):
- `boundary_split`:首拍前后都没有明显能量变化 → 可能是长回合被切开的一段
- `no_score_evidence`:末拍后没有得分应有的响动

实测移除 120 / 134 个,换来回合完整度。

另外:**发球常常太轻,瞬态检测会漏掉**,所以固定回溯 0.9s 覆盖抛球+发球动作。

### 5. 自动遮挡检测不可靠,只能靠人眼

试了 5 种自动方案(帧差比、肤色 blob、高频能量、人脸检测、局部纹理比),
全部翻车 —— 这批素材是暖色调照明,**紫色地板会落进 YCrCb 肤色区间**,导致整块画面被判为肤色。

最终方案:自动规则初筛 + **联系表人工核验**。
每回合抽首/中/末三帧拼成一行,一眼就能看出是否切在回合中间、是否有人挡镜头。

> 这是本方案里唯一保留人工的环节,而且成本很低(一场 20 行图)。

---

## 三、快速开始

```bash
# 1. 改配置(唯一需要改的文件)
vim config/base.yaml     # 改 source_dir / clips / 各种参数

# 2. 跑完整流程
python pipeline.py

# 3. 只跑某一场 / 跳过某阶段
python pipeline.py --only m3
python pipeline.py --skip analyze      # 复用已有选段,只重剪
python pipeline.py --from build        # 从剪辑开始
```

输出:
```
output/master/3号场_龙泽_羽毛球集锦.mp4     # 母版 ~495MB
output/master/2号场_龙泽_羽毛球集锦.mp4     # 母版 ~491MB
output/wechat/*.mp4                         # 微信版 ~82MB
```

### 依赖

```bash
pip install -r requirements.txt
# 系统依赖:ffmpeg(含 nvenc)、NVIDIA 显卡驱动
```

---

## 四、配置体系(唯一参数源)

所有数值参数都集中在 `config/base.yaml`,**Python 代码里不再有魔数**。
生效顺序(后者覆盖前者):

```
config/base.yaml  <  --profile <名字>(可重复)  <  --set key=value(可重复)
```

| 层 | 作用 | 例子 |
|---|---|---|
| `config/base.yaml` | 全项目参数基线,带参数取舍的理由注释 | 素材路径、检测阈值、选段权重、编码参数 |
| `config/profiles/*.yaml` | 场景档案,只写差异 | `short-60s` 竖屏 60 秒版、`rehearsal` 快速试跑 |
| `--set` | 临时改单个值试参数,不动文件 | `--set encode.cq=24` |

```bash
# 看当前生效的完整配置
python src/reelconf.py
python src/reelconf.py --profile short-60s

# 换一套输出规格,不动代码、不动 base
python pipeline.py --profile short-60s

# 新增素材:写一个 profile 即可
#   config/profiles/club-2026-11-07.yaml  ->  source_dir + clips
python pipeline.py --profile club-2026-11-07
```

新增一个参数只需要两步:在 `base.yaml` 里加一行 → 在代码里用 `cfg.at("section.key")` 读它。
配置在开工前会被校验(缺项/类型错会直接报错并列出具体路径),
不会再像以前那样跑 10 分钟后才 `KeyError`。

各脚本也能单独跑,都吃同一套配置:

```bash
python src/select_v5.py hits.json motion.csv audio.csv out.json --profile short-60s
```

改动管线后想快速确认链路还通,用合成素材跑一遍(约 10 秒):

```bash
python tools/make_smoke_fixture.py
python pipeline.py --profile work/smoke-profile.yaml
```

### 单独导出某个回合

文档里报的"最长 44 拍 / 28.6 秒"这类极限回合,可以剪成独立片段单独看:

```bash
python tools/export_rally.py --clip m2 --longest      # 该场最长的回合
python tools/export_rally.py --clip m2 --shots 44      # 指定拍数
python tools/export_rally.py --clip m2 --longest --with-bgm   # 混配乐
```

边界与主流程一致:该回合若在最终选段里就直接复用选段边界(含发球前置与得分后延),
否则单独跑一遍 `select_v5` 取它算出的边界。
默认 1.0x 原速并保留原声 —— 便于听清每一拍。

---

## 五、质检

```bash
python tools/qc.py output/master/*.mp4          # 黑帧/冻结/静音/音量
python tools/exp_stutter.py output/master/*.mp4 # 静止帧/运动突变点
python tools/qc_smooth.py output/master/*.mp4   # 时间戳连续性
python tools/verify_speed.py                    # 变速倍率核验
```

**v1 实测基线**(两条成片):

| 指标 | 3号场 | 2号场 |
|---|---|---|
| 静止帧(卡顿) | 4 | 1 |
| 时间戳 std | 0.000 ms | 0.000 ms |
| 顿挫帧 | 0 | 0 |
| 黑帧 / 冻结 / 静音 | 0 / 0 / 0 | 0 / 0 / 0 |
| 码率 | 12 Mbps | 12 Mbps |
| 体积 | 495 MB | 491 MB |

> **静止帧是最关键的指标** —— 只要它上千,就一定有肉眼可见的卡顿。

---

## 六、长回合保障(≥N 拍的长回合必须进成片)

长回合是集锦里最该保留的素材,但它**稀缺** —— 一个 80 分钟的场次可能一个都没有,
所以不能靠"打分高自然就选上了",必须有一道**显式保障**。

机制在 `src/select_v5.py`:

1. `select.long_rally_shots` = 拍数门槛(base 是 **30**,用户原始口径);
2. `select.long_rally_force` = 是否开启强制纳入(base 默认 `false`);
3. 开启后,≥N 拍的回合**先于其它候选入选,且不受时长预算限制**;
4. 同时豁免"缺得分佐证"剔除 —— 30+ 拍的长回合常以一个平淡的球收尾,
   末拍后没有明显响动,但这恰恰是最该保留的高光。

阈值**分两层**:base 保持 30 拍不动,各批次 profile 按**本批实测分布**调整。
11月7日 3号场最初实测"最长只有 28 拍、≥30 拍 0 个",一度把阈值降到 20 拍;
后来用标注集复测发现那是**检测器把长回合切碎造成的假象**
(漏掉一两拍就在回合内部留下 >1.6s 空档,聚类把它切成两段),
修正检测器参数后本批实测 **13 个 ≥30 拍回合**,全部在成片里 ⇒ 阈值保持 30 拍:

```yaml
select:
  long_rally_force: true
  long_rally_shots: 30
eval:
  long_rally_shots: 30     # 评测口径必须与选段口径一致
```

> 汇报长回合指标时**必须带上口径**(本批是 30 拍档),并给出每个长回合的
> 源时间 / 成片位置 / 时长。用 0 个样本声称达标不成立;
> **更不能用"检测器说没有"当作"不存在"** —— 先确认检测器没有把回合切碎。

---

## 七、评估集与一键回归

回合识别不能只靠"看着挺准",要有 ground truth 和可回归的指标。

```bash
# 1. 生成联系表(人工核验的入口;配置里声明了几个窗口就一次跑完几个)
python tools/annotate_rounds.py --video <src.mp4> --hits work/analysis/c3.hits.json \
    --out-dir work/sheets/ann --profile club-2026-11-07

# 2. 人工核验 → 碎片 → 合并成 ground truth(唯一写 GT 的入口,带完整性校验)
python tools/merge_gt.py --fragments work/ann_gt/w*.json \
    --candidates work/sheets/ann/w559.0-1200.0/candidates.json \
    --clip c3 --source "<源文件名>" --out eval/ground_truth/c3_559-1200.json

# 3. 评测:回合识别 P/R/F1 + 选段完整率 + 长回合检出/在片召回
python tools/eval_rounds.py --gt eval/ground_truth \
    --hits work/analysis/c3.hits.json --segments work/analysis/c3.v5.json \
    --manifest work/build/c3.silent.mp4.segments.json \
    --out eval/reports/c3.json --profile club-2026-11-07

# 4. 算法改动后一键回归对比(任一指标下降超容差 → 退出码 1,可直接挂 CI)
python tools/eval_rounds.py ... --compare eval/baseline/baseline.json
```

标注规范(什么算一个回合、边界怎么定、拍数怎么数、窗口怎么扩)见
[`docs/标注规范.md`](docs/标注规范.md);评估集说明见 [`eval/README.md`](eval/README.md)。

> **长回合专窗**:普通标注窗口盖不住全部长回合,必须专门加一个窗口,
> 规则事先声明(见 `docs/标注规范.md` 第八节),否则长回合指标会是 0 个样本。

---

## 八、目录结构

见 `PROJECT_STRUCTURE.md`。要点:

- `src/` 管核心脚本,`tools/` 辅助工具,`config/` 配置,`docs/` 文档
- `output/` 成片(不进 git),`work/` 中间产物(不进 git,可全删重跑)
- 路径全部集中在 `config/base.yaml`,迁移只需改这一个文件

---

## 九、已知限制

- **依赖 NVIDIA 显卡**:NVENC 硬编是性能关键,无显卡时回退到 libx264(慢很多)
- **发球可能漏检**:极轻的发球声检测不到,靠固定 0.9s 回溯兜底
- **背景噪声大时判据变弱**:本方案针对"有持续场地噪声"的环境做过标定
- **固定机位假设**:遮挡检测与回合边界都基于镜头固定的前提

---

## 十、文档

- `docs/操作流程.md` —— 完整流程、每步参数理由
- `docs/踩坑记录.md` —— 所有失败尝试(含被否决的方案)
- `docs/标注规范.md` —— 回合标注规范(评估集怎么标、标什么、窗口怎么扩)
- `docs/执行报告.md` —— 每批素材的执行报告(改了什么、指标、还差什么)
- `docs/微信通道.md` —— iLink 协议逆向笔记(发视频到微信)

---

## 十一、凭据与隐私

微信通道的凭据是**本机运行时凭据,不在版本库里**,由 `src/ilink_client.py` 解析:

| 项 | 环境变量 | 配置文件字段 |
|---|---|---|
| 通道配置路径 | `WX_CHANNEL_CFG` | 默认 `~/.minimax/wechat-channel.yaml` |
| botToken | `WX_BOT_TOKEN` | `botToken` |
| 会话 ID | `WX_CHAT_ID` | `ownerSenderId` / `chatId` |

环境变量优先;都拿不到时脚本打印修复指引并退出。

仓库里**不含**素材原片、成片、中间产物和任何凭据:
`output/` 与 `work/` 已被 `.gitignore` 排除,凭据与个人会话 ID 不进代码。
