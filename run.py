"""python run.py 로 서버를 띄우고 브라우저를 연다."""

import os
import threading
import webbrowser

import uvicorn
from dotenv import load_dotenv

load_dotenv()

PORT = int(os.environ.get("PORT", "8765"))
URL = f"http://127.0.0.1:{PORT}"


def main() -> None:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("\n  [경고] ANTHROPIC_API_KEY 가 없습니다.")
        print("  .env.example 을 .env 로 복사한 뒤 키를 넣어주세요.")
        print("  (서버는 그대로 뜨지만 댓글 생성 시 오류가 납니다.)\n")

    print(f"\n  공감 댓글 생성기 → {URL}\n  종료하려면 Ctrl+C\n")
    threading.Timer(1.2, lambda: webbrowser.open(URL)).start()
    uvicorn.run("app:app", host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
