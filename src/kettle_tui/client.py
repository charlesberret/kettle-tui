"""Localhost kettle-core client (discovery + thin HTTP).

Follows the viewport contract in kettle-core API.md:
- Base: http://127.0.0.1:<port>
- Auth: X-Kettle-Token from ~/.cache/kettle/server.json when present
- Host header must be 127.0.0.1 or localhost
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from . import demo

DISCOVERY_PATH = Path.home() / ".cache" / "kettle" / "server.json"
DEFAULT_URL = "http://127.0.0.1:4242"
TOKEN_HEADER = "X-Kettle-Token"


@dataclass
class Discovery:
    url: str
    token: str | None
    port: int | None
    projects_path: str | None
    pid: int | None
    source: str  # discovery | flag | default | demo


@dataclass
class ClientError(Exception):
    message: str
    code: str | None = None
    status: int | None = None

    def __str__(self) -> str:
        msg = self.message
        if self.code == "curator_unavailable" and "kettle doctor" not in msg.lower():
            msg = f"{msg} — run kettle doctor"
        elif self.code == "queue_corrupt":
            msg = f"{msg} (this desk only; queue.json was not overwritten)"
        elif self.code == "busy":
            msg = f"{msg} — wait for the current turn to finish"
        bits = [msg]
        if self.code:
            bits.append(f"({self.code})")
        if self.status:
            bits.append(f"[{self.status}]")
        return " ".join(bits)


def load_discovery(explicit_url: str | None = None) -> Discovery:
    if explicit_url:
        return Discovery(
            url=explicit_url.rstrip("/"),
            token=_token_from_file(),
            port=_port_from_url(explicit_url),
            projects_path=None,
            pid=None,
            source="flag",
        )

    data = _read_json(DISCOVERY_PATH)
    if isinstance(data, dict) and data.get("url"):
        url = str(data["url"]).rstrip("/")
        return Discovery(
            url=url,
            token=data.get("token") if isinstance(data.get("token"), str) else None,
            port=data.get("port") if isinstance(data.get("port"), int) else _port_from_url(url),
            projects_path=data.get("projects") if isinstance(data.get("projects"), str) else None,
            pid=data.get("pid") if isinstance(data.get("pid"), int) else None,
            source="discovery",
        )

    return Discovery(
        url=DEFAULT_URL,
        token=None,
        port=4242,
        projects_path=None,
        pid=None,
        source="default",
    )


def _token_from_file() -> str | None:
    data = _read_json(DISCOVERY_PATH)
    if isinstance(data, dict) and isinstance(data.get("token"), str):
        return data["token"]
    return None


def _port_from_url(url: str) -> int | None:
    try:
        return urllib.parse.urlparse(url).port
    except Exception:
        return None


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


class KettleClient:
    """Thin JSON client. Demo mode keeps an in-memory queue and never hits the network."""

    def __init__(
        self,
        *,
        demo_mode: bool = False,
        url: str | None = None,
        token: str | None = None,
    ) -> None:
        self.demo_mode = demo_mode
        self._demo_queues = demo.fresh_queues() if demo_mode else {}
        self._demo_files: dict[str, str] = {}
        self._demo_phases: dict[str, str] = dict(demo.DEMO_PHASES) if demo_mode else {}
        if demo_mode:
            self.discovery = Discovery(
                url="demo://local",
                token=None,
                port=None,
                projects_path=None,
                pid=None,
                source="demo",
            )
            self._token = None
            self._base = "demo://local"
        else:
            disc = load_discovery(url)
            if token:
                disc = Discovery(
                    url=disc.url,
                    token=token,
                    port=disc.port,
                    projects_path=disc.projects_path,
                    pid=disc.pid,
                    source=disc.source,
                )
            self.discovery = disc
            self._token = disc.token
            self._base = disc.url

    # ── public API ──────────────────────────────────────────────────────

    def ping(self) -> dict[str, Any]:
        if self.demo_mode:
            return {
                "app": "kettle",
                "version": "demo",
                "api_version": 0,
                "desk_schema_version": 0,
                "port": None,
                "curator": {"provider": "demo", "command_exists": True},
            }
        return self._get("/api/ping")

    def projects(self) -> list[dict[str, Any]]:
        if self.demo_mode:
            return list(demo.DEMO_PROJECTS)
        data = self._get("/api/projects")
        return list(data.get("projects") or [])

    def queue(self, project_id: str, *, mutate: bool = False) -> dict[str, Any]:
        if self.demo_mode:
            entries = self._demo_queues.setdefault(project_id, [])
            return {"curated": "demo", "entries": list(entries)}
        qs = urllib.parse.urlencode(
            {
                "project": project_id,
                "mutate": "1" if mutate else "0",
            }
        )
        return self._get(f"/api/queue?{qs}")

    def interview_status(self, project_id: str) -> dict[str, Any]:
        """Founding phase beacon — mirrors GET /api/interview."""
        if self.demo_mode:
            phase = self._demo_phases.get(project_id, "founded")
            entries = self._demo_queues.get(project_id) or []
            content = [
                e
                for e in entries
                if e.get("id") != "desk-proposal"
                and e.get("founding_phase") != "proposal"
            ]
            has_proposal = any(
                e.get("id") == "desk-proposal" or e.get("founding_phase") == "proposal"
                for e in entries
            ) or phase == "proposing"
            # Map to API status field. Content entries do not found the desk.
            if phase == "founded":
                status = "founded"
            elif phase == "proposing" or has_proposal:
                status = "proposing"
            elif phase == "interviewing":
                status = "interviewing"
            else:
                status = "none"
            founded = status == "founded"
            return {
                "status": status,
                "founded": founded,
                "has_charter": founded,
                "has_session": phase in ("interviewing", "proposing", "founded"),
                "has_entries": bool(content),
                "entry_count": len(content),
                "has_proposal": has_proposal,
                "proposal_path": demo.PROPOSAL_PATH,
            }
        qs = urllib.parse.urlencode({"project": project_id, "mutate": "0"})
        return self._get(f"/api/interview?{qs}")

    def file_text(self, project_id: str, path: str) -> str:
        """File body for the editor. Raises ClientError on hard failure."""
        if self.demo_mode:
            key = f"{project_id}:{path}"
            if key in self._demo_files:
                return self._demo_files[key]
            return demo.file_content(path)
        qs = urllib.parse.urlencode({"project": project_id, "path": path})
        data = self._get(f"/api/file?{qs}")
        content = data.get("content") if isinstance(data, dict) else None
        if not isinstance(content, str):
            raise ClientError(message="file response missing content")
        return content

    def entry_diff(self, project_id: str, entry_id: str) -> str:
        """Unified diff vs snapshot for an entry (GET /api/diff)."""
        if self.demo_mode:
            e = self._demo_entry(project_id, entry_id)
            path = e.get("path") or entry_id
            d = e.get("diff") if isinstance(e.get("diff"), dict) else {}
            a, r = d.get("added", 0) or 0, d.get("removed", 0) or 0
            return (
                f"--- a/{path}\n+++ b/{path}\n"
                f"@@ demo diff +{a} -{r} @@\n"
                f" (demo mode — no real snapshot)\n"
            )
        qs = urllib.parse.urlencode({"project": project_id, "id": entry_id})
        data = self._get(f"/api/diff?{qs}")
        text = data.get("text")
        if isinstance(text, str):
            return text
        legacy = data.get("diff")
        if isinstance(legacy, str):
            return legacy
        return ""

    def save_file(self, project_id: str, path: str, content: str) -> None:
        if self.demo_mode:
            self._demo_files[f"{project_id}:{path}"] = content
            return
        self._post("/api/save", {"project": project_id, "path": path, "content": content})

    def clear_session(self, project_id: str) -> dict[str, Any]:
        if self.demo_mode:
            return {"ok": True}
        return self._post("/api/clear-session", {"project": project_id})

    def compact_events(
        self, project_id: str, *, model: str | None = None
    ) -> Iterator[dict[str, Any]]:
        if self.demo_mode:
            yield {"t": "text", "s": "— demo compact —\n"}
            yield {"t": "text", "s": "Session summarized (offline stub).\n"}
            yield {"t": "done"}
            return
        body: dict[str, Any] = {"project": project_id}
        if model:
            body["model"] = model
        yield from self._post_ndjson("/api/compact", body)

    def ensure_reachable(self, *, timeout_s: float = 12.0) -> bool:
        """Ping; if unreachable, try to spawn `kettle --web --no-open` and wait.

        Returns True if a core answers. Demo mode always True.
        """
        if self.demo_mode:
            return True
        try:
            self.ping()
            return True
        except ClientError:
            pass
        kettle = shutil.which("kettle")
        if not kettle:
            return False
        try:
            subprocess.Popen(  # noqa: S603 — intentional local core launch
                [kettle, "--web", "--no-open"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            return False
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            # Refresh discovery/token if a new core just wrote server.json
            if self.discovery.source != "flag":
                disc = load_discovery()
                self.discovery = disc
                self._token = disc.token
                self._base = disc.url
            else:
                tok = _token_from_file()
                if tok:
                    self._token = tok
            try:
                self.ping()
                return True
            except ClientError:
                time.sleep(0.35)
        return False

    def approve(self, project_id: str, entry_id: str) -> dict[str, Any]:
        if self.demo_mode:
            e = self._demo_entry(project_id, entry_id)
            e["status"] = "approved"
            from datetime import date

            e["approved"] = date.today().isoformat()
            return {"ok": True, "status": "approved"}
        return self._post("/api/approve", {"project": project_id, "id": entry_id})

    def comment(self, project_id: str, entry_id: str, text: str) -> dict[str, Any]:
        if self.demo_mode:
            e = self._demo_entry(project_id, entry_id)
            t = text.strip()
            if t:
                e["comment"] = t
            else:
                e.pop("comment", None)
            return {"ok": True, "comment": t}
        return self._post(
            "/api/comment",
            {"project": project_id, "id": entry_id, "text": text},
        )

    def chat_events(
        self,
        project_id: str,
        message: str,
        *,
        entry_id: str | None = None,
        model: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield NDJSON objects from POST /api/chat (or a demo stub)."""
        if self.demo_mode:
            yield {"t": "text", "s": f"[demo] You said: {message.strip()}\n"}
            yield {
                "t": "text",
                "s": (
                    "This is offline demo mode — no curator runs. "
                    "Connect to a live kettle core for real chat.\n"
                ),
            }
            yield {"t": "done"}
            return

        body: dict[str, Any] = {"project": project_id, "message": message}
        if entry_id:
            body["id"] = entry_id
        if model:
            body["model"] = model
        yield from self._post_ndjson("/api/chat", body)

    def sweep_events(
        self,
        project_id: str,
        *,
        thrust: str = "",
        model: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield NDJSON objects from POST /api/sweep (or a demo stub)."""
        if self.demo_mode:
            yield {"t": "text", "s": "— demo sweep started —\n"}
            if thrust.strip():
                yield {"t": "text", "s": f"steer: {thrust.strip()}\n"}
            yield {
                "t": "text",
                "s": (
                    "Offline demo: no curator. Would apply rulings, assess diffs, "
                    "log the sitting, and restock the queue.\n"
                ),
            }
            yield {"t": "done"}
            return

        body: dict[str, Any] = {"project": project_id}
        if thrust.strip():
            body["thrust"] = thrust.strip()
        if model:
            body["model"] = model
        yield from self._post_ndjson("/api/sweep", body)

    def interview_events(
        self,
        project_id: str,
        message: str = "",
        *,
        model: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """POST /api/interview — kickoff (empty message) or turn."""
        if self.demo_mode:
            phase = self._demo_phases.get(project_id, "none")
            if phase in ("proposing", "founded"):
                yield {
                    "t": "text",
                    "s": "Desk Proposal is ready — edit it, then Found Desk.\n",
                }
                yield {"t": "done"}
                return
            self._demo_phases[project_id] = "interviewing"
            msg = (message or "").strip()
            if not msg:
                yield {"t": "text", "s": "— founding interview (demo) —\n"}
                yield {
                    "t": "text",
                    "s": (
                        f"I'll survey **{project_id}** from the files (tool-less).\n\n"
                        "What should this desk protect? Answer here, then press "
                        "**draft proposal** when ready.\n"
                    ),
                }
            else:
                yield {"t": "text", "s": f"[demo curator] Heard: {msg}\n"}
                yield {
                    "t": "text",
                    "s": "Noted. Anything else, or draft the proposal?\n",
                }
            yield {"t": "done"}
            return

        body: dict[str, Any] = {"project": project_id, "message": message}
        if model:
            body["model"] = model
        yield from self._post_ndjson("/api/interview", body)

    def propose_events(
        self, project_id: str, *, model: str | None = None
    ) -> Iterator[dict[str, Any]]:
        """POST /api/propose — finish interview → DESK-PROPOSAL.md."""
        if self.demo_mode:
            self._demo_phases[project_id] = "proposing"
            entries = self._demo_queues.setdefault(project_id, [])
            if not any(e.get("id") == "desk-proposal" for e in entries):
                entries.append(demo.proposal_entry())
            self._demo_files[f"{project_id}:{demo.PROPOSAL_PATH}"] = demo.PROPOSAL_BODY
            yield {"t": "text", "s": "— drafting desk proposal (demo) —\n"}
            yield {
                "t": "text",
                "s": (
                    f"Wrote `{demo.PROPOSAL_PATH}`. Edit it in the center pane. "
                    "Chat to revise. When it matches your intent, **Found Desk**.\n"
                ),
            }
            yield {"t": "done"}
            return

        body: dict[str, Any] = {"project": project_id}
        if model:
            body["model"] = model
        yield from self._post_ndjson("/api/propose", body)

    def found_events(
        self, project_id: str, *, model: str | None = None
    ) -> Iterator[dict[str, Any]]:
        """POST /api/found — digest approved proposal + first content batch."""
        if self.demo_mode:
            self._demo_phases[project_id] = "founded"
            # Drop proposal placeholder; stock content queue
            self._demo_queues[project_id] = demo.first_content_batch()
            yield {"t": "text", "s": "— founding desk (demo) —\n"}
            yield {
                "t": "text",
                "s": (
                    "Charter and first queue filed. The sitting is open — "
                    "select an entry on the left.\n"
                ),
            }
            yield {"t": "done"}
            return

        body: dict[str, Any] = {"project": project_id}
        if model:
            body["model"] = model
        yield from self._post_ndjson("/api/found", body)

    def _demo_entry(self, project_id: str, entry_id: str) -> dict[str, Any]:
        for e in self._demo_queues.get(project_id) or []:
            if str(e.get("id")) == str(entry_id):
                return e
        raise ClientError(message=f"unknown entry: {entry_id}", code="not_found", status=404)

    # ── HTTP ────────────────────────────────────────────────────────────

    def _get(self, path: str) -> dict[str, Any]:
        return self._request_json("GET", path)

    def _post(self, path: str, body: dict | None = None) -> dict[str, Any]:
        return self._request_json("POST", path, body)

    def _reload_discovery(self) -> None:
        """Re-read server.json after a stale token (core restarted)."""
        if self.demo_mode:
            return
        if self.discovery.source != "flag":
            disc = load_discovery()
            self.discovery = disc
            self._token = disc.token
            self._base = disc.url
        else:
            tok = _token_from_file()
            if tok:
                self._token = tok

    def _client_error_from_http(self, e: urllib.error.HTTPError) -> ClientError:
        err_body: dict[str, Any] = {}
        try:
            err_body = json.loads(e.read().decode("utf-8"))
        except Exception:
            pass
        return ClientError(
            message=str(err_body.get("error") or e.reason or "http error"),
            code=err_body.get("code") if isinstance(err_body.get("code"), str) else None,
            status=e.code,
        )

    def _request_json(
        self, method: str, path: str, body: dict | None = None
    ) -> dict[str, Any]:
        raw = self._request_raw(method, path, body, accept="application/json")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise ClientError(message=f"invalid JSON from {path}") from e

    def _post_ndjson(self, path: str, body: dict) -> Iterator[dict[str, Any]]:
        yield from self._post_ndjson_once(path, body, auth_retry=True)

    def _post_ndjson_once(
        self, path: str, body: dict, *, auth_retry: bool
    ) -> Iterator[dict[str, Any]]:
        url = self._base + path
        data = json.dumps(body).encode("utf-8")
        headers = {
            "Accept": "application/x-ndjson, application/json",
            "Content-Type": "application/json",
            "Host": self._host_header(),
        }
        if self._token:
            headers[TOKEN_HEADER] = self._token
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                for line in resp:
                    line = line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        yield {"t": "text", "s": line + "\n"}
        except urllib.error.HTTPError as e:
            err = self._client_error_from_http(e)
            if err.code == "auth" and auth_retry:
                self._reload_discovery()
                yield from self._post_ndjson_once(path, body, auth_retry=False)
                return
            raise err from e
        except urllib.error.URLError as e:
            raise ClientError(message=f"cannot reach {self._base}: {e.reason}") from e

    def _request_raw(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        accept: str = "application/json",
        auth_retry: bool = True,
    ) -> str:
        url = self._base + path
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {
            "Accept": accept,
            "Host": self._host_header(),
        }
        if self._token:
            headers[TOKEN_HEADER] = self._token
        if data is not None:
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            err = self._client_error_from_http(e)
            if err.code == "auth" and auth_retry:
                self._reload_discovery()
                return self._request_raw(
                    method, path, body, accept=accept, auth_retry=False
                )
            raise err from e
        except urllib.error.URLError as e:
            raise ClientError(message=f"cannot reach {self._base}: {e.reason}") from e

    def _host_header(self) -> str:
        parsed = urllib.parse.urlparse(self._base)
        host = parsed.hostname or "127.0.0.1"
        if parsed.port:
            return f"{host}:{parsed.port}"
        return host


def resolve_project_root(root: Any) -> Path | None:
    """Absolute path of a registry `root`, or None if it cannot be resolved."""
    if not root:
        return None
    try:
        return Path(str(root)).expanduser().resolve()
    except (OSError, RuntimeError):
        return None


def match_project_here(
    projects: list[dict[str, Any]],
    here: str | Path | None = None,
) -> str | None:
    """Id of the registered desk whose root is `here` or an ancestor of it.

    Longest root wins so a nested desk beats a parent tree. A directory *above*
    a desk does not match — that is a different place, not that project.
    """
    try:
        here_p = Path(here or os.getcwd()).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    best_id: str | None = None
    best_len = -1
    for p in projects:
        root = resolve_project_root(p.get("root"))
        if root is None:
            continue
        try:
            here_p.relative_to(root)
        except ValueError:
            continue
        n = len(root.parts)
        if n > best_len:
            best_len = n
            pid = p.get("id")
            best_id = str(pid) if pid else None
    return best_id


def default_project_id(
    projects: list[dict[str, Any]],
    preferred: str | None = None,
    *,
    here: str | Path | None = None,
    require_here: bool = False,
) -> str | None:
    """Pick a desk id.

    Order: ``--project`` / preferred id, then ``KETTLE_PROJECT``, then the
    registered root that contains ``here`` (cwd when omitted). With
    ``require_here`` (``kettle-tui --here``) a miss stays unbound — do not
    silently open some other project's sitting.
    """
    if preferred:
        for p in projects:
            if p.get("id") == preferred:
                return preferred
    env = os.environ.get("KETTLE_PROJECT")
    if env:
        for p in projects:
            if p.get("id") == env:
                return env
    matched = match_project_here(projects, here)
    if matched:
        return matched
    if require_here:
        return None
    return projects[0]["id"] if projects else None


def sort_queue(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Match web: unhandled first, then urgency high→low."""
    order = {"high": 0, "medium": 1, "low": 2}

    def key(e: dict[str, Any]) -> tuple:
        st = (e.get("status") or "pending").lower()
        handled = 1 if st in ("approved", "rework") else 0
        urg = order.get((e.get("urgency") or "").lower(), 9)
        return (handled, urg)

    return sorted(entries, key=key)


def normalize_phase(status: dict[str, Any] | None) -> str:
    """Map GET /api/interview payload → none|interviewing|proposing|founded.

    Core founded = charter (PROJECT.md) AND ≥1 content entry. Viewports must
    use the ``founded`` boolean when present. ``has_entries`` alone is not
    founded — a found run can file the queue and still fail to write the charter.
    """
    if not status:
        return "none"
    if "founded" in status:
        if status.get("founded") is True:
            return "founded"
    elif str(status.get("status") or "").lower() == "founded":
        return "founded"
    st = str(status.get("status") or "none").lower()
    if st in ("interviewing", "proposing", "none"):
        if st == "none" and status.get("has_proposal"):
            return "proposing"
        return st
    if st == "founded":
        # Boolean was False (or absent and we already returned); stay unfounded.
        if status.get("has_proposal"):
            return "proposing"
        return "none"
    if status.get("has_proposal"):
        return "proposing"
    return "none"


def ritual_controls(
    *,
    has_project: bool,
    has_entry: bool,
    approved: bool,
    streaming: bool = False,
    phase: str = "founded",
) -> dict[str, Any]:
    """Mirror kettle-vscode ritualParity.ritualControls."""
    interviewing = phase in ("none", "interviewing")
    proposing = phase == "proposing"
    can_file = has_entry and not interviewing and not streaming
    return {
        "interviewing": interviewing,
        "proposing": proposing,
        "can_approve": (
            (has_entry and not streaming)
            if proposing
            else (can_file and not approved)
        ),
        "can_comment": can_file and not proposing,
        "can_sweep": (
            has_project
            and not interviewing
            and not proposing
            and not streaming
        ),
        "approve_label": "Found Desk" if proposing else "Approve",
    }


def founding_steps(phase: str) -> tuple[int, int, str]:
    """Return (step_index_1based, total_steps, title) for TUI step chrome.

    Pattern mirrors tuillem/dialog wizards: numbered steps, one primary action.
    """
    total = 3
    if phase in ("none",):
        return 1, total, "Begin interview"
    if phase == "interviewing":
        return 1, total, "Founding interview"
    if phase == "proposing":
        return 2, total, "Edit desk proposal"
    return 3, total, "Sitting open"
