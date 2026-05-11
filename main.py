import hashlib
import os
from fastapi import FastAPI, BackgroundTasks, Security, HTTPException, status
from fastapi import Query
from fastapi.security import APIKeyHeader
from dotenv import load_dotenv
from processor import run_etl

load_dotenv()

app = FastAPI()

api_key_header = APIKeyHeader(name="X-API-Key")

def verify_api_key(api_key: str = Security(api_key_header)):
    expected_hash = os.getenv("API_KEY_HASH")
    if not expected_hash or hashlib.sha256(api_key.encode()).hexdigest() != expected_hash:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")

@app.post("/etl/", dependencies=[Security(verify_api_key)])
async def etl(
    folder_id: str,
    sheet_id: str,
    sheet_name: str,
    filter_type: str = "both",
    anchor_column: str = Query("A", description="Cell reference (e.g. 'A' for next row, 'A3' for specific cell)"),
    is_bulk: bool = Query(False, description="True: One row per folder. False: One row per file."),
    image_max_size_mb: float = Query(0.2, ge=0.05, le=10.0, description="Target size in MB (0.2 = 200KB)"),
    video_max_size_mb: float = Query(5.0, ge=0.5, le=100.0),
    max_files: int = Query(0, ge=0, description="Max files to upload (0 = unlimited)"),
    one_pic_per_folder: bool = Query(False, description="True: Upload only 1 image per folder (skips remaining images in each folder)."),
    background_tasks: BackgroundTasks = None
):
    background_tasks.add_task(
        run_etl,
        folder_id,
        filter_type,
        sheet_id,
        sheet_name,
        anchor_column,
        is_bulk,
        image_max_size_mb,
        video_max_size_mb,
        max_files,
        one_pic_per_folder
    )
    return {
        "status": "Job Queued",
        "mode": "Bulk" if is_bulk else "Single Row",
        "settings": {"image_max_mb": image_max_size_mb, "video_max_mb": video_max_size_mb, "max_files": max_files, "one_pic_per_folder": one_pic_per_folder, "sheet": f"{sheet_name}!{anchor_column}"}
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)