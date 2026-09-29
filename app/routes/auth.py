import functools
import json
import logging
import os
import secrets
import urllib.parse
import urllib.request
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from app.utils import get_supabase_client, get_supabase_admin_client, extract_display_name, extract_real_name

# 로깅 설정
logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def get_site_url() -> str:
    """
    사이트 기본 URL을 결정합니다.
    1. 환경 변수 SITE_URL이 설정되어 있으면 최우선 사용
    2. 현재 Flask 요청(request)이 있으면 host 및 X-Forwarded 헤더 기반으로 정확한 URL 산출
    3. 최후 fallback: Azure 기본 배포 URL 또는 http://localhost:5000
    """
    configured_url = os.getenv("SITE_URL")
    if configured_url and configured_url.strip():
        return configured_url.strip().rstrip("/")

    try:
        if request:
            # Azure App Service 환경 감지
            proto = request.headers.get("X-Forwarded-Proto") or request.scheme
            host = request.headers.get("X-Forwarded-Host") or request.host
            if host:
                return f"{proto}://{host}".rstrip("/")
    except Exception:
        pass

    # Azure 호스트 환경변수(WEBSITE_HOSTNAME)가 있는 경우 자동 생성
    website_hostname = os.getenv("WEBSITE_HOSTNAME")
    if website_hostname:
        return f"https://{website_hostname}".rstrip("/")

    return "https://vibe-fashion-002-ghcuezf9b7c4g3fs.koreacentral-01.azurewebsites.net"


def login_required(view):
    """
    로그인 필수 데코레이터:
    Flask session에서 user_id(session['user']['id'])를 확인하고,
    미인증 사용자는 로그인 페이지로 안내 및 리다이렉트합니다.
    """
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        user = session.get("user")
        if not user or not user.get("id"):
            login_url = url_for("auth.login", error="login_required", next=request.path)
            return redirect(login_url)
        return view(**kwargs)
    return wrapped_view


def validate_password_complexity(password: str) -> tuple[bool, str | None]:
    """
    비밀번호 복잡도 검증:
    - 영문 대문자 포함
    - 특수 문자 포함
    - 8자 이상
    """
    if not password:
        return False, "missing_fields"
    if len(password) < 8:
        return False, "password_too_short"
    if not any(c.isupper() for c in password):
        return False, "password_need_uppercase"
    # 특수문자 검증 (!@#$%^&*(),.?":{}|<>[\]/\-_+=~`';)
    special_chars = set("!@#$%^&*(),.?\":{}|<>[]/\\-_+=~`';")
    if not any(c in special_chars for c in password):
        return False, "password_need_special"
    return True, None


def get_error_message(code: str) -> str:
    """
    URL error 파라미터 코드를 한국어 에러 메시지로 변환합니다.
    """
    messages = {
        "email_not_confirmed": "이메일 인증이 완료되지 않았습니다. 메일함의 인증 링크를 클릭하여 인증을 완료해 주세요.",
        "invalid_credentials": "이메일 또는 비밀번호가 일치하지 않습니다.",
        "current_password_incorrect": "현재 비밀번호가 일치하지 않습니다.",
        "missing_fields": "필수 입력 항목을 모두 작성해 주세요.",
        "invalid_email": "올바른 이메일 형식을 입력해 주세요.",
        "password_too_short": "비밀번호는 최소 8자 이상이어야 합니다.",
        "password_need_uppercase": "비밀번호에 최소 1개 이상의 영문 대문자가 포함되어야 합니다.",
        "password_need_special": "비밀번호에 최소 1개 이상의 특수문자(!@#$%^&* 등)가 포함되어야 합니다.",
        "password_mismatch": "비밀번호 확인이 일치하지 않습니다.",
        "invalid_token": "인증 토큰이 유효하지 않거나 만료되었습니다. 다시 시도해 주세요.",
        "token_required": "인증 토큰 또는 링크 정보가 누락되었습니다.",
        "reset_failed": "비밀번호 재설정 처리 중 오류가 발생했습니다. 이메일과 인증코드를 확인해 주세요.",
        "auth_error": "인증 서비스 연결에 실패했습니다. 잠시 후 다시 시도해 주세요.",
        "rate_limit": "이메일 발송 한도를 초과했습니다. 잠시 후(또는 1시간 후) 다시 시도해 주세요.",
        "user_already_exists": "이미 등록된 이메일 주소입니다. 로그인하거나 비밀번호 찾기를 이용해 주세요.",
        "nickname_exists": "이미 다른 회원이 사용 중인 닉네임입니다. 다른 닉네임을 입력해 주세요.",
        "nickname_too_short": "닉네임은 2자 이상이어야 합니다.",
        "name_required": "이름(실명)을 입력해 주세요.",
        "smtp_error": "SMTP 메일 서버 연결에 실패했습니다. Supabase의 SMTP 설정을 확인해 주세요.",
        "naver_not_configured": "네이버 로그인 설정(Client ID/Secret)이 완료되지 않았습니다.",
        "naver_auth_failed": "네이버 로그인 인증에 실패했습니다.",
        "login_required": "로그인이 필요한 서비스입니다.",
        "delete_failed": "회원 탈퇴 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
    }
    return messages.get(code, "요청 처리 중 오류가 발생했습니다.")


