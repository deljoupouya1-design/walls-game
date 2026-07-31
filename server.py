import asyncio
import uuid
import pathlib
from collections import deque
from typing import Dict, List, Tuple
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- تنظیمات ربات تلگرام ---
BOT_TOKEN = "8907207711:AAHU0MSi5VxVF2_spApYywY8hr3R9IivNjM"
SHORT_NAME = "WallsGame"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def send_welcome(message: types.Message):
    await message.answer(
        "سلام! به بازی دو نفره Walls خوش آمدی 🎮\n\n"
        "برای ساختن یک اتاق بازی جدید، دستور /play را بفرست."
    )

@dp.message(Command("play"))
async def start_game(message: types.Message):
    try:
        game_id = str(uuid.uuid4())[:6]
        bot_info = await bot.get_me()
        app_url = f"https://t.me/{bot_info.username}/{SHORT_NAME}?startapp={game_id}"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎮 ورود به این مسابقه", url=app_url)]
        ])
        
        await message.answer(
            f"🔴 <b>یک مسابقه جدید ایجاد شد!</b>\n\n"
            f"آیدی اتاق: <code>{game_id}</code>\n\n"
            f"این پیام را برای دوستت فوروارد کن تا هر دو روی دکمه زیر کلیک کنید:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Error in /play command: {e}")

# --- مدیریت چرخه حیات سرور و اجرای هم‌زمان ربات ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # هنگام روشن شدن سرور، پولینگ ربات در پس‌زمینه اجرا می‌شود
    polling_task = asyncio.create_task(dp.start_polling(bot))
    yield
    # هنگام خاموش شدن
    polling_task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def serve_frontend():
    html_path = pathlib.Path("frontend/index.html")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))

class GameRoom:
    def __init__(self):
        self.sockets: Dict[WebSocket, dict] = {}
        self.current_turn: str = "p1"
        self.p1_pos = (8, 4)
        self.p2_pos = (0, 4)
        self.p1_walls_left = 10
        self.p2_walls_left = 10
        self.walls: List[dict] = []
        self.is_game_over = False
        self.winner = None

    def assign_role(self, websocket: WebSocket, user_id: str) -> str:
        for ws, info in list(self.sockets.items()):
            if info["user_id"] == user_id:
                return info["role"]

        active_roles = [info["role"] for info in self.sockets.values()]
        if "p1" not in active_roles:
            role = "p1"
        elif "p2" not in active_roles:
            role = "p2"
        else:
            role = "watch"

        self.sockets[websocket] = {"user_id": user_id, "role": role}
        return role

    def switch_turn(self):
        self.current_turn = "p2" if self.current_turn == "p1" else "p1"

    def is_path_blocked(self, start_pos: Tuple[int, int], target_row: int, temp_walls: List[dict]) -> bool:
        queue = deque([start_pos])
        visited = set([start_pos])
        while queue:
            r, c = queue.popleft()
            if r == target_row:
                return False
            for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                nr, nc = r + dr, c + dc
                if 0 <= nr < 9 and 0 <= nc < 9 and (nr, nc) not in visited:
                    if not self.is_wall_blocking(r, c, nr, nc, temp_walls):
                        visited.add((nr, nc))
                        queue.append((nr, nc))
        return True

    def is_wall_blocking(self, r1: int, c1: int, r2: int, c2: int, temp_walls: List[dict]) -> bool:
        for w in temp_walls:
            wr, wc, ori = w["r"], w["c"], w["ori"]
            if c1 == c2:
                top_r = min(r1, r2)
                if ori == "H" and wr == top_r and (wc == c1 or wc == c1 - 1):
                    return True
            elif r1 == r2:
                left_c = min(c1, c2)
                if ori == "V" and wc == left_c and (wr == r1 or wr == r1 - 1):
                    return True
        return False

    def validate_wall_placement(self, new_wall: dict) -> bool:
        for w in self.walls:
            if w["r"] == new_wall["r"] and w["c"] == new_wall["c"]:
                return False
            if w["ori"] == new_wall["ori"]:
                if w["ori"] == "H" and w["r"] == new_wall["r"] and abs(w["c"] - new_wall["c"]) < 2:
                    return False
                if w["ori"] == "V" and w["c"] == new_wall["c"] and abs(w["r"] - new_wall["r"]) < 2:
                    return False

        temp_walls = self.walls + [new_wall]
        if self.is_path_blocked(self.p1_pos, 0, temp_walls) or self.is_path_blocked(self.p2_pos, 8, temp_walls):
            return False
        return True

