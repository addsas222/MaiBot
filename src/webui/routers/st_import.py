"""SillyTavern 数据导入接口：角色卡（PNG/JSON）与世界书（lorebook JSON）。

角色卡遵循酒馆 V2/V3 角色卡规范：PNG 的 tEXt 块 ``chara``/``ccv3`` 携带
base64 编码的卡片 JSON，或直接为 JSON 文件。导入后存入
``data/character_cards/``，可预览组合提示词并应用到人格配置。

世界书条目逐条写入 A_Memorix 知识库（``memory_service.ingest_text``），
external_id 采用确定性命名保证重复导入幂等。
"""

from __future__ import annotations

import base64
import json
import re
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional

import tomlkit
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from src.common.logger import get_logger
from src.common.version import PROJECT_ROOT
from src.services.memory_service import memory_service
from src.webui.dependencies import require_auth

logger = get_logger("webui.st_import")

router = APIRouter(prefix="/st-import", tags=["SillyTavern 导入"], dependencies=[Depends(require_auth)])

CARDS_ROOT = PROJECT_ROOT / "data" / "character_cards"
WORLDBOOK_INDEX_DIR = CARDS_ROOT / "_worldbooks"
ACTIVE_CARD_PATH = CARDS_ROOT / "_active_card.json"
CONFIG_PATH = PROJECT_ROOT / "config" / "bot_config.toml"

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_TEXT_CHUNK = b"tEXt"
_CARD_JSON_KEYS = ("spec", "name", "description", "char_greeting", "first_mes")


def _safe_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", str(name or "").strip())
    return cleaned[:80] or "unnamed"


def _decode_card_json(raw: bytes) -> Dict[str, Any]:
    try:
        payload = json.loads(base64.b64decode(raw).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"角色卡数据解码失败: {exc}") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="角色卡内容不是 JSON 对象")
    return payload


def _extract_card_from_png(data: bytes) -> Optional[Dict[str, Any]]:
    """遍历 PNG tEXt 块提取 chara/ccv3 角色卡；非角色 PNG 返回 None。"""
    if not data.startswith(_PNG_SIGNATURE):
        return None
    offset = len(_PNG_SIGNATURE)
    found: Dict[str, Any] = {}
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if chunk_type != _TEXT_CHUNK:
            continue
        keyword, _, text = chunk_data.partition(b"\x00")
        key = keyword.decode("latin-1", errors="replace").strip().lower()
        if key in {"chara", "ccv3"} and key not in found:
            try:
                found[key] = json.loads(base64.b64decode(text))
            except Exception:
                continue
        if "chara" in found:
            break
    # ccv3 优先（字段更全），回退 v2
    card = found.get("ccv3") or found.get("chara")
    return card if isinstance(card, dict) else None


def _normalize_card(card: Dict[str, Any]) -> Dict[str, Any]:
    """兼容 V1/V2/V3 字段位置，归一到扁平结构。"""
    data = card.get("data") if isinstance(card.get("data"), dict) else {}

    def pick(*keys: str) -> str:
        return next((str(card[k]) for k in keys if str(card.get(k) or "").strip()), "")

    first_mes = pick("first_mes", "char_greeting") or str(data.get("first_mes") or "")
    return {
        "name": pick("name") or str(data.get("name") or "") or "未命名角色",
        "description": str(data.get("description") or ""),
        "personality": str(data.get("personality") or ""),
        "scenario": str(data.get("scenario") or ""),
        "first_mes": first_mes,
        "mes_example": str(data.get("mes_example") or ""),
        "system_prompt": str(data.get("system_prompt") or ""),
        "tags": list(data.get("tags") or card.get("tags") or []),
        "creator": str(data.get("creator") or card.get("creator") or ""),
        "character_version": str(data.get("character_version") or card.get("character_version") or ""),
        "spec": str(card.get("spec") or ("V3" if "ccv3" in card else "unknown")),
    }


