import logging
import uuid
from flask import Blueprint, render_template, request, session, redirect, url_for, flash
from app.utils import get_user_supabase_client as _get_user_client, get_supabase_client

# 로깅 설정
logger = logging.getLogger(__name__)

order_bp = Blueprint("order", __name__, url_prefix="/order")

_AUTH_ERROR_CODES = {"42501", "PGRST301", "PGRST303"}


def _is_auth_error(e):
    """JWT 만료/누락으로 RLS에서 거부된 오류인지 판별."""
    return str(getattr(e, "code", "")) in _AUTH_ERROR_CODES


def _get_stock(option):
    """stock / stock_quantity 두 컬럼이 혼재하므로 값이 채워진 쪽을 재고로 사용."""
    return max(int(option.get("stock") or 0), int(option.get("stock_quantity") or 0))


def _calc_totals(items):
    """상품 금액, 배송비(5만원 이상 무료, 미만 3,000원), 최종 결제 금액을 반환."""
    subtotal = sum(item["price"] * item["quantity"] for item in items)
    shipping_fee = 0 if subtotal >= 50000 else 3000
    return subtotal, shipping_fee, subtotal + shipping_fee


def _validate_phone(phone):
    """휴대폰 번호 형식 검증 (010-0000-0000)."""
    import re
    pattern = r'^01[0-9]-\d{3,4}-\d{4}$'
    return bool(re.match(pattern, phone))


def _validate_address(address):
    """배송지 주소 최소 5자 이상 검증."""
    return len(address.strip()) >= 5