rooms: Dict[str, GameRoom] = {}

@app.websocket("/ws/{game_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: str, user_id: str):
    await websocket.accept()
    if game_id not in rooms:
        rooms[game_id] = GameRoom()
    
    room = rooms[game_id]
    role = room.assign_role(websocket, user_id)

    await websocket.send_json({
        "type": "init",
        "role": role,
        "current_turn": room.current_turn,
        "p1_walls": room.p1_walls_left,
        "p2_walls": room.p2_walls_left,
        "is_game_over": room.is_game_over,
        "winner": room.winner
    })

    try:
        while True:
            data = await websocket.receive_json()
            if room.is_game_over:
                continue

            sender_info = room.sockets.get(websocket)
            if not sender_info or sender_info["role"] not in ["p1", "p2"]:
                continue

            sender_role = sender_info["role"]
            if sender_role != room.current_turn:
                await websocket.send_json({"type": "error", "message": "نوبت شما نیست!"})
                continue

            msg_type = data.get("type")
            
            if msg_type == "move":
                new_r, new_c = data["row"], data["col"]
                curr_r, curr_c = room.p1_pos if sender_role == "p1" else room.p2_pos

                if (abs(curr_r - new_r) + abs(curr_c - new_c)) != 1:
                    await websocket.send_json({"type": "error", "message": "حرکت غیرمجاز!"})
                    continue

                if room.is_wall_blocking(curr_r, curr_c, new_r, new_c, room.walls):
                    await websocket.send_json({"type": "error", "message": "دیوار مانع حرکت است!"})
                    continue

                if sender_role == "p1":
                    room.p1_pos = (new_r, new_c)
                    if new_r == 0:
                        room.is_game_over, room.winner = True, "p1"
                else:
                    room.p2_pos = (new_r, new_c)
                    if new_r == 8:
                        room.is_game_over, room.winner = True, "p2"
                    
                room.switch_turn()
                data.update({"next_turn": room.current_turn, "player_role": sender_role, "is_game_over": room.is_game_over, "winner": room.winner})

                for ws_conn in list(room.sockets.keys()):
                    await ws_conn.send_json(data)

            elif msg_type == "wall":
                walls_left = room.p1_walls_left if sender_role == "p1" else room.p2_walls_left
                if walls_left <= 0:
                    await websocket.send_json({"type": "error", "message": "دیواری باقی نمانده است!"})
                    continue

                wall_dict = {"r": data.get("r"), "c": data.get("c"), "ori": data.get("orientation")}
                if wall_dict["r"] is None or wall_dict["c"] is None or not room.validate_wall_placement(wall_dict):
                    await websocket.send_json({"type": "error", "message": "دیوار نامعتبر است!"})
                    continue

                if sender_role == "p1":
                    room.p1_walls_left -= 1
                else:
                    room.p2_walls_left -= 1

                room.walls.append(wall_dict)
                room.switch_turn()
                data.update({"next_turn": room.current_turn, "player_role": sender_role, "p1_walls": room.p1_walls_left, "p2_walls": room.p2_walls_left})

                for ws_conn in list(room.sockets.keys()):
                    await ws_conn.send_json(data)

    except Exception:
        pass
    finally:
        if websocket in room.sockets:
            del room.sockets[websocket]
        if len(room.sockets) == 0 and game_id in rooms:
            del rooms[game_id]