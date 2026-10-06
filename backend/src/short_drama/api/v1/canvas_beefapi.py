"""源设备授权入口；只接受当前可信账号，不接收企业源或密钥参数。"""

from fastapi import APIRouter, Request

from short_drama.api.v1.canvases import Canvases
from short_drama.schemas.canvas_beefapi import CanvasBeefAPISummary, CanvasBeefAPIWallet
from short_drama.service.canvas_beefapi_service import CanvasBeefAPIService

router = APIRouter(prefix="/canvas-runtime/beefapi/connection", tags=["Canvas BeefAPI"])


def connection(request, service):
    return CanvasBeefAPIService(service.session, request.app.state.settings)


@router.get("", response_model=CanvasBeefAPISummary, response_model_exclude_none=True)
def read_connection(request: Request, service: Canvases):
    return connection(request, service).status()


@router.post("/start", response_model=CanvasBeefAPISummary, response_model_exclude_none=True)
def start_connection(request: Request, service: Canvases):
    return connection(request, service).start()


@router.post("/cancel", response_model=CanvasBeefAPISummary, response_model_exclude_none=True)
def cancel_connection(request: Request, service: Canvases):
    return connection(request, service).cancel()


@router.post("/disconnect", response_model=CanvasBeefAPISummary, response_model_exclude_none=True)
def disconnect_connection(request: Request, service: Canvases):
    return connection(request, service).disconnect()


@router.post("/open-wallet", response_model=CanvasBeefAPIWallet)
def open_wallet(request: Request, service: Canvases):
    return connection(request, service).wallet()
