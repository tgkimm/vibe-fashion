import functools
import logging
import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from app.utils import get_supabase_client, get_supabase_admin_client, extract_display_name

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


def get_error_message(code: str) -> str:
    """
    URL error 파라미터 코드를 한국어 에러 메시지로 변환합니다.
    """
    messages = {
        "email_not_confirmed": "이메일 인증이 완료되지 않았습니다. 메일함의 인증 링크를 클릭하여 인증을 완료해 주세요.",
        "invalid_credentials": "이메일 또는 비밀번호가 일치하지 않습니다.",
        "missing_fields": "필수 입력 항목을 모두 작성해 주세요.",
        "invalid_email": "올바른 이메일 형식을 입력해 주세요.",
        "password_too_short": "비밀번호는 최소 6자 이상이어야 합니다.",
        "password_mismatch": "비밀번호 확인이 일치하지 않습니다.",
        "invalid_token": "인증 토큰이 유효하지 않거나 만료되었습니다. 다시 시도해 주세요.",
        "token_required": "인증 토큰 또는 링크 정보가 누락되었습니다.",
        "reset_failed": "비밀번호 재설정 처리 중 오류가 발생했습니다. 이메일과 인증코드를 확인해 주세요.",
        "auth_error": "인증 서비스 연결에 실패했습니다. 잠시 후 다시 시도해 주세요.",
        "rate_limit": "이메일 발송 한도를 초과했습니다. 잠시 후(또는 1시간 후) 다시 시도해 주세요.",
        "user_already_exists": "이미 등록된 이메일 주소입니다. 로그인하거나 비밀번호 찾기를 이용해 주세요.",
        "nickname_exists": "이미 다른 회원이 사용 중인 닉네임입니다. 다른 닉네임을 입력해 주세요.",
        "nickname_too_short": "닉네임은 2자 이상이어야 합니다.",
        "smtp_error": "SMTP 메일 서버 연결에 실패했습니다. Supabase의 SMTP 설정을 확인해 주세요.",
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
        "nickname_updated": "닉네임이 성공적으로 변경되었습니다.",
        "email_update_sent": "새 이메일 주소로 인증 메일이 발송되었습니다. 메일함의 링크를 클릭하여 인증을 완료해 주세요.",
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


@auth_bp.route("/kakao")
def kakao_login():
    """
    카카오 OAuth 로그인 시작:
    - Supabase signInWithOAuth를 통해 카카오 인증 URL 생성 후 리다이렉트
    - 인증 완료 후 콜백 주소: {SITE_URL}/auth/confirm
    """
    supabase = get_supabase_client()
    if not supabase:
        return redirect(url_for("auth.login", error="auth_error"))

    site_url = get_site_url()
    redirect_to = f"{site_url}/auth/confirm"

    try:
        res = supabase.auth.sign_in_with_oauth({
            "provider": "kakao",
            "options": {
                "redirect_to": redirect_to
            }
        })
        if res and hasattr(res, "url") and res.url:
            return redirect(res.url)
        return redirect(url_for("auth.login", error="auth_error"))
    except Exception as e:
        logger.error(f"[카카오 로그인 시작 오류] {e}", exc_info=True)
        return redirect(url_for("auth.login", error="auth_error"))


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

        # 5. 닉네임 중복 검증
        if name:
            try:
                existing = supabase.table("profiles").select("id").eq("name", name).execute()
                if existing.data and len(existing.data) > 0:
                    return redirect(url_for("auth.signup", error="nickname_exists", email=email, name=name))
            except Exception as e:
                logger.warning(f"[회원가입 닉네임 중복확인 경고] {e}")

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
            err_str = str(e).lower()
            if "rate limit" in err_str or "over_email_send_rate_limit" in err_str:
                return redirect(url_for("auth.signup", error="rate_limit", email=email, name=name))
            elif "user already registered" in err_str or "already registered" in err_str:
                return redirect(url_for("auth.signup", error="user_already_exists", email=email, name=name))
            elif "smtp" in err_str:
                return redirect(url_for("auth.signup", error="smtp_error", email=email, name=name, detail=str(e)[:100]))
            return redirect(url_for("auth.signup", error="auth_error", email=email, name=name, detail=str(e)[:100]))

    custom_error = request.args.get("detail") if error_code == "auth_error" and request.args.get("detail") else error_msg
    return render_template(
        "auth/register.html",
        email=request.args.get("email", ""),
        name=request.args.get("name", ""),
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
            auth_response = supabase.auth.exchange_code_for_session({
                "auth_code": code
            })
        # 4. token만 넘어온 경우 token_hash로 재시도
        elif token:
            auth_response = supabase.auth.verify_otp({
                "token_hash": token,
                "type": otp_type
            })

        if auth_response and auth_response.user:
            user = auth_response.user
            display_name = extract_display_name(user)

            # 성공 시 Flask session 저장
            session["user"] = {
                "id": str(user.id),
                "email": user.email,
                "name": display_name,
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

            session["user"] = {
                "id": str(user.id),
                "email": user.email,
                "name": display_name,
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

        if len(password) < 6:
            return redirect(url_for("auth.reset_password", error="password_too_short", email=email))

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

    # 1. 닉네임 변경 처리
    if action == "update_nickname":
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
                    {"user_metadata": {"name": new_nickname, "full_name": new_nickname}}
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

    return redirect(url_for("auth.mypage"))


@auth_bp.route("/delete-account", methods=["POST"])
@login_required
def delete_account():
    """
    회원 탈퇴:
    - 로그인된 본인 계정(profiles 및 auth.users)을 삭제하고 세션 종료
    """
    user_info = session.get("user")
    if not user_info or not user_info.get("id"):
        return redirect(url_for("auth.login", error="login_required"))

    user_id = user_info["id"]

    try:
        # 1. profiles 및 연관 데이터 정리
        supabase = get_supabase_client()
        admin_client = get_supabase_admin_client()

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

        # 2. 세션 정리
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

