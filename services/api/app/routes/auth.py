"""账号与会话接口：AUTH-01—05（#46 / #55 draft.4）。

五条路由各自写明门禁与 CSRF 的适用性，不用「路由器级依赖」统一套：
``POST /auth/logout`` 必须豁免演示门禁（否则 demo cookie 一过期就登不出去），
而注册/登录在 restricted 部署下**仍要过门禁**，两种策略无法共用一个依赖。

注册/登录都**不消费会话 Cookie**：浏览器的 HttpOnly Cookie 前端删不掉，
若把「带了一个失效会话」当成「提供了无效凭证」，用户一过期就再也登录不上。
这两条是全局「提供但无效一律 401」的显式例外，只适用于这两个操作。
"""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Response

from .. import auth
from ..agents.profile import normalize_profile
from ..errors import ApiProblem
from ..identity import (
    Identity,
    clear_session_cookie,
    client_key,
    require_csrf,
    require_origin,
    require_user,
    session_is_live,
    set_session_cookie,
)
from ..schemas import (
    LoginRequest,
    PasswordChangeRequest,
    RegisterRequest,
    RegisterResponse,
    SessionResponse,
    User,
)
from ..security import GatedRoute

#: 注册/登录/会话恢复/改密：restricted 下先过演示门禁，且**先于**请求体校验
#: （``GatedRoute`` 在解析请求体之前跑；写在 handler 里会被 422 抢先，见 #61 复审）。
router = APIRouter(tags=["auth"], route_class=GatedRoute)
#: 登出豁免门禁：门禁过期后也要能清 Cookie（门禁豁免清单只有它与 GET /health）。
logout_router = APIRouter(tags=["auth"])

INVALID_CREDENTIALS_MESSAGE = "用户名或密码错误。"
SIGNUP_KEY_REJECTED_MESSAGE = "审核员注册密钥无效。"


@router.post(
    "/auth/register",
    status_code=201,
    response_model=RegisterResponse,
    operation_id="registerUser",
    summary="AUTH-01",
)
def register_user(request: Request, body: RegisterRequest) -> RegisterResponse:
    require_origin(request)
    settings = request.app.state.settings
    accounts = request.app.state.accounts
    limiter = request.app.state.login_limiter
    key = client_key(request)

    # 纯字段校验放在限流块外：格式错误不是「凭证错误」，不该消耗失败次数。
    username = auth.normalize_username(body.username)
    if not auth.username_is_valid(username):
        raise ApiProblem(
            422,
            "VALIDATION_ERROR",
            "用户名不合法：3—32 位小写字母、数字、下划线、点或连字符。",
            {"fieldErrors": [{"field": "username", "message": "用户名只允许 a-z、0-9、_ . -。"}]},
        )
    if body.reviewer_signup_key is None and body.enterprise is None:
        # 企业用户必须提交 EnterpriseInput 四要素；审核员可不建企业，留到首次 API-08。
        raise ApiProblem(
            422,
            "VALIDATION_ERROR",
            "企业用户注册必须提供企业信息（企业名、期间、地区、行业）。",
            {"fieldErrors": [{"field": "enterprise", "message": "缺少企业信息。"}]},
        )
    profile = normalize_profile(body.enterprise) if body.enterprise is not None else None

    with limiter.attempt(key):
        role = "enterprise"
        if body.reviewer_signup_key is not None:
            # 密钥错误与「该部署根本没有配置密钥」返回同一个 403 与同一文案，
            # 不回显部署细节，也不告诉调用方密钥是否正确。
            if not _signup_key_matches(body.reviewer_signup_key, settings.reviewer_signup_key):
                raise ApiProblem(403, "ROLE_FORBIDDEN", SIGNUP_KEY_REJECTED_MESSAGE)
            role = "reviewer"

        account = accounts.register(
            username=username,
            password_hash=auth.hash_password(body.password),
            role=role,
            profile=profile,
        )
    return RegisterResponse(
        user=User(
            id=account["id"],
            username=account["username"],
            role=account["role"],
            enterprise_id=account["enterprise_id"],
        )
    )


