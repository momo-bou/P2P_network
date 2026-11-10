"""Direct TCP peers. Networking never calls Tkinter; events go through a queue."""
import hashlib
import ipaddress
import os
from pathlib import Path
import queue
import socket
import threading
import uuid

from protocol import CHUNK_SIZE, recv_exact, recv_json, send_json


def valid_port(value):
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    return port


class Connection:
    def __init__(self, sock, peer_id, name, address):
        self.sock, self.peer_id, self.name, self.address = sock, peer_id, name, address
        # Keep an entire outgoing file together, even if Send Text is clicked.
        self.send_lock = threading.Lock()


class P2PNode:
    def __init__(self, name, port, download_dir):
        if not name.strip() or len(name.strip()) > 80:
            raise ValueError("Enter a peer name of 1 to 80 characters")
        self.name = name.strip()
        self.port = valid_port(port)
        self.peer_id = uuid.uuid4().hex
        self.download_dir = Path(download_dir).resolve()
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.events = queue.Queue()
        self.lock = threading.RLock()
        self.peers = {}
        self.sockets = set()
        self.pending = {}
        self.listener = None
        self.running = False

    def emit(self, kind, **values):
        self.events.put(dict(kind=kind, **values))

    @staticmethod
    def worker(function, *args):
        threading.Thread(target=function, args=args, daemon=True).start()

    def start(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("0.0.0.0", self.port))
            listener.listen(16)
            listener.settimeout(0.5)
        except Exception:
            listener.close()
            raise
        self.listener = listener
        self.running = True
        self.worker(self._accept)
        self.emit("log", text=f"{self.name} listening on port {self.port}")

    def _track(self, sock):
        with self.lock:
            if not self.running:
                sock.close()
                return False
            self.sockets.add(sock)
            return True

    def _accept(self):
        while self.running:
            try:
                sock, address = self.listener.accept()
                if self._track(sock):
                    self.worker(self._session, sock, address, False)
            except socket.timeout:
                continue
            except OSError:
                break

    def connect(self, host, port):
        if not self.running:
            raise ValueError("Start your peer first")
        ipaddress.IPv4Address(host.strip())
        self.worker(self._connect, host.strip(), valid_port(port))

    def _connect(self, host, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if not self._track(sock):
            return
        try:
            sock.settimeout(8)
            sock.connect((host, port))
        except OSError as error:
            self.emit("error", text=f"Connection to {host}:{port} failed: {error}")
            self._close(sock)
            return
        self._session(sock, (host, port), True)

    def _hello(self, kind):
        return dict(type=kind, peer_id=self.peer_id, peer_name=self.name, port=self.port)

    def _session(self, sock, address, outgoing):
        connection = None
        try:
            sock.settimeout(8)
            if outgoing:
                send_json(sock, self._hello("hello"))
            hello = recv_json(sock)
            if hello.get("type") != ("hello_ack" if outgoing else "hello"):
                raise ValueError("Invalid handshake")
            peer_id, name = hello.get("peer_id"), hello.get("peer_name")
            if not isinstance(peer_id, str) or not 1 <= len(peer_id) <= 64:
                raise ValueError("Invalid peer ID")
            if not isinstance(name, str) or not 1 <= len(name) <= 80:
                raise ValueError("Invalid peer name")
            remote_port = valid_port(hello.get("port"))
            if peer_id == self.peer_id:
                raise ValueError("You cannot connect to yourself")
            if not outgoing:
                send_json(sock, self._hello("hello_ack"))
            sock.settimeout(None)
            with self.lock:
                if not self.running:
                    return
                if peer_id in self.peers:
                    raise ValueError("This peer is already connected")
                connection = Connection(sock, peer_id, name, (address[0], remote_port))
                self.peers[peer_id] = connection
            self.emit("peers")
            self.emit("log", text=f"Connected: {name} at {address[0]}:{remote_port}")
            while self.running:
                message = recv_json(sock)
                kind = message.get("type")
                if kind == "text":
                    if not isinstance(message.get("message"), str):
                        raise ValueError("Invalid text message")
                    self.emit("log", text=f"{name} -> You: {message['message']}")
                elif kind == "file":
                    self._receive_file(connection, message)
                elif kind == "file_ack":
                    with self.lock:
                        filename = self.pending.pop((peer_id, message.get("transfer_id")), None)
                    if filename:
                        self.emit("log", text=f"{name} confirmed receipt: {filename}")
                else:
                    raise ValueError("Unknown message type")
        except (OSError, ValueError, TypeError, ConnectionError) as error:
            if self.running:
                self.emit("error", text=f"{connection.name if connection else address}: {error}")
        finally:
            with self.lock:
                if connection and self.peers.get(connection.peer_id) is connection:
                    del self.peers[connection.peer_id]
                    for key in list(self.pending):
                        if key[0] == connection.peer_id:
                            del self.pending[key]
                    self.emit("log", text=f"Disconnected: {connection.name}")
                    self.emit("peers")
            self._close(sock)

    def snapshot(self):
        with self.lock:
            return list(self.peers.values())

    def _peer(self, peer_id):
        with self.lock:
            if peer_id not in self.peers:
                raise ValueError("Select a connected peer")
            return self.peers[peer_id]

    def send_text(self, peer_id, text):
        if not text.strip():
            raise ValueError("Enter a message first")
        if len(text.encode("utf-8")) > 200000:
            raise ValueError("Text is too long; send it as a file instead")
        self.worker(self._send_control, self._peer(peer_id), dict(type="text", message=text),
                    f"You -> {self._peer(peer_id).name}: {text}")

    def _send_control(self, connection, message, log=None):
        try:
            with connection.send_lock:
                send_json(connection.sock, message)
            if log:
                self.emit("log", text=log)
        except (OSError, ValueError) as error:
            self.emit("error", text=f"Send failed: {error}")
            self._close(connection.sock)

    def send_file(self, peer_id, path):
        self.worker(self._send_file, self._peer(peer_id), Path(path))

    def _send_file(self, connection, path):
        started = False
        transfer_id = uuid.uuid4().hex
        try:
            with path.open("rb") as stream:
                size = os.fstat(stream.fileno()).st_size
                self.emit("log", text=f"Sending {path.name} ({size:,} bytes) to {connection.name}")
                with connection.send_lock:
                    started = True
                    send_json(connection.sock, dict(type="file", filename=path.name,
                              filesize=size, transfer_id=transfer_id))
                    remaining, digest = size, hashlib.sha256()
                    last_percent = -1
                    while remaining:
                        chunk = stream.read(min(CHUNK_SIZE, remaining))
                        if not chunk:
                            raise OSError("Source file changed during transfer")
                        connection.sock.sendall(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                        percent = (size - remaining) * 100 // size
                        if percent != last_percent:
                            self.emit("progress", text=f"Sending {path.name}", percent=percent)
                            last_percent = percent
                    with self.lock:
                        self.pending[(connection.peer_id, transfer_id)] = path.name
                    send_json(connection.sock, dict(type="file_end", sha256=digest.hexdigest()))
                self.emit("progress", text=f"Sent {path.name}; awaiting receipt", percent=100)
        except (OSError, ValueError) as error:
            self.emit("error", text=f"File send failed: {error}")
            if started:
                self._close(connection.sock)  # A partial body cannot be reused as a frame.

    def _receive_file(self, connection, message):
        size, filename, transfer_id = (message.get(key) for key in
                                      ("filesize", "filename", "transfer_id"))
        if type(size) is not int or size < 0:
            raise ValueError("Invalid file size")
        if not isinstance(filename, str) or not filename or len(filename) > 255:
            raise ValueError("Invalid filename")
        if not isinstance(transfer_id, str) or not 1 <= len(transfer_id) <= 64:
            raise ValueError("Invalid transfer ID")
        # Never allow a remote path to escape the chosen download directory.
        filename = filename.replace("\\", "/").split("/")[-1]
        filename = "".join("_" if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in filename)
        filename = filename.rstrip(" .")[:180] or "received_file"
        if filename.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)], *[f"LPT{i}" for i in range(10)]}:
            filename = "_" + filename
        temp = self.download_dir / (uuid.uuid4().hex + ".part")
        reserved = None
        try:
            with temp.open("xb") as stream:
                remaining, digest = size, hashlib.sha256()
                last_percent = -1
                while remaining:
                    chunk = recv_exact(connection.sock, min(CHUNK_SIZE, remaining))
                    stream.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                    percent = (size - remaining) * 100 // size
                    if percent != last_percent:
                        self.emit("progress", text=f"Receiving {filename}", percent=percent)
                        last_percent = percent
            end = recv_json(connection.sock)
            if end.get("type") != "file_end" or end.get("sha256") != digest.hexdigest():
                raise ValueError("File integrity check failed")
            # Reserve a unique filename atomically, preserving existing downloads.
            candidate = self.download_dir / filename
            number = 1
            while True:
                try:
                    with candidate.open("xb"):
                        pass
                    reserved = candidate
                    break
                except FileExistsError:
                    original = Path(filename)
                    candidate = self.download_dir / f"{original.stem} ({number}){original.suffix}"
                    number += 1
            os.replace(temp, candidate)
            reserved = None
            self.emit("received", path=str(candidate), sha256=digest.hexdigest(), peer=connection.name)
            self.emit("progress", text=f"Received {candidate.name}", percent=100)
            # Do not block this receive loop on a concurrent outgoing file.
            self.worker(self._send_control, connection,
                        dict(type="file_ack", transfer_id=transfer_id))
        finally:
            temp.unlink(missing_ok=True)
            if reserved:
                reserved.unlink(missing_ok=True)

    def _close(self, sock):
        with self.lock:
            self.sockets.discard(sock)
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    def stop(self):
        self.running = False
        if self.listener:
            self.listener.close()
        with self.lock:
            sockets = list(self.sockets)
        for sock in sockets:
            self._close(sock)
        self.emit("log", text="Peer stopped")