@order_bp.route("/checkout", methods=["GET", "POST"])
def checkout():
    """
    주문서 페이지:
    - 로그인 필수, 장바구니 비어있으면 /cart 리다이렉트
    - 품절 아이템이 있으면 /cart로 리다이렉트
    - 장바구니 아이템 목록 표시 (수정 불가)
    - 배송지 입력 폼: 수령인 이름, 휴대폰 번호, 주소, 메모(선택)
    - 기본 배송지 불러오기 버튼
    - 배송비 포함 결제 금액 요약
    - "결제하기" 버튼 (더미 결제 → 바로 주문 완료 처리)
    """
    # 1. 로그인 확인
    user = session.get("user")
    if not user or not user.get("id"):
        flash("주문을 진행하시려면 먼저 로그인이 필요합니다.", "warning")
        return redirect(url_for("auth.login", next=url_for("order.checkout")))

    user_id = user["id"]
    logger.info(f"[주문서] user_id={user_id}")

    supabase = _get_user_client()
    if not supabase:
        logger.error("[주문서] Supabase 연결 실패")
        flash("데이터베이스 연결에 실패했습니다.", "error")
        return redirect(url_for("main.index"))

    try:
        # 2. 장바구니 조회
        cart_res = (
            supabase.table("carts")
            .select("id, product_id, option_id, quantity")
            .eq("user_id", user_id)
            .order("created_at", desc=False)
            .execute()
        )

        cart_items = cart_res.data or []
        if not cart_items:
            flash("장바구니가 비어 있습니다. 상품을 먼저 담아주세요.", "info")
            return redirect(url_for("cart.view_cart"))

        logger.info(f"[주문서] 장바구니 {len(cart_items)}개 아이템")

        # 3. 각 아이템의 상품, 옵션, 재고 정보 조회
        items = []
        has_out_of_stock = False

        for cart_item in cart_items:
            try:
                product_id = cart_item["product_id"]
                option_id = cart_item["option_id"]
                quantity = cart_item["quantity"]

                # 상품 정보
                prod_res = (
                    supabase.table("products")
                    .select("id, name, price, sale_price, product_images(image_url, is_primary)")
                    .eq("id", product_id)
                    .single()
                    .execute()
                )

                if not prod_res.data:
                    logger.warning(f"[주문서] 상품 조회 실패: product_id={product_id}")
                    continue

                product = prod_res.data

                # 옵션 정보
                opt_res = (
                    supabase.table("product_options")
                    .select("id, color, size, stock, stock_quantity, additional_price")
                    .eq("id", option_id)
                    .single()
                    .execute()
                )

                option = opt_res.data if opt_res.data else {}
                stock = _get_stock(option)

                # 품절 체크
                if stock == 0:
                    has_out_of_stock = True

                # 가격 계산
                raw_price = product.get("sale_price") if product.get("sale_price") is not None else product.get("price", 0)
                unit_price = int(float(raw_price or 0)) + int(float(option.get("additional_price") or 0))

                # 썸네일
                images = product.get("product_images") or []
                primary_img = next((img for img in images if img.get("is_primary")), None)
                thumbnail_url = primary_img.get("image_url") if primary_img else (images[0].get("image_url") if images else "https://picsum.photos/600/800")

                item = {
                    "id": cart_item["id"],
                    "product_id": product_id,
                    "option_id": option_id,
                    "name": product.get("name"),
                    "color": option.get("color"),
                    "size": option.get("size"),
                    "price": unit_price,
                    "thumbnail_url": thumbnail_url,
                    "quantity": quantity,
                    "stock": stock
                }

                items.append(item)

            except Exception as e:
                logger.warning(f"[주문서 아이템 처리 오류] cart_item={cart_item}, error={e}", exc_info=True)
                continue

        # 4. 품절 아이템 있으면 리다이렉트
        if has_out_of_stock:
            flash("품절된 상품이 있어 주문할 수 없습니다. 품절 상품을 삭제해주세요.", "warning")
            return redirect(url_for("cart.view_cart"))

        # 5. 계산
        total_amount, shipping_fee, final_total = _calc_totals(items)
        total_count = sum(item["quantity"] for item in items)

        # GET 요청 - 주문서 폼 표시
        if request.method == "GET":
            # 프로필에서 기본 배송지 조회
            default_address = ""
            default_name = user.get("real_name") or user.get("name") or ""
            default_phone = ""

            try:
                profile_res = (
                    supabase.table("profiles")
                    .select("name, phone, address")
                    .eq("id", user_id)
                    .single()
                    .execute()
                )
                if profile_res.data:
                    default_name = profile_res.data.get("name") or default_name
                    default_phone = profile_res.data.get("phone") or ""
                    default_address = profile_res.data.get("address") or ""
            except Exception as e:
                logger.warning(f"[주문서 프로필 조회 경고] {e}")

            logger.info(f"[주문서 조회 완료] {len(items)}개 아이템, 총액={total_amount}")
            return render_template(
                "order/checkout.html",
                items=items,
                total_amount=total_amount,
                shipping_fee=shipping_fee,
                final_total=final_total,
                total_count=total_count,
                default_name=default_name,
                default_phone=default_phone,
                default_address=default_address,
                user=user
            )

        # POST 요청 - 주문 처리
        else:
            recipient_name = request.form.get("recipient_name", "").strip()
            phone_number = request.form.get("phone_number", "").strip()
            shipping_address = request.form.get("shipping_address", "").strip()
            shipping_memo = request.form.get("shipping_memo", "").strip()

            # 폼 검증
            if not recipient_name:
                flash("수령인 이름을 입력해 주세요.", "warning")
                return redirect(url_for("order.checkout"))

            if not phone_number or not _validate_phone(phone_number):
                flash("휴대폰 번호는 010-0000-0000 형식으로 입력해 주세요.", "warning")
                return redirect(url_for("order.checkout"))

            if not shipping_address or not _validate_address(shipping_address):
                flash("배송 주소는 최소 5자 이상 입력해 주세요.", "warning")
                return redirect(url_for("order.checkout"))

            # 주문 생성
            try:
                # 1. orders 테이블에 주문 생성
                order_id = str(uuid.uuid4())
                order_number = f"ORD-{uuid.uuid4().hex[:12].upper()}"

                shipping_data = {
                    "recipient_name": recipient_name,
                    "phone_number": phone_number,
                    "address": shipping_address,
                    "memo": shipping_memo
                }

                order_payload = {
                    "id": order_id,
                    "user_id": user_id,
                    "order_number": order_number,
                    "total_amount": total_amount,
                    "payment_amount": final_total,
                    "status": "PAID",
                    "shipping_address": shipping_data,
                    "payment_method": "dummy"
                }

                supabase.table("orders").insert(order_payload).execute()
                logger.info(f"[주문 생성] order_id={order_id}, order_number={order_number}")

                # 2. order_items 테이블에 상세 항목 생성
                order_items_payload = []
                for item in items:
                    order_item = {
                        "order_id": order_id,
                        "product_id": item["product_id"],
                        "option_id": item["option_id"],
                        "product_name": item["name"],
                        "option_name": f"{item['color']} / {item['size']}" if item['color'] or item['size'] else None,
                        "unit_price": item["price"],
                        "quantity": item["quantity"],
                        "total_price": item["price"] * item["quantity"]
                    }
                    order_items_payload.append(order_item)

                if order_items_payload:
                    supabase.table("order_items").insert(order_items_payload).execute()
                logger.info(f"[주문 항목 생성] {len(order_items_payload)}개")

                # 3. 장바구니 비우기
                for cart_item in cart_items:
                    supabase.table("carts").delete().eq("id", cart_item["id"]).execute()
                logger.info("[장바구니 비우기 완료]")

                # 4. 세션 장바구니 비우기
                session.pop("cart", None)

                flash("주문이 정상적으로 접수되었습니다! 감사합니다.", "success")
                return redirect(url_for("order.checkout_success", order_id=order_id, order_number=order_number))

            except Exception as e:
                logger.error(f"[주문 처리 오류] 예외 발생: {e}", exc_info=True)
                if _is_auth_error(e):
                    flash("로그인이 만료되었습니다. 다시 로그인해 주세요.", "error")
                    return redirect(url_for("auth.login", next=url_for("order.checkout")))
                flash("주문 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.", "error")
                return redirect(url_for("order.checkout"))

    except Exception as e:
        logger.error(f"[주문서 조회 오류] 예외 발생: {e}", exc_info=True)
        if _is_auth_error(e):
            flash("로그인이 만료되었습니다. 다시 로그인해 주세요.", "error")
            return redirect(url_for("auth.login", next=url_for("order.checkout")))
        flash("주문서 조회 중 오류가 발생했습니다.", "error")
        return redirect(url_for("main.index"))


