# 归档基线(config/select/)

这里的 `*.hits.json` / `*.v5.json` 是**重构等价性核验**的比对基准:
`tools/verify_equivalence.py` 重跑一遍,要求与这里逐字节一致。

## 适用参数版本 ⚠️

| 文件 | 内容 |
|---|---|
| `baseline_params.json` | 归档基线**是用哪一组参数跑出来的**(analysis + select 段),**按批次分别记** |
| `*.hits.json` | 击球检测结果(击球时刻 + 回合聚类) |
| `*.v5.json` | 选段结果(入选片段 + 打分) |

**`baseline_params.json` 是这套基线的"版本号",按批次记。**
`verify_equivalence.py` 每次跑都会先比对当前生效参数与该批次的版本:

- **一致** → 正常做字节级核验,门禁有意义;
- **不一致** → **不核验**(标成"跳过",不是通过),并明确打印差异来源:
  - 源素材还在 → 提示按当前参数重生成基线后重新核验;
  - 源素材已删 → 明确说"该批次从此失去字节级核验能力"。

> 为什么必须这样:`--no-redetect` 会复用这里的 `hits.json`。
> 如果改了**检测参数**却复用旧 hits,那是在拿新逻辑配旧数据 ——
> 门禁会给出一个**假的绿灯**。参数版本戳就是为了堵这个洞。

## 什么时候要重新生成基线

| 改动 | 要不要重生成基线 |
|---|---|
| 改 `base.yaml` 的 `analysis` 段 | **要** —— hits 会变 |
| 改 `base.yaml` 的 `select` 段 | **要** —— v5 会变 |
| 只改某个 `config/profiles/*.yaml` | **不用** —— base 没动,旧批次不受影响 |
| 只改 `src/select_v5.py` / `src/build_v4.py` 的实现 | **要跑一次核验**(这正是它存在的意义) |

重新生成后:

```bash
python tools/verify_equivalence.py --reuse-csv --no-redetect   # 先确认能复现
python tools/verify_equivalence.py --stamp-params             # 再打上新的参数版本戳
```

## 当前基线覆盖的批次

| key | 批次 | 状态 |
|---|---|---|
| `m2` / `m3` | 2026-10-31 龙泽 2号场/3号场 | 源视频已从磁盘删除,只能靠归档基线核验 |
| `f1` / `f2` | 2026-11-01 龙泽 1号场/2号场 | 源视频仍在,可完整重跑 |
| `c3` | 2026-11-07 龙泽 3号场 | **不在本基线里** —— 它用 profile 层的检测参数(聚类 B / 计数 G),与 base 不同源,混进来核验没有意义 |

> `c3` 的等价性由 `tools/eval_rounds.py` 的评估集 + 回归基线保证,不走这套字节级比对。
