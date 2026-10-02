import functools
import os
from flask import Flask, session, request, redirect, url_for, abort
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix

# .env 파일에서 환경 변수 불러오기
load_dotenv()


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


def admin_required(view):
    """
    관리자 권한 필수 데코레이터:
    - 세션에 user_id가 없으면 /auth/login으로 리다이렉트
    - profiles 테이블의 role이 'admin'이 아니면 403 반환
    """
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        user = session.get("user")
        user_id = user.get("id") if user else None
        if not user_id:
            login_url = url_for("auth.login", error="login_required", next=request.path)
            return redirect(login_url)

        try:
            from app.utils import get_supabase_admin_client, get_supabase_client
            admin_client = get_supabase_admin_client()
            db_client = admin_client or get_supabase_client()
            if not db_client:
                abort(403)
            res = db_client.table("profiles").select("role").eq("id", user_id).execute()
            if not res.data or res.data[0].get("role") != "admin":
                abort(403)
        except Exception:
            abort(403)

        return view(**kwargs)
    return wrapped_view


def create_app():
    """
    애플리케이션 팩토리 함수:
    Flask 앱 인스턴스를 생성하고 설정 및 블루프린트(라우트)를 등록합니다.
    """
    # 1. Flask 애플리케이션 객체 생성
    app = Flask(__name__)

    # Azure App Service 리버스 프록시 헤더(X-Forwarded-Proto, X-Forwarded-Host 등) 신뢰 처리
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    # 2. 기본 설정 적용
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "default-dev-secret-key")

    # 3. 블루프린트(라우트 분리) 등록
    from app.routes.main import main_bp
    from app.routes.auth import auth_bp
    from app.routes.cart import cart_bp
    from app.routes.order import order_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(cart_bp)
    app.register_blueprint(order_bp)

    # 4. 관리자 라우트 등록
    @app.route("/admin")
    @login_required
    @admin_required
    def admin_dashboard():
        from datetime import datetime, timezone, timedelta
        from flask import render_template
        from app.utils import get_supabase_admin_client, get_supabase_client

        admin_client = get_supabase_admin_client()
        db_client = admin_client or get_supabase_client()

        # 한국 표준시(KST: UTC+9) 기준 오늘 날짜 산출
        kst = timezone(timedelta(hours=9))
        now_kst = datetime.now(kst)
        today_start_kst = datetime(now_kst.year, now_kst.month, now_kst.day, 0, 0, 0, tzinfo=kst)
        # DB 비교용 UTC ISO 문자열
        today_start_utc_iso = today_start_kst.astimezone(timezone.utc).isoformat()
        today_str = now_kst.strftime("%Y-%m-%d")

        today_order_count = 0
        today_sales = 0
        pending_refund_count = 0
        total_member_count = 0
        recent_orders = []

        if db_client:
            try:
                # 1. 오늘 주문 조회 (오늘 00:00 KST 이후 생성된 주문)
                today_orders_res = (
                    db_client.table("orders")
                    .select("id, status, total_amount, payment_amount")
                    .gte("created_at", today_start_utc_iso)
                    .execute()
                )
                today_orders = today_orders_res.data or []

                # ① 오늘 신규 주문 수: status != 'CANCELLED' (대소문자 무관 비교)
                today_order_count = sum(
                    1 for o in today_orders if (o.get("status") or "").upper() != "CANCELLED"
                )

                # ② 오늘 매출: status IN ('PAID', 'PREPARING', 'SHIPPED', 'DELIVERED') 합계
                paid_statuses = {"PAID", "PREPARING", "SHIPPED", "DELIVERED"}
                today_sales = sum(
                    float(o.get("total_amount") or o.get("payment_amount") or 0)
                    for o in today_orders
                    if (o.get("status") or "").upper() in paid_statuses
                )
            except Exception as e:
                app.logger.warning(f"[관리자 대시보드 오늘 주문 집계 오류] {e}")

            try:
                # ③ 처리 대기 환불: status = 'REQUESTED' (대소문자 무관) count
                # Supabase의 eq('status', 'REQUESTED') 및 'requested' 모두 대응
                refunds_res = (
                    db_client.table("refunds")
                    .select("id, status")
                    .execute()
                )
                refunds_data = refunds_res.data or []
                pending_refund_count = sum(
                    1 for r in refunds_data if (r.get("status") or "").upper() == "REQUESTED"
                )
            except Exception as e:
                app.logger.warning(f"[관리자 대시보드 환불 집계 오류] {e}")

            try:
                # ④ 전체 회원 수: profiles count
                profiles_res = db_client.table("profiles").select("id", count="exact").execute()
                if hasattr(profiles_res, "count") and profiles_res.count is not None:
                    total_member_count = profiles_res.count
                else:
                    total_member_count = len(profiles_res.data or [])
            except Exception as e:
                app.logger.warning(f"[관리자 대시보드 회원 수 집계 오류] {e}")

            try:
                # 최근 주문 5건 (주문번호, 고객이메일, 금액, 상태, 주문시각)
                recent_orders_res = (
                    db_client.table("orders")
                    .select("id, order_number, total_amount, payment_amount, status, created_at, profiles(email, name)")
                    .order("created_at", desc=True)
                    .limit(5)
                    .execute()
                )
                raw_recent_orders = recent_orders_res.data or []
                for ro in raw_recent_orders:
                    # 고객 이메일 추출
                    prof = ro.get("profiles") or {}
                    customer_email = prof.get("email") or "미등록"

                    # 주문 시각 포맷 (KST 변환)
                    created_at_raw = ro.get("created_at") or ""
                    created_at_formatted = "-"
                    if created_at_raw:
                        try:
                            # 'Z' 또는 ISO timezone 파싱
                            dt = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
                            dt_kst = dt.astimezone(kst)
                            created_at_formatted = dt_kst.strftime("%Y-%m-%d %H:%M")
                        except Exception:
                            created_at_formatted = created_at_raw[:16]

                    recent_orders.append({
                        "order_number": ro.get("order_number") or ro.get("id"),
                        "customer_email": customer_email,
                        "total_amount": float(ro.get("total_amount") or ro.get("payment_amount") or 0),
                        "status": ro.get("status") or "-",
                        "created_at_formatted": created_at_formatted,
                    })
            except Exception as e:
                app.logger.warning(f"[관리자 대시보드 최근 주문 조회 오류] {e}")

        return render_template(
            "admin/dashboard.html",
            today_str=today_str,
            today_order_count=today_order_count,
            today_sales=today_sales,
            pending_refund_count=pending_refund_count,
            total_member_count=total_member_count,
            recent_orders=recent_orders,
        )

    @app.route("/admin/products", defaults={"product_id": None}, methods=["GET", "POST"])
    @app.route("/admin/products/<product_id>", methods=["PATCH"])
    @login_required
    @admin_required
    def admin_products(product_id=None):
        """
        관리자 상품 관리 페이지:
        - GET /admin/products: 전체 상품 및 카테고리 목록 조회
        - POST /admin/products: 신규 상품 등록 (JSON 또는 Form 요청)
        """
        from flask import render_template, request, jsonify
        from app.utils import get_supabase_admin_client, get_supabase_client

        admin_client = get_supabase_admin_client()
        db_client = admin_client or get_supabase_client()

        # POST 요청 처리: 신규 상품 등록
        if request.method == "POST":
            if not db_client:
                return jsonify({"success": False, "message": "데이터베이스 연결에 실패했습니다."}), 500

            data = request.get_json(silent=True) or request.form

            name = (data.get("name") or "").strip()
            category_id = data.get("category_id")
            description = (data.get("description") or "").strip()
            image_url = (data.get("image_url") or "").strip()
            raw_price = data.get("price")
            raw_original_price = data.get("original_price") if "original_price" in data else data.get("sale_price")

            # 1. 필수 필드 및 유효성 검증
            if not name:
                return jsonify({"success": False, "message": "상품 이름을 입력해 주세요."}), 400

            try:
                price = float(raw_price) if raw_price is not None and str(raw_price).strip() != "" else 0
            except (ValueError, TypeError):
                return jsonify({"success": False, "message": "유효한 가격을 입력해 주세요."}), 400

            if price <= 0:
                return jsonify({"success": False, "message": "가격은 0보다 커야 합니다."}), 400

            # 정가 검증: 정가는 가격보다 작을 수 없음
            original_price = None
            if raw_original_price is not None and str(raw_original_price).strip() != "":
                try:
                    original_price = float(raw_original_price)
                except (ValueError, TypeError):
                    return jsonify({"success": False, "message": "유효한 정가를 입력해 주세요."}), 400

                if original_price < price:
                    return jsonify({"success": False, "message": "정가는 가격보다 작을 수 없습니다."}), 400
            else:
                # 정가가 명시되지 않았으면 기본적으로 판매가격과 동일하게 설정
                original_price = price

            # 카테고리 ID 정수 변환 (선택 사항 또는 유효한 카테고리)
            parsed_category_id = None
            if category_id:
                try:
                    parsed_category_id = int(category_id)
                except (ValueError, TypeError):
                    parsed_category_id = None

            try:
                # 2. products 테이블에 저장 (is_active=true 명시적 삽입)
                # 스키마 매핑:
                # schema.sql: price NUMERIC (정가/기본가격), sale_price NUMERIC (할인가/판매가)
                # 정가(original_price)와 가격(price)의 관계:
                # original_price > price 인 경우 할인 상품 -> price=original_price, sale_price=price
                # original_price == price 인 경우 일반 상품 -> price=price, sale_price=NULL
                db_price = original_price
                db_sale_price = price if original_price > price else None

                product_insert_payload = {
                    "name": name,
                    "description": description or None,
                    "price": db_price,
                    "sale_price": db_sale_price,
                    "is_active": True,
                }
                if parsed_category_id is not None:
                    product_insert_payload["category_id"] = parsed_category_id

                prod_res = db_client.table("products").insert(product_insert_payload).execute()
                new_product = prod_res.data[0] if (prod_res.data and len(prod_res.data) > 0) else None

                if not new_product:
                    return jsonify({"success": False, "message": "상품 등록에 실패했습니다."}), 500

                new_product_id = new_product.get("id")

                # 3. 이미지 URL이 제공된 경우 product_images 테이블에 등록
                if image_url and new_product_id:
                    try:
                        db_client.table("product_images").insert({
                            "product_id": new_product_id,
                            "image_url": image_url,
                            "is_primary": True,
                            "sort_order": 1,
                        }).execute()
                    except Exception as img_err:
                        app.logger.warning(f"[상품 이미지 등록 오류] {img_err}")

                # 4. 등록된 상품 정보 조회 및 반환 (목록 즉시 반영용)
                # 카테고리명 조회
                category_name = "미지정"
                if parsed_category_id:
                    cat_check = db_client.table("categories").select("name").eq("id", parsed_category_id).execute()
                    if cat_check.data:
                        category_name = cat_check.data[0].get("name") or "미지정"

                return jsonify({
                    "success": True,
                    "message": "신규 상품이 성공적으로 등록되었습니다.",
                    "product": {
                        "id": new_product_id,
                        "name": new_product.get("name"),
                        "description": new_product.get("description") or "",
                        "category_id": parsed_category_id,
                        "category_name": category_name,
                        "price": price,
                        "original_price": original_price,
                        "total_stock": 0,
                        "is_active": True,
                        "thumbnail_url": image_url or "",
                        "image_url": image_url or "",
                    }
                }), 201

            except Exception as e:
                app.logger.error(f"[신규 상품 등록 오류] {e}", exc_info=True)
                return jsonify({"success": False, "message": f"상품 등록 중 오류가 발생했습니다: {str(e)}"}), 500

        # PATCH 요청 처리: 기존 상품 수정
        if request.method == "PATCH":
            import math
            from flask import request, jsonify
            from app.utils import get_supabase_admin_client, get_supabase_client

            admin_client = get_supabase_admin_client()
            update_client = admin_client or get_supabase_client()
            if not update_client:
                return jsonify({"success": False, "message": "데이터베이스 연결에 실패했습니다."}), 500

            data = request.get_json(silent=True) or request.form
            name = (data.get("name") or "").strip()
            category_id = data.get("category_id")
            description = (data.get("description") or "").strip()
            image_url = (data.get("image_url") or "").strip()

            if not name:
                return jsonify({"success": False, "message": "상품 이름을 입력해 주세요."}), 400

            try:
                price = float(data.get("price"))
            except (ValueError, TypeError):
                return jsonify({"success": False, "message": "유효한 가격을 입력해 주세요."}), 400
            if not math.isfinite(price) or price <= 0:
                return jsonify({"success": False, "message": "가격은 0보다 커야 합니다."}), 400

            try:
                original_price = float(data.get("original_price"))
            except (ValueError, TypeError):
                return jsonify({"success": False, "message": "유효한 정가를 입력해 주세요."}), 400
            if not math.isfinite(original_price) or original_price < price:
                return jsonify({"success": False, "message": "정가는 가격보다 작을 수 없습니다."}), 400

            parsed_category_id = None
            if category_id:
                try:
                    parsed_category_id = int(category_id)
                except (ValueError, TypeError):
                    return jsonify({"success": False, "message": "유효한 카테고리를 선택해 주세요."}), 400

            try:
                db_price = original_price
                db_sale_price = price if original_price > price else None
                update_client.table("products").update({
                    "name": name,
                    "description": description or None,
                    "category_id": parsed_category_id,
                    "price": db_price,
                    "sale_price": db_sale_price,
                }).eq("id", product_id).execute()

                image_query = (
                    update_client.table("product_images")
                    .select("id")
                    .eq("product_id", product_id)
                    .eq("is_primary", True)
                    .execute()
                )
                primary_image = (image_query.data or [None])[0]
                if image_url:
                    image_payload = {"image_url": image_url, "is_primary": True, "sort_order": 1}
                    if primary_image:
                        update_client.table("product_images").update(image_payload).eq("id", primary_image["id"]).execute()
                    else:
                        update_client.table("product_images").insert({
                            "product_id": product_id,
                            **image_payload,
                        }).execute()
                elif primary_image:
                    update_client.table("product_images").delete().eq("id", primary_image["id"]).execute()

                category_name = "미지정"
                if parsed_category_id is not None:
                    category_result = update_client.table("categories").select("name").eq("id", parsed_category_id).execute()
                    if category_result.data:
                        category_name = category_result.data[0].get("name") or category_name

                product_result = (
                    update_client.table("products")
                    .select("id, name, description, price, sale_price, is_active, category_id, product_options(stock, stock_quantity), product_images(image_url, is_primary, sort_order)")
                    .eq("id", product_id)
                    .single()
                    .execute()
                )
                updated = product_result.data
                images = updated.get("product_images") or []
                images.sort(key=lambda image: (not image.get("is_primary", False), image.get("sort_order", 0)))
                thumbnail_url = images[0].get("image_url") if images else ""
                options = updated.get("product_options") or []
                total_stock = sum(max(int(option.get("stock") or 0), int(option.get("stock_quantity") or 0)) for option in options)
                effective_price = float(updated.get("sale_price") if updated.get("sale_price") is not None else updated.get("price") or 0)
                return jsonify({
                    "success": True,
                    "message": "상품 정보가 성공적으로 수정되었습니다.",
                    "product": {
                        "id": updated.get("id"),
                        "name": updated.get("name") or name,
                        "description": updated.get("description") or "",
                        "category_id": updated.get("category_id"),
                        "category_name": category_name,
                        "price": effective_price,
                        "original_price": float(updated.get("price") or 0),
                        "total_stock": total_stock,
                        "is_active": bool(updated.get("is_active", False)),
                        "thumbnail_url": thumbnail_url,
                    },
                })
            except Exception as e:
                app.logger.error(f"[상품 수정 오류] {e}", exc_info=True)
                return jsonify({"success": False, "message": f"상품 수정 중 오류가 발생했습니다: {str(e)}"}), 500

        # GET 요청 처리: 상품 목록 및 카테고리 목록 조회
        products = []
        categories = []
        if db_client:
            try:
                # 카테고리 목록 조회
                cat_res = db_client.table("categories").select("id, name, slug").order("sort_order", desc=False).execute()
                categories = cat_res.data or []
            except Exception as e:
                app.logger.warning(f"[관리자 카테고리 목록 조회 오류] {e}")

            try:
                # RLS를 우회/관리자 권한으로 비활성 상품 포함 전체 상품 조회
                res = (
                    db_client.table("products")
                    .select(
                        "id, category_id, name, description, price, sale_price, is_active, created_at, "
                        "categories(id, name), "
                        "product_options(id, stock, stock_quantity), "
                        "product_images(image_url, is_primary, sort_order)"
                    )
                    .order("created_at", desc=True)
                    .execute()
                )
                raw_products = res.data or []

                for item in raw_products:
                    # 1. 카테고리명
                    category = item.get("categories") or {}
                    category_name = category.get("name") or "미지정"

                    # 2. 가격 및 정가 (sale_price가 있으면 판매가격이 sale_price, 정가가 price)
                    raw_price = float(item.get("price") or 0)
                    raw_sale_price = float(item["sale_price"]) if item.get("sale_price") is not None else None
                    if raw_sale_price is not None:
                        effective_price = raw_sale_price
                        original_price = raw_price
                    else:
                        effective_price = raw_price
                        original_price = raw_price

                    # 3. 옵션 총 재고 계산 (stock / stock_quantity 호환 처리)
                    options = item.get("product_options") or []
                    total_stock = sum(
                        max(int(opt.get("stock") or 0), int(opt.get("stock_quantity") or 0))
                        for opt in options
                    )

                    # 4. 대표 이미지
                    images = item.get("product_images") or []
                    images.sort(key=lambda x: (not x.get("is_primary", False), x.get("sort_order", 0)))
                    primary_img = next((img for img in images if img.get("is_primary")), None)
                    thumbnail_url = (
                        primary_img.get("image_url")
                        if primary_img
                        else (images[0].get("image_url") if images else "")
                    )

                    products.append({
                        "id": item.get("id"),
                        "name": item.get("name") or "",
                        "description": item.get("description") or "",
                        "category_id": item.get("category_id"),
                        "category_name": category_name,
                        "price": effective_price,
                        "original_price": original_price,
                        "total_stock": total_stock,
                        "is_active": bool(item.get("is_active", False)),
                        "thumbnail_url": thumbnail_url,
                        "image_url": thumbnail_url,
                    })
            except Exception as e:
                app.logger.error(f"[관리자 상품 목록 조회 오류] {e}", exc_info=True)

        return render_template("admin/products.html", products=products, categories=categories)

    # 5. 템플릿 전역 변수 및 컨텍스트 프로세서 등록
    @app.context_processor
    def inject_cart_count():
        from flask import session
        current_user = session.get("user")
        total_count = 0
        if current_user and current_user.get("id"):
            try:
                from app.utils import get_user_supabase_client
                supabase = get_user_supabase_client()
                if supabase:
                    cart_res = (
                        supabase.table("carts")
                        .select("quantity")
                        .eq("user_id", current_user["id"])
                        .execute()
                    )
                    total_count = sum(item.get("quantity", 0) for item in (cart_res.data or []))
            except Exception:
                pass
        else:
            cart = session.get("cart", {})
            total_count = sum(item.get("quantity", 1) for item in cart.values())
        return {"cart_count": total_count, "current_user": current_user}

    return app
