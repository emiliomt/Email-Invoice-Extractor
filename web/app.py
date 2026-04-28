from pathlib import Path

import boto3
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from src.config import settings
from web import runner

app = FastAPI(title="Invoice PDF Extractor")

_STATIC = Path(__file__).parent / "static"


# ------------------------------------------------------------------ #
# Routes                                                               #
# ------------------------------------------------------------------ #

@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    return (_STATIC / "index.html").read_text(encoding="utf-8")


class RunRequest(BaseModel):
    folder: str = "INBOX"
    dry_run: bool = False
    file_type: str = "pdf"


@app.post("/run")
async def start_run(req: RunRequest):
    if req.file_type not in ("pdf", "xml", "both"):
        return JSONResponse({"error": "file_type must be 'pdf', 'xml', or 'both'"}, status_code=422)
    started = runner.start_run(req.folder, req.dry_run, req.file_type)
    if not started:
        return JSONResponse({"error": "A run is already in progress"}, status_code=409)
    return {"started": True}


@app.get("/status")
async def status():
    s = runner.get_state()
    return {
        "running": s.running,
        "stats": s.stats,
        "started_at": s.started_at,
        "finished_at": s.finished_at,
    }


@app.get("/logs")
async def logs(since: int = 0):
    return runner.get_state().snapshot_logs(since)


@app.delete("/state")
async def reset_state():
    if settings.state_s3_key:
        client = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            endpoint_url=settings.aws_endpoint_url or None,
        )
        try:
            client.delete_object(Bucket=settings.s3_bucket_name, Key=settings.state_s3_key)
        except Exception:
            pass
    else:
        import pathlib
        p = pathlib.Path(settings.state_file_path)
        if p.exists():
            p.unlink()
    return {"reset": True}


@app.get("/batches")
async def list_batches():
    client = boto3.client(
        "s3",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        endpoint_url=settings.aws_endpoint_url or None,
    )
    prefix = settings.s3_key_prefix.rstrip("/") + "/"
    paginator = client.get_paginator("list_objects_v2")
    batches = []
    for page in paginator.paginate(Bucket=settings.s3_bucket_name, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".zip"):
                batches.append({
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "last_modified": obj["LastModified"].isoformat(),
                })
    batches.sort(key=lambda x: x["last_modified"], reverse=True)
    return {"batches": batches}
