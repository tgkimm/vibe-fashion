import os
from flask import Flask
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix

# .env 파일에서 환경 변수 불러오기
load_dotenv()

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

    # 4. 템플릿 전역 변수 및 컨텍스트 프로세서 등록
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
