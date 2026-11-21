"""미스터리 셀러 웹 서버 (HTTP + WebSocket, 외부 라이브러리 없음).

    python web_server.py [포트]        # 기본 8080, 환경변수 PORT 도 지원

브라우저에서 http://서버주소:포트 로 접속하면 바로 게임이 열려요.
게임 규칙/진행은 server.py(GameServer)를 그대로 사용합니다.
"""
import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import struct
import sys
import traceback
from urllib.parse import unquote, urlsplit

from common import MAX_MSG
from server import GameServer, Player

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def ws_frame(opcode: int, payload: bytes) -> bytes:
    n = len(payload)
    if n < 126:
        head = struct.pack(">BB", 0x80 | opcode, n)
    elif n < 65536:
        head = struct.pack(">BBH", 0x80 | opcode, 126, n)
    else:
        head = struct.pack(">BBQ", 0x80 | opcode, 127, n)
    return head + payload


async def read_frame(reader):
    b1, b2 = await reader.readexactly(2)
    fin, op = b1 & 0x80, b1 & 0x0F
    n = b2 & 0x7F
    if n == 126:
        (n,) = struct.unpack(">H", await reader.readexactly(2))
    elif n == 127:
        (n,) = struct.unpack(">Q", await reader.readexactly(8))
    if n > MAX_MSG:
        raise ConnectionError("frame too large")
    mask = await reader.readexactly(4) if b2 & 0x80 else None
    data = await reader.readexactly(n)
    if mask and n:
        key = (mask * (n // 4 + 1))[:n]
        data = (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(n, "big")
    return fin, op, data


class WSWriter:
    """GameServer 가 쓰는 writer.write(pack(msg)) 를 웹소켓 텍스트 프레임으로 바꿔 준다."""

    def __init__(self, writer):
        self.w = writer

    def write(self, data: bytes):
        if self.w.is_closing():            # 이미 끊긴 연결에는 보내지 않음
            return
        self.w.write(ws_frame(0x1, data[4:]))     # 앞의 4바이트 길이 헤더는 웹소켓에선 불필요


class WebServer:
    def __init__(self):
        self.game = GameServer()

    async def handle(self, reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
            lines = head.decode("latin1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            headers = {}
            for ln in lines[1:]:
                if ":" in ln:
                    k, _, v = ln.partition(":")
                    headers[k.strip().lower()] = v.strip()
            if headers.get("upgrade", "").lower() == "websocket":
                await self.ws_session(reader, writer, headers)
            else:
                self.serve_file(writer, method, urlsplit(target).path)
                await writer.drain()
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, OSError, ValueError):
            pass
        except Exception:
            traceback.print_exc()
        finally:
            try:
                writer.close()
            except Exception:
                pass

    # ── 정적 파일 ────────────────────────────────────────
    def serve_file(self, writer, method, path):
        path = unquote(path)
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if not full.startswith(STATIC + os.sep) or not os.path.isfile(full):
            body, status, ctype = "찾을 수 없어요".encode("utf-8"), "404 Not Found", "text/plain; charset=utf-8"
        else:
            with open(full, "rb") as f:
                body = f.read()
            status = "200 OK"
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
        cache = "no-cache" if path.endswith(".html") else "public, max-age=86400"
        out = (f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
               f"Cache-Control: {cache}\r\nConnection: close\r\n\r\n").encode("latin1")
        writer.write(out + (b"" if method == "HEAD" else body))

    # ── 웹소켓 ──────────────────────────────────────────
    async def ws_session(self, reader, writer, headers):
        key = headers.get("sec-websocket-key", "")
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        p = Player(WSWriter(writer))
        buf = b""
        try:
            while True:
                fin, op, data = await read_frame(reader)
                if op == 0x8:
                    break
                if op == 0x9:
                    writer.write(ws_frame(0xA, data))
                    continue
                if op not in (0x1, 0x0):
                    continue
                buf += data
                if len(buf) > MAX_MSG:
                    break
                if not fin:
                    continue
                raw, buf = buf, b""
                try:
                    msg = json.loads(raw.decode("utf-8"))
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    try:
                        self.game.dispatch(p, msg)
                    except Exception:
                        traceback.print_exc()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            self.game.remove_player(p)


async def serve(port):
    web = WebServer()
    server = await asyncio.start_server(web.handle, "0.0.0.0", port)
    asyncio.get_running_loop().create_task(web.game.ticker())
    print(f"[미스터리 셀러] http://localhost:{port} 에서 실행 중 (종료: Ctrl+C)")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8080))
    try:
        asyncio.run(serve(port))
    except KeyboardInterrupt:
        pass
