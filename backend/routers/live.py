"""Live desktop streaming router — real-time desktop viewing and interaction.

Provides WebSocket-based live desktop streaming and enhanced browser interaction
capabilities for real-time remote control and monitoring.
"""

from __future__ import annotations

import asyncio
import io
import platform
import time
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agents.router import Pipeline
from capabilities.desktop_control import DesktopControl
from capabilities.browser_automation import BrowserAutomation

router = APIRouter(prefix="/api/live", tags=["live"])


class MouseMoveRequest(BaseModel):
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)


class MouseClickRequest(BaseModel):
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    button: str = Field(default="left", pattern="^(left|right|middle)$")


class TypeTextRequest(BaseModel):
    text: str = Field(..., min_length=1)
    interval: float = Field(default=0.02, ge=0.001, le=1.0)


class KeyPressRequest(BaseModel):
    key: str = Field(..., min_length=1)


class BrowserActionRequest(BaseModel):
    action: str = Field(..., pattern="^(click|fill|scroll|hover|press_key|select_option|go_back|go_forward)$")
    selector: Optional[str] = None
    value: Optional[str] = None
    pixels: Optional[int] = None


def _pipeline(request) -> Pipeline:
    return request.app.state.pipeline


def _desktop(request) -> DesktopControl:
    return _pipeline(request).desktop


def _browser(request) -> BrowserAutomation:
    return _pipeline(request).browser


@router.get("/desktop/screenshot")
async def desktop_screenshot(request):
    """Get a single desktop screenshot as image."""
    desktop = _desktop(request)
    try:
        image_bytes = await desktop.screenshot()
        return StreamingResponse(
            io.BytesIO(image_bytes),
            media_type="image/png",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
        )
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.websocket("/desktop/stream")
async def desktop_stream(websocket: WebSocket):
    """WebSocket endpoint for live desktop streaming.
    
    Sends desktop screenshots at ~2 FPS for real-time viewing.
    """
    await websocket.accept()
    desktop = _desktop(websocket)
    
    try:
        while True:
            start_time = time.time()
            
            try:
                image_bytes = await desktop.screenshot()
                # Send as base64 for WebSocket compatibility
                import base64
                image_b64 = base64.b64encode(image_bytes).decode('utf-8')
                
                await websocket.send_json({
                    "type": "frame",
                    "image": image_b64,
                    "timestamp": time.time()
                })
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "message": str(e)
                })
                break
            
            # Maintain ~2 FPS
            elapsed = time.time() - start_time
            delay = max(0, 0.5 - elapsed)
            await asyncio.sleep(delay)
            
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(e)
            })
        except:
            pass


@router.post("/desktop/mouse/move")
async def mouse_move(request, body: MouseMoveRequest):
    """Move mouse to specified coordinates."""
    desktop = _desktop(request)
    try:
        await desktop.move_mouse(body.x, body.y)
        return {"success": True, "x": body.x, "y": body.y}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/desktop/mouse/click")
async def mouse_click(request, body: MouseClickRequest):
    """Click at specified coordinates."""
    desktop = _desktop(request)
    try:
        await desktop.click(body.x, body.y, body.button)
        return {"success": True, "x": body.x, "y": body.y, "button": body.button}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/desktop/type")
async def type_text(request, body: TypeTextRequest):
    """Type text using keyboard simulation."""
    desktop = _desktop(request)
    try:
        await desktop.type_text(body.text, body.interval)
        return {"success": True, "text": body.text}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/desktop/key")
async def press_key(request, body: KeyPressRequest):
    """Press a keyboard key."""
    desktop = _desktop(request)
    try:
        # Map common key names to pyautogui format
        key_map = {
            "enter": "enter",
            "return": "enter",
            "escape": "esc",
            "tab": "tab",
            "space": "space",
            "backspace": "backspace",
            "delete": "delete",
            "up": "up",
            "down": "down",
            "left": "left",
            "right": "right",
        }
        key = key_map.get(body.key.lower(), body.key)
        
        import pyautogui
        await asyncio.to_thread(pyautogui.press, key)
        return {"success": True, "key": key}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/browser/action")
async def browser_action(request, body: BrowserActionRequest):
    """Perform enhanced browser interaction."""
    browser = _browser(request)
    
    try:
        if body.action == "click" and body.selector:
            await browser.click(body.selector)
            return {"success": True, "action": "click", "selector": body.selector}
        
        elif body.action == "fill" and body.selector and body.value:
            await browser.fill(body.selector, body.value)
            return {"success": True, "action": "fill", "selector": body.selector}
        
        elif body.action == "scroll" and body.pixels:
            result = await browser.scroll(body.pixels)
            return {"success": True, "action": "scroll", "result": result}
        
        elif body.action == "hover" and body.selector:
            result = await browser.hover(body.selector)
            return {"success": True, "action": "hover", "result": result}
        
        elif body.action == "press_key" and body.value:
            result = await browser.press_key(body.value)
            return {"success": True, "action": "press_key", "result": result}
        
        elif body.action == "select_option" and body.selector and body.value:
            result = await browser.select_option(body.selector, body.value)
            return {"success": True, "action": "select_option", "result": result}
        
        elif body.action == "go_back":
            result = await browser.go_back()
            return {"success": True, "action": "go_back", "result": result}
        
        elif body.action == "go_forward":
            result = await browser.go_forward()
            return {"success": True, "action": "go_forward", "result": result}
        
        else:
            return {"success": False, "error": "Invalid action or missing parameters"}
            
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/browser/text")
async def browser_get_text(request, selector: Optional[str] = None):
    """Get text from browser page or specific element."""
    browser = _browser(request)
    try:
        if selector:
            text = await browser.get_text(selector)
        else:
            text = await browser.extract_text()
        return {"success": True, "text": text}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/browser/attribute")
async def browser_get_attribute(request, selector: str, attribute: str):
    """Get attribute value from browser element."""
    browser = _browser(request)
    try:
        value = await browser.get_attribute(selector, attribute)
        return {"success": True, "attribute": attribute, "value": value}
    except Exception as e:
        return {"success": False, "error": str(e)}