def _card_path(card_id: str) -> Path:
    safe = _safe_name(card_id)
    if not safe or safe.startswith("."):
        raise HTTPException(status_code=400, detail="非法的角色卡 ID")
    path = CARDS_ROOT / f"{safe}.json"
    if not path.resolve().is_relative_to(CARDS_ROOT.resolve()):
        raise HTTPException(status_code=400, detail="非法的角色卡 ID")
    return path


def _parse_mes_example(raw: str, char_name: str) -> str:
    """解析酒馆示例对话：<START> 分块，替换 {{char}}/{{user}} 宏为可读文本。"""
    raw = str(raw or "").strip()
    if not raw:
        return ""
    blocks = [b.strip() for b in re.split(r"<START>", raw, flags=re.IGNORECASE) if b.strip()]
    lines: List[str] = []
    for block in blocks[:6]:
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            line = line.replace("{{char}}", char_name).replace("{{user}}", "对方")
            line = re.sub(r"\{\{[^}]+\}\}", "", line).strip()
            if line:
                lines.append(line)
        if len(lines) >= 24:
            break
    return "\n".join(lines[:24])


def _composed_prompt(card: Dict[str, Any]) -> str:
    """把角色卡组装为麦麦人格设定文本（应用时写入 personality.personality）。"""
    sections = [f"你在扮演角色「{card['name']}」。"]
    if card["description"]:
        sections.append(f"【角色设定】\n{card['description']}")
    if card["personality"]:
        sections.append(f"【性格】\n{card['personality']}")
    if card["scenario"]:
        sections.append(f"【场景】\n{card['scenario']}")
    example_dialogue = _parse_mes_example(card.get("mes_example") or "", card["name"])
    if example_dialogue:
        sections.append(f"【说话风格示例（仅供参考，勿照搬原文）】\n{example_dialogue}")
    return "\n\n".join(sections)


@router.post("/cards")
async def upload_card(file: UploadFile = File(...)) -> Dict[str, Any]:
    raw = await file.read()
    card = _extract_card_from_png(raw)
    if card is None:
        try:
            card = json.loads(raw.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="无法解析文件：既不是含角色卡的 PNG，也不是 JSON 文件") from None
    normalized = _normalize_card(card)
    CARDS_ROOT.mkdir(parents=True, exist_ok=True)
    path = _card_path(normalized["name"])
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"角色卡已导入: {normalized['name']}（{path.name}）")
    return {"success": True, "card_id": path.stem, "card": normalized}


@router.get("/cards")
async def list_cards() -> Dict[str, Any]:
    cards: List[Dict[str, Any]] = []
    if CARDS_ROOT.is_dir():
        for path in sorted(CARDS_ROOT.glob("*.json")):
            try:
                card = json.loads(path.read_text(encoding="utf-8"))
                cards.append({"card_id": path.stem, "name": card.get("name"), "spec": card.get("spec"), "tags": card.get("tags", [])})
            except (OSError, ValueError):
                continue
    return {"success": True, "cards": cards}


@router.get("/cards/{card_id}")
async def get_card(card_id: str) -> Dict[str, Any]:
    path = _card_path(card_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"未找到角色卡: {card_id}")
    card = json.loads(path.read_text(encoding="utf-8"))
    return {"success": True, "card": card, "composed_prompt": _composed_prompt(card)}


@router.delete("/cards/{card_id}")
async def delete_card(card_id: str) -> Dict[str, Any]:
    path = _card_path(card_id)
    if path.is_file():
        path.unlink()
    return {"success": True}


