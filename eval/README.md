# 评估集

回合识别的 ground truth 与回归评测。规范见 [`docs/标注规范.md`](../docs/标注规范.md)。

```
eval/
├── ground_truth/     标注数据(进 git)—— 每个文件 = 一个标注窗口
├── baseline/         回归基线报告(进 git)—— 改算法前后对比用
└── reports/          每次评测的输出(进 git,便于回溯历史)
```

## 目录

| 路径 | 内容 |
|---|---|
| `ground_truth/c3_1200-2400.json` | 11月7日 3号场,**普通窗口**(1200.0-2400.0s)全量精标,90 个回合 —— 无偏的 P/R/F1 依据 |
| `ground_truth/c3_559-1200.json` | 11月7日 3号场,**长回合专窗前段**(559.0-1200.0s),32 个回合(含空档扫描补录的漏检回合) |
| `ground_truth/c3_2400-3039.json` | 11月7日 3号场,**长回合专窗后段**(2400.0-3039.1s) |
| `ground_truth/c3_long_rallies.json` | 11月7日 3号场,**长回合专集**(13 个 ≥30 拍回合)—— R4 的召回与在片证据 |
| `tables/c3_shot_truth_table.json` | 拍数**裁定表**(26 行 × 四口径 + 证据路径)。不是 GT:没有 `rallies`/`scope` 语义,放这里而非 `ground_truth/`(`load_gt` 扫目录时按标注文件读) |
| `reports/sweep_c3.json` | 检测参数扫描结果(每个参数组合的 P/R/F1 与长回合口径) |
| `reports/reel_check_c3.jpg` | **从最终成片**按长回合的成片位置抽帧的联系表 —— R4 的最终视觉证据(体积大,**不入库**;本地由成片抽帧生成,同名 `.png` 是同内容的无损版) |
| `reports/c3.json` | 最近一次评测的完整报告 |
| `baseline/baseline.json` | 回归基线:改算法后 `--compare` 对照它 |

两个标注集的**口径不同,不能混算**:

- `c3_1200-2400.json` 是 `scope=detection`:该窗口内**每一个**真回合都标了(连检测器
  完全漏掉的也补上了),所以能算 precision/recall/F1。
- `c3_long_rallies.json` 是 `scope=recall`:只标了 ≥30 拍的回合,而且是**候选驱动**的
  (先用检测器找候选,再用"音频包络条 + 6 帧联系表"逐个目视核验)。
  它能回答"已知的 ≥30 拍回合有没有被检出 / 进片",**不能**证明"全片没有别的 ≥30 拍回合"。
  `tools/eval_rounds.py` 对 `scope=recall` 的集合**只算召回**,不混进 F1。

## 跑评测

标注集有两种口径,**必须分开跑**:

```bash
# 1. 生成联系表(标注入口):配置里声明了几个窗口就一次跑完几个
python tools/annotate_rounds.py --video <src.mp4> --hits work/analysis/c3.hits.json \
    --out-dir work/sheets/ann --profile club-2026-11-07

# 2. 回合识别 P/R/F1 + 选段完整率(detection 口径:三个完整标注窗口)
python tools/eval_rounds.py --gt eval/ground_truth --scope detection \
    --hits work/analysis/c3.hits.json \
    --segments work/analysis/c3.v5.json \
    --manifest work/build/c3.silent.mp4.segments.json \
    --out eval/reports/c3_3windows.json --profile club-2026-11-07

# 3. 长回合召回与在片证据(recall 口径:长回合专集)
python tools/eval_rounds.py --gt eval/ground_truth --scope recall \
    --hits work/analysis/c3.hits.json \
    --segments work/analysis/c3.v5.json \
    --manifest work/build/c3.silent.mp4.segments.json \
    --out eval/reports/c3_long_rally.json --profile club-2026-11-07

# 4. 算法改动后一键回归对比(指标下降超容差 → 退出码 1)
python tools/eval_rounds.py --gt eval/ground_truth --scope detection ... \
    --compare eval/baseline/baseline_3windows.json --profile club-2026-11-07
python tools/eval_rounds.py --gt eval/ground_truth --scope recall ... \
    --compare eval/baseline/baseline_long_rally.json --profile club-2026-11-07

# 5. 把当前结果存成新基线(确认是改进之后再存)
python tools/eval_rounds.py ... --save-baseline eval/baseline/baseline_3windows.json
```

### 基线文件对应哪套标注集(**不要混用**)

| 基线文件 | 用的标注集 | 对应的回归命令 |
|---|---|---|
| `baseline/baseline_3windows.json` | 三个完整标注窗口(157 个回合) | `--scope detection --compare eval/baseline/baseline_3windows.json` |
| `baseline/baseline_long_rally.json` | 长回合专集(13 个 ≥30 拍回合) | `--scope recall --compare eval/baseline/baseline_long_rally.json` |
| `baseline/baseline.json` | 早期只有普通窗口 + 长回合专集时存的 | 指标与上面两套**不可直接比较**(标注集不同) |

> `--profile` 决定长回合口径(`eval.long_rally_shots`)。
> 11月7日 这批用 30 拍档(= 用户原始口径),base 也是 30 拍档。
> **报告里的长回合指标必须带口径说明和样本数。**

## 参数扫描(用数据选参数,而不是凭感觉)

```bash
python tools/sweep_params.py --audio work/c3.f32le \
    --gt eval/ground_truth/c3_1200-2400.json --out eval/reports/sweep_c3.json
```

一次算完每个候选参数组合的 P/R/F1 与长回合口径,直接看哪个最好。
只读,不碰流水线产物。

## 标注数据怎么产生的

```bash
# 1. 逐张联系表人工核验 → 每张表一个碎片 json(work/ann_gt/)
# 2. 合并碎片成 ground truth(唯一写 GT 的入口,带完整性校验)
python tools/merge_gt.py --fragments work/ann_gt/w559.0-1200.0.*.json \
    --candidates work/sheets/ann/w559.0-1200.0/candidates.json \
    --clip c3 --source "20261107_龙泽_3号场.mp4" \
    --out eval/ground_truth/c3_559-1200.json
```

`tools/merge_gt.py` 会强制校验:候选 id 判全、区间合法、不越窗口、标注区间不重叠 ——
校验不过直接退出码 1,不产出 GT。

## 指标一览

| 指标 | 验收项 |
|---|---|
| `detection.precision` / `recall` / `f1` | R2 回合识别准确度 |
| `shot_count_mae` | 拍数估计偏差(影响长回合判定);只统计标注员**确认过拍数**的回合 |
| `shot_count_uncertain` | 拍数不确定、被排除在 MAE 之外的回合数 |
| `segment.complete_rally_rate` | R3 片段是完整回合 |
| `long_rally.recall_detection` | R4 长回合检出 |
| `long_rally.recall_reel` | R4 长回合在片 |
| `long_rally.list[].reel_start` / `reel_duration` | R4 每个长回合在成片里的位置与时长 |
