import logging
import os
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash
from supabase import create_client, Client

# 로깅 설정
logger = logging.getLogger(__name__)

cart_bp = Blueprint("cart", __name__, url_prefix="/cart")


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


@cart_bp.route("/")
def view_cart():
    """
    장바구니 페이지:
    세션에 저장된 품목 목록을 렌더링합니다.
    """
    cart = session.get("cart", {})
    items = list(cart.values())
    total_amount = sum(item["price"] * item["quantity"] for item in items)
    total_count = sum(item["quantity"] for item in items)

    return render_template(
        "cart/cart.html",
        items=items,
        total_amount=total_amount,
        total_count=total_count
    )


@cart_bp.route("/add", methods=["POST"])
def add_to_cart():
    """
    장바구니 상품 추가 (AJAX / Form):
    요청 바디에서 product_id와 quantity를 받아 세션 기반 장바구니에 추가합니다.
    """
    data = request.get_json(silent=True) or request.form
    product_id = data.get("product_id")
    try:
        quantity = int(data.get("quantity", 1))
        if quantity < 1:
            quantity = 1
    except (ValueError, TypeError):
        quantity = 1

    if not product_id:
        return jsonify({"success": False, "message": "상품 ID가 전달되지 않았습니다."}), 400

    supabase = get_supabase_client()
    if not supabase:
        return jsonify({"success": False, "message": "데이터베이스 연결에 실패했습니다."}), 500

    try:
        # 상품 정보 조회
        res = (
            supabase.table("products")
            .select("id, name, description, price, sale_price, product_images(image_url, is_primary)")
            .eq("id", product_id)
            .eq("is_active", True)
            .single()
            .execute()
        )

        product = res.data
        if not product:
            return jsonify({"success": False, "message": "존재하지 않거나 판매 중지된 상품입니다."}), 404

        # 가격 및 이미지 결정
        raw_price = product.get("sale_price") if product.get("sale_price") is not None else product.get("price", 0)
        unit_price = int(float(raw_price))

        images = product.get("product_images") or []
        primary_image = next((img for img in images if img.get("is_primary")), None)
        thumbnail_url = primary_image.get("image_url") if primary_image else (images[0].get("image_url") if images else "https://picsum.photos/600/800")

        # 세션 카트 갱신
        cart = session.get("cart", {})
        if product_id in cart:
            cart[product_id]["quantity"] += quantity
        else:
            cart[product_id] = {
                "product_id": product_id,
                "name": product.get("name"),
                "price": unit_price,
                "thumbnail_url": thumbnail_url,
                "quantity": quantity
            }

        session["cart"] = cart
        session.modified = True

        total_count = sum(item["quantity"] for item in cart.values())
        total_amount = sum(item["price"] * item["quantity"] for item in cart.values())

        return jsonify({
            "success": True,
            "message": f"'{product.get('name')}' 상품이 장바구니에 담겼습니다.",
            "cart_count": total_count,
            "total_amount": total_amount,
            "item": cart[product_id]
        })
    except Exception as e:
        logger.error(f"[장바구니 추가 오류] 예외 발생: {e}", exc_info=True)
        return jsonify({"success": False, "message": "장바구니에 담는 중 오류가 발생했습니다."}), 500


@cart_bp.route("/update", methods=["POST"])
def update_quantity():
    """
    장바구니 품목 수량 변경
    """
    data = request.get_json(silent=True) or request.form
    product_id = data.get("product_id")
    try:
        quantity = int(data.get("quantity", 1))
    except (ValueError, TypeError):
        quantity = 1

    cart = session.get("cart", {})
    if product_id in cart:
        if quantity <= 0:
            del cart[product_id]
        else:
            cart[product_id]["quantity"] = quantity

        session["cart"] = cart
        session.modified = True

    total_count = sum(item["quantity"] for item in cart.values())
    total_amount = sum(item["price"] * item["quantity"] for item in cart.values())

    return jsonify({
        "success": True,
        "cart_count": total_count,
        "total_amount": total_amount
    })


@cart_bp.route("/remove", methods=["POST"])
def remove_from_cart():
    """
    장바구니 품목 삭제
    """
    data = request.get_json(silent=True) or request.form
    product_id = data.get("product_id")

    cart = session.get("cart", {})
    if product_id in cart:
        del cart[product_id]
        session["cart"] = cart
        session.modified = True

    total_count = sum(item["quantity"] for item in cart.values())
    total_amount = sum(item["price"] * item["quantity"] for item in cart.values())

    return jsonify({
        "success": True,
        "cart_count": total_count,
        "total_amount": total_amount
    })


@cart_bp.route("/count")
def get_cart_count():
    """
    현재 장바구니 품목 개수 반환
    """
    cart = session.get("cart", {})
    total_count = sum(item["quantity"] for item in cart.values())
    return jsonify({"cart_count": total_count})


@cart_bp.route("/checkout", methods=["GET", "POST"])
def checkout():
    """
    주문/결제 페이지:
    - 로그인 상태 확인: 비로그인 상태일 경우 안내 메시지와 함께 로그인 페이지로 리다이렉트 (로그인 후 다시 결제 페이지로 복귀)
    - 장바구니가 비어 있을 경우 장바구니로 리다이렉트
    - GET: 결제 주문서 폼 표시
    - POST: 주문 완료 처리
    """
    # 1. 로그인 여부 검증
    if "user" not in session:
        flash("결제를 진행하시려면 먼저 로그인이 필요합니다.", "warning")
        return redirect(url_for("auth.login", next=url_for("cart.checkout")))

    # 2. 장바구니 품목 확인
    cart = session.get("cart", {})
    if not cart:
        flash("장바구니가 비어 있습니다. 상품을 먼저 담아주세요.", "info")
        return redirect(url_for("cart.view_cart"))

    items = list(cart.values())
    total_amount = sum(item["price"] * item["quantity"] for item in items)
    total_count = sum(item["quantity"] for item in items)

    if request.method == "POST":
        # 주문 완료 처리: 세션 장바구니 비우기
        session.pop("cart", None)
        flash("성공적으로 주문이 접수되었습니다! 감사합니다.", "success")
        return render_template(
            "cart/checkout_success.html",
            total_amount=total_amount,
            total_count=total_count
        )

    return render_template(
        "cart/checkout.html",
        items=items,
        total_amount=total_amount,
        total_count=total_count,
        user=session.get("user")
    )
