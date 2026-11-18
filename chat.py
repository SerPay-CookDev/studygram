import customtkinter as ctk
from tkinter import filedialog, messagebox
from PIL import Image
from datetime import datetime
import hashlib
import hmac
import json
import os
import secrets
import shutil
import uuid

CHAT_FILE = r"\\192.168.13.30\обмен\24-28\chat.jsonl"
CHAT_DIR = os.path.dirname(CHAT_FILE)
ATTACH_DIR = os.path.join(CHAT_DIR, "attachments")
USERS_FILE = os.path.join(CHAT_DIR, "users.json")

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

app = ctk.CTk()
app.title("Общий чат")
app.geometry("680x720")
app.minsize(440, 500)


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, 310_000
    ).hex()


def load_users():
    try:
        with open(USERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_users(users):
    temp_file = USERS_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, USERS_FILE)


def authenticate():
    result = {"name": None}
    #app.withdraw()

    dialog = ctk.CTkToplevel(app)
    dialog.title("Вход в чат")
    dialog.geometry("360x330")
    dialog.resizable(False, False)
    dialog.grab_set()

    ctk.CTkLabel(
        dialog, text="Вход или регистрация",
        font=("Segoe UI", 18, "bold")
    ).pack(pady=(22, 12))

    username = ctk.CTkEntry(
        dialog, placeholder_text="Уникальный никнейм", width=280
    )
    username.pack(pady=7)

    password = ctk.CTkEntry(
        dialog, placeholder_text="Пароль", show="*", width=280
    )
    password.pack(pady=7)

    confirm = ctk.CTkEntry(
        dialog, placeholder_text="Повторите пароль для регистрации",
        show="*", width=280
    )
    confirm.pack(pady=7)

    status = ctk.CTkLabel(dialog, text="", text_color="#b42318")
    status.pack(pady=4)

    def login():
        nick = username.get().strip()
        pwd = password.get()

        if not nick or not pwd:
            status.configure(text="Введите никнейм и пароль")
            return

        try:
            users = load_users()
            account = users.get(nick.casefold())
        except (OSError, json.JSONDecodeError):
            status.configure(text="Не удалось прочитать файл пользователей")
            return

        if not account:
            status.configure(text="Аккаунт не найден — зарегистрируйтесь")
            return

        try:
            salt = bytes.fromhex(account["salt"])
            actual = password_hash(pwd, salt)
            expected = account["hash"]
        except (KeyError, ValueError, TypeError):
            status.configure(text="Повреждены данные аккаунта")
            return

        if not hmac.compare_digest(actual, expected):
            status.configure(text="Неверный пароль")
            return

        result["name"] = account["name"]
        dialog.destroy()

    def register():
        nick = username.get().strip()
        pwd = password.get()

        if len(nick) < 3:
            status.configure(text="Никнейм: минимум 3 символа")
            return
        if len(pwd) < 8:
            status.configure(text="Пароль: минимум 8 символов")
            return
        if pwd != confirm.get():
            status.configure(text="Пароли не совпадают")
            return

        try:
            users = load_users()
            key = nick.casefold()

            if key in users:
                status.configure(text="Этот никнейм уже занят")
                return

            salt = secrets.token_bytes(16)
            users[key] = {
                "name": nick,
                "salt": salt.hex(),
                "hash": password_hash(pwd, salt)
            }
            save_users(users)
        except (OSError, json.JSONDecodeError) as e:
            status.configure(text=f"Не удалось сохранить: {e}")
            return

        result["name"] = nick
        dialog.destroy()

    buttons = ctk.CTkFrame(dialog, fg_color="transparent")
    buttons.pack(pady=10)

    ctk.CTkButton(
        buttons, text="Войти", command=login, width=120
    ).pack(side="left", padx=6)

    ctk.CTkButton(
        buttons, text="Регистрация", command=register, width=120
    ).pack(side="left", padx=6)

    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    app.wait_window(dialog)
    #app.deiconify()
    return result["name"]


name = authenticate()
if not name:
    app.destroy()
    raise SystemExit

# Состояние выбранной переписки
active_peer = None
last_count = -1
image_refs = []
known_contacts = set()

# Левая панель и область переписки
main = ctk.CTkFrame(app, corner_radius=16)
main.pack(fill="both", expand=True, padx=12, pady=12)
main.grid_columnconfigure(1, weight=1)
main.grid_rowconfigure(0, weight=1)

left = ctk.CTkFrame(main, width=240, corner_radius=16)
left.grid(row=0, column=0, sticky="ns", padx=(8, 4), pady=8)
left.grid_propagate(False)

ctk.CTkLabel(
    left, text="Чаты", font=("Segoe UI", 18, "bold")
).pack(anchor="w", padx=12, pady=(12, 6))

search = ctk.CTkEntry(left, placeholder_text="Найти пользователя…")
search.pack(fill="x", padx=10, pady=5)

search_results = ctk.CTkFrame(left, fg_color="transparent")
search_results.pack(fill="x", padx=6)

ctk.CTkLabel(
    left, text="Личные переписки", font=("Segoe UI", 12, "bold")
).pack(anchor="w", padx=12, pady=(14, 4))

contacts = ctk.CTkScrollableFrame(left, fg_color="transparent")
contacts.pack(fill="both", expand=True, padx=5, pady=(0, 8))

right = ctk.CTkFrame(main, corner_radius=16, fg_color="#e8e5df")
right.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=8)
right.grid_rowconfigure(1, weight=1)
right.grid_columnconfigure(0, weight=1)

chat_title = ctk.CTkLabel(
    right, text="Выберите чат или найдите пользователя",
    font=("Segoe UI", 15, "bold"), anchor="w"
)
chat_title.grid(row=0, column=0, sticky="ew", padx=14, pady=14)

