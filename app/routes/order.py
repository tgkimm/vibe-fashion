import datetime
import logging
import random
import re
import time
import uuid
from flask import Blueprint, render_template, request, session, redirect, url_for, flash
from app.utils import (
    get_user_supabase_client as _get_user_client,
    get_supabase_client,
    get_supabase_admin_client,
)

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
    pattern = r"^01[0-9]-\d{3,4}-\d{4}$"
    return bool(re.match(pattern, (phone or "").strip()))


def _validate_address(address):
    """배송지 주소 최소 5자 이상 검증."""
    return len((address or "").strip()) >= 5


def _generate_order_number():
    """'VF-' + 오늘날짜(YYYYMMDD) + '-' + 4자리 랜덤숫자 + 밀리초 타임스탬프 뒷 3자리."""
    today_str = datetime.datetime.now().strftime("%Y%m%d")
    rand_4 = f"{random.randint(0, 9999):04d}"
    ms_tail = f"{int(time.time() * 1000) % 1000:03d}"
    return f"VF-{today_str}-{rand_4}{ms_tail}"


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
            # 프로필 및 auth 메타데이터에서 기본 배송지/연락처/이름 조회
            default_address = user.get("address") or ""
            default_name = user.get("real_name") or user.get("name") or ""
            default_phone = user.get("phone") or ""

            # 1) profiles 테이블 조회 (phone, name 등)
            try:
                profile_res = (
                    supabase.table("profiles")
                    .select("*")
                    .eq("id", user_id)
                    .single()
                    .execute()
                )
                if profile_res.data:
                    p_data = profile_res.data
                    default_name = p_data.get("name") or default_name
                    default_phone = p_data.get("phone") or default_phone
                    if p_data.get("address"):
                        default_address = p_data.get("address")
            except Exception as e:
                logger.warning(f"[주문서 profiles 조회 경고] {e}")

            # 2) auth.users 메타데이터 조회 (마이페이지 기본 배송지가 메타데이터에 저장된 경우 대비)
            if not default_address or not default_phone:
                try:
                    admin_client = get_supabase_admin_client()
                    if admin_client:
                        u_res = admin_client.auth.admin.get_user_by_id(user_id)
                        if u_res and getattr(u_res, "user", None):
                            meta = getattr(u_res.user, "user_metadata", {}) or {}
                            if not default_address and meta.get("address"):
                                default_address = meta.get("address")
                            if not default_phone and meta.get("phone"):
                                default_phone = meta.get("phone")
                            if not default_name:
                                default_name = meta.get("real_name") or meta.get("full_name") or meta.get("name") or default_name
                except Exception as e:
                    logger.warning(f"[주문서 auth.users 메타데이터 조회 경고] {e}")

            logger.info(f"[주문서 조회 완료] {len(items)}개 아이템, 총액={total_amount}, 기본배송지={'있음' if default_address else '없음'}")
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

        # POST 요청 처리 - create_order 로 위임
        return create_order()

    except Exception as e:
        logger.error(f"[주문서 조회 오류] 예외 발생: {e}", exc_info=True)
        if _is_auth_error(e):
            flash("로그인이 만료되었습니다. 다시 로그인해 주세요.", "error")
            return redirect(url_for("auth.login", next=url_for("order.checkout")))
        flash("주문서 조회 중 오류가 발생했습니다.", "error")
        return redirect(url_for("main.index"))


