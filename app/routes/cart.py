import logging
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash
from app.utils import get_user_supabase_client as _get_user_client

# 로깅 설정
logger = logging.getLogger(__name__)

cart_bp = Blueprint("cart", __name__, url_prefix="/cart")

_AUTH_ERROR_CODES = {"42501", "PGRST301", "PGRST303"}


def _is_auth_error(e):
    """JWT 만료/누락으로 RLS에서 거부된 오류인지 판별."""
    return str(getattr(e, "code", "")) in _AUTH_ERROR_CODES


def _auth_expired_response():
    return jsonify({"success": False, "message": "로그인이 만료되었습니다. 다시 로그인해 주세요."}), 401


def _calc_totals(items):
    """상품 금액, 배송비(5만원 이상 무료, 미만 3,000원), 최종 결제 금액을 반환."""
    subtotal = sum(item["price"] * item["quantity"] for item in items)
    shipping_fee = 0 if subtotal >= 50000 else 3000
    return subtotal, shipping_fee, subtotal + shipping_fee


def _get_stock(option):
    """stock / stock_quantity 두 컬럼이 혼재하므로 값이 채워진 쪽을 재고로 사용."""
    return max(int(option.get("stock") or 0), int(option.get("stock_quantity") or 0))


@cart_bp.route("/")
def view_cart():
    """
    장바구니 페이지:
    DB(carts 테이블)에서 현재 사용자의 장바구니 품목을 조회합니다.
    - 미로그인 시 로그인 페이지로 리다이렉트
    - 로그인 사용자의 cart_id, product 정보 포함하여 렌더링
    """
    # 1. 로그인 확인
    user = session.get("user")
    if not user or not user.get("id"):
        logger.info("[장바구니 조회] 미로그인 상태, 로그인 페이지로 리다이렉트")
        return redirect(url_for("auth.login", next=url_for("cart.view_cart")))

    user_id = user["id"]
    logger.info(f"[장바구니 조회] user_id={user_id}")

    supabase = _get_user_client()

    if not supabase:
        logger.error("[장바구니 조회] Supabase 연결 실패")
        flash("데이터베이스 연결에 실패했습니다.", "error")
        return redirect(url_for("main.index"))

    try:
        # 2. 현재 사용자의 장바구니 아이템 조회
        cart_res = (
            supabase.table("carts")
            .select("id, product_id, option_id, quantity")
            .eq("user_id", user_id)
            .order("created_at", desc=False)
            .execute()
        )

        logger.info(f"[장바구니 조회] DB 결과: {len(cart_res.data or [])}개 아이템")

        if not cart_res.data:
            return render_template(
                "cart/cart.html",
                items=[],
                total_amount=0,
                shipping_fee=0,
                final_total=0,
                total_count=0
            )

        cart_items = cart_res.data
        items = []
        total_amount = 0
        total_count = 0

        # 3. 각 cart_item에 대해 product 및 option 정보 조회
        for cart_item in cart_items:
            try:
                product_id = cart_item["product_id"]
                option_id = cart_item["option_id"]
                quantity = cart_item["quantity"]

                # product 정보 조회
                prod_res = (
                    supabase.table("products")
                    .select("id, name, price, sale_price, product_images(image_url, is_primary)")
                    .eq("id", product_id)
                    .single()
                    .execute()
                )

                if not prod_res.data:
                    logger.warning(f"[장바구니] 상품 조회 실패: product_id={product_id}")
                    continue

                product = prod_res.data

                # option 정보 조회 (color, size, additional_price, stock 등)
                opt_res = (
                    supabase.table("product_options")
                    .select("id, color, size, stock, stock_quantity, additional_price")
                    .eq("id", option_id)
                    .single()
                    .execute()
                )

                option = opt_res.data if opt_res.data else {}

                # 재고 확인 (stock 우선, 없으면 stock_quantity)
                stock = _get_stock(option)
                is_out_of_stock = stock == 0

                # 가격 계산
                raw_price = product.get("sale_price") if product.get("sale_price") is not None else product.get("price", 0)
                unit_price = int(float(raw_price or 0)) + int(float(option.get("additional_price") or 0))

                # 썸네일 이미지
                images = product.get("product_images") or []
                primary_img = next((img for img in images if img.get("is_primary")), None)
                thumbnail_url = primary_img.get("image_url") if primary_img else (images[0].get("image_url") if images else "https://picsum.photos/600/800")

                # item 구성
                item = {
                    "id": cart_item["id"],  # cart_id (DB pk)
                    "product_id": product_id,
                    "option_id": option_id,
                    "name": product.get("name"),
                    "color": option.get("color"),
                    "size": option.get("size"),
                    "price": unit_price,
                    "thumbnail_url": thumbnail_url,
                    "quantity": quantity,
                    "stock": stock,
                    "is_out_of_stock": is_out_of_stock,
                    "key": cart_item["id"]  # 호환성 유지
                }

                items.append(item)
                total_amount += unit_price * quantity
                total_count += quantity

            except Exception as e:
                logger.warning(f"[장바구니 항목 처리 오류] cart_item={cart_item}, error={e}", exc_info=True)
                continue

        total_amount, shipping_fee, final_total = _calc_totals(items)
        logger.info(f"[장바구니 조회 완료] 최종 {len(items)}개 아이템, 상품금액={total_amount}, 배송비={shipping_fee}")
        return render_template(
            "cart/cart.html",
            items=items,
            total_amount=total_amount,
            shipping_fee=shipping_fee,
            final_total=final_total,
            total_count=total_count
        )

    except Exception as e:
        logger.error(f"[장바구니 조회 오류] 예외 발생: {e}", exc_info=True)
        if _is_auth_error(e):
            flash("로그인이 만료되었습니다. 다시 로그인해 주세요.", "error")
            return redirect(url_for("auth.login", next=url_for("cart.view_cart")))
        flash("장바구니 조회 중 오류가 발생했습니다.", "error")
        return redirect(url_for("main.index"))


