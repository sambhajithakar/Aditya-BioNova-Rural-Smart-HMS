from fastapi import APIRouter

from .. import __version__
from ..config import get_settings
from ..schemas import HealthOut

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    s = get_settings()
    return HealthOut(
        status="ok",
        service=s.app_name,
        version=__version__,
        environment=s.environment,
    )
