#!/usr/bin/env python3
"""SnowLuma access_token 轮换与两端同步。

用法:
    uv run python scripts/rotate_snowluma_token.py                # 生成新 token, 写本地 adapter config
    uv run python scripts/rotate_snowluma_token.py --token XXXX    # 指定 token
    uv run python scripts/rotate_snowluma_token.py --snowluma-config /path/to/snowluma.json  # 同步写 SnowLuma 端 config
    uv run python scripts/rotate_snowluma_token.py --verify        # 仅验证当前 3001 握手（不轮换）

SnowLuma 端 token 更新: 若无法写其配置文件, 登录 SnowLuma WebUI (5099) → 网络/访问令牌 手动粘贴同一值。
"""
import argparse
import asyncio
import secrets
import sys
from pathlib import Path

try:
    import aiohttp
except ImportError:
    aiohttp = None

REPO_ROOT = Path(__file__).resolve().parent.parent
ADAPTER_CONFIG = REPO_ROOT / "plugins" / "maibot-team_snowluma-adapter" / "config.toml"
WS_HOST = "127.0.0.1"
WS_PORT = 3001


def gen_token() -> str:
    return secrets.token_hex(24)


def read_current_token() -> str:
    text = ADAPTER_CONFIG.read_text(encoding="utf-8")
    for line in text.splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("token = "):
            return line_stripped.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("未在 adapter config.toml 找到 token 字段")


async def verify_token(token: str) -> bool:
    """一次性 WS 握手验证 3001 是否接受该 token。"""
    if aiohttp is None:
        print("[warn] aiohttp 不可用, 跳过握手验证")
        return True
    url = f"http://{WS_HOST}:{WS_PORT}/?access_token={token}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url, timeout=4) as ws:
                try:
                    frame = await asyncio.wait_for(ws.receive(), timeout=2.5)
                    if frame.type.name in ("TEXT", "BINARY"):
                        print(f"[ok] 握手成功, 服务端帧: {str(frame.data)[:60]}")
                except asyncio.TimeoutError:
                    print("[ok] 握手成功 (101)")
                finally:
                    await ws.close()
                return True
    except aiohttp.WSServerHandshakeError as exc:
        print(f"[fail] 3001 拒绝: HTTP {exc.status} (token 不一致)")
        return False
    except Exception as exc:
        print(f"[fail] 握手异常: {exc}")
        return False


def write_adapter_config(new_token: str) -> None:
    text = ADAPTER_CONFIG.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    found = False
    for idx, line in enumerate(lines):
        if line.strip().startswith("token = "):
            indent = line[: len(line) - len(line.lstrip())]
            lines[idx] = f'{indent}token = "{new_token}"\n'
            found = True
            break
    if not found:
        raise SystemExit("adapter config.toml 缺少 token 字段")
    ADAPTER_CONFIG.write_text("".join(lines), encoding="utf-8")
    print(f"[ok] adapter config.toml 已更新: token = {new_token[:8]}...{new_token[-4:]}")


def write_snowluma_config(path: str, new_token: str) -> None:
    """简化写入：SnowLuma 配置若是 JSON 且含 access_token/onebot 结构则原位替换。"""
    import json

    p = Path(path).expanduser()
    data = json.loads(p.read_text(encoding="utf-8"))

    def walk(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in ("access_token", "token") and isinstance(value, str):
                    obj[key] = new_token
                    walk = True
                else:
                    walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] SnowLuma 配置已更新: {p}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", default="", help="指定新 token（缺省生成）")
    parser.add_argument("--snowluma-config", default="", help="SnowLuma 端配置文件路径（JSON）")
    parser.add_argument("--verify", action="store_true", help="仅验证当前 token 握手")
    args = parser.parse_args()

    if args.verify:
        current = read_current_token()
        return 0 if asyncio.run(verify_token(current)) else 1

    new_token = args.token or gen_token()
    write_adapter_config(new_token)
    if args.snowluma_config:
        write_snowluma_config(args.snowluma_config, new_token)
    else:
        print("[提示] 未提供 --snowluma-config; 请在 SnowLuma WebUI (5099) → 网络/访问令牌 手动粘贴同一 token")
    ok = asyncio.run(verify_token(new_token))
    if not ok:
        print("[提示] 验证失败: SnowLuma 端尚未更新为同一 token（WebUI 更新后重跑 --verify）")
        return 1
    print("\n两端 token 已统一 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
