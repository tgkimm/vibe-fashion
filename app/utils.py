"""
공통 유틸리티 모듈:
- Supabase 클라이언트 생성 (Anon / Admin)
- 사이트 URL 생성
- 사용자 메타데이터 파싱 헬퍼
"""
import logging
import os
from supabase import create_client, Client

logger = logging.getLogger(__name__)


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
    Service Role Key를 사용하여 최고 관리자 권한의 Supabase 클라이언트를 반환합니다.
    (RLS 우회, 사용자 완전 삭제 및 강제 비밀번호 변경 등에 사용)
    """
    supabase_url = os.getenv("SUPABASE_URL")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")

    if not supabase_url or not service_key:
        return None

    try:
        return create_client(supabase_url, service_key)
    except Exception as e:
        logger.error(f"[Supabase Admin 클라이언트 오류] {e}", exc_info=True)
        return None


def extract_display_name(user) -> str:
    """
    Supabase user 객체에서 닉네임을 우선순위에 따라 추출합니다:
    1. user_metadata['nickname']
    2. user_metadata['name'] (단, 소셜 프로바이더 기본 이름/실명이 아니라 닉네임 용도로 설정된 값)
    3. 이메일 아이디 부분 (@ 앞)
    4. 기본값 '회원'
    """
    if not user:
        return "회원"
    user_metadata = getattr(user, "user_metadata", {}) or {}
    email = getattr(user, "email", "") or ""

    nickname = user_metadata.get("nickname")
    if nickname and nickname.strip():
        return nickname.strip()

    name = user_metadata.get("name")
    if name and name.strip():
        return name.strip()

    full_name = user_metadata.get("full_name")
    if full_name and full_name.strip():
        return full_name.strip()

    return (email.split("@")[0] if email else "회원")


def extract_real_name(user) -> str:
    """
    Supabase user 객체에서 실명(이름)을 우선순위에 따라 추출합니다:
    1. user_metadata['real_name']
    2. user_metadata['full_name']
    3. user_metadata['user_name']
    4. user_metadata['preferred_username']
    5. user_metadata['name']
    6. 빈 문자열
    """
    if not user:
        return ""
    user_metadata = getattr(user, "user_metadata", {}) or {}

    real_name = user_metadata.get("real_name")
    if real_name and real_name.strip():
        return real_name.strip()

    full_name = user_metadata.get("full_name")
    if full_name and full_name.strip():
        return full_name.strip()

    user_name = user_metadata.get("user_name")
    if user_name and user_name.strip():
        return user_name.strip()

    pref_name = user_metadata.get("preferred_username")
    if pref_name and pref_name.strip():
        return pref_name.strip()

    name = user_metadata.get("name")
    if name and name.strip():
        return name.strip()

    return ""
