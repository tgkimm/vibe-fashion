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

    @app.route("/admin/products")
    @login_required
    @admin_required
    def admin_products():
        """
        관리자 상품 관리 페이지: GET /admin/products
        - DB의 전체 상품을 조회하여 목록 테이블로 표시
        - 이름, 카테고리, 가격, 정가, 재고, 활성상태 컬럼 표시
        """
        from flask import render_template
        from app.utils import get_supabase_admin_client, get_supabase_client

        admin_client = get_supabase_admin_client()
        db_client = admin_client or get_supabase_client()

        products = []
        if db_client:
            try:
                # RLS를 우회/관리자 권한으로 비활성 상품 포함 전체 상품 조회
                res = (
                    db_client.table("products")
                    .select(
                        "id, name, description, price, sale_price, is_active, created_at, "
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
                        "category_name": category_name,
                        "price": effective_price,
                        "original_price": original_price,
                        "total_stock": total_stock,
                        "is_active": bool(item.get("is_active", False)),
                        "thumbnail_url": thumbnail_url,
                    })
            except Exception as e:
                app.logger.error(f"[관리자 상품 목록 조회 오류] {e}", exc_info=True)

        return render_template("admin/products.html", products=products)

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
