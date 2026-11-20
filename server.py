"""미스터리 셀러 서버.

    python server.py [포트]

방 코드로 방을 찾아 접속하는 구조라서 누군가는 서버를 켜 둬야 합니다.
(클라이언트에서 서버 주소를 비워 두면 게임 안에서 자동으로 서버를 열어 주기도 해요.)
"""
import asyncio
import json
import random
import struct
import sys
import threading
import time
import traceback
from collections import Counter

from common import *


class Player:
    _seq = 1

    def __init__(self, writer):
        self.pid = Player._seq
        Player._seq += 1
        self.writer = writer
        self.name = ""
        self.room = None
        self.at_lobby = True      # 결과 화면을 보는 중이면 False
        self.uploads = []

    def send(self, msg):
        try:
            self.writer.write(pack(msg))
        except Exception:
            pass


class Room:
    def __init__(self, code, max_players):
        self.code = code
        self.max_players = max_players
        self.mode = "default"      # default | upload | mix
        self.players = {}          # pid -> Player (입장 순서 유지)
        self.host = None
        self.images = {}           # id -> (owner pid, base64 jpeg)
        self.img_seq = 0
        # 게임 진행 상태
        self.phase = "lobby"       # lobby | vote | sale
        self.game_ids = []
        self.seller = None
        self.buyers = []
        self.candidates = []
        self.votes = {}
        self.price_input = None
        self.price = DEFAULT_PRICE
        self.item = None
        self.bought = []
        self.deadline = 0.0


def err(msg):
    return {"t": "error", "msg": msg}