def get_success_message(code: str) -> str:
    """
    URL msg / success 파라미터 코드를 한국어 성공 메시지로 변환합니다.
    """
    messages = {
        "signup_sent": "회원가입 인증 메일을 발송했습니다. 메일함을 확인해 주세요.",
        "confirmed": "이메일 인증이 성공적으로 완료되었습니다.",
        "reset_sent": "비밀번호 재설정 링크(및 인증코드)가 이메일로 발송되었습니다. 메일함을 확인해 주세요.",
        "password_changed": "비밀번호가 성공적으로 변경되었습니다. 새 비밀번호로 로그인해 주세요.",
        "name_updated": "이름이 성공적으로 변경되었습니다.",
        "nickname_updated": "닉네임이 성공적으로 변경되었습니다.",
        "email_update_sent": "새 이메일 주소로 인증 메일이 발송되었습니다. 메일함의 링크를 클릭하여 인증을 완료해 주세요.",
        "password_updated": "비밀번호가 성공적으로 변경되었습니다.",
        "logged_out": "정상적으로 로그아웃되었습니다.",
        "account_deleted": "회원 탈퇴가 완료되었습니다. 그동안 이용해 주셔서 감사합니다.",
    }
    return messages.get(code, code)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """
    [1] GET/POST /auth/login - 로그인 폼 + 처리
    - 이메일 미인증 시 error=email_not_confirmed 로 리다이렉트
    - 에러/성공 메시지는 URL 파라미터로 전달 및 한국어 표시
    """
    next_url = request.args.get("next") or request.form.get("next") or ""

    if "user" in session:
        return redirect(next_url or url_for("auth.mypage"))

    # URL 쿼리 파라미터로 전달된 에러 / 성공 메시지 파싱
    error_code = request.args.get("error", "")
    error_msg = get_error_message(error_code) if error_code else None

    msg_code = request.args.get("msg") or request.args.get("success") or ""
    success_msg = get_success_message(msg_code) if msg_code else None

    if request.method == "POST":
        email = request.form.get("email") or request.form.get("login_id", "")
        email = email.strip()
        password = request.form.get("password", "").strip()

        if not email or not password:
            redirect_params = {"error": "missing_fields"}
            if next_url:
                redirect_params["next"] = next_url
            return redirect(url_for("auth.login", **redirect_params))

        supabase = get_supabase_client()
        if not supabase:
            redirect_params = {"error": "auth_error"}
            if next_url:
                redirect_params["next"] = next_url
            return redirect(url_for("auth.login", **redirect_params))

        try:
            auth_response = supabase.auth.sign_in_with_password({
                "email": email,
                "password": password
            })

            if auth_response and auth_response.user:
                user = auth_response.user

                # 이메일 미인증 상태 검증 (email_confirmed_at / confirmed_at 확인)
                is_confirmed = bool(
                    getattr(user, "email_confirmed_at", None)
                    or getattr(user, "confirmed_at", None)
                )

                if not is_confirmed:
                    redirect_params = {"error": "email_not_confirmed", "email": email}
                    if next_url:
                        redirect_params["next"] = next_url
                    return redirect(url_for("auth.login", **redirect_params))

                display_name = extract_display_name(user)
                real_name = extract_real_name(user)

                # profiles 테이블의 닉네임과 이름 조회 (동기화)
                try:
                    p_res = supabase.table("profiles").select("name, real_name").eq("id", str(user.id)).execute()
                    if p_res.data and len(p_res.data) > 0:
                        p_row = p_res.data[0]
                        if p_row.get("name"):
                            display_name = p_row.get("name")
                        if p_row.get("real_name"):
                            real_name = p_row.get("real_name")
                except Exception as pe:
                    logger.warning(f"[로그인 프로필 조회 경고] {pe}")

                # Flask session 저장
                session["user"] = {
                    "id": str(user.id),
                    "email": user.email,
                    "name": display_name,
                    "real_name": real_name or display_name,
                }
                if auth_response.session and getattr(auth_response.session, "access_token", None):
                    session["access_token"] = auth_response.session.access_token
                session.permanent = True

                if next_url and next_url.startswith("/"):
                    return redirect(next_url)
                return redirect(url_for("auth.mypage"))
            else:
                redirect_params = {"error": "invalid_credentials"}
                if next_url:
                    redirect_params["next"] = next_url
                return redirect(url_for("auth.login", **redirect_params))

        except Exception as e:
            logger.error(f"[로그인 실패] 예외 발생: {e}", exc_info=True)
            err_msg_str = str(e).lower()
            if "email not confirmed" in err_msg_str or "email_not_confirmed" in err_msg_str:
                error_param = "email_not_confirmed"
            elif "invalid login credentials" in err_msg_str or "invalid_grant" in err_msg_str:
                error_param = "invalid_credentials"
            else:
                error_param = "auth_error"

            redirect_params = {"error": error_param}
            if email:
                redirect_params["email"] = email
            if next_url:
                redirect_params["next"] = next_url
            return redirect(url_for("auth.login", **redirect_params))

    return render_template(
        "auth/login.html",
        email=request.args.get("email", ""),
        next=next_url,
        error_msg=error_msg,
        success_msg=success_msg
    )


@auth_bp.route("/kakao")
def kakao_login():
    """
    카카오 OAuth 로그인 시작:
    - Supabase signInWithOAuth를 통해 카카오 인증 URL 생성 후 리다이렉트
    - 인증 완료 후 콜백 주소: KAKAO_REDIRECT_URI 환경변수가 있으면 우선 사용, 없으면 {SITE_URL}/auth/confirm
    - PKCE code_verifier를 Flask 세션에 저장하여 콜백 시 검증할 수 있도록 지원
    - prompt='select_account': 탈퇴 후 재가입 또는 재로그인 시 자동 로그인을 방지하고 계정 선택/동의창 표시
    """
    supabase = get_supabase_client()
    if not supabase:
        return redirect(url_for("auth.login", error="auth_error"))

    custom_redirect = os.getenv("KAKAO_REDIRECT_URI")
    if custom_redirect and custom_redirect.strip():
        redirect_to = custom_redirect.strip()
    else:
        site_url = get_site_url()
        redirect_to = f"{site_url}/auth/confirm"

    try:
        res = supabase.auth.sign_in_with_oauth({
            "provider": "kakao",
            "options": {
                "redirect_to": redirect_to,
                "query_params": {
                    "prompt": "select_account"
                }
            }
        })

        # supabase-py가 생성하여 메모리에 저장한 code_verifier를 Flask session에 보관
        code_verifier = supabase.auth._storage.get_item(f"{supabase.auth._storage_key}-code-verifier")
        if code_verifier:
            session["oauth_code_verifier"] = code_verifier
            session.modified = True

        if res and hasattr(res, "url") and res.url:
            return redirect(res.url)
        return redirect(url_for("auth.login", error="auth_error"))
    except Exception as e:
        logger.error(f"[카카오 로그인 시작 오류] {e}", exc_info=True)
        return redirect(url_for("auth.login", error="auth_error"))


