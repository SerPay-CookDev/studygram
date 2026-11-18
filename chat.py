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
last_signature = None
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

chat_header = ctk.CTkFrame(right, fg_color="transparent")
chat_header.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
chat_header.grid_columnconfigure(0, weight=1)

chat_title = ctk.CTkLabel(
    chat_header, text="Выберите чат или найдите пользователя",
    font=("Segoe UI", 15, "bold"), anchor="w"
)
chat_title.grid(row=0, column=0, sticky="ew", padx=6, pady=6)

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


def conversation_key(msg):
    if msg.get("chat_type") == "group" or msg.get("type") == "group_message":
        group_id = msg.get("group_id") or msg.get("recipient") or "unknown"
        return f"group:{str(group_id).casefold()}"

    participants = sorted({
        str(msg.get("sender", "")).casefold(),
        str(msg.get("recipient", "")).casefold()
    })
    return "dm:" + "|".join(participants)


def read_messages():
    """Load message records and apply append-only deletion events."""
    records = []
    try:
        with open(CHAT_FILE, encoding="utf-8") as f:
            for line_number, line in enumerate(f):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append((line_number, line, record))
    except (FileNotFoundError, OSError):
        return []

    messages_by_id = {}
    ordered_ids = []
    delete_events = []
    chat_clear_events = []

    for position, raw_line, record in records:
        if record.get("record_type") == "delete_message":
            delete_events.append((position, record))
            continue
        if record.get("record_type") == "delete_chat":
            chat_clear_events.append((position, record))
            continue

        # Старые сообщения без ID остаются адресуемыми после перезапуска.
        message_id = record.get("id") or hashlib.sha256(
            f"{position}\0{raw_line}".encode("utf-8")
        ).hexdigest()
        record["id"] = message_id
        messages_by_id[message_id] = (position, record)
        ordered_ids.append(message_id)

    deleted_ids = set()
    for _position, event in delete_events:
        target = messages_by_id.get(event.get("message_id"))
        if not target:
            continue
        _target_position, target_msg = target
        requester = str(event.get("requester", "")).casefold()
        sender = str(target_msg.get("sender", "")).casefold()
        recipient = str(target_msg.get("recipient", "")).casefold()
        is_group = (
            target_msg.get("chat_type") == "group"
            or target_msg.get("type") == "group_message"
        )
        if (is_group and requester == sender) or (
            not is_group and requester in {sender, recipient}
        ):
            deleted_ids.add(event.get("message_id"))

    # Удаление чата скрывает историю только у удалившего пользователя.
    # Новые сообщения в этой переписке после события снова появятся.
    clear_positions = {}
    for position, event in chat_clear_events:
        user_key = str(event.get("user", "")).casefold()
        chat_key = event.get("chat_key")
        if user_key and chat_key:
            clear_positions[(user_key, chat_key)] = position

    current_user = name.casefold()
    visible = []
    for message_id in ordered_ids:
        position, msg = messages_by_id[message_id]
        if message_id in deleted_ids:
            continue
        cutoff = clear_positions.get((current_user, conversation_key(msg)))
        if cutoff is not None and position <= cutoff:
            continue
        visible.append(msg)
    return visible


