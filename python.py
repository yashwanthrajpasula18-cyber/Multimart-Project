"""
Backend: REST API + static file server for the multi-store order system.
Run:  python3 server.py     then open http://localhost:8000
Uses only the standard library, and the Shop engine from shop.py.
"""
import json
import os
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from shop import Shop, PaymentGateway, OutOfStock

HERE = os.path.dirname(os.path.abspath(__file__))


def seed():
    s = Shop(reservation_ttl=120)
    s.add_store("S-A", "Alpha Mobiles")
    s.add_store("S-B", "Beta Electronics")
    s.add_store("S-C", "Gamma Stationery")
    s.add_stock("S-A", "PHONE", 3, 1_500_000)
    s.add_stock("S-B", "PHONE", 2, 1_480_000)
    s.add_stock("S-A", "CHARGER", 6, 79_900)
    s.add_stock("S-B", "EARBUDS", 4, 249_900)
    s.add_stock("S-C", "NOTEBOOK", 20, 6_000)
    s.add_stock("S-C", "PEN", 30, 2_500)
    return s


shop = seed()
gateway = PaymentGateway(shop)


def state():
    with shop.lock:
        db = shop.db
        stores = [dict(r) for r in db.execute("SELECT * FROM stores")]
        order_ids = [r["id"] for r in db.execute("SELECT id FROM orders ORDER BY rowid DESC")]
        orders = [shop.order_view(i) for i in order_ids]
        timeline = [dict(r) for r in db.execute(
            "SELECT order_id, message, attention, ts FROM timeline ORDER BY id DESC LIMIT 60")]
        attention = [dict(r) for r in db.execute(
            "SELECT order_id, message FROM timeline WHERE attention=1 ORDER BY id DESC LIMIT 20")]
        try:
            shop.check_invariants()
            consistent = True
        except AssertionError as e:
            consistent = str(e)
        return {"stores": stores, "inventory": shop.inventory(), "orders": orders,
                "timeline": timeline, "attention": attention, "consistent": consistent}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, ctype="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        if self.path == "/api/state":
            return self._send(200, state())
        static = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/index.html": ("index.html", "text/html; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8"),
                  "/app.jsx": ("app.jsx", "text/javascript; charset=utf-8")}
        if self.path in static:
            name, ctype = static[self.path]
            with open(os.path.join(HERE, name), "rb") as f:
                return self._send(200, f.read(), ctype)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        global shop, gateway
        n = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        try:
            p = self.path
            if p == "/api/order":
                items = [(i["sku"], int(i["qty"])) for i in data["items"] if int(i["qty"]) > 0]
                if not items:
                    return self._send(400, {"error": "add at least one item"})
                return self._send(200, {"order_id": shop.place_order(data.get("customer") or "guest", items)})
            if p == "/api/pay":
                results = gateway.notify(data["order_id"], data.get("kind", "payment.succeeded"),
                                         duplicates=int(data.get("duplicates", 2)))
                return self._send(200, {"results": results})
            if p == "/api/dispatch":
                return self._send(200, {"result": shop.dispatch_part(int(data["part_id"]))})
            if p == "/api/store/fail":
                shop.fail_store(data["store_id"]); return self._send(200, {"ok": True})
            if p == "/api/store/restore":
                shop.restore_store(data["store_id"]); return self._send(200, {"ok": True})
            if p == "/api/expire":
                return self._send(200, {"expired": shop.expire_reservations()})
            if p == "/api/reset":
                shop = seed(); gateway = PaymentGateway(shop)
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})
        except OutOfStock as e:
            self._send(409, {"error": f"Out of stock - {e}"})
        except (KeyError, ValueError) as e:
            self._send(400, {"error": f"bad request: {e}"})


if __name__ == "__main__":
    print("Open http://localhost:8000")
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()