@auth_bp.route("/naver")
def naver_login():
    """
    네이버 OAuth 로그인 시작:
    - NAVER_CLIENT_ID, NAVER_REDIRECT_URI를 읽어 네이버 인증 페이지로 리다이렉트
    - CSRF 방지를 위한 state 토큰 생성 및 Flask session 저장
    - auth_type='reprompt': 탈퇴 후 재가입 또는 재로그인 시 기존 동의 세션을 재사용하지 않고 처음부터 권한 동의창 표시
    """
    client_id = os.getenv("NAVER_CLIENT_ID")
    if not client_id or "your_naver" in client_id:
        logger.warning("[네이버 로그인] NAVER_CLIENT_ID 환경변수가 설정되지 않았습니다.")
        return redirect(url_for("auth.login", error="naver_not_configured"))

    custom_redirect = os.getenv("NAVER_REDIRECT_URI")
    if custom_redirect and custom_redirect.strip():
        redirect_uri = custom_redirect.strip()
    else:
        site_url = get_site_url()
        redirect_uri = f"{site_url}/auth/naver/callback"

    state = secrets.token_urlsafe(16)
    session["naver_oauth_state"] = state
    session.modified = True

    params = {
        "response_type": "code",
        "client_id": client_id.strip(),
        "redirect_uri": redirect_uri,
        "state": state,
        "auth_type": "reprompt"
    }
    naver_auth_url = f"https://nid.naver.com/oauth2.0/authorize?{urllib.parse.urlencode(params)}"
    return redirect(naver_auth_url)


@auth_bp.route("/naver/callback")
def naver_callback():
    """
    네이버 OAuth 인증 콜백:
    1. state 검증
    2. 네이버 토큰 발급 API 호출 (code -> access_token)
    3. 네이버 회원 프로필 조회 API 호출
    4. Supabase auth.users 및 profiles 테이블 연동 / 가입 / 세션 등록
    """
    code = request.args.get("code")
    state = request.args.get("state")
    saved_state = session.pop("naver_oauth_state", None)

    if not code or not state or state != saved_state:
        logger.error("[네이버 로그인] state 불일치 또는 code 누락")
        return redirect(url_for("auth.login", error="naver_auth_failed"))

    client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
    client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret or "your_naver" in client_id:
        return redirect(url_for("auth.login", error="naver_not_configured"))

    # 1. 접근 토큰 요청
    token_url = "https://nid.naver.com/oauth2.0/token"
    token_params = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "state": state
    }

    try:
        req = urllib.request.Request(
            f"{token_url}?{urllib.parse.urlencode(token_params)}",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req) as response:
            token_data = json.loads(response.read().decode("utf-8"))

        naver_access_token = token_data.get("access_token")
        if not naver_access_token:
            logger.error(f"[네이버 로그인] 토큰 발급 실패: {token_data}")
            return redirect(url_for("auth.login", error="naver_auth_failed"))

        # 2. 네이버 프로필 정보 조회
        profile_url = "https://openapi.naver.com/v1/nid/me"
        profile_req = urllib.request.Request(
            profile_url,
            headers={
                "Authorization": f"Bearer {naver_access_token}",
                "User-Agent": "Mozilla/5.0"
            }
        )
        with urllib.request.urlopen(profile_req) as profile_res:
            profile_json = json.loads(profile_res.read().decode("utf-8"))

        if profile_json.get("resultcode") != "00":
            logger.error(f"[네이버 로그인] 프로필 조회 실패: {profile_json}")
            return redirect(url_for("auth.login", error="naver_auth_failed"))

        naver_account = profile_json.get("response", {})
        naver_id = naver_account.get("id")
        email = naver_account.get("email") or f"naver_{naver_id[:10]}@naver.com"
        naver_real_name = naver_account.get("name") or ""
        naver_nickname = naver_account.get("nickname") or naver_real_name or email.split("@")[0]
        profile_image = naver_account.get("profile_image")

        nickname = naver_nickname
        real_name = naver_real_name or nickname

        # 3. Supabase 회원 연동 (Admin 클라이언트 활용)
        admin_client = get_supabase_admin_client()
        supabase = get_supabase_client()

        user_id = None
        if admin_client:
            # 이메일로 기존 계정 확인
            existing_users = admin_client.auth.admin.list_users()
            target_user = next((u for u in existing_users if getattr(u, "email", None) == email), None)

            if target_user:
                user_id = str(target_user.id)
                meta = getattr(target_user, "user_metadata", {}) or {}
                nickname = meta.get("nickname") or meta.get("name") or nickname
                real_name = meta.get("real_name") or meta.get("full_name") or real_name
            else:
                # 신규 계정 생성: 이름은 네이버 계정의 실명, 닉네임은 사용자가 나중에 변경 가능
                new_user = admin_client.auth.admin.create_user({
                    "email": email,
                    "email_confirm": True,
                    "user_metadata": {
                        "name": nickname,
                        "nickname": nickname,
                        "real_name": real_name,
                        "full_name": real_name,
                        "picture": profile_image,
                        "avatar_url": profile_image,
                        "provider": "naver"
                    }
                })
                if new_user and new_user.user:
                    user_id = str(new_user.user.id)

            # profiles 테이블 확인 및 동기화
            if user_id:
                try:
                    profile_check = admin_client.table("profiles").select("id, name, real_name").eq("id", user_id).execute()
                    if profile_check.data and len(profile_check.data) > 0:
                        db_row = profile_check.data[0]
                        if db_row.get("name"):
                            nickname = db_row.get("name")
                        if db_row.get("real_name"):
                            real_name = db_row.get("real_name")
                    else:
                        profile_insert_data = {
                            "id": user_id,
                            "email": email,
                            "name": nickname,
                            "avatar_url": profile_image,
                            "role": "customer",
                            "grade": "BRONZE"
                        }
                        try:
                            # real_name 컬럼이 있을 경우 저장
                            admin_client.table("profiles").insert({**profile_insert_data, "real_name": real_name}).execute()
                        except Exception:
                            admin_client.table("profiles").insert(profile_insert_data).execute()
                except Exception as pe:
                    logger.warning(f"[네이버 프로필 테이블 동기화 경고] {pe}")

        # 4. 세션 등록 및 로그인 완료
        session["user"] = {
            "id": user_id or f"naver_{naver_id}",
            "email": email,
            "name": nickname,
            "real_name": real_name,
        }
        session["naver_access_token"] = naver_access_token
        session.permanent = True
        return redirect(url_for("auth.mypage"))

    except Exception as e:
        logger.error(f"[네이버 콜백 처리 오류] {e}", exc_info=True)
        return redirect(url_for("auth.login", error="naver_auth_failed"))