def append_event(event):
    with open(CHAT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def clear_frame(frame):
    for widget in frame.winfo_children():
        widget.destroy()


def select_peer(peer):
    global active_peer, last_signature
    if peer.casefold() == name.casefold():
        return
    active_peer = peer
    chat_title.configure(text=peer)
    last_signature = None
    refresh()


def delete_chat(peer):
    if not messagebox.askyesno(
        "Удалить чат",
        f"Скрыть историю переписки с пользователем {peer} только у вас?"
    ):
        return

    chat_key = "dm:" + "|".join(sorted({name.casefold(), peer.casefold()}))
    try:
        append_event({
            "record_type": "delete_chat",
            "user": name,
            "chat_key": chat_key,
            "time": datetime.now().isoformat(timespec="seconds")
        })
    except OSError as e:
        messagebox.showerror("Ошибка удаления чата", str(e))
        return

    global active_peer, last_signature
    if active_peer and active_peer.casefold() == peer.casefold():
        active_peer = None
        chat_title.configure(text="Выберите чат или найдите пользователя")
        clear_frame(messages)
        image_refs.clear()
    last_signature = None
    refresh()


def delete_message(msg):
    is_group = (
        msg.get("chat_type") == "group"
        or msg.get("type") == "group_message"
    )
    own = msg.get("sender", "").casefold() == name.casefold()
    if is_group and not own:
        return

    prompt = "Удалить это сообщение? Оно исчезнет у всех участников чата."
    if not messagebox.askyesno("Удалить сообщение", prompt):
        return

    try:
        append_event({
            "record_type": "delete_message",
            "message_id": msg["id"],
            "requester": name,
            "time": datetime.now().isoformat(timespec="seconds")
        })
    except OSError as e:
        messagebox.showerror("Ошибка удаления сообщения", str(e))
        return

    global last_signature
    last_signature = None
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
            row = ctk.CTkFrame(contacts, fg_color="transparent")
            row.pack(fill="x", padx=3, pady=3)
            ctk.CTkButton(
                row, text=peer, anchor="w",
                corner_radius=12, height=38,
                command=lambda p=peer: select_peer(p)
            ).pack(side="left", fill="x", expand=True)
            ctk.CTkButton(
                row, text="×", width=34, height=34, corner_radius=10,
                fg_color="transparent", text_color="#b42318",
                hover_color="#f8d7da",
                command=lambda p=peer: delete_chat(p)
            ).pack(side="right", padx=(4, 0))


def show_message(msg):
    own = msg.get("sender", "").casefold() == name.casefold()
    is_group = (
        msg.get("chat_type") == "group"
        or msg.get("type") == "group_message"
    )
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

    # В личной переписке любой из участников может удалить сообщение;
    # в групповой — только автор.
    if not is_group or own:
        ctk.CTkButton(
            bubble, text="Удалить сообщение", width=130, height=26,
            corner_radius=10, fg_color="transparent",
            text_color="#b42318", hover_color="#f8d7da",
            command=lambda m=msg: delete_message(m)
        ).pack(anchor="e", padx=8, pady=(0, 7))


def send(text=None, attachment=None):
    if not active_peer:
        messagebox.showinfo("Выберите чат", "Найдите пользователя или выберите переписку.")
        return

    text = (text if text is not None else entry.get()).strip()
    if not text and not attachment:
        return

    msg = {
        "id": uuid.uuid4().hex,
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

        append_event(msg)
        entry.delete(0, "end")
    except OSError as e:
        messagebox.showerror("Ошибка отправки", str(e))


def choose_file():
    path = filedialog.askopenfilename(title="Выберите фото или файл")
    if path:
        send(attachment=path)


def refresh():
    global last_signature

    all_messages = read_messages()
    update_contacts(all_messages)

    if active_peer:
        peer_key = active_peer.casefold()
        current_messages = [
            msg for msg in all_messages
            if {str(msg.get("sender", "")).casefold(),
                str(msg.get("recipient", "")).casefold()}
            == {name.casefold(), peer_key}
        ]
        signature = tuple(msg.get("id") for msg in current_messages)

        if signature != last_signature:
            clear_frame(messages)
            image_refs.clear()
            for msg in current_messages:
                show_message(msg)
            last_signature = signature
            messages.after(100, lambda: messages._parent_canvas.yview_moveto(1))

    app.after(1200, refresh)


ctk.CTkButton(
    chat_header, text="Удалить чат", width=100, height=30,
    corner_radius=10, fg_color="transparent", text_color="#b42318",
    hover_color="#f8d7da",
    command=lambda: delete_chat(active_peer) if active_peer else None
).grid(row=0, column=1, padx=6, pady=4)

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