@router.post("/cards/{card_id}/apply-personality")
async def apply_personality(card_id: str) -> Dict[str, Any]:
    """把角色卡组合文本写入 bot_config.toml 的 personality.personality（tomlkit 原位改写保留注释）。"""
    path = _card_path(card_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"未找到角色卡: {card_id}")
    if not CONFIG_PATH.is_file():
        raise HTTPException(status_code=500, detail="未找到 bot_config.toml")
    card = json.loads(path.read_text(encoding="utf-8"))
    composed = _composed_prompt(card)

    doc = tomlkit.parse(CONFIG_PATH.read_text(encoding="utf-8"))
    personality_table = doc.get("personality")
    if not isinstance(personality_table, dict):
        raise HTTPException(status_code=500, detail='bot_config.toml 缺少 [personality] 配置节')
    personality_table["personality"] = composed
    CONFIG_PATH.write_text(tomlkit.dumps(doc), encoding="utf-8")
    # 记录激活卡：供开场白注入等运行时功能定位当前角色
    CARDS_ROOT.mkdir(parents=True, exist_ok=True)
    ACTIVE_CARD_PATH.write_text(
        json.dumps({"card_id": path.stem, "name": card.get("name"), "first_mes": card.get("first_mes")}, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(f"角色卡已应用为人格设定: {card.get('name')}（需重启生效）")
    return {"success": True, "message": "人格设定已写入 bot_config.toml，重启后生效", "composed_prompt": composed}


# ---------- 世界书 ----------

def _parse_worldbook(payload: Any) -> List[Dict[str, Any]]:
    entries: Any = payload.get("entries") if isinstance(payload, dict) else None
    result: List[Dict[str, Any]] = []
    if isinstance(entries, dict):
        items = entries.values()
    elif isinstance(entries, list):
        items = entries
    else:
        items = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content") or "").strip()
        if not content:
            continue
        keys = entry.get("key") or entry.get("keys") or []
        if isinstance(keys, str):
            keys = [keys]
        result.append(
            {
                "uid": str(entry.get("uid") or entry.get("id") or len(result)),
                "keys": [str(k).strip() for k in keys if str(k).strip()],
                "content": content,
                "enabled": bool(entry.get("disable", False)) is False,
                "comment": str(entry.get("comment") or entry.get("name") or ""),
            }
        )
    return result


@router.post("/worldbooks")
async def upload_worldbook(file: UploadFile = File(...), book_name: str = "worldbook") -> Dict[str, Any]:
    raw = await file.read()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"世界书 JSON 解析失败: {exc}") from None
    entries = _parse_worldbook(payload)
    book_tag = _safe_name(book_name)
    imported = skipped = failed = 0
    errors: List[str] = []
    for entry in entries:
        if not entry["enabled"]:
            skipped += 1
            continue
        keys_prefix = f"（触发词: {', '.join(entry['keys'][:8])}）" if entry["keys"] else ""
        text = f"【世界书·{entry['comment'] or entry['uid']}】{keys_prefix}\n{entry['content']}"
        try:
            result = await memory_service.ingest_text(
                external_id=f"sillytavern_worldbook:{book_tag}:{entry['uid']}",
                source_type="worldbook",
                text=text,
                tags=["sillytavern", "worldbook", book_tag],
            )
            if getattr(result, "success", False):
                imported += 1
            else:
                failed += 1
                errors.append(str(getattr(result, "error_message", "") or f"条目 {entry['uid']} 写入失败"))
        except Exception as exc:
            failed += 1
            errors.append(f"条目 {entry['uid']}: {exc}")
    logger.info(f"世界书导入完成: {book_name} 成功 {imported}/跳过 {skipped}/失败 {failed}")
    # 同步写本地关键词索引，供回复链路做酒馆式触发词扫描
    WORLDBOOK_INDEX_DIR.mkdir(parents=True, exist_ok=True)
    index_path = WORLDBOOK_INDEX_DIR / f"{book_tag}.json"
    index_path.write_text(
        json.dumps(
            [{"uid": f"{book_tag}:{e['uid']}", "keys": e["keys"], "content": e["content"], "comment": e["comment"]} for e in entries if e["enabled"]],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "success": failed == 0,
        "total_entries": len(entries),
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "errors": errors[:10],
    }
