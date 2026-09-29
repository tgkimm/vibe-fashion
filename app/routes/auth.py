import functools
import logging
import os
import re
from urllib.parse import urlencode
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from supabase import create_client, Client

# 로깅 설정
logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def get_site_url() -> str:
    """
    사이트 기본 URL을 결정합니다.
    1. 환경 변수 SITE_URL이 명시적으로 설정되어 있으면 우선 사용
    2. 그렇지 않고 현재 Flask 요청(request) 컨텍스트가 있으면 실제 유입된 host_url (예: Azure 배포 주소) 사용
    3. 최후 fallback: os.getenv("SITE_URL", "http://localhost:5000")
    """
    configured_url = os.getenv("SITE_URL")
    if configured_url and configured_url.strip():
        return configured_url.strip().rstrip("/")

    try:
        if request and request.host_url:
            return request.host_url.rstrip("/")
    except Exception:
        pass

    return os.getenv("SITE_URL", "http://localhost:5000").rstrip("/")


def get_supabase_client() -> Client | None:
    """
    환경변수에서 SUPABASE_URL과 SUPABASE_ANON_KEY를 읽어
    Supabase 클라이언트를 초기화하여 반환합니다.
    """
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        logger.error("[Supabase 오류] SUPABASE_URL 또는 SUPABASE_ANON_KEY 환경변수가 설정되지 않았습니다.")
        return None

    try:
        return create_client(supabase_url, supabase_key)
    except Exception as e:
        logger.error(f"[Supabase 연결 실패] 클라이언트 초기화 중 예외 발생: {e}", exc_info=True)
        return None


def get_supabase_admin_client() -> Client | None:
    """
    관리자 작업(사용자 검증, 비밀번호 강제 업데이트 등)을 위한 Service Role Supabase 클라이언트를 초기화하여 반환합니다.
    """
    supabase_url = os.getenv("SUPABASE_URL")
    service_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not service_key:
        return None

    try:
        return create_client(supabase_url, service_key)
    except Exception as e:
        logger.error(f"[Supabase Admin 클라이언트 오류] {e}", exc_info=True)
        return None


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


def get_error_message(code: str) -> str:
    """
    URL error 파라미터 코드를 한국어 에러 메시지로 변환합니다.
    """
    messages = {
        "email_not_confirmed": "이메일 인증이 완료되지 않았습니다. 메일함의 인증 링크를 확인해 주세요.",
        "invalid_credentials": "이메일 또는 비밀번호가 일치하지 않습니다.",
        "missing_fields": "필수 입력 항목을 모두 작성해 주세요.",
        "invalid_email": "올바른 이메일 형식을 입력해 주세요.",
        "password_too_short": "비밀번호는 최소 6자 이상이어야 합니다.",
        "password_mismatch": "비밀번호 확인이 일치하지 않습니다.",
        "invalid_token": "인증 토큰이 유효하지 않거나 만료되었습니다.",
        "token_required": "인증 토큰 또는 링크 정보가 누락되었습니다.",
        "reset_failed": "비밀번호 재설정 처리 중 오류가 발생했습니다.",
        "auth_error": "인증 서비스 연결에 실패했습니다. 잠시 후 다시 시도해 주세요.",
        "login_required": "로그인이 필요한 서비스입니다.",
    }
    return messages.get(code, "요청 처리 중 오류가 발생했습니다.")


