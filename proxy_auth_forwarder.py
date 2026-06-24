import argparse
import base64
import selectors
import socket
import socketserver
import sys
from urllib.parse import urlparse, unquote


class UpstreamProxy:
    def __init__(self, url: str):
        parsed = urlparse(url if '://' in url else 'http://' + url)
        if not parsed.hostname or not parsed.port:
            raise ValueError('UPSTREAM_HTTP_PROXY must include host and port')
        self.host = parsed.hostname
        self.port = int(parsed.port)
        self.auth_header = ''
        if parsed.username or parsed.password:
            username = unquote(parsed.username or '')
            password = unquote(parsed.password or '')
            token = base64.b64encode(f'{username}:{password}'.encode()).decode()
            self.auth_header = f'Proxy-Authorization: Basic {token}\r\n'


UPSTREAM: UpstreamProxy | None = None


def recv_headers(sock: socket.socket, limit: int = 65536) -> bytes:
    data = bytearray()
    while b'\r\n\r\n' not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > limit:
            raise ValueError('request headers too large')
    return bytes(data)


def add_proxy_auth(header_blob: bytes, auth_header: str) -> bytes:
    if not auth_header:
        return header_blob
    header_end = header_blob.find(b'\r\n\r\n')
    if header_end < 0:
        return header_blob
    headers = header_blob[:header_end].decode('iso-8859-1', errors='replace').split('\r\n')
    body = header_blob[header_end + 4:]
    filtered = [line for line in headers if not line.lower().startswith('proxy-authorization:')]
    rebuilt = '\r\n'.join(filtered) + '\r\n' + auth_header + '\r\n'
    return rebuilt.encode('iso-8859-1') + body


def tunnel(sock_a: socket.socket, sock_b: socket.socket) -> None:
    sel = selectors.DefaultSelector()
    sock_a.setblocking(False)
    sock_b.setblocking(False)
    sel.register(sock_a, selectors.EVENT_READ, sock_b)
    sel.register(sock_b, selectors.EVENT_READ, sock_a)
    try:
        while True:
            events = sel.select(timeout=300)
            if not events:
                return
            for key, _ in events:
                src = key.fileobj
                dst = key.data
                try:
                    data = src.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                try:
                    dst.sendall(data)
                except OSError:
                    return
    finally:
        sel.close()


class ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        assert UPSTREAM is not None
        client = self.request
        upstream = None
        try:
            header_blob = recv_headers(client)
            if not header_blob:
                return
            first_line = header_blob.split(b'\r\n', 1)[0].decode('iso-8859-1', errors='replace')
            parts = first_line.split()
            if len(parts) < 3:
                return

            upstream = socket.create_connection((UPSTREAM.host, UPSTREAM.port), timeout=30)
            upstream.settimeout(30)
            if parts[0].upper() == 'CONNECT':
                target = parts[1]
                request = (
                    f'CONNECT {target} HTTP/1.1\r\n'
                    f'Host: {target}\r\n'
                    f'{UPSTREAM.auth_header}'
                    '\r\n'
                ).encode('iso-8859-1')
                upstream.sendall(request)
                response = recv_headers(upstream)
                client.sendall(response)
                status = response.split(b'\r\n', 1)[0]
                if b' 200 ' not in status and not status.endswith(b' 200'):
                    return
                tunnel(client, upstream)
            else:
                upstream.sendall(add_proxy_auth(header_blob, UPSTREAM.auth_header))
                tunnel(client, upstream)
        except Exception as exc:
            try:
                sys.stderr.write(f'proxy forwarder error: {exc}\n')
                sys.stderr.flush()
            except Exception:
                pass
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except OSError:
                    pass


class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    parser = argparse.ArgumentParser(description='Unauthenticated local proxy that forwards through an authenticated upstream proxy.')
    parser.add_argument('--upstream', required=True)
    parser.add_argument('--listen-host', default='127.0.0.1')
    parser.add_argument('--listen-port', type=int, default=18080)
    args = parser.parse_args()

    global UPSTREAM
    UPSTREAM = UpstreamProxy(args.upstream)
    with ThreadingTCPServer((args.listen_host, args.listen_port), ProxyHandler) as server:
        print(f'Proxy auth forwarder listening on {args.listen_host}:{args.listen_port} -> {UPSTREAM.host}:{UPSTREAM.port}', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
