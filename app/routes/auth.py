import logging
import os
import re
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from supabase import create_client, Client

# 로깅 설정
logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


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
    관리자 작업(회원가입 자동 인증, 사용자 검증 등)을 위한 Service Role Supabase 클라이언트를 초기화하여 반환합니다.
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


def validate_password_strength(password: str) -> tuple[bool, str]:
    """
    비밀번호 유효성 검증:
    1. 최소 6자 이상
    2. 최소 하나의 대문자 포함 (A-Z)
    3. 최소 하나의 특수문자 포함 (!@#$%^&*()_+-=[]{}|;:,.<>?)
    """
    if len(password) < 6:
        return False, "비밀번호는 최소 6자 이상이어야 합니다."
    if not re.search(r"[A-Z]", password):
        return False, "비밀번호에는 최소 1개 이상의 대문자(A-Z)가 포함되어야 합니다."
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{}|;:,.<>?/~`]", password):
        return False, "비밀번호에는 최소 1개 이상의 특수기호(!@#$%^&* 등)가 포함되어야 합니다."
    return True, ""


@auth_bp.route("/check-id", methods=["GET", "POST"])
def check_id():
    """
    아이디/이메일 중복 체크 비동기 API:
    - query param 또는 json으로 'username' 또는 'email'을 받아 DB 존재 여부 확인
    """
    data = request.get_json(silent=True) or request.values
    user_id = (data.get("username") or data.get("email") or "").strip().lower()

    if not user_id:
        return jsonify({"available": False, "message": "아이디를 입력해 주세요."}), 400

    admin_client = get_supabase_admin_client()
    if not admin_client:
        return jsonify({"available": False, "message": "서버 인증 설정을 확인할 수 없습니다."}), 500

    try:
        users = admin_client.auth.admin.list_users()
        email_to_check = user_id if "@" in user_id else f"{user_id}@vibe.com"

        exists = any(
            (u.email and u.email.lower() == email_to_check.lower()) or
            (u.user_metadata and str(u.user_metadata.get("username", "")).lower() == user_id.lower())
            for u in users
        )

        if exists:
            return jsonify({
                "available": False,
                "exists": True,
                "message": "이미 사용 중인 아이디입니다. 다른 아이디를 입력해 주세요."
            })
        else:
            return jsonify({
                "available": True,
                "exists": False,
                "message": "사용 가능한 멋진 아이디입니다!"
            })
    except Exception as e:
        logger.error(f"[아이디 중복 확인 오류] {e}", exc_info=True)
        return jsonify({"available": False, "message": "아이디 확인 중 오류가 발생했습니다."}), 500


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """
    로그인 페이지 및 처리:
    - 아이디(영문/숫자) 또는 이메일로 로그인 지원
    - next 파라미터가 있을 경우 로그인 성공 시 해당 페이지(예: 결제 페이지)로 리다이렉트
    """
    next_url = request.args.get("next") or request.form.get("next") or ""

    if "user" in session:
        return redirect(next_url or url_for("main.index"))

    if request.method == "POST":
        login_id = request.form.get("login_id", "").strip()
        password = request.form.get("password", "").strip()

        if not login_id or not password:
            flash("아이디(또는 이메일)와 비밀번호를 모두 입력해 주세요.", "danger")
            return render_template("auth/login.html", login_id=login_id, next=next_url)

        email = login_id if "@" in login_id else f"{login_id.lower()}@vibe.com"

        supabase = get_supabase_client()
        if not supabase:
            flash("인증 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.", "danger")
            return render_template("auth/login.html", login_id=login_id, next=next_url)

        try:
            auth_response = supabase.auth.sign_in_with_password({
                "email": email,
                "password": password
            })

            if auth_response and auth_response.user:
                user = auth_response.user
                user_metadata = getattr(user, "user_metadata", {}) or {}
                display_name = user_metadata.get("name") or user_metadata.get("username") or email.split("@")[0]

                session["user"] = {
                    "id": str(user.id),
                    "email": user.email,
                    "username": user_metadata.get("username", login_id),
                    "name": display_name,
                }
                session.permanent = True
                flash(f"{display_name}님, 환영합니다!", "success")

                # next 파라미터가 안전한 내부 경로인지 확인 후 리다이렉트
                if next_url and next_url.startswith("/"):
                    return redirect(next_url)
                return redirect(url_for("main.index"))
            else:
                flash("아이디 또는 비밀번호가 올바르지 않습니다.", "danger")
        except Exception as e:
            logger.error(f"[로그인 실패] 예외 발생: {e}", exc_info=True)
            err_msg = str(e).lower()
            if "invalid login credentials" in err_msg or "invalid_grant" in err_msg:
                flash("아이디 또는 비밀번호가 올바르지 않습니다.", "danger")
            elif "email not confirmed" in err_msg:
                flash("이메일 인증이 완료되지 않았습니다.", "warning")
            else:
                flash("로그인 처리 중 오류가 발생했습니다. 다시 시도해 주세요.", "danger")

        return render_template("auth/login.html", login_id=login_id, next=next_url)

    return render_template("auth/login.html", next=next_url)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """
    회원가입 페이지 및 처리:
    1. 아이디 DB 중복 검증
    2. 비밀번호 보안 검증 (6자 이상, 대문자 포함, 특수기호 포함)
    3. 비밀번호 확인 일치 검증
    4. Supabase Auth 계정 생성 (이메일 인증 불필요하게 자동 승인 생성)
    """
    if "user" in session:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "").strip()
        password_confirm = request.form.get("password_confirm", "").strip()

        # 1. 필수값 체크
        if not username or not name or not password:
            flash("모든 필수 입력 항목을 작성해 주세요.", "danger")
            return render_template("auth/register.html", username=username, name=name)

        # 2. 아이디 형식 검증 (영문, 숫자 3~20자 또는 유효한 이메일 형식)
        if "@" in username:
            email = username.lower()
            pure_username = username.split("@")[0]
        else:
            if not re.match(r"^[a-zA-Z0-9_-]{3,20}$", username):
                flash("아이디는 3~20자의 영문, 숫자, 밑줄(_), 하이픈(-)만 사용할 수 있습니다.", "danger")
                return render_template("auth/register.html", username=username, name=name)
            email = f"{username.lower()}@vibe.com"
            pure_username = username

        # 3. 비밀번호 강도 검증 (6자 이상, 특수문자, 대문자 포함)
        is_valid_pw, pw_err_msg = validate_password_strength(password)
        if not is_valid_pw:
            flash(pw_err_msg, "danger")
            return render_template("auth/register.html", username=username, name=name)

        # 4. 비밀번호 확인 일치 검증
        if password != password_confirm:
            flash("비밀번호 확인이 일치하지 않습니다.", "danger")
            return render_template("auth/register.html", username=username, name=name)

        # 5. DB에 아이디가 실제로 존재하는지 사전 검증 (중복 체크)
        admin_client = get_supabase_admin_client()
        if not admin_client:
            flash("인증 서비스를 초기화할 수 없습니다. 잠시 후 다시 시도해 주세요.", "danger")
            return render_template("auth/register.html", username=username, name=name)

        try:
            users = admin_client.auth.admin.list_users()
            exists = any(
                (u.email and u.email.lower() == email.lower()) or
                (u.user_metadata and str(u.user_metadata.get("username", "")).lower() == pure_username.lower())
                for u in users
            )
            if exists:
                flash(f"'{username}'은(는) 이미 등록된 아이디입니다. 다른 아이디를 입력해 주세요.", "danger")
                return render_template("auth/register.html", username=username, name=name)
        except Exception as e:
            logger.error(f"[아이디 중복 검증 오류] {e}", exc_info=True)

        # 6. 실제 계정 생성 (Admin API로 email_confirm=True 생성하여 즉시 로그인 가능)
        try:
            created = admin_client.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {
                    "name": name,
                    "full_name": name,
                    "username": pure_username,
                }
            })

            if created and created.user:
                # 가입 즉시 세션 로그인 처리
                session["user"] = {
                    "id": str(created.user.id),
                    "email": email,
                    "username": pure_username,
                    "name": name,
                }
                session.permanent = True
                flash(f"회원가입이 완료되었습니다! {name}님, 쇼핑을 즐겨보세요.", "success")
                return redirect(url_for("main.index"))
            else:
                flash("회원가입 처리 중 문제가 발생했습니다. 다시 시도해 주세요.", "danger")
        except Exception as e:
            logger.error(f"[회원가입 생성 오류] {e}", exc_info=True)
            err_msg = str(e).lower()
            if "already registered" in err_msg or "user_already_exists" in err_msg:
                flash("이미 등록된 아이디/이메일입니다.", "warning")
            else:
                flash(f"회원가입 실패: {e}", "danger")

        return render_template("auth/register.html", username=username, name=name)

    return render_template("auth/register.html")


@auth_bp.route("/logout")
def logout():
    """
    로그아웃: 세션 클리어 후 홈으로 리다이렉트
    """
    session.pop("user", None)
    flash("정상적으로 로그아웃되었습니다.", "info")
    return redirect(url_for("main.index"))
