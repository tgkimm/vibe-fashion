import os
from flask import Flask
from dotenv import load_dotenv

# .env 파일에서 환경 변수 불러오기
load_dotenv()

def create_app():
    """
    애플리케이션 팩토리 함수:
    Flask 앱 인스턴스를 생성하고 설정 및 블루프린트(라우트)를 등록합니다.
    """
    # 1. Flask 애플리케이션 객체 생성
    app = Flask(__name__)

    # 2. 기본 설정 적용
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "default-dev-secret-key")

    # 3. 블루프린트(라우트 분리) 등록
    from app.routes.main import main_bp
    app.register_blueprint(main_bp)

    return app
