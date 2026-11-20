"""클라이언트/서버가 함께 쓰는 상수와 통신 규약."""
import json
import struct

DEFAULT_PORT = 5555

# ── 게임 규칙 (여기 값만 바꿔도 밸런스 조절 가능) ─────────────────
DEFAULT_ITEM_COUNT = 12      # 기본 사진 개수 (items.py 의 그림 수와 같아야 함)
DEFAULT_PRICE = 10000        # 판매자가 가격을 입력하지 않았을 때
MAX_PRICE = 99_999_999
MIN_PLAYERS = 2              # 판매자 1 + 구매자 1 이상
MAX_PLAYERS_LIMIT = 6        # 인게임 로비 슬롯이 6칸
DEFAULT_MAX_PLAYERS = 2
CANDIDATES = 4               # 투표에 올라오는 물건 수
VOTE_SECONDS = 60            # 투표 시간
VOTE_ALL_DONE_SECONDS = 3    # 모든 구매자가 투표하면 남은 시간이 이 값까지 줄어듦
SALE_SECONDS = 480           # 판매 시간 8분
SALE_ALL_DONE_SECONDS = 2    # 모든 구매자가 구매하면 바로 종료
MAX_UPLOADS_PER_PLAYER = 8
MAX_NAME_LEN = 10
MAX_CHAT_LEN = 60
MAX_IMAGE_B64 = 700_000

CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 헷갈리는 O/0/I/1 제외
CODE_LEN = 6

MAX_MSG = 4 * 1024 * 1024


def pack(msg: dict) -> bytes:
    data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    return struct.pack(">I", len(data)) + data


def recv_exact(sock, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return bytes(buf)


def read_msg(sock) -> dict:
    (n,) = struct.unpack(">I", recv_exact(sock, 4))
    if n > MAX_MSG:
        raise ConnectionError("message too large")
    return json.loads(recv_exact(sock, n).decode("utf-8"))


def fmt_won(n) -> str:
    return f"{int(n):,}원"