@auth_bp.route("/signup", methods=["GET", "POST"])
@auth_bp.route("/register", methods=["GET", "POST"])
def signup():
    """
    [2] GET/POST /auth/signup - 회원가입 폼 + 처리
    - 가입 성공 시 /auth/signup-complete 페이지 이동
    - SITE_URL을 기반으로 confirm 리다이렉트 URL 설정
    """
    if "user" in session:
        return redirect(url_for("auth.mypage"))

    error_code = request.args.get("error", "")
    error_msg = get_error_message(error_code) if error_code else None

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        real_name = request.form.get("real_name", "").strip()
        nickname = (request.form.get("nickname") or request.form.get("name") or "").strip()
        password = request.form.get("password", "").strip()
        password_confirm = request.form.get("password_confirm", "").strip()

        # 1. 필수값 체크
        if not email or not password:
            return redirect(url_for("auth.signup", error="missing_fields", email=email, real_name=real_name, nickname=nickname))

        if not real_name:
            return redirect(url_for("auth.signup", error="name_required", email=email, real_name=real_name, nickname=nickname))

        if not nickname or len(nickname) < 2:
            return redirect(url_for("auth.signup", error="nickname_too_short", email=email, real_name=real_name, nickname=nickname))

        # 2. 이메일 형식 검증
        if "@" not in email or "." not in email:
            return redirect(url_for("auth.signup", error="invalid_email", email=email, real_name=real_name, nickname=nickname))

        # 3. 비밀번호 복잡도 검증 (영문 대문자 포함 8자 이상, 특수문자 포함)
        is_valid_pw, pw_err_code = validate_password_complexity(password)
        if not is_valid_pw:
            return redirect(url_for("auth.signup", error=pw_err_code, email=email, real_name=real_name, nickname=nickname))

        # 4. 비밀번호 일치 검증
        if password_confirm and password != password_confirm:
            return redirect(url_for("auth.signup", error="password_mismatch", email=email, real_name=real_name, nickname=nickname))

        supabase = get_supabase_client()
        if not supabase:
            return redirect(url_for("auth.signup", error="auth_error", email=email, real_name=real_name, nickname=nickname))

        # 5. 닉네임 중복 검증
        try:
            existing = supabase.table("profiles").select("id").eq("name", nickname).execute()
            if existing.data and len(existing.data) > 0:
                return redirect(url_for("auth.signup", error="nickname_exists", email=email, real_name=real_name, nickname=nickname))
        except Exception as e:
            logger.warning(f"[회원가입 닉네임 중복확인 경고] {e}")

        site_url = get_site_url()
        email_redirect_to = f"{site_url}/auth/confirm"

        try:
            signup_options = {
                "email_redirect_to": email_redirect_to,
                "data": {
                    "name": nickname,
                    "nickname": nickname,
                    "real_name": real_name,
                    "full_name": real_name,
                }
            }

            res = supabase.auth.sign_up({
                "email": email,
                "password": password,
                "options": signup_options
            })

            # 가입 성공 시 /auth/signup-complete 로 리다이렉트
            return redirect(url_for("auth.signup_complete", email=email))

        except Exception as e:
            logger.error(f"[회원가입 오류] {e}", exc_info=True)
            err_str = str(e).lower()
            if "rate limit" in err_str or "over_email_send_rate_limit" in err_str:
                return redirect(url_for("auth.signup", error="rate_limit", email=email, real_name=real_name, nickname=nickname))
            elif "user already registered" in err_str or "already registered" in err_str:
                return redirect(url_for("auth.signup", error="user_already_exists", email=email, real_name=real_name, nickname=nickname))
            elif "smtp" in err_str:
                return redirect(url_for("auth.signup", error="smtp_error", email=email, real_name=real_name, nickname=nickname, detail=str(e)[:100]))
            return redirect(url_for("auth.signup", error="auth_error", email=email, real_name=real_name, nickname=nickname, detail=str(e)[:100]))

    custom_error = request.args.get("detail") if error_code == "auth_error" and request.args.get("detail") else error_msg
    return render_template(
        "auth/register.html",
        email=request.args.get("email", ""),
        real_name=request.args.get("real_name", ""),
        nickname=request.args.get("nickname", "") or request.args.get("name", ""),
        error_msg=custom_error
    )


@auth_bp.route("/signup-complete", methods=["GET"])
def signup_complete():
    """
    [3] GET /auth/signup-complete - "인증 메일을 보냈습니다" 안내 페이지
    """
    email = request.args.get("email", "")
    error_code = request.args.get("error", "")
    error_msg = get_error_message(error_code) if error_code else None

    msg_code = request.args.get("msg", "")
    success_msg = get_success_message(msg_code) if msg_code else None

    return render_template(
        "auth/signup_complete.html",
        email=email,
        error_msg=error_msg,
        success_msg=success_msg
    )


