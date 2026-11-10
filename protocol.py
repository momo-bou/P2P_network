"""Length-prefixed JSON control messages; file bodies are raw bytes."""
import json
import struct

CHUNK_SIZE = 64 * 1024
MAX_JSON = 1024 * 1024


def recv_exact(sock, count):
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(min(count - len(data), CHUNK_SIZE))
        if not chunk:
            raise ConnectionError("Peer disconnected before all data arrived")
        data.extend(chunk)
    return bytes(data)


def send_json(sock, message):
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    if not 0 < len(payload) <= MAX_JSON:
        raise ValueError("Message is too large")
    sock.sendall(struct.pack("!I", len(payload)) + payload)


def recv_json(sock):
    length = struct.unpack("!I", recv_exact(sock, 4))[0]
    if not 0 < length <= MAX_JSON:
        raise ValueError("Invalid JSON frame length")
    message = json.loads(recv_exact(sock, length).decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("Expected a JSON object")
    return message
