import logging
from flask import Blueprint, render_template, request, jsonify, abort
from app.utils import get_supabase_client

# 로깅 설정
logger = logging.getLogger(__name__)

# 메인 기능 관련 라우트를 관리하는 블루프린트 생성
main_bp = Blueprint("main", __name__)


def format_product_item(item: dict) -> dict:
    """
    Supabase 상품 원본 딕셔너리를 템플릿 렌더링에 적합한 형태로 변환합니다.
    """
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

    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "description": item.get("description", ""),
        "price": formatted_price,
        "thumbnail_url": thumbnail_url,
    }


@main_bp.route("/")
def index():
    """
    메인 홈 페이지:
    - 인기 상품 (is_featured=True, is_active=True): 최대 8개 노출
    - 신상품 (최신 등록순, is_active=True): 최대 8개 노출
    - 인기 상품/신상품 각각 전체 개수를 파악하여 '더보기' 버튼 표시 여부 판단
    """
    popular_products = []
    new_products = []
    has_more_popular = False
    has_more_new = False

    try:
        supabase = get_supabase_client()
        if supabase:
            # 1. 인기 상품 조회 (is_featured=True, 최대 8개 노출, 9개 조회하여 더보기 여부 확인)
            pop_response = (
                supabase.table("products")
                .select("id, name, description, price, sale_price, is_active, is_featured, product_images(image_url, is_primary, sort_order)")
                .eq("is_active", True)
                .eq("is_featured", True)
                .order("created_at", desc=False)
                .limit(9)
                .execute()
            )
            raw_popular = pop_response.data or []
            if len(raw_popular) > 8:
                has_more_popular = True
                raw_popular = raw_popular[:8]
            popular_products = [format_product_item(item) for item in raw_popular]

            # 2. 신상품 조회 (최신 등록순, 최대 8개 노출, 9개 조회하여 더보기 여부 확인)
            new_response = (
                supabase.table("products")
                .select("id, name, description, price, sale_price, is_active, is_featured, product_images(image_url, is_primary, sort_order)")
                .eq("is_active", True)
                .order("created_at", desc=True)
                .limit(9)
                .execute()
            )
            raw_new = new_response.data or []
            if len(raw_new) > 8:
                has_more_new = True
                raw_new = raw_new[:8]
            new_products = [format_product_item(item) for item in raw_new]

    except Exception as e:
        logger.error(f"[상품 조회 오류] Supabase에서 상품 데이터를 가져오는 중 에러 발생: {e}", exc_info=True)
        popular_products = []
        new_products = []

    return render_template(
        "index.html",
        popular_products=popular_products,
        new_products=new_products,
        has_more_popular=has_more_popular,
        has_more_new=has_more_new,
    )


@main_bp.route("/products")
def product_list():
    """
    상품 전체 / 카테고리별 / 신상품 / 인기상품 목록 페이지 (더보기 페이지):
    - tab=popular : 인기 상품 전체 목록
    - tab=new     : 신상품 전체 목록
    - 기타        : 전체 활성 상품 목록
    """
    tab = request.args.get("tab", "all")
    title = "전체 상품"
    subtitle = "VIBE-FASHION의 모든 스타일을 만나보세요"

    products = []
    try:
        supabase = get_supabase_client()
        if supabase:
            query = (
                supabase.table("products")
                .select("id, name, description, price, sale_price, is_active, is_featured, product_images(image_url, is_primary, sort_order)")
                .eq("is_active", True)
            )

            if tab == "popular":
                query = query.eq("is_featured", True).order("created_at", desc=False)
                title = "인기 상품 전체보기"
                subtitle = "많은 고객들이 사랑하는 베스트셀러 컬렉션입니다"
            elif tab == "new":
                query = query.order("created_at", desc=True)
                title = "신상품 전체보기"
                subtitle = "이번 시즌 가장 새롭게 입고된 트렌디 아이템입니다"
            else:
                query = query.order("created_at", desc=True)

            response = query.execute()
            raw_products = response.data or []
            products = [format_product_item(item) for item in raw_products]
    except Exception as e:
        logger.error(f"[상품 목록 조회 오류] {e}", exc_info=True)
        products = []

    return render_template(
        "products.html",
        products=products,
        tab=tab,
        title=title,
        subtitle=subtitle,
    )