@router.post(
    "/auth/login",
    response_model=SessionResponse,
    operation_id="loginUser",
    summary="AUTH-02",
)
def login_user(request: Request, response: Response, body: LoginRequest) -> SessionResponse:
    require_origin(request)
    settings = request.app.state.settings
    accounts = request.app.state.accounts
    limiter = request.app.state.login_limiter
    key = client_key(request)
    username = auth.normalize_username(body.username)

    # 限流块从「查账号 + 验密码」开始：慢哈希与失败判定都在名额之内，
    # 并发请求无法在首个失败落账前一起挤进 scrypt。
    with limiter.attempt(key):
        now = datetime.now(timezone.utc)
        account = accounts.find_user_by_username(username)
        if (
            account is None
            or account["status"] != "active"
            or not auth.verify_password(body.password, account["password_hash"])
        ):
            raise ApiProblem(401, "INVALID_CREDENTIALS", INVALID_CREDENTIALS_MESSAGE)

        # 同一浏览器不留两个活会话（也避免会话固定）：请求里带了**有效**旧会话就撤销它；
        # 无效或过期的一律忽略，不当作「提供了无效凭证」。
        _revoke_presented_session(request, accounts, now)

        ttl = timedelta(hours=settings.session_ttl_hours)
        token = auth.new_session_token()
        expires_at = now + ttl
        accounts.create_session(
            user_id=account["id"],
            token_hash=auth.digest(token),
            csrf_hash=auth.digest(auth.csrf_token_for(token)),
            created_at=now,
            expires_at=expires_at,
        )
    set_session_cookie(response, token, settings, max_age=int(ttl.total_seconds()))
    response.headers["Cache-Control"] = "no-store"

    enterprise_id = accounts.enterprise_id_for_user(account["id"])
    return SessionResponse(
        user=User(
            id=account["id"],
            username=account["username"],
            role=account["role"],
            enterprise_id=enterprise_id,
        ),
        expires_at=expires_at,
        csrf_token=auth.csrf_token_for(token),
    )


@logout_router.post("/auth/logout", status_code=204, operation_id="logoutUser", summary="AUTH-03")
def logout_user(request: Request) -> Response:
    # 豁免演示门禁：门禁过期后也要能登出并清 Cookie。
    accounts = request.app.state.accounts
    settings = request.app.state.settings
    token = request.cookies.get(auth.SESSION_COOKIE, "")
    now = datetime.now(timezone.utc)

    if token:
        row = accounts.find_session(auth.digest(token))
        if row is not None and session_is_live(row, settings, now):
            # 先校验后动作：403 时不撤销会话、也不下发 Set-Cookie——
            # 被拒的请求不产生任何状态变化，「登出失败」不会顺手把人踢下线。
            require_origin(request)
            supplied = request.headers.get(auth.CSRF_HEADER, "")
            expected = auth.csrf_token_for(token)
            if not supplied or not hmac.compare_digest(supplied, expected):
                raise ApiProblem(
                    403,
                    "CSRF_REQUIRED",
                    "登出请求缺少或未通过 CSRF 校验，请刷新页面后重试。",
                )
            accounts.revoke_session(row["id"], at=now)

    response = Response(status_code=204)
    clear_session_cookie(response, settings)
    return response


@router.get(
    "/auth/me",
    response_model=SessionResponse,
    operation_id="getCurrentUser",
    summary="AUTH-04",
)
def get_current_user(request: Request, response: Response) -> SessionResponse:
    identity = require_user(request)
    response.headers["Cache-Control"] = "no-store"
    return SessionResponse(
        user=_user_of(identity),
        expires_at=identity.expires_at,
        csrf_token=identity.csrf_token or "",
    )


@router.post(
    "/auth/password",
    status_code=204,
    operation_id="changePassword",
    summary="AUTH-05",
)
def change_password(request: Request, body: PasswordChangeRequest) -> Response:
    identity = require_user(request)
    require_csrf(request, identity)
    accounts = request.app.state.accounts

    account = accounts.get_user(identity.user_id)
    if account is None or not auth.verify_password(
        body.current_password, account["password_hash"]
    ):
        raise ApiProblem(401, "INVALID_CREDENTIALS", "当前密码不正确。")
    accounts.update_password(
        user_id=identity.user_id, password_hash=auth.hash_password(body.new_password)
    )

    response = Response(status_code=204)
    clear_session_cookie(response, request.app.state.settings)
    return response


def _user_of(identity: Identity) -> User:
    assert identity.user_id is not None and identity.username is not None
    return User(
        id=identity.user_id,
        username=identity.username,
        role=identity.role or "enterprise",
        enterprise_id=identity.enterprise_id,
    )


def _revoke_presented_session(request: Request, accounts, now: datetime) -> None:
    token = request.cookies.get(auth.SESSION_COOKIE, "")
    if not token:
        return
    row = accounts.find_session(auth.digest(token))
    if row is not None and session_is_live(row, request.app.state.settings, now):
        accounts.revoke_session(row["id"], at=now)


def _signup_key_matches(supplied: str, expected: str) -> bool:
    """部署没有配置密钥时一律 False（与「密钥错误」同一个 403，不泄露配置状态）。"""

    if not expected:
        return False
    return hmac.compare_digest(supplied, expected)