@order_bp.route("/create", methods=["POST"])
def create_order():
    """
    주문 생성 처리: POST /order/create
    처리 순서:
    1. 장바구니 조회 + 재고 확인 (재고 부족 시 에러, 처리 중단, 아무것도 쓰지 않음)
    2. 배송지 입력값 서버 측 재검증 (휴대폰 번호 패턴, 주소 최소 길이)
    3. 주문번호 생성: 'VF-' + 오늘날짜(YYYYMMDD) + '-' + 4자리 랜덤숫자 + 밀리초 타임스탬프 뒷 3자리
    4. orders 테이블에 INSERT (status='PAID')
    5. order_items INSERT (상품명, 색상, 사이즈, 가격 스냅샷)
    6. product_options.stock 차감 (service_role 키 사용, 조건부 UPDATE: WHERE id = 옵션ID AND stock >= 수량)
       - 영향받은 행이 0개면 "방금 재고가 소진되었습니다" 에러로 롤백 처리 (생성된 orders/order_items 삭제 및 복원)
    7. carts 아이템 DELETE
    8. /order/complete/<order_id> 리다이렉트
    """
    # 0. 로그인 확인
    user = session.get("user")
    if not user or not user.get("id"):
        flash("로그인이 필요합니다. 다시 로그인해 주세요.", "warning")
        return redirect(url_for("auth.login", next=url_for("order.checkout")))

    user_id = user["id"]
    logger.info(f"[주문 생성 시작] user_id={user_id}")

    supabase = _get_user_client()
    admin_supabase = get_supabase_admin_client()

    if not supabase or not admin_supabase:
        logger.error("[주문 생성] Supabase 클라이언트 초기화 실패")
        flash("데이터베이스 연결에 실패했습니다.", "error")
        return redirect(url_for("order.checkout"))

    try:
        # 1. 장바구니 조회 + 재고 사전 확인 (재고 부족 시 에러, 처리 중단, 아무것도 쓰지 않음)
        cart_res = (
            supabase.table("carts")
            .select("id, product_id, option_id, quantity")
            .eq("user_id", user_id)
            .order("created_at", desc=False)
            .execute()
        )
        cart_items = cart_res.data or []
        if not cart_items:
            flash("장바구니가 비어 있습니다.", "info")
            return redirect(url_for("cart.view_cart"))

        items = []
        for cart_item in cart_items:
            product_id = cart_item["product_id"]
            option_id = cart_item["option_id"]
            quantity = int(cart_item.get("quantity") or 1)

            # 상품 정보 조회
            prod_res = (
                supabase.table("products")
                .select("id, name, price, sale_price")
                .eq("id", product_id)
                .single()
                .execute()
            )
            if not prod_res.data:
                flash("상품 정보를 찾을 수 없습니다.", "error")
                return redirect(url_for("cart.view_cart"))
            product = prod_res.data

            # 옵션 정보 및 현재 재고 조회
            opt_res = (
                supabase.table("product_options")
                .select("id, color, size, stock, stock_quantity, additional_price")
                .eq("id", option_id)
                .single()
                .execute()
            )
            if not opt_res.data:
                flash("상품 옵션 정보를 찾을 수 없습니다.", "error")
                return redirect(url_for("cart.view_cart"))
            option = opt_res.data

            current_stock = _get_stock(option)
            if current_stock < quantity:
                logger.warning(f"[주문 생성 중단] 재고 부족: option_id={option_id}, 재고={current_stock}, 요청={quantity}")
                flash(f"'{product.get('name')}' 상품의 재고가 부족합니다 (현재 {current_stock}개).", "warning")
                return redirect(url_for("cart.view_cart"))

            raw_price = product.get("sale_price") if product.get("sale_price") is not None else product.get("price", 0)
            unit_price = int(float(raw_price or 0)) + int(float(option.get("additional_price") or 0))

            items.append({
                "cart_id": cart_item["id"],
                "product_id": product_id,
                "option_id": option_id,
                "name": product.get("name"),
                "color": option.get("color"),
                "size": option.get("size"),
                "price": unit_price,
                "quantity": quantity,
                "current_stock": current_stock,
                "option_raw": option,
            })

        total_amount, shipping_fee, final_total = _calc_totals(items)

        # 2. 배송지 입력값 서버 측 재검증 (휴대폰 번호 패턴, 주소 최소 길이)
        recipient_name = request.form.get("recipient_name", "").strip()
        phone_number = request.form.get("phone_number", "").strip()
        shipping_address = request.form.get("shipping_address", "").strip()
        shipping_memo = request.form.get("shipping_memo", "").strip()
        custom_shipping_memo = request.form.get("custom_shipping_memo", "").strip()

        # 직접 입력 선택 시 custom_shipping_memo 값 사용
        if shipping_memo == "직접 입력":
            shipping_memo = custom_shipping_memo

        if not recipient_name:
            flash("수령인 이름을 입력해 주세요.", "warning")
            return redirect(url_for("order.checkout"))

        if not _validate_phone(phone_number):
            flash("휴대폰 번호는 010-0000-0000 형식으로 입력해 주세요.", "warning")
            return redirect(url_for("order.checkout"))

        if not _validate_address(shipping_address):
            flash("배송 주소는 최소 5자 이상 입력해 주세요.", "warning")
            return redirect(url_for("order.checkout"))

        # 3. 주문번호 생성: 'VF-' + 오늘날짜(YYYYMMDD) + '-' + 4자리 랜덤숫자 + 밀리초 뒷 3자리
        order_number = _generate_order_number()
        order_id = str(uuid.uuid4())
        logger.info(f"[주문 번호 생성] order_number={order_number}, order_id={order_id}")

        shipping_data = {
            "recipient_name": recipient_name,
            "phone_number": phone_number,
            "address": shipping_address,
            "memo": shipping_memo,
        }

        # 4. orders 테이블에 INSERT (status='PAID')
        order_payload = {
            "id": order_id,
            "user_id": user_id,
            "order_number": order_number,
            "total_amount": total_amount,
            "payment_amount": final_total,
            "status": "PAID",
            "shipping_address": shipping_data,
            "payment_method": "dummy",
        }
        supabase.table("orders").insert(order_payload).execute()
        logger.info(f"[orders INSERT 성공] order_id={order_id}")

        # 5. order_items INSERT (상품명, 색상, 사이즈, 가격 스냅샷)
        order_items_payload = []
        for item in items:
            opt_desc_parts = [p for p in [item.get("color"), item.get("size")] if p]
            opt_desc = " / ".join(opt_desc_parts) if opt_desc_parts else None
            order_items_payload.append({
                "order_id": order_id,
                "product_id": item["product_id"],
                "option_id": item["option_id"],
                "product_name": item["name"],
                "option_name": opt_desc,
                "unit_price": item["price"],
                "quantity": item["quantity"],
                "total_price": item["price"] * item["quantity"],
            })

        supabase.table("order_items").insert(order_items_payload).execute()
        logger.info(f"[order_items INSERT 성공] {len(order_items_payload)}개 항목")

        # 6. product_options.stock 차감 (service_role 키 사용, 조건부 UPDATE)
        #    UPDATE ... SET stock = stock - 수량 WHERE id = 옵션ID AND stock >= 수량
        #    영향받은 행이 0개면 "방금 재고가 소진되었습니다" 에러로 롤백 처리
        decremented_options = []  # 롤백 대비 이력
        for item in items:
            opt_id = item["option_id"]
            qty = item["quantity"]

            # 최신 재고 조회
            latest_opt_res = (
                admin_supabase.table("product_options")
                .select("id, stock, stock_quantity")
                .eq("id", opt_id)
                .single()
                .execute()
            )
            latest_opt = latest_opt_res.data or {}
            curr_stock_val = latest_opt.get("stock")
            curr_qty_val = latest_opt.get("stock_quantity")

            # stock 컬럼 또는 stock_quantity 컬럼 중 활성화된 컬럼 기준으로 조건부 UPDATE
            update_payload = {}
            query = admin_supabase.table("product_options").eq("id", opt_id)

            if curr_stock_val is not None and curr_stock_val >= qty:
                update_payload["stock"] = curr_stock_val - qty
                query = query.gte("stock", qty)
            elif curr_qty_val is not None and curr_qty_val >= qty:
                update_payload["stock_quantity"] = curr_qty_val - qty
                query = query.gte("stock_quantity", qty)
            else:
                # 조건 만족 불가 -> 롤백
                logger.warning(f"[재고 차감 실패] 조건 불충족: opt_id={opt_id}")
                _rollback_order(admin_supabase, order_id, decremented_options)
                flash("방금 재고가 소진되었습니다.", "error")
                return redirect(url_for("cart.view_cart"))

            update_res = query.update(update_payload).execute()
            affected_rows = update_res.data or []

            if len(affected_rows) == 0:
                # 영향받은 행 0개 -> 동시성 재고 소진 발생, 롤백
                logger.warning(f"[재고 차감 실패 - 영향받은 행 0] opt_id={opt_id}, qty={qty}")
                _rollback_order(admin_supabase, order_id, decremented_options)
                flash("방금 재고가 소진되었습니다.", "error")
                return redirect(url_for("cart.view_cart"))

            decremented_options.append({
                "option_id": opt_id,
                "qty": qty,
                "target_col": "stock" if "stock" in update_payload else "stock_quantity",
            })
            logger.info(f"[재고 차감 성공] opt_id={opt_id}, 차감수량={qty}")

        # 7. carts 아이템 DELETE
        for cart_item in cart_items:
            supabase.table("carts").delete().eq("id", cart_item["id"]).execute()
        session.pop("cart", None)
        logger.info(f"[장바구니 비우기 완료] user_id={user_id}")

        # 8. /order/complete/<order_id> 리다이렉트
        flash("주문이 성공적으로 완료되었습니다!", "success")
        return redirect(url_for("order.order_complete", order_id=order_id))

    except Exception as e:
        logger.error(f"[주문 생성 오류] 예외 발생: {e}", exc_info=True)
        if _is_auth_error(e):
            flash("로그인이 만료되었습니다. 다시 로그인해 주세요.", "error")
            return redirect(url_for("auth.login", next=url_for("order.checkout")))
        flash("주문 처리 중 오류가 발생했습니다. 다시 시도해 주세요.", "error")
        return redirect(url_for("order.checkout"))