@auth_bp.route("/confirm", methods=["GET"])
def confirm():
    """
    [4] GET /auth/confirm - 이메일 인증 링크 클릭 처리
    - verify_otp 호출 → 성공 시 Flask session 저장 → /mypage
    - token_hash, token 또는 code 등 Supabase 파라미터 지원
    - 파라미터가 URL 해시(#)로 전달된 경우를 대비해 confirm.html 템플릿 렌더링
    """
    token_hash = request.args.get("token_hash")
    token = request.args.get("token")
    otp_type = request.args.get("type", "signup")
    code = request.args.get("code")
    email = request.args.get("email")

    # 만약 서버로 전달된 쿼리 파라미터가 하나도 없다면, Supabase Implicit flow 해시(#access_token=...)일 수 있으므로 confirm.html 렌더링
    if not token_hash and not token and not code:
        return render_template("auth/confirm.html")

    supabase = get_supabase_client()
    if not supabase:
        return redirect(url_for("auth.login", error="auth_error"))

    try:
        auth_response = None

        # 1. token_hash 파라미터가 있는 경우 (Supabase 최신 권장)
        if token_hash:
            auth_response = supabase.auth.verify_otp({
                "token_hash": token_hash,
                "type": otp_type
            })
        # 2. email + token (기존 6자리 또는 토큰 형태)
        elif token and email:
            auth_response = supabase.auth.verify_otp({
                "email": email,
                "token": token,
                "type": otp_type
            })
        # 3. PKCE auth code 형태
        elif code:
            code_verifier = session.pop("oauth_code_verifier", None)
            exchange_params = {"auth_code": code}
            if code_verifier:
                exchange_params["code_verifier"] = code_verifier
            auth_response = supabase.auth.exchange_code_for_session(exchange_params)
        # 4. token만 넘어온 경우 token_hash로 재시도
        elif token:
            auth_response = supabase.auth.verify_otp({
                "token_hash": token,
                "type": otp_type
            })

        if auth_response and auth_response.user:
            user = auth_response.user
            display_name = extract_display_name(user)
            real_name = extract_real_name(user)

            # profiles 테이블 확인 및 동기화
            try:
                p_res = supabase.table("profiles").select("name, real_name").eq("id", str(user.id)).execute()
                if p_res.data and len(p_res.data) > 0:
                    p_row = p_res.data[0]
                    if p_row.get("name"):
                        display_name = p_row.get("name")
                    if p_row.get("real_name"):
                        real_name = p_row.get("real_name")
                else:
                    # 신규 생성 (소셜 또는 이메일 첫 확인 시)
                    admin_client = get_supabase_admin_client()
                    client_to_use = admin_client or supabase
                    profile_payload = {
                        "id": str(user.id),
                        "email": user.email,
                        "name": display_name,
                        "role": "customer",
                        "grade": "BRONZE"
                    }
                    try:
                        client_to_use.table("profiles").insert({**profile_payload, "real_name": real_name}).execute()
                    except Exception:
                        client_to_use.table("profiles").insert(profile_payload).execute()
            except Exception as pe:
                logger.warning(f"[이메일/소셜 인증 후 profiles 동기화 경고] {pe}")

            # 성공 시 Flask session 저장
            session["user"] = {
                "id": str(user.id),
                "email": user.email,
                "name": display_name,
                "real_name": real_name or display_name,
            }
            if auth_response.session and getattr(auth_response.session, "access_token", None):
                session["access_token"] = auth_response.session.access_token
            session.permanent = True

            # 비밀번호 재설정 확인 링크인 경우 새 비밀번호 설정 페이지로 이동
            if otp_type == "recovery":
                return redirect(url_for("auth.reset_password"))

            return redirect(url_for("auth.mypage"))
        else:
            return redirect(url_for("auth.login", error="invalid_token"))

    except Exception as e:
        logger.error(f"[이메일 인증 오류] {e}", exc_info=True)
        return redirect(url_for("auth.login", error="invalid_token"))