messages = ctk.CTkScrollableFrame(right, corner_radius=14, fg_color="#e8e5df")
messages.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)

bottom = ctk.CTkFrame(right, corner_radius=16, fg_color="#f0f2f5")
bottom.grid(row=2, column=0, sticky="ew", padx=8, pady=8)
bottom.grid_columnconfigure(0, weight=1)

entry = ctk.CTkEntry(
    bottom, placeholder_text="Напишите сообщение…",
    corner_radius=18, height=42
)
entry.grid(row=0, column=0, sticky="ew", padx=(10, 5), pady=9)

def read_messages():
    try:
        with open(CHAT_FILE, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError):
        return []

def clear_frame(frame):
    for widget in frame.winfo_children():
        widget.destroy()

def select_peer(peer):
    global active_peer, last_count
    if peer.casefold() == name.casefold():
        return
    active_peer = peer
    chat_title.configure(text=peer)
    last_count = -1
    refresh()

def search_users(event=None):
    clear_frame(search_results)
    query = search.get().strip().casefold()
    if not query:
        return

    try:
        users = load_users()
    except (OSError, json.JSONDecodeError):
        return

    matches = [
        account["name"] for key, account in users.items()
        if query in account["name"].casefold()
        and key != name.casefold()
    ]

    for peer in matches[:8]:
        ctk.CTkButton(
            search_results, text=peer, anchor="w",
            corner_radius=12, height=34,
            command=lambda p=peer: select_peer(p)
        ).pack(fill="x", pady=2)

def update_contacts(all_messages):
    global known_contacts
    peers = set()

    for msg in all_messages:
        sender = msg.get("sender")
        recipient = msg.get("recipient")
        if not sender or not recipient:
            continue
        if sender.casefold() == name.casefold():
            peers.add(recipient)
        elif recipient.casefold() == name.casefold():
            peers.add(sender)

    if peers != known_contacts:
        known_contacts = peers
        clear_frame(contacts)
        for peer in sorted(peers, key=str.casefold):
            ctk.CTkButton(
                contacts, text=peer, anchor="w",
                corner_radius=12, height=38,
                command=lambda p=peer: select_peer(p)
            ).pack(fill="x", padx=3, pady=3)

def show_message(msg):
    own = msg.get("sender", "").casefold() == name.casefold()
    color = "#d9fdd3" if own else "white"

    bubble = ctk.CTkFrame(messages, corner_radius=16, fg_color=color)
    bubble.pack(anchor="e" if own else "w", padx=8, pady=5)

    ctk.CTkLabel(
        bubble,
        text=f'{msg.get("sender", "")} · {msg.get("time", "")}',
        text_color="#527da3", font=("Segoe UI", 10, "bold")
    ).pack(anchor="w", padx=10, pady=(7, 2))

    if msg.get("text"):
        ctk.CTkLabel(
            bubble, text=msg["text"], wraplength=420,
            justify="left", font=("Segoe UI", 12)
        ).pack(anchor="w", padx=10, pady=4)

    filename = msg.get("file")
    if filename:
        path = os.path.join(ATTACH_DIR, filename)
        ext = os.path.splitext(filename)[1].lower()

        if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
            try:
                img = Image.open(path)
                img.thumbnail((280, 220))
                photo = ctk.CTkImage(img, img, size=img.size)
                image_refs.append(photo)
                ctk.CTkLabel(bubble, text="", image=photo).pack(padx=8, pady=4)
            except Exception:
                pass

        ctk.CTkButton(
            bubble, text=f"Открыть: {os.path.basename(filename)}",
            corner_radius=12, height=30,
            command=lambda f=filename: open_attachment(f)
        ).pack(anchor="w", padx=8, pady=(3, 8))

def send(text=None, attachment=None):
    if not active_peer:
        messagebox.showinfo("Выберите чат", "Найдите пользователя или выберите переписку.")
        return

    text = (text if text is not None else entry.get()).strip()
    if not text and not attachment:
        return

    msg = {
        "sender": name,
        "recipient": active_peer,
        "time": datetime.now().strftime("%H:%M"),
        "text": text
    }

    try:
        if attachment:
            os.makedirs(ATTACH_DIR, exist_ok=True)
            filename = f"{uuid.uuid4().hex}_{os.path.basename(attachment)}"
            shutil.copy2(attachment, os.path.join(ATTACH_DIR, filename))
            msg["file"] = filename

        with open(CHAT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        entry.delete(0, "end")
    except OSError as e:
        messagebox.showerror("Ошибка отправки", str(e))

def choose_file():
    path = filedialog.askopenfilename(title="Выберите фото или файл")
    if path:
        send(attachment=path)

def refresh():
    global last_count

    all_messages = read_messages()
    update_contacts(all_messages)

    if active_peer and len(all_messages) != last_count:
        clear_frame(messages)
        image_refs.clear()

        for msg in all_messages:
            sender = msg.get("sender", "").casefold()
            recipient = msg.get("recipient", "").casefold()
            participants = {sender, recipient}
            if participants == {name.casefold(), active_peer.casefold()}:
                show_message(msg)

        last_count = len(all_messages)
        messages.after(100, lambda: messages._parent_canvas.yview_moveto(1))

    app.after(1200, refresh)

search.bind("<KeyRelease>", search_users)
search.bind("<Return>", search_users)
entry.bind("<Return>", lambda event: send())

ctk.CTkButton(
    bottom, text="＋ Файл", width=90, corner_radius=18,
    command=choose_file
).grid(row=0, column=1, padx=4, pady=9)

ctk.CTkButton(
    bottom, text="➤", width=48, corner_radius=18,
    command=send
).grid(row=0, column=2, padx=(4, 10), pady=9)

refresh()
app.mainloop()