@order_bp.route("/checkout-success", methods=["GET"])
def checkout_success():
    """
    주문 완료 페이지
    """
    order_id = request.args.get("order_id")
    order_number = request.args.get("order_number")

    if not order_id or not order_number:
        flash("주문 정보가 없습니다.", "warning")
        return redirect(url_for("main.index"))

    user = session.get("user")
    supabase = get_supabase_client()

    if not supabase:
        return render_template(
            "order/checkout_success.html",
            order_id=order_id,
            order_number=order_number,
            user=user,
            items=[],
            total_amount=0,
            shipping_fee=0,
            final_total=0,
            total_count=0
        )

    try:
        # 주문 정보 조회
        order_res = (
            supabase.table("orders")
            .select("*")
            .eq("id", order_id)
            .single()
            .execute()
        )

        order_data = order_res.data if order_res.data else {}
        total_amount = float(order_data.get("total_amount") or 0)
        payment_amount = float(order_data.get("payment_amount") or 0)
        shipping_fee = payment_amount - total_amount

        # 주문 항목 조회
        items_res = (
            supabase.table("order_items")
            .select("*")
            .eq("order_id", order_id)
            .execute()
        )

        items = items_res.data or []
        total_count = sum(item["quantity"] for item in items)

        return render_template(
            "order/checkout_success.html",
            order_id=order_id,
            order_number=order_number,
            user=user,
            items=items,
            total_amount=total_amount,
            shipping_fee=shipping_fee,
            final_total=payment_amount,
            total_count=total_count
        )

    except Exception as e:
        logger.error(f"[주문 완료 조회 오류] {e}", exc_info=True)
        return render_template(
            "order/checkout_success.html",
            order_id=order_id,
            order_number=order_number,
            user=user,
            items=[],
            total_amount=0,
            shipping_fee=0,
            final_total=0,
            total_count=0
        )