@auth_bp.route("/confirm-session", methods=["POST"])
def confirm_session():
    """
    Supabase 해시(#access_token=...) 형태로 브라우저에 도달한 인증 세션을
    서버 Flask 세션에 동기화하는 API
    """
    data = request.get_json(silent=True) or {}
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")

    if not access_token:
        return jsonify({"success": False, "message": "인증 토큰이 전달되지 않았습니다."}), 400

    supabase = get_supabase_client()
    if not supabase:
        return jsonify({"success": False, "message": "인증 서비스 연결 실패"}), 500

    try:
        user_response = supabase.auth.get_user(jwt=access_token)
        if user_response and user_response.user:
            user = user_response.user
            display_name = extract_display_name(user)
            real_name = extract_real_name(user)

            try:
                p_res = supabase.table("profiles").select("name, real_name").eq("id", str(user.id)).execute()
                if p_res.data and len(p_res.data) > 0:
                    p_row = p_res.data[0]
                    if p_row.get("name"):
                        display_name = p_row.get("name")
                    if p_row.get("real_name"):
                        real_name = p_row.get("real_name")
            except Exception as pe:
                logger.warning(f"[해시 세션 profiles 조회 경고] {pe}")

            session["user"] = {
                "id": str(user.id),
                "email": user.email,
                "name": display_name,
                "real_name": real_name or display_name,
            }
            session["access_token"] = access_token
            if refresh_token:
                session["refresh_token"] = refresh_token
            session.permanent = True

            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "message": "유효하지 않은 인증 토큰입니다."}), 400
    except Exception as e:
        logger.error(f"[해시 세션 확인 오류] {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """
    [5] GET/POST /auth/forgot-password - 비밀번호 재설정 메일 발송
    - SITE_URL 기반 리다이렉트 설정
    - reset_password_for_email 호출
    """
    error_code = request.args.get("error", "")
    error_msg = get_error_message(error_code) if error_code else None

    msg_code = request.args.get("msg", "")
    success_msg = get_success_message(msg_code) if msg_code else None

    if request.method == "POST":
        email = request.form.get("email", "").strip()

        if not email:
            return redirect(url_for("auth.forgot_password", error="missing_fields"))

        supabase = get_supabase_client()
        if not supabase:
            return redirect(url_for("auth.forgot_password", error="auth_error"))

        site_url = get_site_url()
        redirect_to = f"{site_url}/auth/reset-password"

        try:
            # Supabase reset_password_for_email 호출
            supabase.auth.reset_password_for_email(
                email,
                options={"redirect_to": redirect_to}
            )
            return redirect(url_for("auth.forgot_password", msg="reset_sent"))
        except Exception as e:
            logger.error(f"[비밀번호 재설정 요청 오류] {e}", exc_info=True)
            return redirect(url_for("auth.forgot_password", error="auth_error"))

    return render_template(
        "auth/forgot_password.html",
        error_msg=error_msg,
        success_msg=success_msg
    )


@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    """
    [6] GET/POST /auth/reset-password - 새 비밀번호 설정
    - 링크 클릭(token_hash 또는 code) 또는 이메일+인증코드 입력을 통한 이메일 인증 기반 비밀번호 재설정
    """
    error_code = request.args.get("error", "")
    error_msg = get_error_message(error_code) if error_code else None

    msg_code = request.args.get("msg", "")
    success_msg = get_success_message(msg_code) if msg_code else None

    supabase = get_supabase_client()

    # GET/POST 시 전달된 파라미터 캡처
    token = request.args.get("token") or request.args.get("token_hash") or request.form.get("token")
    code = request.args.get("code") or request.form.get("code")
    req_type = request.args.get("type", "recovery")
    email = request.args.get("email") or request.form.get("email", "").strip()

    # 링크를 통해 token이나 code로 바로 진입한 경우 세션 인증 교환 시도
    if (code or token) and "user" not in session and supabase:
        try:
            auth_response = None
            if code:
                auth_response = supabase.auth.exchange_code_for_session({"auth_code": code})
            elif token:
                auth_response = supabase.auth.verify_otp({
                    "token_hash": token,
                    "type": req_type
                })

            if auth_response and auth_response.user:
                session["user"] = {
                    "id": str(auth_response.user.id),
                    "email": auth_response.user.email,
                    "name": extract_display_name(auth_response.user),
                    "real_name": extract_real_name(auth_response.user),
                }
                if auth_response.session and getattr(auth_response.session, "access_token", None):
                    session["access_token"] = auth_response.session.access_token
        except Exception as e:
            logger.warning(f"[비밀번호 재설정 토큰 처리 경고] {e}")

    if request.method == "POST":
        password = request.form.get("password", "").strip()
        password_confirm = request.form.get("password_confirm", "").strip()
        otp_token = request.form.get("otp_token", "").strip()

        if not password:
            return redirect(url_for("auth.reset_password", error="missing_fields", email=email))

        # 비밀번호 복잡도 검증
        is_valid_pw, pw_err_code = validate_password_complexity(password)
        if not is_valid_pw:
            return redirect(url_for("auth.reset_password", error=pw_err_code, email=email))

        if password != password_confirm:
            return redirect(url_for("auth.reset_password", error="password_mismatch", email=email))

        # 세션이 없고 직접 이메일과 인증코드를 입력한 경우 verify_otp로 이메일 인증 먼저 수행
        if "user" not in session and otp_token and email and supabase:
            try:
                auth_response = supabase.auth.verify_otp({
                    "email": email,
                    "token": otp_token,
                    "type": "recovery"
                })
                if auth_response and auth_response.user:
                    session["user"] = {
                        "id": str(auth_response.user.id),
                        "email": auth_response.user.email,
                        "name": extract_display_name(auth_response.user),
                        "real_name": extract_real_name(auth_response.user),
                    }
                    if auth_response.session and getattr(auth_response.session, "access_token", None):
                        session["access_token"] = auth_response.session.access_token
            except Exception as e:
                logger.error(f"[이메일 인증코드 검증 실패] {e}", exc_info=True)
                return redirect(url_for("auth.reset_password", error="invalid_token", email=email))

        user_info = session.get("user")
        access_token = session.get("access_token")

        if not user_info and not access_token:
            return redirect(url_for("auth.reset_password", error="token_required", email=email))

        # 1. access_token이 있는 경우 일반 클라이언트 update_user 시도
        updated = False
        if access_token and supabase:
            try:
                supabase.auth._request(
                    "PUT",
                    "user",
                    jwt=access_token,
                    body={"password": password}
                )
                updated = True
            except Exception as e:
                logger.error(f"[사용자 토큰 비밀번호 업데이트 실패] {e}", exc_info=True)

        # 2. 세션에 user id가 있는 경우 admin 클라이언트로 업데이트 시도
        if not updated and user_info and user_info.get("id"):
            admin_client = get_supabase_admin_client()
            if admin_client:
                try:
                    admin_client.auth.admin.update_user_by_id(
                        user_info["id"],
                        {"password": password}
                    )
                    updated = True
                except Exception as e:
                    logger.error(f"[Admin 비밀번호 업데이트 실패] {e}", exc_info=True)

        if updated:
            session.pop("user", None)
            session.pop("access_token", None)
            return redirect(url_for("auth.login", msg="password_changed"))
        else:
            return redirect(url_for("auth.reset_password", error="reset_failed", email=email))

    has_valid_session = bool(session.get("user") or session.get("access_token"))

    return render_template(
        "auth/reset_password.html",
        token=token,
        code=code,
        type=req_type,
        email=email,
        has_valid_session=has_valid_session,
        error_msg=error_msg,
        success_msg=success_msg
    )


@auth_bp.route("/check-nickname", methods=["GET"])
def check_nickname():
    """
    닉네임 중복 확인 API (JSON)
    - GET /auth/check-nickname?nickname=xxx
    """
    nickname = request.args.get("nickname", "").strip()
    if not nickname or len(nickname) < 2:
        return jsonify({"available": False, "message": "닉네임은 2자 이상 입력해 주세요."})

    supabase = get_supabase_client()
    if not supabase:
        return jsonify({"available": False, "message": "인증 서버 연결 실패"}), 500

    try:
        current_user_id = session.get("user", {}).get("id")
        query = supabase.table("profiles").select("id").eq("name", nickname)
        res = query.execute()

        # 본인의 현재 닉네임과 동일한 경우는 사용 가능 처리
        if res.data:
            if current_user_id and len(res.data) == 1 and res.data[0].get("id") == current_user_id:
                return jsonify({"available": True, "message": "현재 사용 중인 본인의 닉네임입니다."})
            return jsonify({"available": False, "message": "이미 사용 중인 닉네임입니다."})
        return jsonify({"available": True, "message": "사용 가능한 닉네임입니다."})
    except Exception as e:
        logger.error(f"[닉네임 중복 확인 오류] {e}", exc_info=True)
        return jsonify({"available": False, "message": "확인 중 오류가 발생했습니다."}), 500


@auth_bp.route("/mypage")
@login_required
def mypage():
    """
    마이페이지:
    - login_required 데코레이터 적용
    - 회원 정보 표시
    """
    error_code = request.args.get("error", "")
    error_msg = get_error_message(error_code) if error_code else None

    msg_code = request.args.get("msg") or request.args.get("success") or ""
    success_msg = get_success_message(msg_code) if msg_code else None

    user_info = session.get("user")
    supabase = get_supabase_client()
    if user_info and supabase:
        try:
            p_res = supabase.table("profiles").select("name, real_name").eq("id", user_info["id"]).execute()
            if p_res.data and len(p_res.data) > 0:
                p_row = p_res.data[0]
                if p_row.get("name"):
                    user_info["name"] = p_row.get("name")
                if p_row.get("real_name"):
                    user_info["real_name"] = p_row.get("real_name")
                session["user"] = user_info
                session.modified = True
        except Exception as pe:
            logger.warning(f"[마이페이지 프로필 최신화 경고] {pe}")

    return render_template(
        "mypage.html",
        user=session.get("user"),
        error_msg=error_msg,
        success_msg=success_msg
    )


@auth_bp.route("/update-profile", methods=["POST"])
@login_required
def update_profile():
    """
    개인정보 수정:
    - 아이디(UUID)는 변경 불가
    - 이름(실명) 변경 시: profiles 및 auth.users 메타데이터 수정
    - 닉네임 변경 시: 다른 회원과 중복 체크 후 profiles 및 auth.users 메타데이터 수정
    - 이메일 변경 시: Supabase auth.update_user(email=new_email)를 통해 새 이메일로 인증 메일 발송 및 재인증 요구
    """
    user_info = session.get("user")
    if not user_info or not user_info.get("id"):
        return redirect(url_for("auth.login", error="login_required"))

    user_id = user_info["id"]
    action = request.form.get("action", "")
    supabase = get_supabase_client()
    admin_client = get_supabase_admin_client()

    if not supabase:
        return redirect(url_for("auth.mypage", error="auth_error"))

    # 1. 이름(실명) 변경 처리
    if action == "update_name":
        new_name = request.form.get("name", "").strip()
        if not new_name:
            return redirect(url_for("auth.mypage", error="name_required"))

        # profiles 테이블 업데이트
        try:
            client_to_use = admin_client or supabase
            try:
                client_to_use.table("profiles").update({"real_name": new_name}).eq("id", user_id).execute()
            except Exception as pe:
                logger.warning(f"[profiles real_name 컬럼 업데이트 시도 실패] {pe}")
        except Exception as e:
            logger.error(f"[profiles 이름 변경 실패] {e}", exc_info=True)

        # auth.users 메타데이터 업데이트
        if admin_client:
            try:
                admin_client.auth.admin.update_user_by_id(
                    user_id,
                    {"user_metadata": {"real_name": new_name, "full_name": new_name}}
                )
            except Exception as e:
                logger.warning(f"[auth.users 메타데이터 이름 변경 실패] {e}")

        session["user"]["real_name"] = new_name
        session.modified = True
        return redirect(url_for("auth.mypage", msg="name_updated"))

    # 2. 닉네임 변경 처리
    elif action == "update_nickname":
        new_nickname = request.form.get("nickname", "").strip()
        if not new_nickname or len(new_nickname) < 2:
            return redirect(url_for("auth.mypage", error="nickname_too_short"))

        # 중복 닉네임 체크 (다른 회원과 중복 여부)
        try:
            check_res = supabase.table("profiles").select("id").eq("name", new_nickname).execute()
            if check_res.data:
                # 본인의 기존 닉네임이 아닌 다른 회원이 이미 쓰고 있는 경우
                other_users = [u for u in check_res.data if u.get("id") != user_id]
                if other_users:
                    return redirect(url_for("auth.mypage", error="nickname_exists"))
        except Exception as e:
            logger.warning(f"[닉네임 중복 확인 예외] {e}")

        # profiles 테이블 업데이트
        try:
            if admin_client:
                admin_client.table("profiles").update({"name": new_nickname}).eq("id", user_id).execute()
            else:
                supabase.table("profiles").update({"name": new_nickname}).eq("id", user_id).execute()
        except Exception as e:
            logger.error(f"[profiles 닉네임 변경 실패] {e}", exc_info=True)

        # auth.users 메타데이터 업데이트
        if admin_client:
            try:
                admin_client.auth.admin.update_user_by_id(
                    user_id,
                    {"user_metadata": {"nickname": new_nickname, "name": new_nickname}}
                )
            except Exception as e:
                logger.warning(f"[auth.users 메타데이터 닉네임 변경 실패] {e}")

        # 세션 정보 갱신
        session["user"]["name"] = new_nickname
        session.modified = True
        return redirect(url_for("auth.mypage", msg="nickname_updated"))

    # 2. 이메일 변경 처리 (새 이메일로 인증 메일 발송)
    elif action == "update_email":
        new_email = request.form.get("email", "").strip()
        if not new_email or "@" not in new_email or "." not in new_email:
            return redirect(url_for("auth.mypage", error="invalid_email"))

        if new_email.lower() == user_info.get("email", "").lower():
            flash("현재 사용 중인 이메일과 동일합니다.", "info")
            return redirect(url_for("auth.mypage"))

        site_url = get_site_url()
        email_redirect_to = f"{site_url}/auth/confirm"
        access_token = session.get("access_token")

        try:
            # access_token이 있으면 일반 클라이언트로 이메일 변경 요청 (Supabase가 변경 확인 이메일 자동 발송)
            if access_token:
                supabase.auth._request(
                    "PUT",
                    "user",
                    jwt=access_token,
                    body={"email": new_email},
                    options={"email_redirect_to": email_redirect_to}
                )
            elif admin_client:
                # access_token이 없는 경우 admin_client로 변경 요청
                admin_client.auth.admin.update_user_by_id(
                    user_id,
                    {"email": new_email}
                )
                # profiles 테이블도 동기화
                admin_client.table("profiles").update({"email": new_email}).eq("id", user_id).execute()

            return redirect(url_for("auth.mypage", msg="email_update_sent"))
        except Exception as e:
            logger.error(f"[이메일 변경 요청 실패] {e}", exc_info=True)
            err_str = str(e).lower()
            if "already registered" in err_str:
                return redirect(url_for("auth.mypage", error="user_already_exists"))
            return redirect(url_for("auth.mypage", error="auth_error"))

    # 3. 비밀번호 변경 처리 (현재 비밀번호 확인 + 새 비밀번호 복잡도/일치 검증)
    elif action == "update_password":
        current_password = request.form.get("current_password", "").strip()
        new_password = request.form.get("new_password", "").strip()
        new_password_confirm = request.form.get("new_password_confirm", "").strip()

        if not current_password or not new_password:
            return redirect(url_for("auth.mypage", error="missing_fields"))

        # 비밀번호 확인 일치 여부
        if new_password != new_password_confirm:
            return redirect(url_for("auth.mypage", error="password_mismatch"))

        # 새 비밀번호 복잡도 검증 (대문자, 특수문자, 8자 이상)
        is_valid_pw, pw_err_code = validate_password_complexity(new_password)
        if not is_valid_pw:
            return redirect(url_for("auth.mypage", error=pw_err_code))

        # 현재 비밀번호가 맞는지 확인 (기존 이메일 + 현재 비밀번호로 재인증 시도)
        user_email = user_info.get("email")
        if not user_email:
            return redirect(url_for("auth.mypage", error="auth_error"))

        try:
            verify_res = supabase.auth.sign_in_with_password({
                "email": user_email,
                "password": current_password
            })
            if not verify_res or not verify_res.user:
                return redirect(url_for("auth.mypage", error="current_password_incorrect"))
        except Exception as e:
            logger.warning(f"[현재 비밀번호 확인 실패] {e}")
            return redirect(url_for("auth.mypage", error="current_password_incorrect"))

        # 비밀번호 변경 적용
        access_token = getattr(verify_res.session, "access_token", None) or session.get("access_token")
        updated = False

        if access_token:
            try:
                supabase.auth._request(
                    "PUT",
                    "user",
                    jwt=access_token,
                    body={"password": new_password}
                )
                updated = True
            except Exception as e:
                logger.error(f"[사용자 토큰 비밀번호 변경 실패] {e}", exc_info=True)

        if not updated and admin_client:
            try:
                admin_client.auth.admin.update_user_by_id(
                    user_id,
                    {"password": new_password}
                )
                updated = True
            except Exception as e:
                logger.error(f"[Admin 비밀번호 변경 실패] {e}", exc_info=True)

        if updated:
            return redirect(url_for("auth.mypage", msg="password_updated"))
        else:
            return redirect(url_for("auth.mypage", error="reset_failed"))

    return redirect(url_for("auth.mypage"))


@auth_bp.route("/delete-account", methods=["POST"])
@login_required
def delete_account():
    """
    회원 탈퇴:
    - 소셜 연동 해제 (카카오/네이버 연동 해제 API 호출)
    - 로그인된 본인 계정(profiles 및 auth.users)을 삭제하고 세션 종료
    """
    user_info = session.get("user")
    if not user_info or not user_info.get("id"):
        return redirect(url_for("auth.login", error="login_required"))

    user_id = user_info["id"]
    supabase = get_supabase_client()
    admin_client = get_supabase_admin_client()

    # 1. 소셜 로그인 연결 해제 (카카오 / 네이버)
    # (1) 카카오 연결 끊기 (/v1/user/unlink)
    kakao_admin_key = os.getenv("KAKAO_ADMIN_KEY") or os.getenv("KAKAO_REST_API_KEY")
    if admin_client and kakao_admin_key:
        try:
            target_user = admin_client.auth.admin.get_user_by_id(user_id)
            if target_user and getattr(target_user, "user", None):
                user_obj = target_user.user
                identities = getattr(user_obj, "identities", []) or []
                kakao_identity = next((i for i in identities if getattr(i, "provider", "") == "kakao"), None)
                if kakao_identity:
                    identity_data = getattr(kakao_identity, "identity_data", {}) or {}
                    kakao_user_id = getattr(kakao_identity, "id", None) or identity_data.get("sub") or identity_data.get("provider_id")
                    if kakao_user_id:
                        unlink_req = urllib.request.Request(
                            "https://kapi.kakao.com/v1/user/unlink",
                            data=urllib.parse.urlencode({"target_id_type": "user_id", "target_id": kakao_user_id}).encode("utf-8"),
                            headers={
                                "Authorization": f"KakaoAK {kakao_admin_key.strip()}",
                                "Content-Type": "application/x-www-form-urlencoded"
                            }
                        )
                        with urllib.request.urlopen(unlink_req) as resp:
                            logger.info(f"[카카오 회원 탈퇴 연동 해제 성공] status: {resp.status}")
        except Exception as ke:
            logger.warning(f"[카카오 탈퇴 연동 해제 경고] {ke}")

    # (2) 네이버 연동 해제 (service_delete)
    naver_access_token = session.get("naver_access_token")
    naver_client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
    naver_client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()
    if naver_access_token and naver_client_id and naver_client_secret:
        try:
            delete_params = {
                "grant_type": "delete",
                "client_id": naver_client_id,
                "client_secret": naver_client_secret,
                "access_token": naver_access_token,
                "service_provider": "NAVER"
            }
            del_req = urllib.request.Request(
                f"https://nid.naver.com/oauth2.0/token?{urllib.parse.urlencode(delete_params)}",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(del_req) as del_resp:
                logger.info(f"[네이버 회원 탈퇴 연동 해제 응답] {del_resp.read().decode('utf-8')}")
        except Exception as ne:
            logger.warning(f"[네이버 탈퇴 연동 해제 경고] {ne}")

    try:
        # 2. profiles 및 연관 데이터 정리
        # profiles 테이블 데이터 삭제
        if admin_client:
            try:
                admin_client.table("profiles").delete().eq("id", user_id).execute()
            except Exception as e:
                logger.warning(f"[프로필 삭제 경고] {e}")

            # auth.users 계정 삭제
            admin_client.auth.admin.delete_user(user_id)
        elif supabase:
            try:
                supabase.table("profiles").delete().eq("id", user_id).execute()
            except Exception as e:
                logger.warning(f"[프로필 삭제 경고] {e}")

        # 3. 세션 정리
        session.clear()
        return redirect(url_for("auth.login", msg="account_deleted"))

    except Exception as e:
        logger.error(f"[회원 탈퇴 처리 실패] {e}", exc_info=True)
        flash("회원 탈퇴 처리 중 오류가 발생했습니다.", "danger")
        return redirect(url_for("auth.mypage"))


@auth_bp.route("/logout")
def logout():
    """
    로그아웃: 세션 클리어 후 로그인 페이지로 이동
    """
    session.pop("user", None)
    session.pop("access_token", None)
    return redirect(url_for("auth.login", msg="logged_out"))