@main_bp.route("/products/<product_id>")
def product_detail(product_id: str):
    """
    상품 상세 페이지: GET /products/<product_id>
    - Supabase에서 product_id로 상품 정보, 이미지, 카테고리 조회
    - 상품 이미지, 이름, 가격(할인가/정가), 설명 표시
    - product_options 테이블에서 해당 상품의 색상(color) 목록을 DISTINCT로 조회
    """
    supabase = get_supabase_client()
    if not supabase:
        abort(500, description="데이터베이스 연결에 실패했습니다.")

    try:
        # 1. 상품 정보 및 이미지 조회
        prod_res = (
            supabase.table("products")
            .select("id, category_id, name, description, price, sale_price, is_active, is_featured, product_images(id, image_url, is_primary, sort_order)")
            .eq("id", product_id)
            .eq("is_active", True)
            .single()
            .execute()
        )

        item = prod_res.data
        if not item:
            abort(404, description="요청하신 상품을 찾을 수 없거나 판매가 중단되었습니다.")

        # 정가, 할인가, 할인율 계산
        raw_price = float(item.get("price", 0))
        raw_sale_price = float(item["sale_price"]) if item.get("sale_price") is not None else None
        effective_price = int(raw_sale_price) if raw_sale_price is not None else int(raw_price)
        is_on_sale = (raw_sale_price is not None) and (raw_sale_price < raw_price)
        discount_rate = int(round((1 - (raw_sale_price / raw_price)) * 100)) if is_on_sale and raw_price > 0 else 0

        # 이미지 정렬 및 대표 이미지 추출
        images = item.get("product_images") or []
        images.sort(key=lambda x: (not x.get("is_primary", False), x.get("sort_order", 0)))
        primary_image = next((img for img in images if img.get("is_primary")), None)
        thumbnail_url = primary_image.get("image_url") if primary_image else (images[0].get("image_url") if images else "https://picsum.photos/600/800")

        product = {
            "id": item.get("id"),
            "name": item.get("name"),
            "description": item.get("description", ""),
            "unit_price": effective_price,
            "price_formatted": f"{effective_price:,}원",
            "original_price_formatted": f"{int(raw_price):,}원",
            "is_on_sale": is_on_sale,
            "discount_rate": discount_rate,
            "thumbnail_url": thumbnail_url,
            "images": images,
        }

        # 2. product_options 테이블에서 해당 상품의 색상(color) 목록을 DISTINCT로 조회
        # color가 NULL이 아닌 옵션 목록을 가져와 중복 제거
        options_res = (
            supabase.table("product_options")
            .select("color")
            .eq("product_id", product_id)
            .not_.is_("color", "null")
            .execute()
        )
        colors = sorted(list({opt["color"].strip() for opt in (options_res.data or []) if opt.get("color")}))

        return render_template(
            "product_detail.html",
            product=product,
            colors=colors,
        )

    except Exception as e:
        logger.error(f"[상품 상세 조회 오류] {e}", exc_info=True)
        abort(404, description="상품 정보를 불러오는 중 오류가 발생했습니다.")


@main_bp.route("/api/products/<product_id>/sizes")
def get_product_sizes(product_id: str):
    """
    상품 옵션별 사이즈/재고 조회 API:
    GET /api/products/<product_id>/sizes?color=<선택한 색상>
    - product_options에서 product_id + color로 필터링
    - size, stock을 JSON 배열로 반환
      예: [{"size": "S", "stock": 3}, {"size": "M", "stock": 0}]
    """
    color = request.args.get("color", "").strip()
    if not color:
        return jsonify({"error": "색상(color) 파라미터가 필요합니다."}), 400

    supabase = get_supabase_client()
    if not supabase:
        return jsonify({"error": "데이터베이스 연결 실패"}), 500

    try:
        # product_options에서 product_id + color로 필터링
        res = (
            supabase.table("product_options")
            .select("size, stock")
            .eq("product_id", product_id)
            .eq("color", color)
            .not_.is_("size", "null")
            .execute()
        )

        rows = res.data or []

        # 사이즈 정렬 (S, M, L 등 표준 사이즈 순서 권장)
        size_order = {"XS": 1, "S": 2, "M": 3, "L": 4, "XL": 5, "XXL": 6, "FREE": 7}
        def sort_key(row):
            s = str(row.get("size", "")).upper()
            return (size_order.get(s, 99), s)

        sorted_rows = sorted(rows, key=sort_key)

        result = [
            {
                "size": row.get("size"),
                "stock": int(row.get("stock", 0))
            }
            for row in sorted_rows
        ]

        return jsonify(result)

    except Exception as e:
        logger.error(f"[상품 사이즈 목록 API 조회 오류] {e}", exc_info=True)
        return jsonify({"error": "사이즈 정보를 조회하지 못했습니다."}), 500