def get_success_message(code: str) -> str:
    """
    URL msg / success 파라미터 코드를 한국어 성공 메시지로 변환합니다.
    """
    messages = {
        "signup_sent": "회원가입 인증 메일을 발송했습니다. 메일함을 확인해 주세요.",
        "confirmed": "이메일 인증이 성공적으로 완료되었습니다.",
        "reset_sent": "비밀번호 재설정 링크가 이메일로 발송되었습니다.",
        "password_changed": "비밀번호가 성공적으로 변경되었습니다. 새 비밀번호로 로그인해 주세요.",
        "logged_out": "정상적으로 로그아웃되었습니다.",
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
                user_metadata = getattr(user, "user_metadata", {}) or {}
                display_name = (
                    user_metadata.get("name")
                    or user_metadata.get("full_name")
                    or (user.email.split("@")[0] if user.email else "회원")
                )

                # Flask session 저장
                session["user"] = {
                    "id": str(user.id),
                    "email": user.email,
                    "name": display_name,
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
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "").strip()
        password_confirm = request.form.get("password_confirm", "").strip()

        # 1. 필수값 체크
        if not email or not password:
            return redirect(url_for("auth.signup", error="missing_fields", email=email, name=name))

        # 2. 이메일 형식 검증
        if "@" not in email or "." not in email:
            return redirect(url_for("auth.signup", error="invalid_email", email=email, name=name))

        # 3. 비밀번호 길이 검증
        if len(password) < 6:
            return redirect(url_for("auth.signup", error="password_too_short", email=email, name=name))

        # 4. 비밀번호 일치 검증
        if password_confirm and password != password_confirm:
            return redirect(url_for("auth.signup", error="password_mismatch", email=email, name=name))

        supabase = get_supabase_client()
        if not supabase:
            return redirect(url_for("auth.signup", error="auth_error", email=email, name=name))

        site_url = get_site_url()
        email_redirect_to = f"{site_url}/auth/confirm"

        try:
            signup_options = {
                "email_redirect_to": email_redirect_to,
                "data": {
                    "name": name or email.split("@")[0],
                    "full_name": name or email.split("@")[0],
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
            return redirect(url_for("auth.signup", error="auth_error", email=email, name=name))

    return render_template(
        "auth/register.html",
        email=request.args.get("email", ""),
        name=request.args.get("name", ""),
        error_msg=error_msg
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
    """
    token_hash = request.args.get("token_hash")
    token = request.args.get("token")
    otp_type = request.args.get("type", "signup")
    code = request.args.get("code")
    email = request.args.get("email")

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
            auth_response = supabase.auth.exchange_code_for_session({
                "auth_code": code
            })
        # 4. token만 넘어온 경우 token_hash로 재시도
        elif token:
            auth_response = supabase.auth.verify_otp({
                "token_hash": token,
                "type": otp_type
            })
        else:
            return redirect(url_for("auth.login", error="token_required"))

        if auth_response and auth_response.user:
            user = auth_response.user
            user_metadata = getattr(user, "user_metadata", {}) or {}
            display_name = (
                user_metadata.get("name")
                or user_metadata.get("full_name")
                or (user.email.split("@")[0] if user.email else "회원")
            )

            # 성공 시 Flask session 저장
            session["user"] = {
                "id": str(user.id),
                "email": user.email,
                "name": display_name,
            }
            if auth_response.session and getattr(auth_response.session, "access_token", None):
                session["access_token"] = auth_response.session.access_token
            session.permanent = True

            # 비밀번호 재설정 확인 링크인 경우 새 비밀번호 설정 페이지로 분기 가능
            if otp_type == "recovery":
                return redirect(url_for("auth.reset_password"))

            return redirect(url_for("auth.mypage"))
        else:
            return redirect(url_for("auth.login", error="invalid_token"))

    except Exception as e:
        logger.error(f"[이메일 인증 오류] {e}", exc_info=True)
        return redirect(url_for("auth.login", error="invalid_token"))


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
    - Supabase 이메일 링크를 통해 유입되거나 세션이 있는 경우 새 비밀번호 적용
    - URL 파라미터에 token_hash, code 등이 포함된 경우 세션 교환 또는 update_user 처리
    """
    error_code = request.args.get("error", "")
    error_msg = get_error_message(error_code) if error_code else None

    msg_code = request.args.get("msg", "")
    success_msg = get_success_message(msg_code) if msg_code else None

    supabase = get_supabase_client()

    # GET 요청 시 전달된 파라미터 캡처
    token = request.args.get("token") or request.args.get("token_hash") or request.form.get("token")
    code = request.args.get("code") or request.form.get("code")
    req_type = request.args.get("type", "recovery")

    # 만약 code나 token이 있고 세션이 아직 없다면 검증 시도
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
                    "name": (getattr(auth_response.user, "user_metadata", {}) or {}).get("name", "회원"),
                }
                if auth_response.session and getattr(auth_response.session, "access_token", None):
                    session["access_token"] = auth_response.session.access_token
        except Exception as e:
            logger.warning(f"[비밀번호 재설정 토큰 처리 경고] {e}")

    if request.method == "POST":
        password = request.form.get("password", "").strip()
        password_confirm = request.form.get("password_confirm", "").strip()

        if not password:
            return redirect(url_for("auth.reset_password", error="missing_fields"))

        if len(password) < 6:
            return redirect(url_for("auth.reset_password", error="password_too_short"))

        if password != password_confirm:
            return redirect(url_for("auth.reset_password", error="password_mismatch"))

        user_info = session.get("user")
        access_token = session.get("access_token")

        # 1. access_token이 있는 경우 일반 클라이언트 update_user 시도
        updated = False
        if access_token and supabase:
            try:
                # 직접 Authorization 헤더로 user 업데이트
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
            return redirect(url_for("auth.reset_password", error="reset_failed"))

    return render_template(
        "auth/reset_password.html",
        token=token,
        code=code,
        type=req_type,
        error_msg=error_msg,
        success_msg=success_msg
    )


@auth_bp.route("/mypage")
@login_required
def mypage():
    """
    마이페이지:
    - login_required 데코레이터 적용
    - 회원 정보 표시
    """
    return render_template("mypage.html", user=session.get("user"))


@auth_bp.route("/logout")
def logout():
    """
    로그아웃: 세션 클리어 후 로그인 페이지로 이동
    """
    session.pop("user", None)
    session.pop("access_token", None)
    return redirect(url_for("auth.login", msg="logged_out"))

