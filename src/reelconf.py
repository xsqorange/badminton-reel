"""
reelconf —— 集锦剪辑管线的配置层。

职责:
  1. 读取 base.yaml / profile 文件 / 已解析的 JSON 快照
  2. 按 base < profile < --set 的顺序合并(dict 深合并, list 整体替换)
  3. 校验必填项与类型 —— 缺参数在开工前就报错,而不是跑 10 分钟后 KeyError
  4. 提供点号路径访问与相对路径解析

设计约束:
  - **配置文件是唯一的参数来源**。src/ 与 tools/ 里不再允许出现魔数。
  - 新增参数只需改 config/base.yaml,不需要改任何 Python 文件。
  - 只依赖 PyYAML(requirements.txt 已声明),不再自带 YAML 解析器。

用法:
  from reelconf import load_config
  cfg = load_config("config/base.yaml", profiles=["short-60s"])
  cfg.at("encode.cq")            # -> 22
  cfg.path("output.master_dir")  # -> 绝对路径
"""
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_MISSING = object()


# ---------------------------------------------------------------- YAML

def _require_yaml():
    try:
        import yaml  # noqa: F401
        return yaml
    except ImportError:
        raise SystemExit(
            "缺少 PyYAML。请先安装依赖:\n"
            "    pip install -r requirements.txt\n"
            "(或 pip install PyYAML)"
        )