def _rollback_order(admin_supabase, order_id, decremented_options):
    """재고 부족 시 주문 데이터 삭제 및 이미 차감된 재고 복원."""
    try:
        # 차감했던 재고 원상 복구
        for dec in decremented_options:
            opt_id = dec["option_id"]
            qty = dec["qty"]
            col = dec["target_col"]
            cur_res = admin_supabase.table("product_options").select(col).eq("id", opt_id).single().execute()
            if cur_res.data:
                new_val = (cur_res.data.get(col) or 0) + qty
                admin_supabase.table("product_options").update({col: new_val}).eq("id", opt_id).execute()

        # order_items 및 orders 삭제
        admin_supabase.table("order_items").delete().eq("order_id", order_id).execute()
        admin_supabase.table("orders").delete().eq("id", order_id).execute()
        logger.info(f"[주문 롤백 완료] order_id={order_id}")
    except Exception as re:
        logger.error(f"[주문 롤백 중 오류] {re}", exc_info=True)


@order_bp.route("/complete/<order_id>", methods=["GET"])
@order_bp.route("/checkout-success", methods=["GET"])
def order_complete(order_id=None):
    """
    주문 완료 페이지: GET /order/complete/<order_id>
    - 로그인 필수
    - 본인 주문 확인 (orders.user_id != session.user.id 차단)
    - 주문번호, 배송지, 주문 상품 목록, 결제 금액 표시
    - 마이페이지로 / 쇼핑 계속하기 버튼 제공
    """
    user = session.get("user")
    if not user or not user.get("id"):
        flash("로그인이 필요한 페이지입니다.", "warning")
        return redirect(url_for("auth.login"))

    user_id = user["id"]
    target_order_id = order_id or request.args.get("order_id")

    if not target_order_id:
        flash("주문 정보가 없습니다.", "warning")
        return redirect(url_for("main.index"))

    admin_supabase = get_supabase_admin_client()
    if not admin_supabase:
        logger.error("[주문 완료 조회] Supabase 연결 실패")
        flash("데이터베이스 연결에 실패했습니다.", "error")
        return redirect(url_for("main.index"))

    try:
        # 1. 주문 정보 조회
        order_res = (
            admin_supabase.table("orders")
            .select("*")
            .eq("id", target_order_id)
            .single()
            .execute()
        )

        order_data = order_res.data
        if not order_data:
            flash("존재하지 않는 주문입니다.", "warning")
            return redirect(url_for("main.index"))

        # 2. 본인 주문 여부 확인 (다른 사용자의 order_id 접근 차단)
        if str(order_data.get("user_id")) != str(user_id):
            logger.warning(f"[주문 완료 조회 차단] 접근 권한 없음: order_user={order_data.get('user_id')}, session_user={user_id}")
            flash("해당 주문 내역에 접근할 권한이 없습니다.", "error")
            return redirect(url_for("main.index"))

        # 3. 금액 및 배송지 정보 계산
        total_amount = float(order_data.get("total_amount") or 0)
        payment_amount = float(order_data.get("payment_amount") or 0)
        shipping_fee = payment_amount - total_amount
        shipping_address = order_data.get("shipping_address") or {}

        # 4. 주문 상세 상품 목록 조회
        items_res = (
            admin_supabase.table("order_items")
            .select("*")
            .eq("order_id", target_order_id)
            .order("id")
            .execute()
        )
        items = items_res.data or []
        total_count = sum(item.get("quantity", 0) for item in items)
        order_status = str(order_data.get("status") or "").lower()

        return render_template(
            "order/checkout_success.html",
            order_id=target_order_id,
            order=order_data,
            order_number=order_data.get("order_number"),
            order_status=order_status,
            shipping_address=shipping_address,
            user=user,
            items=items,
            total_amount=total_amount,
            shipping_fee=shipping_fee,
            final_total=payment_amount,
            total_count=total_count,
            created_at=order_data.get("created_at")
        )

    except Exception as e:
        logger.error(f"[주문 완료 조회 오류] {e}", exc_info=True)
        flash("주문 정보를 불러오는 중 오류가 발생했습니다.", "error")
        return redirect(url_for("main.index"))
