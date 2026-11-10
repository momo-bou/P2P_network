"""Run with: python main.py. All Tkinter updates happen on the main thread."""
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from p2p_node import P2PNode


class App:
    def __init__(self, root):
        self.root, self.node = root, None
        self.peer_ids = []
        root.title("P2P Share | Text and File Transfer")
        root.geometry("980x680")
        root.minsize(820, 580)
        self.name = tk.StringVar(value=socket.gethostname())
        self.port = tk.StringVar(value="5000")
        self.host = tk.StringVar(value="127.0.0.1")
        self.remote_port = tk.StringVar(value="5001")
        self.message = tk.StringVar()
        self.folder = tk.StringVar(value=str(Path(__file__).resolve().parent / "downloads"))
        self.status = tk.StringVar(value="Start your peer to begin.")
        style = ttk.Style()
        style.configure("TButton", padding=6)
        style.configure("Title.TLabel", font=("Segoe UI", 19, "bold"))
        outer = ttk.Frame(root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="P2P Share", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Direct text and file sharing between computers on the same Wi-Fi / LAN").pack(anchor="w", pady=(0, 12))

        local = ttk.LabelFrame(outer, text="1. Start your peer", padding=10)
        local.pack(fill="x")
        ttk.Label(local, text="Your name").grid(row=0, column=0)
        self.name_entry = ttk.Entry(local, textvariable=self.name, width=20)
        self.name_entry.grid(row=0, column=1, padx=6)
        ttk.Label(local, text="Listening port").grid(row=0, column=2)
        self.port_entry = ttk.Entry(local, textvariable=self.port, width=8)
        self.port_entry.grid(row=0, column=3, padx=6)
        self.start_button = ttk.Button(local, text="Start Peer", command=self.start)
        self.start_button.grid(row=0, column=4, padx=6)
        self.stop_button = ttk.Button(local, text="Stop", command=self.stop, state="disabled")
        self.stop_button.grid(row=0, column=5)
        try:
            addresses = ", ".join(sorted(set(socket.gethostbyname_ex(socket.gethostname())[2])))
        except OSError:
            addresses = "Use ipconfig (Windows) to find your IPv4 address"
        ttk.Label(local, text=f"Possible local IP addresses: {addresses}  |  Same PC: 127.0.0.1").grid(row=1, column=0, columnspan=6, sticky="w", pady=(8, 0))

        remote = ttk.LabelFrame(outer, text="2. Connect to another peer", padding=10)
        remote.pack(fill="x", pady=10)
        ttk.Label(remote, text="Remote IPv4").pack(side="left")
        ttk.Entry(remote, textvariable=self.host, width=20).pack(side="left", padx=6)
        ttk.Label(remote, text="Remote port").pack(side="left")
        ttk.Entry(remote, textvariable=self.remote_port, width=8).pack(side="left", padx=6)
        ttk.Button(remote, text="Connect", command=self.connect).pack(side="left", padx=6)

        middle = ttk.Frame(outer)
        middle.pack(fill="both", expand=True)
        left = ttk.LabelFrame(middle, text="3. Select a connected peer", padding=6)
        left.pack(side="left", fill="y", padx=(0, 10))
        self.peers = tk.Listbox(left, width=28, exportselection=False)
        self.peers.pack(fill="both", expand=True)
        right = ttk.LabelFrame(middle, text="Messages and events", padding=6)
        right.pack(side="left", fill="both", expand=True)
        self.log = scrolledtext.ScrolledText(right, wrap="word", state="disabled", height=12, font=("Segoe UI", 10))
        self.log.pack(fill="both", expand=True)

        send = ttk.Frame(outer)
        send.pack(fill="x", pady=10)
        entry = ttk.Entry(send, textvariable=self.message)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda event: self.send_text())
        ttk.Button(send, text="Send Text", command=self.send_text).pack(side="left", padx=6)
        ttk.Button(send, text="Choose File & Send", command=self.send_file).pack(side="left")

        downloads = ttk.Frame(outer)
        downloads.pack(fill="x")
        self.folder_button = ttk.Button(downloads, text="Choose receive folder", command=self.choose_folder)
        self.folder_button.pack(side="left")
        ttk.Button(downloads, text="Open received files", command=self.open_folder).pack(side="left", padx=6)
        ttk.Label(outer, textvariable=self.folder, wraplength=900).pack(anchor="w", pady=4)
        self.progress = ttk.Progressbar(outer, maximum=100)
        self.progress.pack(fill="x", pady=(4, 0))
        ttk.Label(outer, textvariable=self.status).pack(anchor="w", pady=4)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)

    def report(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def attempt(self, function):
        try:
            function()
        except (OSError, ValueError, TypeError) as error:
            messagebox.showerror("P2P Share", str(error))

    def start(self):
        def action():
            node = P2PNode(self.name.get(), self.port.get(), self.folder.get())
            node.start()
            self.node = node
            for widget in (self.name_entry, self.port_entry, self.start_button, self.folder_button):
                widget.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.status.set(f"Listening on port {node.port}. Connect to a peer, then select their name.")
        self.attempt(action)

    def stop(self):
        if self.node:
            self.node.stop()
            self.node = None
        self.peers.delete(0, "end")
        self.peer_ids = []
        for widget in (self.name_entry, self.port_entry, self.start_button, self.folder_button):
            widget.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.progress["value"] = 0
        self.status.set("Stopped. You can start again.")
        self.report("Peer stopped")

    def require_node(self):
        if not self.node:
            raise ValueError("Click Start Peer first")
        return self.node

    def selected(self):
        self.require_node()
        selection = self.peers.curselection()
        if not selection:
            raise ValueError("Select a name in the connected peer list first")
        return self.peer_ids[selection[0]]

    def connect(self):
        self.attempt(lambda: self.require_node().connect(self.host.get(), self.remote_port.get()))

    def send_text(self):
        def action():
            peer = self.selected()
            self.node.send_text(peer, self.message.get())
            self.message.set("")
        self.attempt(action)

    def send_file(self):
        def action():
            peer = self.selected()
            path = filedialog.askopenfilename(title="Select any file to send", filetypes=[("All files", "*")])
            if path:
                self.node.send_file(peer, path)
        self.attempt(action)

    def choose_folder(self):
        path = filedialog.askdirectory(title="Choose where received files are saved")
        if path:
            self.folder.set(path)

    def open_folder(self):
        def action():
            path = Path(self.folder.get())
            path.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(path))
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
        self.attempt(action)

    def refresh_peers(self):
        selection = self.peers.curselection()
        selected = self.peer_ids[selection[0]] if selection else None
        self.peers.delete(0, "end")
        self.peer_ids = []
        for connection in self.node.snapshot():
            self.peer_ids.append(connection.peer_id)
            self.peers.insert("end", f"{connection.name} [{connection.peer_id[:8]}]")
        if selected in self.peer_ids:
            self.peers.selection_set(self.peer_ids.index(selected))

    def poll(self):
        if self.node:
            for _ in range(150):
                try:
                    event = self.node.events.get_nowait()
                except queue.Empty:
                    break
                if event["kind"] == "peers":
                    self.refresh_peers()
                elif event["kind"] == "progress":
                    self.progress["value"] = event["percent"]
                    self.status.set(event["text"])
                elif event["kind"] == "received":
                    self.report(f"Received from {event['peer']}: {event['path']} (SHA-256 verified)")
                else:
                    self.report(("ERROR: " if event["kind"] == "error" else "") + event["text"])
        self.root.after(100, self.poll)

    def close(self):
        if self.node:
            self.node.stop()
        self.root.destroy()


if __name__ == "__main__":
    App(tk.Tk()).root.mainloop()