@cart_bp.route("/add", methods=["POST"])
def add_to_cart():
    """
    장바구니 상품 추가 (AJAX / Form):
    1. 요청 바디에서 product_option_id, quantity 수신
       (기존 product_id, color, size 기반 호출도 하위 호환 지원)
    2. 로그인 여부 확인: 미로그인 시 /auth/login 으로 리다이렉트
    3. product_options.stock 조회하여 재고 부족 시 에러 반환 및 DB 쓰기 방지
    4. carts 테이블에 upsert (같은 옵션이면 수량 누적)
    5. 누적 후 수량이 재고를 초과하게 되는 경우도 에러 처리
    6. 성공 시 JSON: {"success": True, "message": "장바구니에 담겼습니다"}
    """
    data = request.get_json(silent=True) or request.form
    logger.info(f"[장바구니 추가] 요청 데이터: {data}")

    # 1. 로그인 확인: 미로그인 시 /auth/login 으로 리다이렉트
    user = session.get("user")
    if not user or not user.get("id"):
        logger.warning("[장바구니 추가] 미로그인 상태")
        return redirect(url_for("auth.login", next=request.referrer or url_for("cart.view_cart")))

    user_id = user["id"]
    logger.info(f"[장바구니 추가] user_id={user_id}")

    # 2. 파라미터 파싱 (product_option_id)
    product_option_id = data.get("product_option_id")
    raw_qty = data.get("quantity", 1)
    try:
        quantity = int(raw_qty)
        if quantity < 1:
            quantity = 1
    except (ValueError, TypeError):
        quantity = 1

    supabase = _get_user_client()

    if not supabase:
        logger.error("[장바구니 추가] Supabase 연결 실패")
        return jsonify({"success": False, "message": "데이터베이스 연결에 실패했습니다."}), 500

    try:
        # 하위 호환성: 만약 product_option_id 대신 product_id, color, size가 전달된 경우 매핑
        if not product_option_id:
            product_id = data.get("product_id")
            color = data.get("color") or ""
            size = data.get("size") or ""
            logger.info(f"[장바구니 추가] product_id={product_id}, color={color}, size={size} (옵션 검색 중)")
            
            if product_id:
                opt_query = (
                    supabase.table("product_options")
                    .select("id, color, size, stock, stock_quantity")
                    .eq("product_id", product_id)
                )
                if color:
                    opt_query = opt_query.eq("color", color)
                if size:
                    opt_query = opt_query.eq("size", size)
                opt_res = opt_query.order("id").execute()

                logger.info(f"[장바구니 추가] 옵션 조회 결과: {len(opt_res.data or [])}개")
                # 첫 옵션이 품절(Black/S 등)일 수 있으므로 재고가 충분한 첫 옵션을 선택
                in_stock = [o for o in (opt_res.data or []) if _get_stock(o) >= quantity]
                if in_stock:
                    logger.info(f"[장바구니 추가] 찾은 옵션: {in_stock[0]}")
                    product_option_id = in_stock[0]["id"]
                elif opt_res.data:
                    logger.warning("[장바구니 추가] 해당 상품의 모든 옵션이 재고 부족")
                    return jsonify({"success": False, "message": "선택하신 상품은 현재 품절 상태입니다."}), 400
                else:
                    logger.warning(f"[장바구니 추가] 매칭하는 옵션을 찾을 수 없음 (color={color}, size={size})")

        if not product_option_id:
            logger.error("[장바구니 추가] product_option_id 파싱 실패")
            return jsonify({"success": False, "message": "상품 옵션 ID가 전달되지 않았습니다."}), 400

        try:
            product_option_id = int(product_option_id)
        except (ValueError, TypeError):
            logger.error(f"[장바구니 추가] product_option_id 타입 변환 실패: {product_option_id}")
            return jsonify({"success": False, "message": "유효하지 않은 상품 옵션 ID입니다."}), 400

        # 3. product_options.stock 조회
        opt_res = (
            supabase.table("product_options")
            .select("id, product_id, stock, stock_quantity, color, size, additional_price")
            .eq("id", product_option_id)
            .execute()
        )

        if not opt_res.data or len(opt_res.data) == 0:
            logger.error(f"[장바구니 추가] product_option 조회 실패: {product_option_id}")
            return jsonify({"success": False, "message": "존재하지 않는 상품 옵션입니다."}), 404

        option = opt_res.data[0]
        stock = _get_stock(option)

        # 4. 담기 전 요청 수량이 재고보다 많은 경우 에러 반환 (DB에 아무것도 쓰지 않음)
        if stock < quantity:
            logger.warning(f"[장바구니 추가] 재고 부족: 요청={quantity}, 재고={stock}")
            return jsonify({
                "success": False,
                "message": f"재고가 부족합니다(현재 {stock}개)"
            }), 400

        # 5. 기존 carts 테이블 조회 (같은 옵션이면 수량 누적 검사)
        cart_item_res = (
            supabase.table("carts")
            .select("id, quantity")
            .eq("user_id", user_id)
            .eq("option_id", product_option_id)
            .execute()
        )

        existing_qty = 0
        if cart_item_res.data and len(cart_item_res.data) > 0:
            existing_qty = int(cart_item_res.data[0].get("quantity") or 0)
            logger.info(f"[장바구니 추가] 기존 수량: {existing_qty}")

        # 6. 누적 후 수량이 재고를 초과하게 되는 경우도 동일하게 에러 처리
        total_qty = existing_qty + quantity
        if total_qty > stock:
            logger.warning(f"[장바구니 추가] 누적 후 재고 초과: {existing_qty} + {quantity} > {stock}")
            return jsonify({
                "success": False,
                "message": f"재고가 부족합니다(현재 {stock}개)"
            }), 400

        # 7. carts 테이블에 upsert
        target_product_id = option.get("product_id")
        upsert_payload = {
            "user_id": user_id,
            "product_id": target_product_id,
            "option_id": product_option_id,
            "quantity": total_qty
        }
        logger.info(f"[장바구니 추가] DB upsert: {upsert_payload}")

        supabase.table("carts").upsert(
            upsert_payload,
            on_conflict="user_id,product_id,option_id"
        ).execute()
        logger.info(f"[장바구니 추가] DB upsert 완료")

        # 세션 동기화 (기존 세션 기반 뷰 호환용)
        try:
            prod_res = (
                supabase.table("products")
                .select("id, name, price, sale_price, product_images(image_url, is_primary)")
                .eq("id", target_product_id)
                .single()
                .execute()
            )
            prod_data = prod_res.data or {}
            raw_price = prod_data.get("sale_price") if prod_data.get("sale_price") is not None else prod_data.get("price", 0)
            unit_price = int(float(raw_price or 0)) + int(float(option.get("additional_price") or 0))
            images = prod_data.get("product_images") or []
            primary_img = next((img for img in images if img.get("is_primary")), None)
            thumb = primary_img.get("image_url") if primary_img else (images[0].get("image_url") if images else "https://picsum.photos/600/800")

            color_val = option.get("color")
            size_val = option.get("size")
            cart_key = f"{target_product_id}_{color_val}_{size_val}" if (color_val or size_val) else str(product_option_id)

            cart = session.get("cart", {})
            cart[cart_key] = {
                "key": cart_key,
                "product_id": target_product_id,
                "option_id": product_option_id,
                "name": prod_data.get("name"),
                "color": color_val,
                "size": size_val,
                "price": unit_price,
                "thumbnail_url": thumb,
                "quantity": total_qty
            }
            session["cart"] = cart
            session.modified = True
        except Exception as se:
            logger.warning(f"[장바구니 세션 동기화 경고] {se}")

        # 8. 성공 시 JSON 반환
        return jsonify({
            "success": True,
            "message": "장바구니에 담겼습니다"
        })

    except Exception as e:
        logger.error(f"[장바구니 추가 오류] 예외 발생: {e}", exc_info=True)
        if _is_auth_error(e):
            return _auth_expired_response()
        return jsonify({"success": False, "message": "장바구니에 담는 중 오류가 발생했습니다."}), 500


