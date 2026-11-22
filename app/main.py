"""FastAPI 路由层：薄封装，业务逻辑全部在 app.services / app.solver。"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db as dbmod
from . import services

DB_PATH = os.environ.get("CHRONO_DB_PATH", "/data/chrono.db")

app = FastAPI(title="考古编年核对", version="1.0.0")

_conn = None
_conn_lock = threading.Lock()  # 单 sqlite 连接串行化，保证提交事务隔离


def get_conn():
    global _conn
    if _conn is None:
        with _conn_lock:
            if _conn is None:
                _conn = dbmod.connect(DB_PATH)
                dbmod.init_db(_conn)
    return _conn


# --------------------------------------------------------------------------- #
# 请求模型（动作结构按动作类型不同而不同，用宽松模型 + services 层校验）
# --------------------------------------------------------------------------- #
class SubmissionIn(BaseModel):
    actions: List[Dict[str, Any]] = Field(..., min_length=1)


@app.exception_handler(services.ValidationError)
async def validation_error_handler(_request, exc: services.ValidationError):
    return JSONResponse(status_code=422, content={"error": exc.to_dict()})


@app.get("/api/state")
def api_state():
    """当前全部层位、依据、登记、流水与编年表。"""
    with _conn_lock:
        return services.full_state(get_conn())


@app.get("/api/chronology")
def api_chronology():
    """仅取当前编年表（无解时仍返回冲突与原始候选域）。"""
    with _conn_lock:
        return services.current_chronology(get_conn())


@app.post("/api/submissions")
def api_submit(payload: SubmissionIn):
    """按最新入库状态核对一批改动；全部依据与登记须同时成立，否则整批驳回。"""
    # ValidationError 由上方 exception_handler 统一映射为 422
    with _conn_lock:
        result = services.submit(get_conn(), payload.actions)
    if not result["accepted"]:
        return JSONResponse(status_code=409, content=result)
    return result


@app.get("/api/submissions")
def api_submissions():
    conn = get_conn()
    with _conn_lock:
        return [
            {
                "id": row["id"],
                "accepted": bool(row["accepted"]),
                "reason": row["reason"],
                "created_at": row["created_at"],
                "detail": services._safe_json(row["detail"]),
            }
            for row in dbmod.list_submissions(conn)
        ]


# --------------------------------------------------------------------------- #
# 静态页面
# --------------------------------------------------------------------------- #
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
