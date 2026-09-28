import logging
import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
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


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """
    로그인 페이지 및 처리:
    - GET: 로그인 폼 표시
    - POST: Supabase Auth 이메일/비밀번호 로그인 인증 후 세션 저장
    """
    # 이미 로그인된 상태인 경우 홈으로 리다이렉트
    if "user" in session:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        if not email or not password:
            flash("이메일과 비밀번호를 모두 입력해 주세요.", "danger")
            return render_template("auth/login.html", email=email)

        supabase = get_supabase_client()
        if not supabase:
            flash("인증 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.", "danger")
            return render_template("auth/login.html", email=email)

        try:
            auth_response = supabase.auth.sign_in_with_password({
                "email": email,
                "password": password
            })

            if auth_response and auth_response.user:
                user = auth_response.user
                user_metadata = getattr(user, "user_metadata", {}) or {}
                display_name = user_metadata.get("name") or user_metadata.get("full_name") or email.split("@")[0]

                # 세션에 로그인 정보 저장
                session["user"] = {
                    "id": str(user.id),
                    "email": user.email,
                    "name": display_name,
                }
                session.permanent = True
                flash(f"{display_name}님, 환영합니다!", "success")
                return redirect(url_for("main.index"))
            else:
                flash("이메일 또는 비밀번호가 올바르지 않습니다.", "danger")
        except Exception as e:
            logger.error(f"[로그인 실패] 예외 발생: {e}", exc_info=True)
            err_msg = str(e).lower()
            if "invalid login credentials" in err_msg or "invalid_grant" in err_msg:
                flash("이메일 또는 비밀번호가 올바르지 않습니다.", "danger")
            elif "email not confirmed" in err_msg:
                flash("이메일 인증이 완료되지 않았습니다. 메일함을 확인해 주세요.", "warning")
            else:
                flash("로그인 처리 중 오류가 발생했습니다. 다시 시도해 주세요.", "danger")

        return render_template("auth/login.html", email=email)

    return render_template("auth/login.html")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """
    회원가입 페이지 및 처리:
    - GET: 회원가입 폼 표시
    - POST: Supabase Auth 신규 유저 생성
    """
    # 이미 로그인된 상태인 경우 홈으로 리다이렉트
    if "user" in session:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        password_confirm = request.form.get("password_confirm", "").strip()

        if not name or not email or not password:
            flash("모든 필수 입력 항목을 작성해 주세요.", "danger")
            return render_template("auth/register.html", name=name, email=email)

        if len(password) < 6:
            flash("비밀번호는 최소 6자 이상이어야 합니다.", "danger")
            return render_template("auth/register.html", name=name, email=email)

        if password != password_confirm:
            flash("비밀번호 확인이 일치하지 않습니다.", "danger")
            return render_template("auth/register.html", name=name, email=email)

        supabase = get_supabase_client()
        if not supabase:
            flash("인증 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.", "danger")
            return render_template("auth/register.html", name=name, email=email)

        try:
            auth_response = supabase.auth.sign_up({
                "email": email,
                "password": password,
                "options": {
                    "data": {
                        "name": name,
                        "full_name": name,
                    }
                }
            })

            if auth_response and auth_response.user:
                # 이메일 확인이 비활성화되어 세션이 바로 발급된 경우 자동 로그인 처리
                if auth_response.session:
                    session["user"] = {
                        "id": str(auth_response.user.id),
                        "email": auth_response.user.email,
                        "name": name,
                    }
                    session.permanent = True
                    flash(f"회원가입이 완료되었습니다. {name}님 환영합니다!", "success")
                    return redirect(url_for("main.index"))
                else:
                    flash("회원가입이 완료되었습니다! 로그인해 주세요.", "success")
                    return redirect(url_for("auth.login"))
            else:
                flash("회원가입 중 오류가 발생했습니다. 다시 시도해 주세요.", "danger")
        except Exception as e:
            logger.error(f"[회원가입 실패] 예외 발생: {e}", exc_info=True)
            err_msg = str(e).lower()
            if "already registered" in err_msg or "user_already_exists" in err_msg:
                flash("이미 가입된 이메일 주소입니다. 로그인해 주세요.", "warning")
            else:
                flash("회원가입 처리 중 오류가 발생했습니다. 다시 시도해 주세요.", "danger")

        return render_template("auth/register.html", name=name, email=email)

    return render_template("auth/register.html")


@auth_bp.route("/logout")
def logout():
    """
    로그아웃: 세션 클리어 후 홈으로 리다이렉트
    """
    session.pop("user", None)
    flash("정상적으로 로그아웃되었습니다.", "info")
    return redirect(url_for("main.index"))