def load_yaml(path):
    """读 YAML 文件,返回 dict。"""
    yaml = _require_yaml()
    if not os.path.exists(path):
        raise SystemExit(f"配置文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise SystemExit(f"配置文件顶层必须是字典: {path}")
    return data


def load_json(path):
    """读 JSON 文件(用于 pipeline 写出的已解析配置快照)。"""
    if not os.path.exists(path):
        raise SystemExit(f"配置文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 合并

def deep_merge(base, over):
    """深合并: dict 递归合并, list / 标量整体替换。

    list 替换而不是逐元素合并,是因为 clips 这类列表语义上是"整份换掉",
    元素级合并会产生无法解释的中间态。
    """
    out = dict(base)
    for k, v in (over or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def set_dotted(data, dotted, value):
    """按点号路径写值: 'encode.cq' -> data['encode']['cq'] = value"""
    parts = dotted.split(".")
    node = data
    for p in parts[:-1]:
        if p not in node or not isinstance(node[p], dict):
            node[p] = {}
        node = node[p]
    node[parts[-1]] = value


def coerce(text):
    """把 --set 的字符串转成合适的类型: true/false/int/float/str"""
    s = text.strip()
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "none", ""):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


# ---------------------------------------------------------------- 路径

def resolve_path(p, base=ROOT):
    """相对路径按项目根解析,绝对路径原样返回。"""
    if not p:
        return p
    if os.path.isabs(p):
        return os.path.normpath(p)
    return os.path.normpath(os.path.join(base, p))


# ---------------------------------------------------------------- 校验

# (点号路径, 期望类型, 是否必填)
# 类型用可调用判断;None 表示任意类型
SCHEMA = [
    ("source_dir", str, True),
    ("clips", list, True),

    ("analysis.motion_sample_fps", (int, float), True),
    ("analysis.motion_scale", str, True),
    ("analysis.audio_stats_reset", int, True),
    ("analysis.audio_sample_rate", int, True),
    ("analysis.hit_env_window_ms", (int, float), True),
    ("analysis.hit_thresh_db", (int, float), True),
    ("analysis.hit_median_window", int, True),
    ("analysis.hit_median_offset_db", (int, float), True),
    ("analysis.hit_rise_db", (int, float), True),
    ("analysis.hit_min_gap", (int, float), True),
    ("analysis.hit_min_rally_shots", int, True),
    ("analysis.rally_max_gap", (int, float), True),
    ("analysis.min_shots", int, True),

    ("select.target_seconds", (int, float), True),
    ("select.speed", (int, float), True),
    ("select.min_lead", (int, float), True),
    ("select.min_tail", (int, float), True),
    ("select.max_lead", (int, float), True),
    ("select.max_tail", (int, float), True),
    ("select.min_gap_between", (int, float), True),
    ("select.bucket_seconds", (int, float), True),
    ("select.baseline_window_seconds", (int, float), True),
    ("select.local_base_half_buckets", int, True),
    ("select.motion_window_seconds", (int, float), True),
    ("select.motion_avg_window", int, True),
    ("select.boundary_scan_max_seconds", (int, float), True),
    ("select.lead_resume_db", (int, float), True),
    ("select.tail_fall_db", (int, float), True),
    ("select.boundary_min_gap", (int, float), True),
    ("select.score_peak_start", (int, float), True),
    ("select.score_peak_end", (int, float), True),
    ("select.score_peak_ratio", (int, float), True),
    ("select.score_peak_floor", (int, float), True),
    ("select.min_segment_extra", (int, float), True),
    ("select.weight_shots", (int, float), True),
    ("select.weight_regularity", (int, float), True),
    ("select.weight_duration", (int, float), True),
    ("select.weight_motion", (int, float), True),
    ("select.shots_score_cap", (int, float), True),
    ("select.shots_score_max", (int, float), True),
    ("select.duration_tier1", (int, float), True),
    ("select.duration_tier2", (int, float), True),
    ("select.duration_tier3", (int, float), True),
    ("select.duration_score_tier1", (int, float), True),
    ("select.duration_score_tier2", (int, float), True),
    ("select.duration_score_tier3", (int, float), True),
    ("select.duration_score_tier4", (int, float), True),

    ("build.min_gap_between", (int, float), True),
    ("build.cut_padding", (int, float), True),

    ("encode.fps", int, True),
    ("encode.scale", str, True),
    ("encode.scale_flags", str, True),
    ("encode.fps_round", str, True),
    ("encode.fps_start_time", (int, float), True),
    ("encode.vsync", str, True),
    ("encode.preset", str, True),
    ("encode.tune", str, True),
    ("encode.rc", str, True),
    ("encode.cq", (int, float), True),
    ("encode.b_v", str, True),
    ("encode.maxrate", str, True),
    ("encode.bufsize", str, True),
    ("encode.pix_fmt", str, True),
    ("encode.gop", int, True),
    ("encode.movflags", str, True),
    ("encode.x264_preset", str, True),
    ("encode.x264_crf", (int, float), True),

    ("mix.audio_codec", str, True),
    ("mix.audio_bitrate", str, True),
    ("mix.audio_sample_rate", str, True),

    ("music.bed_file", str, True),
    ("music.bed_seconds", (int, float), True),
    ("music.volume", (int, float), True),
    ("music.limiter", (int, float), True),
    ("music.fade_in", (int, float), True),
    ("music.fade_out", (int, float), True),
    ("music.bed_crossfade", (int, float), True),
    ("music.bed_loop_crossfade", (int, float), True),
    ("music.bed_fade_in", (int, float), True),
    ("music.bed_fade_out", (int, float), True),
    ("music.bed_limiter", (int, float), True),
    ("music.bed_bitrate", str, True),

    ("wechat.enabled", bool, True),
    ("wechat.cq", (int, float), True),
    ("wechat.maxrate", str, True),
    ("wechat.bufsize", str, True),

    ("output.master_dir", str, True),
    ("output.wechat_dir", str, True),
    ("output.work_dir", str, True),
    ("output.analysis_subdir", str, True),
    ("output.build_subdir", str, True),
    ("output.sheets_subdir", str, True),

    ("ilink.base_url", str, True),
    ("ilink.cdn_base", str, True),
    ("ilink.channel_version", str, True),
    ("ilink.app_id", str, True),
]


def validate(data):
    """按 SCHEMA 校验,返回错误列表。"""
    errors = []
    for dotted, types, required in SCHEMA:
        node = data
        found = True
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                found = False
                break
        if not found:
            if required:
                errors.append(f"缺少必填配置项: {dotted}")
            continue
        if types is not None and not isinstance(node, types):
            names = types if isinstance(types, tuple) else (types,)
            want = "/".join(t.__name__ for t in names)
            errors.append(f"配置项类型错误: {dotted} 应为 {want},实际 {type(node).__name__}")
    # clips 的每一条都要有 key/name/file
    clips = data.get("clips")
    if isinstance(clips, list):
        for i, c in enumerate(clips):
            if not isinstance(c, dict):
                errors.append(f"clips[{i}] 必须是字典")
                continue
            for field in ("key", "name", "file"):
                if not c.get(field):
                    errors.append(f"clips[{i}] 缺少字段: {field}")
    return errors


# ---------------------------------------------------------------- Config

class Config:
    """已解析的配置。提供点号路径访问。"""

    def __init__(self, data, root=ROOT, source=None):
        self._data = data
        self.root = root
        self.source = source or ""

    # -- 容器协议 --------------------------------------------------
    def __getitem__(self, key):
        return self._data[key]

    def __contains__(self, key):
        return key in self._data

    def __iter__(self):
        return iter(self._data)

    def keys(self):
        return self._data.keys()

    def items(self):
        return self._data.items()

    # -- 点号访问 --------------------------------------------------
    def get(self, dotted, default=None):
        node = self._data
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def at(self, dotted, default=_MISSING):
        """取必填项;缺失时抛错(附带配置文件路径便于定位)。"""
        val = self.get(dotted, _MISSING)
        if val is _MISSING:
            where = f" (来自 {self.source})" if self.source else ""
            raise SystemExit(f"配置项缺失: {dotted}{where}")
        return val

    def section(self, name):
        """取一整段配置(缺失返回空 dict)。"""
        v = self._data.get(name)
        return v if isinstance(v, dict) else {}

    def path(self, dotted):
        """取路径型配置项并解析成绝对路径。"""
        return resolve_path(self.at(dotted), self.root)

    def as_dict(self):
        return copy.deepcopy(self._data)

    def dumps(self, indent=2):
        return json.dumps(self._data, ensure_ascii=False, indent=indent)


# ---------------------------------------------------------------- 加载

def profile_path(name, profiles_dir=None):
    """profile 名 -> 文件路径。允许省略 .yaml 后缀。"""
    profiles_dir = profiles_dir or os.path.join(ROOT, "config", "profiles")
    if os.path.isabs(name) and os.path.exists(name):
        return name
    cand = name if os.path.splitext(name)[1] else name + ".yaml"
    p = os.path.join(profiles_dir, cand)
    if os.path.exists(p):
        return p
    raise SystemExit(f"profile 不存在: {name} (查找目录 {profiles_dir})")


def load_config(path=None, profiles=(), overrides=(), root=ROOT, validate_schema=True):
    """解析配置。

    path      base 配置文件路径;None -> config/base.yaml
              .json 视为"已解析快照",直接读取、不再叠加 profile
    profiles  profile 名或路径列表,按出现顺序叠加
    overrides "key=value" 字符串列表,优先级最高
    """
    path = path or os.path.join(ROOT, "config", "base.yaml")
    sources = [path]

    if path.lower().endswith(".json"):
        if profiles:
            raise SystemExit(
                f"--config 指向已解析的 JSON 快照,不能再叠加 profile: {path}\n"
                f"请改用 YAML 配置文件: config/base.yaml")
        data = load_json(path)
    else:
        data = load_yaml(path)
        for prof in profiles or ():
            ppath = profile_path(prof)
            sources.append(ppath)
            data = deep_merge(data, load_yaml(ppath))

    for ov in overrides or ():
        if "=" not in ov:
            raise SystemExit(f"--set 需要 key=value 形式: {ov}")
        key, _, val = ov.partition("=")
        set_dotted(data, key.strip(), coerce(val))

    if validate_schema:
        errors = validate(data)
        if errors:
            head = "配置校验失败:\n"
            body = "\n".join(f"  - {e}" for e in errors)
            where = "\n来源: " + "\n      ".join(sources)
            raise SystemExit(head + body + where)

    return Config(data, root=root, source=" -> ".join(sources))


# ---------------------------------------------------------------- CLI 辅助

def add_config_args(parser):
    """给 argparse 挂上标准的配置参数组。"""
    parser.add_argument("--config", default=os.path.join(ROOT, "config", "base.yaml"),
                        help="配置文件路径(.yaml 或已解析的 .json 快照)")
    parser.add_argument("--profile", action="append", default=[],
                        help="叠加一个 profile(config/profiles/<name>.yaml),可重复,按出现顺序叠加")
    parser.add_argument("--set", action="append", default=[], dest="overrides",
                        metavar="KEY=VALUE",
                        help="临时覆盖某个配置项,如 --set encode.cq=24,可重复")
    return parser


def config_from_args(args, root=ROOT):
    """从 argparse 结果构造 Config。"""
    return load_config(args.config, profiles=args.profile,
                       overrides=args.overrides, root=root)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="解析并打印生效的配置")
    add_config_args(ap)
    a = ap.parse_args()
    cfg = config_from_args(a)
    print(cfg.dumps())
