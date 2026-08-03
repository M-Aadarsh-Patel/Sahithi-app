"""Session handling checks — the cookie, the idle timeout, and the htmx redirect.

Starts the app on a free port, exercises it over real HTTP, then stops it. Run it
before a deploy, and after touching anything in current_user or login_required.

**Writes nothing.** Every request that reaches POST /entries is either rejected by
current_user first, or aimed at a student id that does not exist — and the entries
count is asserted unchanged at the end. Safe to run against the live database.

    python test_session.py
"""

import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from itsdangerous import TimestampSigner

# Importing db loads .env, which is where SESSION_SECRET and the passwords live.
from db import db
from ist import IST

HERE = os.path.dirname(os.path.abspath(__file__))
ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and condition
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Follow nothing — the redirect itself is what most of these assertions read."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


opener = urllib.request.build_opener(NoRedirect)


def request(
    base: str,
    method: str,
    path: str,
    cookie: str | None = None,
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], str]:
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(f"{base}{path}", method=method, data=body)
    if body:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if cookie:
        req.add_header("Cookie", f"session={cookie}")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        response = opener.open(req)
        return (
            response.status,
            {k.lower(): v for k, v in response.headers.items()},
            response.read().decode(),
        )
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_server(port: int) -> subprocess.Popen[bytes]:
    """The app on a real port. A free port, so a dev server can stay running."""
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=HERE,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        if server.poll() is not None:
            raise RuntimeError("the server exited during startup")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            return server
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.2)
    server.terminate()
    raise RuntimeError("the server never became healthy")


def forge(signer: TimestampSigner, user_id: object, minutes_ago: int) -> str:
    """A validly signed cookie whose last_seen is however stale we want.

    Beats waiting half an hour, and it is the only way to test the boundary from
    both sides. max_age=None means the signature has no age of its own, so only
    the last_seen inside the payload decides.
    """
    stamp = (datetime.now(IST) - timedelta(minutes=minutes_ago)).isoformat()
    payload = {"user_id": str(user_id), "last_seen": stamp}
    return signer.sign(base64.b64encode(json.dumps(payload).encode())).decode()


def run_checks(base: str) -> None:
    signer = TimestampSigner(str(os.environ["SESSION_SECRET"]))
    sahithi = db.users.find_one({"username": "sahithi"})
    assert sahithi is not None, "seed the users first: python seed_students.py"
    before = db.entries.count_documents({})
    today = datetime.now(IST).strftime("%Y-%m-%d")

    print("\n1. max_age=None -> browser-session cookie")
    status, headers, _ = request(
        base, "POST", "/login",
        data={"username": "sahithi", "password": os.environ["SAHITHI_PASSWORD"]},
    )
    set_cookie = headers.get("set-cookie", "")
    check("login succeeds", status == 303, f"status={status}")
    check("no Max-Age on the cookie", "max-age" not in set_cookie.lower())
    check("no Expires on the cookie", "expires" not in set_cookie.lower())
    check("still httponly", "httponly" in set_cookie.lower())
    live = set_cookie.split("session=", 1)[1].split(";", 1)[0]

    print("\n2. idle timeout, independent of the cookie")
    status, _, body = request(base, "GET", f"/entries/{today}", cookie=live)
    check("fresh session loads entries", status == 200 and "roll" in body, f"status={status}")

    fresh = forge(signer, sahithi["_id"], minutes_ago=29)
    status, _, _ = request(base, "GET", f"/entries/{today}", cookie=fresh)
    check("29 min idle still allowed", status == 200, f"status={status}")

    stale = forge(signer, sahithi["_id"], minutes_ago=31)
    status, headers, _ = request(base, "GET", f"/entries/{today}", cookie=stale)
    check("31 min idle redirected", status == 303, f"status={status}")
    check("  -> Location: /login", headers.get("location") == "/login")
    check("  -> stale cookie cleared", "null" in headers.get("set-cookie", ""))

    rolling = request(base, "GET", f"/entries/{today}", cookie=fresh)[1]
    check("active request re-stamps last_seen", "session=" in rolling.get("set-cookie", ""))

    status, headers, _ = request(base, "GET", "/logout", cookie=live)
    check("logout redirects to /login", status == 303, f"status={status}")
    check("  -> Location: /login", headers.get("location") == "/login")
    check("  -> cookie deleted", "null" in headers.get("set-cookie", ""))
    # Deliberately not asserting that the captured cookie stops working. Sessions
    # are stateless: logout deletes the browser's copy, it cannot revoke a value
    # already signed. IDLE_TIMEOUT is what bounds a stolen one.

    print("\n3. HX-Redirect instead of the login page in a row")
    hx = {"HX-Request": "true"}
    entry = {"student_id": str(sahithi["_id"]), "date": today, "status": "present"}

    status, headers, body = request(base, "POST", "/entries", cookie=stale, headers=hx, data=entry)
    check("expired htmx POST is 401", status == 401, f"status={status}")
    check("  -> HX-Redirect: /login", headers.get("hx-redirect") == "/login")
    check("  -> no Location header to follow", "location" not in headers)
    check("  -> body is not the login page", "<form" not in body.lower(), f"body={body[:60]!r}")

    status, headers, _ = request(base, "POST", "/entries", cookie=stale, data=entry)
    check("expired non-htmx POST still 303", status == 303, f"status={status}")
    check("  -> Location: /login", headers.get("location") == "/login")

    # Valid session, deliberately absent student: proves auth passed, writes nothing.
    status, headers, _ = request(
        base, "POST", "/entries", cookie=live, headers=hx,
        data={**entry, "student_id": "0" * 24},
    )
    check("valid htmx POST gets past auth", status == 404, f"status={status}")
    check("  -> no HX-Redirect on a valid session", "hx-redirect" not in headers)

    print("\n4. nothing was written")
    check("entries unchanged", before == db.entries.count_documents({}), f"before={before}")


def main() -> None:
    port = free_port()
    server = start_server(port)
    try:
        run_checks(f"http://127.0.0.1:{port}")
    finally:
        server.terminate()
        server.wait(timeout=10)
    print("\nALL PASS" if ok else "\nFAILURES ABOVE")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