class GameServer:
    def __init__(self):
        self.rooms = {}

    # ── 접속 처리 ─────────────────────────────────────────
    async def handle(self, reader, writer):
        p = Player(writer)
        try:
            while True:
                (n,) = struct.unpack(">I", await reader.readexactly(4))
                if n > MAX_MSG:
                    break
                body = await reader.readexactly(n)
                try:
                    msg = json.loads(body.decode("utf-8"))
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                try:
                    self.dispatch(p, msg)
                except Exception:
                    traceback.print_exc()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            self.remove_player(p)
            try:
                writer.close()
            except Exception:
                pass

    def dispatch(self, p, msg):
        fn = getattr(self, "on_" + str(msg.get("t")), None)
        if fn:
            fn(p, msg)

    async def ticker(self):
        while True:
            await asyncio.sleep(0.2)
            now = time.monotonic()
            for room in list(self.rooms.values()):
                try:
                    if room.phase == "vote" and now >= room.deadline:
                        self.finish_vote(room)
                    elif room.phase == "sale" and now >= room.deadline:
                        self.finish_sale(room)
                except Exception:
                    traceback.print_exc()

    # ── 도우미 ───────────────────────────────────────────
    @staticmethod
    def clean_name(s):
        s = "".join(ch for ch in str(s) if ch.isprintable()).strip()
        return s[:MAX_NAME_LEN]

    def unique_name(self, room, name):
        taken = {q.name for q in room.players.values()}
        if name not in taken:
            return name
        i = 2
        while f"{name[:MAX_NAME_LEN - 3]}({i})" in taken:
            i += 1
        return f"{name[:MAX_NAME_LEN - 3]}({i})"

    def new_code(self):
        while True:
            code = "".join(random.choice(CODE_CHARS) for _ in range(CODE_LEN))
            if code not in self.rooms:
                return code

    def room_state(self, room):
        return {
            "code": room.code, "max": room.max_players, "mode": room.mode,
            "host": room.host, "phase": room.phase,
            "upload_total": len(room.images),
            "players": [
                {"id": q.pid, "name": q.name, "at_lobby": q.at_lobby, "uploads": len(q.uploads)}
                for q in room.players.values()
            ],
        }

    def broadcast(self, room, msg):
        for q in room.players.values():
            q.send(msg)

    def broadcast_room(self, room):
        self.broadcast(room, {"t": "room", "room": self.room_state(room)})

    @staticmethod
    def participants(room):
        return [room.players[i] for i in room.game_ids if i in room.players]

    def pool(self, room):
        ids = []
        if room.mode in ("default", "mix"):
            ids += [f"d{i}" for i in range(DEFAULT_ITEM_COUNT)]
        if room.mode in ("upload", "mix"):
            ids += list(room.images.keys())
        return ids

    # ── 방 만들기 / 참가 / 나가기 ───────────────────────────
    def on_create(self, p, msg):
        if p.room:
            return p.send(err("이미 방에 들어가 있어요"))
        name = self.clean_name(msg.get("name", ""))
        if not name:
            return p.send(err("이름을 입력해 주세요"))
        try:
            mx = int(msg.get("max", DEFAULT_MAX_PLAYERS))
        except (TypeError, ValueError):
            mx = DEFAULT_MAX_PLAYERS
        if not MIN_PLAYERS <= mx <= MAX_PLAYERS_LIMIT:
            return p.send(err(f"최대 인원은 {MIN_PLAYERS}~{MAX_PLAYERS_LIMIT}명이에요"))
        room = Room(self.new_code(), mx)
        self.rooms[room.code] = room
        self.enter_room(p, room, name)

    def on_join(self, p, msg):
        if p.room:
            return p.send(err("이미 방에 들어가 있어요"))
        name = self.clean_name(msg.get("name", ""))
        if not name:
            return p.send(err("이름을 입력해 주세요"))
        code = str(msg.get("code", "")).strip().upper()
        room = self.rooms.get(code)
        if not room:
            return p.send(err("그런 방 코드는 없어요"))
        if len(room.players) >= room.max_players:
            return p.send(err("방이 가득 찼어요"))
        self.enter_room(p, room, name)

    def enter_room(self, p, room, name):
        p.name = self.unique_name(room, name)
        p.room = room
        p.at_lobby = True
        p.uploads = []
        room.players[p.pid] = p
        if room.host is None:
            room.host = p.pid
        p.send({"t": "joined", "you": p.pid, "room": self.room_state(room)})
        for iid, (_owner, data) in room.images.items():
            p.send({"t": "image", "id": iid, "data": data})
        self.broadcast_room(room)

    def on_leave(self, p, msg):
        self.remove_player(p)

    def remove_player(self, p):
        room = p.room
        if not room:
            return
        p.room = None
        room.players.pop(p.pid, None)
        if not room.players:
            self.rooms.pop(room.code, None)
            return
        if room.host == p.pid:
            room.host = next(iter(room.players))
        if room.phase != "lobby" and p.pid in room.game_ids:
            if p.pid == room.seller:
                self.abort(room, "판매자가 나가서 게임이 끝났어요")
            else:
                if p.pid in room.buyers:
                    room.buyers.remove(p.pid)
                room.votes.pop(p.pid, None)
                if not room.buyers:
                    self.abort(room, "구매자가 모두 나갔어요")
                else:
                    self.check_progress(room)
        self.broadcast_room(room)

    def abort(self, room, text):
        for q in self.participants(room):
            q.at_lobby = True
            q.send({"t": "aborted", "msg": text})
        room.phase = "lobby"

    # ── 인게임 로비 ───────────────────────────────────────
    def on_set_mode(self, p, msg):
        room = p.room
        if not room or room.host != p.pid or room.phase != "lobby":
            return
        if msg.get("mode") in ("default", "upload", "mix"):
            room.mode = msg["mode"]
            self.broadcast_room(room)

    def on_upload(self, p, msg):
        room = p.room
        if not room or room.phase != "lobby":
            return
        if room.mode == "default":
            return p.send(err("기본 사진 모드에서는 업로드할 수 없어요"))
        if len(p.uploads) >= MAX_UPLOADS_PER_PLAYER:
            return p.send(err(f"사진은 한 사람당 {MAX_UPLOADS_PER_PLAYER}장까지 올릴 수 있어요"))
        data = msg.get("data")
        if not isinstance(data, str) or not data or len(data) > MAX_IMAGE_B64:
            return p.send(err("사진 파일이 너무 크거나 올바르지 않아요"))
        room.img_seq += 1
        iid = f"u{room.img_seq}"
        room.images[iid] = (p.pid, data)
        p.uploads.append(iid)
        self.broadcast(room, {"t": "image", "id": iid, "data": data})
        self.broadcast_room(room)

    def on_clear_uploads(self, p, msg):
        room = p.room
        if not room or room.phase != "lobby":
            return
        for iid in p.uploads:
            room.images.pop(iid, None)
        p.uploads = []
        self.broadcast_room(room)

    def on_back_lobby(self, p, msg):
        room = p.room
        if room:
            p.at_lobby = True
            self.broadcast_room(room)

    # ── 게임 시작 → 투표 ─────────────────────────────────
    def on_start(self, p, msg):
        room = p.room
        if not room:
            return
        if room.host != p.pid:
            return p.send(err("방장만 시작할 수 있어요"))
        if room.phase != "lobby":
            return
        if len(room.players) < MIN_PLAYERS:
            return p.send(err(f"최소 {MIN_PLAYERS}명이 필요해요"))
        if any(not q.at_lobby for q in room.players.values()):
            return p.send(err("아직 결과 화면을 보고 있는 사람이 있어요"))
        pool = self.pool(room)
        if not pool:
            return p.send(err("사용할 사진이 없어요. 사진을 업로드해 주세요"))

        room.game_ids = list(room.players.keys())
        room.seller = random.choice(room.game_ids)
        room.buyers = [i for i in room.game_ids if i != room.seller]
        room.candidates = random.sample(pool, min(CANDIDATES, len(pool)))
        room.votes = {}
        room.price_input = None
        room.price = DEFAULT_PRICE
        room.item = None
        room.bought = []
        room.phase = "vote"
        room.deadline = time.monotonic() + VOTE_SECONDS
        seller = room.players[room.seller]
        for q in self.participants(room):
            q.at_lobby = False
            is_seller = q.pid == room.seller
            q.send({
                "t": "game_start",
                "role": "seller" if is_seller else "buyer",
                "seller": room.seller, "seller_name": seller.name,
                "candidates": [] if is_seller else room.candidates,   # 판매자는 후보를 모름
                "ends_in": VOTE_SECONDS,
            })
        self.broadcast_room(room)

    def on_price(self, p, msg):
        room = p.room
        if not room or room.phase != "vote" or p.pid != room.seller:
            return
        try:
            v = int(msg.get("price"))
        except (TypeError, ValueError):
            v = None
        room.price_input = v if v and 0 < v <= MAX_PRICE else None

    def on_vote(self, p, msg):
        room = p.room
        if not room or room.phase != "vote" or p.pid not in room.buyers:
            return
        idx = msg.get("idx")
        if not isinstance(idx, int) or not 0 <= idx < len(room.candidates):
            return
        room.votes[p.pid] = idx
        self.check_progress(room)

    def send_timer(self, room):
        left = max(0.0, room.deadline - time.monotonic())
        for q in self.participants(room):
            q.send({"t": "timer", "ends_in": left})

    def check_progress(self, room):
        now = time.monotonic()
        if room.phase == "vote" and room.buyers and all(b in room.votes for b in room.buyers):
            new = now + VOTE_ALL_DONE_SECONDS
            if room.deadline > new:
                room.deadline = new
                self.send_timer(room)
        elif room.phase == "sale" and room.buyers and all(b in room.bought for b in room.buyers):
            new = now + SALE_ALL_DONE_SECONDS
            if room.deadline > new:
                room.deadline = new
                self.send_timer(room)

    def finish_vote(self, room):
        counts = Counter(room.votes.values())
        if counts:
            top = max(counts.values())
            winners = [i for i, c in counts.items() if c == top]
        else:
            winners = list(range(len(room.candidates)))
        room.item = room.candidates[random.choice(winners)]
        room.price = room.price_input or DEFAULT_PRICE
        room.bought = []
        room.phase = "sale"
        room.deadline = time.monotonic() + SALE_SECONDS
        for q in self.participants(room):
            is_seller = q.pid == room.seller
            q.send({
                "t": "sale_start",
                "item": None if is_seller else room.item,   # 판매자는 물건을 못 봄
                "price": room.price, "ends_in": SALE_SECONDS,
            })

    # ── 판매 단계 ─────────────────────────────────────────
    def on_buy(self, p, msg):
        room = p.room
        if not room or room.phase != "sale" or p.pid not in room.buyers or p.pid in room.bought:
            return
        room.bought.append(p.pid)
        p.send({"t": "bought", "self": True})
        seller = room.players.get(room.seller)
        if seller:
            seller.send({"t": "bought", "name": p.name})
        self.check_progress(room)

    def on_chat(self, p, msg):
        room = p.room
        if not room or room.phase != "sale" or p.pid not in room.game_ids:
            return
        text = str(msg.get("text", "")).replace("\n", " ").strip()[:MAX_CHAT_LEN]
        if not text:
            return
        out = {"t": "chat", "name": p.name, "text": text, "from_seller": p.pid == room.seller}
        if p.pid == room.seller:                 # 판매자 → 모든 구매자
            for b in room.buyers:
                if b in room.players:
                    room.players[b].send(out)
        else:                                    # 구매자 → 판매자
            seller = room.players.get(room.seller)
            if seller:
                seller.send(out)

    def finish_sale(self, room):
        names = [room.players[i].name for i in room.bought if i in room.players]
        seller = room.players.get(room.seller)
        result = {
            "t": "result",
            "seller_name": seller.name if seller else "?",
            "item": room.item, "price": room.price,
            "buyers": names, "count": len(names), "total": room.price * len(names),
        }
        for q in self.participants(room):
            q.send(result)       # 이후 각자 "로비로 나가기"를 눌러야 at_lobby 가 됨
        room.phase = "lobby"
        self.broadcast_room(room)


# ── 실행 ─────────────────────────────────────────────────
async def serve(host, port):
    srv = GameServer()
    server = await asyncio.start_server(srv.handle, host, port)
    asyncio.get_running_loop().create_task(srv.ticker())
    print(f"[서버] {host}:{port} 에서 대기 중 (종료: Ctrl+C)")
    async with server:
        await server.serve_forever()


def start_background(port=DEFAULT_PORT, host="0.0.0.0"):
    """게임 클라이언트 안에서 서버를 백그라운드로 띄운다. 실패하면 OSError."""
    ready = threading.Event()
    errors = []

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        srv = GameServer()
        try:
            loop.run_until_complete(asyncio.start_server(srv.handle, host, port))
        except OSError as e:
            errors.append(e)
            ready.set()
            return
        loop.create_task(srv.ticker())
        ready.set()
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    ready.wait(5)
    if errors:
        raise errors[0]


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    try:
        asyncio.run(serve("0.0.0.0", port))
    except KeyboardInterrupt:
        pass
