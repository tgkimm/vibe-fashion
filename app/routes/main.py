import logging
import os
from dotenv import load_dotenv
from flask import Blueprint, render_template
from supabase import create_client, Client

# 환경 변수 로드 (.env 파일이 있으면 읽어옴)
load_dotenv()

# 로깅 설정
logger = logging.getLogger(__name__)

# 메인 기능 관련 라우트를 관리하는 블루프린트 생성
main_bp = Blueprint("main", __name__)


def get_supabase_client() -> Client | None:
    """
    환경변수에서 SUPABASE_URL과 SUPABASE_ANON_KEY를 읽어
    Supabase 클라이언트를 초기화하여 반환합니다.
    설정 누락 또는 초기화 실패 시 None을 반환합니다.
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


@main_bp.route("/")
def index():
    """
    메인 홈 페이지:
    - Supabase products 테이블에서 is_active=true, is_featured=true인 상품을 최대 4개 조회
    - 가격 포맷팅(예: '19,900원') 및 썸네일 이미지 연결
    - 조회 실패 시 빈 리스트로 안전하게 대체
    """
    products = []

    try:
        supabase = get_supabase_client()
        if supabase:
            # products 및 연결된 product_images 조회 (활성 상품 최대 6개)
            response = (
                supabase.table("products")
                .select("id, name, description, price, sale_price, is_active, is_featured, product_images(image_url, is_primary, sort_order)")
                .eq("is_active", True)
                .order("created_at", desc=False)
                .limit(6)
                .execute()
            )

            raw_products = response.data or []

            for item in raw_products:
                # 가격 포맷팅 ({:,}원)
                raw_price = item.get("sale_price") if item.get("sale_price") is not None else item.get("price", 0)
                try:
                    formatted_price = f"{int(float(raw_price)):,}원"
                except (ValueError, TypeError):
                    formatted_price = f"{raw_price}원"

                # 썸네일 이미지 결정: is_primary=True 우선, 없으면 첫 번째 이미지, 없으면 기본 이미지
                images = item.get("product_images") or []
                primary_image = next((img for img in images if img.get("is_primary")), None)
                if primary_image:
                    thumbnail_url = primary_image.get("image_url")
                elif images:
                    thumbnail_url = images[0].get("image_url")
                else:
                    thumbnail_url = "https://picsum.photos/600/800"

                products.append({
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "description": item.get("description", ""),
                    "price": formatted_price,
                    "thumbnail_url": thumbnail_url,
                })
    except Exception as e:
        logger.error(f"[상품 조회 오류] Supabase에서 상품 데이터를 가져오는 중 에러 발생: {e}", exc_info=True)
        products = []

    return render_template("index.html", products=products)
