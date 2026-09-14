"""로컬 웹 UI 서버."""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()  # anthropic 클라이언트가 만들어지기 전에 .env 를 읽어야 한다

import comment_engine  # noqa: E402
import store  # noqa: E402

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="공감 댓글 생성기")


class GenerateRequest(BaseModel):
    text: str
    count: int | None = Field(default=None, ge=1, le=8)
    extra_instruction: str = ""
    overrides: dict[str, Any] | None = None


class SampleRequest(BaseModel):
    source: str = ""
    comment: str


@app.post("/api/generate")
def api_generate(req: GenerateRequest):
    style = store.load_style()
    if req.overrides:
        # 저장하지 않고 이번 요청에만 적용되는 임시 말투 조정
        style = store.merge(style, req.overrides)
    try:
        return comment_engine.generate(
            req.text,
            count=req.count,
            extra_instruction=req.extra_instruction,
            style=style,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # 모델/네트워크 오류를 UI에 그대로 보여준다
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/config")
def api_get_config():
    return store.load_style()


@app.put("/api/config")
def api_put_config(style: dict[str, Any]):
    return store.save_style(style)


@app.get("/api/samples")
def api_get_samples():
    return store.load_samples()


@app.post("/api/samples")
def api_add_sample(req: SampleRequest):
    if not req.comment.strip():
        raise HTTPException(status_code=400, detail="댓글 내용이 비어 있습니다.")
    return store.add_sample(req.source, req.comment)


@app.delete("/api/samples/{index}")
def api_delete_sample(index: int):
    return store.delete_sample(index)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
