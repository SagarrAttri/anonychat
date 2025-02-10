import random
import string
import sqlite3
import time
from functools import wraps
import asyncio
import nest_asyncio

from flask import Flask, render_template, request, redirect, url_for, session
from flask_socketio import SocketIO, join_room, leave_room, emit

# Allow nested event loops (useful for development in VS Code, etc.)
nest_asyncio.apply()

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Change to a secure secret key for production
socketio = SocketIO(app, async_mode='eventlet')

# In-memory storage for rooms.
# Structure for each room:
# rooms[room_code] = {
#    "mode": "monitored" or "anonymous",
#    "creator": { "username": <creator name>, "sid": <SocketIO sid> } (for monitored rooms),
#    "users": { sid: username, ... },
#    "counter": <number used to assign unique pseudonyms in anonymous mode>
# }
rooms = {}

def generate_room_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

# ------------------------------
# Routes
# ------------------------------

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/create_monitored", methods=["GET", "POST"])
def create_monitored():
    if request.method == "POST":
        username = request.form.get("username")
        room_code = generate_room_code()
        rooms[room_code] = {
            "mode": "monitored",
            "creator": {"username": username, "sid": None},
            "users": {}
        }
        # For monitored rooms, store the creator's real name in session.
        session["room_code"] = room_code
        session["username"] = username
        session["mode"] = "monitored"
        return redirect(url_for("chat", room_code=room_code))
    return render_template("create_monitored.html")

@app.route("/create_anonymous", methods=["GET", "POST"])
def create_anonymous():
    if request.method == "POST":
        room_code = generate_room_code()
        # Initialize the counter for pseudonyms in anonymous mode.
        rooms[room_code] = {
            "mode": "anonymous",
            "creator": None,
            "users": {},
            "counter": 0
        }
        session["room_code"] = room_code
        session["mode"] = "anonymous"
        # For anonymous rooms, we will assign a pseudonym upon joining.
        return redirect(url_for("chat", room_code=room_code))
    return render_template("create_anonymous.html")

@app.route("/join", methods=["GET", "POST"])
def join_room_form():
    if request.method == "POST":
        room_code = request.form.get("room_code").upper()
        if room_code not in rooms:
            return "Room not found", 404
        room = rooms[room_code]
        if room["mode"] == "monitored":
            username = request.form.get("username")
            session["username"] = username
        else:
            # For anonymous rooms, assign a unique pseudonym.
            room["counter"] = room.get("counter", 0) + 1
            session["username"] = "User " + str(room["counter"])
        session["room_code"] = room_code
        session["mode"] = room["mode"]
        return redirect(url_for("chat", room_code=room_code))
    # The join form template always asks for a username;
    # For anonymous rooms you can ignore it or pre-fill as needed.
    return render_template("join_room.html", ask_name=True)

@app.route("/chat/<room_code>")
def chat(room_code):
    if "room_code" not in session or session["room_code"] != room_code:
        return redirect(url_for("index"))
    return render_template("chat.html", room_code=room_code)

# ------------------------------
# SocketIO Event Handlers
# ------------------------------

@socketio.on('join')
def on_join(data):
    room_code = data['room']
    join_room(room_code)
    # Retrieve the username from the Flask session
    username = session.get("username", "Anonymous")
    if room_code in rooms:
        rooms[room_code]["users"][request.sid] = username
        # For monitored rooms, store the creator's SocketIO ID if not already set.
        if rooms[room_code]["mode"] == "monitored":
            if rooms[room_code]["creator"]["sid"] is None:
                rooms[room_code]["creator"]["sid"] = request.sid
        emit('message', f"{username} joined the room.", to=room_code)
    else:
        emit('message', "Room not found.")

@socketio.on('message')
def handle_message(data):
    room_code = data['room']
    msg = data['message']
    room = rooms.get(room_code)
    if not room:
        emit('message', "Room not found.")
        return
    sender_username = room["users"].get(request.sid, "Anonymous")
    if room["mode"] == "anonymous":
        # In anonymous mode, show the assigned pseudonym (e.g. "User 1", "User 2", etc.)
        emit('message', f"{sender_username}: {msg}", to=room_code)
    else:
        # In monitored mode, send "Anonymous" to the participants.
        emit('message', f"Anonymous: {msg}", to=room_code)
        if room["creator"] and room["creator"]["sid"]:
            # The monitor sees the real username.
            socketio.emit('message', f"[MONITOR] {sender_username}: {msg}", to=room["creator"]["sid"])

@socketio.on('disconnect')
def on_disconnect():
    # Remove the user from any room they belong to.
    for room_code, room in list(rooms.items()):
        if request.sid in room["users"]:
            username = room["users"].pop(request.sid)
            leave_room(room_code)
            emit('message', f"{username} has left the room.", to=room_code)
            # If the room becomes empty, remove it.
            if not room["users"]:
                del rooms[room_code]
            break

# ------------------------------
# Run the Application
# ------------------------------

if __name__ == '__main__':
    # For public hosting, the host and port may be set by the platform.
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
