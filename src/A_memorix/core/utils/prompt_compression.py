"""LLM 提示词压缩辅助。

对注入 LLM 提示词的 A_memorix 来源标签进行归一化：
仅保留会话身份（群名/私聊对象/引入批次名）与日期，去除绝对路径、群号、
毫秒时间戳等噪声，并按同样规则压缩存量数据库中的 source 字段。

本地补丁说明：本文件属 A_memorix 实现层（MODIFICATION_POLICY 要求应先改
上游 MaiBot_branch），当前为满足 MaiBot 侧功能需求的临时本地实现，建议尽快
同步上游。
"""
import re

_EXT_PATTERN = re.compile(r"\.(txt|json|md|db|jpg|png)$", re.IGNORECASE)
_IMPORTS_MARK = "/imports/"


def _compress_filename(name: str) -> str:
    """压缩文件名/来源名：剥扩展名，纯数字段仅保留合法日期（YYYYMMDD），
    其余纯数字段（群号/毫秒/时间戳）删除。"""
    name = _EXT_PATTERN.sub("", name)
    parts = [p for p in name.split("_") if p]
    kept = []
    for part in parts:
        if part.isdigit():
            if _is_valid_date(part[:8] if len(part) >= 8 else part):
                kept.append(part[:8])
            continue
        kept.append(part)
    return re.sub(r"_+", "_", "_".join(kept)).strip("_") or "unknown"


def _is_valid_date(value: str) -> bool:
    """判断纯数字段是否为合法 YYYYMMDD 日期。"""
    if len(value) != 8:
        return False
    year, month, day = int(value[:4]), int(value[4:6]), int(value[6:])
    return 1990 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31


def compress_source_label(source: str) -> str:
    """压缩 A_memorix 来源标签为与 LLM 抽取相关的极简形式。

    - ``raw_scan:/abs/path/imports/source/maibot/MaiBot.db`` → ``imports:maibot/MaiBot.db``
    - ``web_import:group_启萌社_123456789_20260819_095115562.txt`` → ``web_import:group_启萌社_20260819``
    - 其余前缀（chat_summary / lpmm_openie 等）原样保留。

    Args:
        source: 原始来源标签。

    Returns:
        压缩后的来源标签。
    """
    label = str(source or "").strip()
    if not label:
        return ""
    if label.startswith("raw_scan:"):
        path = label[len("raw_scan:"):]
        mark_index = path.find(_IMPORTS_MARK)
        if mark_index >= 0:
            kept = path[mark_index + len(_IMPORTS_MARK):]
        else:
            kept = path
        # 绝对路径兜底：仅保留最后两段
        parts = [p for p in kept.split("/") if p]
        if len(parts) > 2:
            parts = parts[-2:]
        if parts:
            # 目录段原样保留（如 20260814/世界观），文件名段应用与 web_import 相同的压缩
            parts[-1] = _compress_filename(parts[-1])
        kept = "/".join(parts) or "unknown"
        return f"imports:{kept}"
    if label.startswith("imports:"):
        # 已迁移形态：目录段保留，文件名段仍需压缩（历史行可能残留扩展名/数字噪声）
        kept = label[len("imports:"):]
        parts = [p for p in kept.split("/") if p]
        if parts:
            parts[-1] = _compress_filename(parts[-1])
        return f"imports:{'/'.join(parts) or 'unknown'}"
    if label.startswith("web_import:"):
        return f"web_import:{_compress_filename(label[len('web_import:'):])}"
    return label
