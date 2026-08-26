from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request

from .core import check_auth_rate_limit, get_current_token, is_token_valid


async def require_auth(
    request: Request,
    maibot_session: Optional[str] = Cookie(None),
) -> str:
    """
    FastAPI 依赖：要求有效认证 + 认证频率限制。

    限流内嵌于此依赖，所有引用 require_auth 的路由统一获得防暴力破解保护：
    每个 IP 每分钟最多 10 次认证失败请求，连续失败 5 次封禁 10 分钟。

    Raises:
        HTTPException 401: 认证失败
        HTTPException 429: 请求过于频繁
    """
    if not is_token_valid(maibot_session):
        # 仅对失败请求计数限流，成功请求不受影响
        await check_auth_rate_limit(request)
        raise HTTPException(status_code=401, detail="Token 无效或已过期")
    assert maibot_session is not None
    return maibot_session


async def require_auth_with_rate_limit(
    request: Request,
    maibot_session: Optional[str] = Cookie(None),
    _rate_limit: None = Depends(check_auth_rate_limit),
) -> str:
    """
    FastAPI 依赖：要求有效认证 + 频率限制

    组合了认证检查和频率限制，适用于敏感操作

    Returns:
        验证通过的 token

    Raises:
        HTTPException 401: 认证失败
        HTTPException 429: 请求过于频繁
    """
    return get_current_token(maibot_session)


def get_optional_token(
    maibot_session: Optional[str] = Cookie(None),
) -> Optional[str]:
    """
    FastAPI 依赖：可选获取 token（不验证）

    用于某些需要知道是否有 token 但不强制验证的场景

    Returns:
        token 字符串或 None
    """
    return maibot_session or None


async def verify_token_optional(
    maibot_session: Optional[str] = Cookie(None),
) -> bool:
    """
    FastAPI 依赖：可选验证 token

    返回 token 是否有效，不抛出异常

    Returns:
        True 如果 token 有效，否则 False
    """
    return is_token_valid(maibot_session)
