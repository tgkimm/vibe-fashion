import os
from app import create_app

# 애플리케이션 인스턴스 생성 (팩토리 패턴 사용)
app = create_app()

if __name__ == "__main__":
    """
    개발용 로컬 서버 실행:
    환경 변수에서 포트와 디버그 모드를 지정할 수 있습니다.
    기본 포트: 5000
    """
    port = int(os.getenv("PORT", 5000))
    # Python 3.13 환경에서 Flask 내장 개발 서버 구동
    app.run(host="0.0.0.0", port=port, debug=True)
