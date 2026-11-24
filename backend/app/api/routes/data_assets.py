from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.services.hpc.storage import HPCStorageError, list_hpc_segy_files

router = APIRouter()


@router.get("/files")
def list_hpc_data_files():
    try:
        files = list_hpc_segy_files()
    except HPCStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "directory": str(settings.hpc_data_dir),
        "files": [
            {
                "fileName": item.file_name,
                "relativePath": item.relative_path,
                "sizeBytes": item.size_bytes,
            }
            for item in files
        ],
    }