@cart_bp.route("/update", methods=["POST"])
def update_quantity():
    """
    장바구니 품목 수량 변경
    """
    data = request.get_json(silent=True) or request.form
    item_key = data.get("item_key") or data.get("product_id")
    try:
        quantity = int(data.get("quantity", 1))
    except (ValueError, TypeError):
        quantity = 1

    cart = session.get("cart", {})
    if item_key in cart:
        if quantity <= 0:
            del cart[item_key]
        else:
            cart[item_key]["quantity"] = quantity

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
    item_key = data.get("item_key") or data.get("product_id")

    cart = session.get("cart", {})
    if item_key in cart:
        del cart[item_key]
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
    현재 장바구니 품목 개수 반환 (로그인 시 DB carts 기준, 미로그인 시 세션 기준)
    """
    user = session.get("user")
    if user and user.get("id"):
        supabase = _get_user_client()
        if supabase:
            try:
                cart_res = (
                    supabase.table("carts")
                    .select("quantity")
                    .eq("user_id", user["id"])
                    .execute()
                )
                total_count = sum(item.get("quantity", 0) for item in (cart_res.data or []))
                return jsonify({"cart_count": total_count})
            except Exception as e:
                logger.warning(f"[장바구니 수량 조회 실패] {e}")
    cart = session.get("cart", {})
    total_count = sum(item.get("quantity", 1) for item in cart.values())
    return jsonify({"cart_count": total_count})


@cart_bp.route("/<int:cart_id>", methods=["DELETE"])
def delete_cart_item(cart_id):
    """
    장바구니 아이템 삭제 (DB 기반):
    1. 로그인 확인
    2. 본인 소유의 장바구니 아이템 확인
    3. carts 테이블에서 DELETE
    4. 성공 시 JSON: {"success": True, "message": "장바구니에서 삭제되었습니다."}
    """
    logger.info(f"[장바구니 삭제] 삭제 요청 시작: cart_id={cart_id}")
    
    # 1. 로그인 확인
    user = session.get("user")
    if not user or not user.get("id"):
        logger.warning(f"[장바구니 삭제] 로그인 되지 않음")
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    user_id = user["id"]
    logger.info(f"[장바구니 삭제] user_id={user_id}")

    supabase = _get_user_client()

    if not supabase:
        logger.error(f"[장바구니 삭제] DB 연결 실패")
        return jsonify({"success": False, "message": "데이터베이스 연결에 실패했습니다."}), 500

    try:
        # 2. carts 테이블에서 해당 cart_id 조회
        logger.info(f"[장바구니 삭제] carts 테이블 조회: cart_id={cart_id}")
        cart_res = (
            supabase.table("carts")
            .select("id, user_id")
            .eq("id", cart_id)
            .execute()
        )

        if not cart_res.data or len(cart_res.data) == 0:
            logger.warning(f"[장바구니 삭제] cart_id={cart_id} 존재하지 않음")
            return jsonify({"success": False, "message": "존재하지 않는 장바구니 아이템입니다."}), 404

        cart_item = cart_res.data[0]
        logger.info(f"[장바구니 삭제] 조회된 cart_item: {cart_item}")

        # 3. 본인 소유 확인 (다른 사용자의 cart_id 접근 차단)
        if str(cart_item["user_id"]) != str(user_id):
            logger.warning(f"[장바구니 삭제] 소유권 검증 실패: cart_user={cart_item['user_id']}, session_user={user_id}")
            return jsonify({"success": False, "message": "접근 권한이 없습니다."}), 403

        # 4. carts 테이블에서 DELETE
        logger.info(f"[장바구니 삭제] DELETE 실행: cart_id={cart_id}")
        delete_res = supabase.table("carts").delete().eq("id", cart_id).execute()
        logger.info(f"[장바구니 삭제] DELETE 완료: {delete_res}")

        # 5. 성공 시 JSON 반환
        logger.info(f"[장바구니 삭제] 성공: cart_id={cart_id}")
        # 세션 cart에서도 해당 항목 제거 (있는 경우)
        cart = session.get("cart", {})
        keys_to_del = [k for k, v in cart.items() if str(v.get("id")) == str(cart_id) or str(v.get("option_id")) == str(cart_item.get("option_id"))]
        for k in keys_to_del:
            del cart[k]
        if keys_to_del:
            session["cart"] = cart
            session.modified = True

        return jsonify({
            "success": True,
            "message": "장바구니에서 삭제되었습니다."
        })

    except Exception as e:
        logger.error(f"[장바구니 삭제 오류] 예외 발생: {e}", exc_info=True)
        if _is_auth_error(e):
            return _auth_expired_response()
        return jsonify({"success": False, "message": "장바구니 삭제 중 오류가 발생했습니다."}), 500


@cart_bp.route("/<int:cart_id>", methods=["PATCH"])
def update_cart_quantity(cart_id):
    """
    장바구니 아이템 수량 변경 (DB 기반):
    1. 로그인 확인
    2. 본인 소유의 장바구니 아이템 확인
    3. quantity >= 1 검증
    4. 해당 옵션의 stock 재고 검증
    5. 성공 시 UPDATE 후 subtotal(소계) 반환
    """
    # 1. 로그인 확인
    user = session.get("user")
    if not user or not user.get("id"):
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    user_id = user["id"]

    # 2. 요청 body 파싱
    data = request.get_json(silent=True) or request.form
    try:
        quantity = int(data.get("quantity", 1))
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "유효하지 않은 수량입니다."}), 400

    # 3. quantity >= 1 검증
    if quantity < 1:
        return jsonify({"success": False, "message": "수량은 1개 이상이어야 합니다."}), 400

    supabase = _get_user_client()

    if not supabase:
        return jsonify({"success": False, "message": "데이터베이스 연결에 실패했습니다."}), 500

    try:
        # 4. carts 테이블에서 해당 cart_id 조회
        cart_res = (
            supabase.table("carts")
            .select("id, user_id, product_id, option_id, quantity")
            .eq("id", cart_id)
            .execute()
        )

        if not cart_res.data or len(cart_res.data) == 0:
            return jsonify({"success": False, "message": "존재하지 않는 장바구니 아이템입니다."}), 404

        cart_item = cart_res.data[0]

        # 5. 본인 소유 확인 (다른 사용자의 cart_id 접근 차단)
        if str(cart_item["user_id"]) != str(user_id):
            return jsonify({"success": False, "message": "접근 권한이 없습니다."}), 403

        # 6. 해당 옵션의 stock 재고 검증
        option_id = cart_item["option_id"]
        opt_res = (
            supabase.table("product_options")
            .select("id, stock, stock_quantity, product_id, additional_price")
            .eq("id", option_id)
            .execute()
        )

        if not opt_res.data or len(opt_res.data) == 0:
            return jsonify({"success": False, "message": "상품 옵션 정보를 찾을 수 없습니다."}), 404

        option = opt_res.data[0]
        stock = _get_stock(option)

        # 7. 변경하려는 quantity가 재고를 초과하면 에러
        if quantity > stock:
            return jsonify({
                "success": False,
                "message": f"재고가 부족합니다(현재 {stock}개)"
            }), 400

        # 8. carts 테이블 UPDATE
        supabase.table("carts").update(
            {"quantity": quantity}
        ).eq("id", cart_id).execute()

        # 9. 소계 계산을 위해 product 정보 조회
        product_id = cart_item["product_id"]
        prod_res = (
            supabase.table("products")
            .select("id, name, price, sale_price")
            .eq("id", product_id)
            .execute()
        )

        if not prod_res.data or len(prod_res.data) == 0:
            logger.warning(f"[장바구니 수량 변경] 상품 조회 실패: {product_id}")
            return jsonify({"success": False, "message": "상품 정보를 찾을 수 없습니다."}), 404

        product = prod_res.data[0]
        raw_price = product.get("sale_price") if product.get("sale_price") is not None else product.get("price", 0)
        unit_price = int(float(raw_price or 0)) + int(float(option.get("additional_price") or 0))
        subtotal = unit_price * quantity

        # 10. 성공 시 새 소계 반환
        return jsonify({
            "success": True,
            "message": f"수량이 {quantity}개로 변경되었습니다.",
            "subtotal": subtotal,
            "quantity": quantity
        })

    except Exception as e:
        logger.error(f"[장바구니 수량 변경 오류] 예외 발생: {e}", exc_info=True)
        if _is_auth_error(e):
            return _auth_expired_response()
        return jsonify({"success": False, "message": "장바구니 수량 변경 중 오류가 발생했습니다."}), 500


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
    total_amount, shipping_fee, final_total = _calc_totals(items)
    total_count = sum(item["quantity"] for item in items)

    if request.method == "POST":
        # 주문 완료 처리: 세션 장바구니 비우기
        session.pop("cart", None)
        flash("성공적으로 주문이 접수되었습니다! 감사합니다.", "success")
        return render_template(
            "cart/checkout_success.html",
            total_amount=total_amount,
            shipping_fee=shipping_fee,
            final_total=final_total,
            total_count=total_count
        )

    return render_template(
        "cart/checkout.html",
        items=items,
        total_amount=total_amount,
        shipping_fee=shipping_fee,
        final_total=final_total,
        total_count=total_count,
        user=session.get("user")
    )
