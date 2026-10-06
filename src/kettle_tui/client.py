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
from .uicopy import UiCopy, in_process_payload

DISCOVERY_PATH = Path.home() / ".cache" / "kettle" / "server.json"
DEFAULT_URL = "http://127.0.0.1:4242"
# A file the user has decided about: approved, sent back for rework, or set aside
# with Not this. Handled files sink to the bottom and do not hold up a finish
# (SITTING-FLOWS §2.3a). Same set as the reference sitting's isHandled().
HANDLED_STATUSES = frozenset({"approved", "rework", "set-aside"})
PROPOSAL_ID = "desk-proposal"
TOKEN_HEADER = "X-Kettle-Token"
# Dual-read: new desks use .kettle/; a leftover .setsweep/ desk is still live.
SWEEP_HEALTH_PATHS = (
    ".kettle/runtime/sweep-health.json",
    ".setsweep/runtime/sweep-health.json",
)


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
            msg = f"{msg} (this project only; queue.json was not overwritten)"
        elif self.code == "busy":
            msg = f"{msg} — wait for the current turn to finish"
        bits = [msg]
        if self.code:
            bits.append(f"({self.code})")
        if self.status:
            bits.append(f"[{self.status}]")
        return " ".join(bits)


def _process_alive(pid: Any) -> bool | None:
    """True/False if we could tell whether *pid* exists; None if unknown."""
    try:
        n = int(pid)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    try:
        os.kill(n, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


def normalize_sweep_state(health: dict[str, Any] | None) -> str:
    """``running|done|failed|idle`` (or empty). A stale ``running`` pid is ``failed``."""
    if not health:
        return ""
    state = str(health.get("state") or "").lower()
    if state == "running" and _process_alive(health.get("pid")) is False:
        return "failed"
    return state


def sweep_health_status(
    health: dict[str, Any] | None, copy: UiCopy | None = None
) -> str | None:
    """Short status-bar token when the last finish (a sweep) did not complete."""
    state = normalize_sweep_state(health)
    if state == "running":
        return (copy or UiCopy()).state("sweeping", "line")
    if state == "failed":
        return "last batch did not finish"
    return None


def sweep_health_detail(health: dict[str, Any] | None) -> str | None:
    """Stream notice: last sweep died, with error and time if present."""
    state = normalize_sweep_state(health)
    if state not in ("running", "failed"):
        return None
    when = health.get("updated") or health.get("started") or "?"
    err = str(health.get("error") or state).strip()
    return f"the last batch did not finish: {err} at {when}"


def is_missing_project_folder(err: ClientError) -> bool:
    """404 from core when the desk root was moved or deleted."""
    if err.code != "not_found" and err.status != 404:
        return False
    msg = (err.message or "").lower()
    return "folder is missing" in msg or "moved or deleted" in msg


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
        self._demo_sweep_health: dict[str, dict[str, Any]] = {}
        self._demo_projects: list[dict[str, Any]] = (
            [dict(p) for p in demo.DEMO_PROJECTS] if demo_mode else []
        )
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

    def ui_copy(self) -> UiCopy:
        """The copy catalog (SITTING-FLOWS §7): live core, then in-process, then literals.

        Never raises — the catalog is words, and a missing one must not keep the
        TUI from opening. A core that predates ``/api/ui-copy`` answers 404.
        """
        if not self.demo_mode:
            try:
                pay = self._get("/api/ui-copy")
            except ClientError:
                pay = None
            if isinstance(pay, dict) and isinstance(pay.get("keys"), dict) and pay["keys"]:
                return UiCopy(pay, source="core")
        local = in_process_payload()
        if local:
            return UiCopy(local, source="in-process")
        return UiCopy()

    def projects(self) -> list[dict[str, Any]]:
        if self.demo_mode:
            return list(self._demo_projects)
        data = self._get("/api/projects")
        return list(data.get("projects") or [])

    def add_project(
        self,
        name: str,
        root: str,
        note: str,
        *,
        kit: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/projects/add — founding sentence (`note`) is required."""
        note = (note or "").strip()
        name = (name or "").strip() or default_project_name(root)
        root = (root or "").strip()
        if not note:
            raise ClientError(
                message=(
                    "a sentence about the project is required: what is this, and "
                    "how could a second reader help?"
                ),
                code="bad_request",
                status=400,
            )
        if not root:
            raise ClientError(
                message="folder path required",
                code="bad_request",
                status=400,
            )
        if self.demo_mode:
            pid = _demo_project_id(name, {p.get("id") for p in self._demo_projects})
            proj = {"id": pid, "name": name, "root": root}
            if kit:
                proj["kit"] = kit
            self._demo_projects.append(proj)
            self._demo_phases[pid] = "none"
            self._demo_queues[pid] = []
            return {"ok": True, "id": pid, "project": proj, **proj}
        body: dict[str, Any] = {"name": name, "root": root, "note": note}
        if kit:
            body["kit"] = kit
        return self._post("/api/projects/add", body)

    def refound_project(self, project_id: str) -> dict[str, Any]:
        """POST /api/projects/refound — reset charter/queue; keep append-only history."""
        if self.demo_mode:
            if not any(p.get("id") == project_id for p in self._demo_projects):
                raise ClientError(
                    message=f"no such project: {project_id}",
                    code="bad_request",
                    status=400,
                )
            self._demo_phases[project_id] = "none"
            self._demo_queues[project_id] = []
            self._demo_files = {
                k: v
                for k, v in self._demo_files.items()
                if not k.startswith(f"{project_id}:")
            }
            return {
                "ok": True,
                "backup": "demo://refound",
                "note_preserved": True,
                "kept": ["working-record.jsonl"],
            }
        return self._post("/api/projects/refound", {"id": project_id})

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

    def interview_status(self, project_id: str, *, mutate: bool = False) -> dict[str, Any]:
        """Founding phase beacon — mirrors GET /api/interview.

        Polling stays ``mutate=0`` (decision D1). Pass ``mutate=True`` on an
        explicit refresh of an unfounded desk so core can re-file a lost
        proposal entry as a safety net.
        """
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
        qs = urllib.parse.urlencode(
            {"project": project_id, "mutate": "1" if mutate else "0"}
        )
        return self._get(f"/api/interview?{qs}")

    def sweep_health(self, project_id: str) -> dict[str, Any]:
        """Read ``<desk>/runtime/sweep-health.json`` (SITTING-FLOWS §4.6).

        Missing file or unreadable JSON is an empty beacon, not an error.
        """
        if self.demo_mode:
            return dict(self._demo_sweep_health.get(project_id) or {})
        for path in SWEEP_HEALTH_PATHS:
            try:
                raw = self.file_text(project_id, path)
            except ClientError as e:
                if e.code == "not_found" or e.status == 404:
                    continue
                raise
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            return data if isinstance(data, dict) else {}
        return {}

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
            out: dict[str, Any] = {"ok": True, "status": "approved"}
            proposal = (
                e.get("id") == "desk-proposal" or e.get("founding_phase") == "proposal"
            )
            if proposal and self._demo_phases.get(project_id) != "founded":
                out["founding"] = True
            return out
        return self._post("/api/approve", {"project": project_id, "id": entry_id})

    def set_aside(
        self, project_id: str, entry_id: str, *, undo: bool = False
    ) -> dict[str, Any]:
        """POST /api/set-aside — Not this; ``undo`` brings the file back to pending.

        Core refuses the proposal entry with 400. Returns ``{ok, status}``.
        """
        if self.demo_mode:
            e = self._demo_entry(project_id, entry_id)
            if is_proposal_entry(e):
                raise ClientError(
                    message="the proposal is approved or edited, not set aside",
                    code="bad_request",
                    status=400,
                )
            if undo:
                e["status"] = "pending"
                e.pop("set_aside", None)
            else:
                from datetime import date

                e["status"] = "set-aside"
                e["set_aside"] = date.today().isoformat()
            return {"ok": True, "status": e["status"]}
        body: dict[str, Any] = {"project": project_id, "id": entry_id}
        if undo:
            body["undo"] = True
        return self._post("/api/set-aside", body)

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
                    "This is offline demo mode — no agent runs. "
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
            if thrust.strip():
                yield {"t": "text", "s": f"your note: {thrust.strip()}\n"}
            yield {
                "t": "text",
                "s": (
                    "Offline demo: no agent. Would apply your decisions, review what "
                    "changed, save a record, and line up the next files.\n"
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
                    "s": "The proposal is ready — edit it, then approve it.\n",
                }
                yield {"t": "done"}
                return
            self._demo_phases[project_id] = "interviewing"
            msg = (message or "").strip()
            if not msg:
                yield {
                    "t": "text",
                    "s": (
                        f"I'll read through **{project_id}** first.\n\n"
                        "What should this project protect? Answer here, then press "
                        "**d** when you want the proposal.\n"
                    ),
                }
            else:
                yield {"t": "text", "s": f"[demo agent] Heard: {msg}\n"}
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
            yield {
                "t": "text",
                "s": (
                    f"Wrote `{demo.PROPOSAL_PATH}`. Edit it in the center pane. "
                    "Chat to revise. When it matches what you want, **Approve** it.\n"
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
            yield {
                "t": "text",
                "s": "Project notes written and the first files lined up.\n",
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


def default_project_name(root: str) -> str:
    """Display name from a folder path (CLI / web parity)."""
    base = Path(root or "").expanduser().name
    return base.replace("-", " ").replace("_", " ").title() or "Project"


def _demo_project_id(name: str, taken: set) -> str:
    slug = "".join(c.lower() if c.isalnum() else "_" for c in name).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    slug = (slug or "project")[:40]
    pid = slug
    n = 2
    while pid in taken:
        pid = f"{slug}_{n}"
        n += 1
    return pid


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


def entry_status(entry: dict[str, Any] | None) -> str:
    return str((entry or {}).get("status") or "pending").lower()


def is_handled(entry: dict[str, Any] | None) -> bool:
    """Approved, rework or set aside — the user has decided about this file."""
    return entry_status(entry) in HANDLED_STATUSES


def is_set_aside(entry: dict[str, Any] | None) -> bool:
    return entry_status(entry) == "set-aside"


def is_proposal_entry(entry: dict[str, Any] | None) -> bool:
    if not entry:
        return False
    return entry.get("id") == PROPOSAL_ID or entry.get("founding_phase") == "proposal"


def all_handled(entries: list[dict[str, Any]]) -> bool:
    """Queue completion: every file handled (and at least one file)."""
    return bool(entries) and all(is_handled(e) for e in entries)


def sort_queue(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Match web: unhandled first, then urgency high→low."""
    order = {"high": 0, "medium": 1, "low": 2}

    def key(e: dict[str, Any]) -> tuple:
        handled = 1 if is_handled(e) else 0
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
    proposal: bool = False,
    set_aside: bool = False,
) -> dict[str, Any]:
    """Mirror kettle-vscode ritualParity.ritualControls.

    Approve reads ``approveLabel`` in every phase ("Found Desk" is retired); only
    its hint changes while a proposal is open. Not this is off during setup and on
    the proposal entry, and reads Bring back on a file already set aside.
    Returns catalog *keys*, not words — the view resolves them.
    """
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
        "can_not_this": can_file and not proposing and not proposal,
        "can_sweep": (
            has_project
            and not interviewing
            and not proposing
            and not streaming
        ),
        "approve_label_key": "approveLabel",
        "approve_hint_key": "approveHintFounding" if proposing else "approveHint",
        "not_this_undo": set_aside,
        "not_this_label_key": "notThisUndoLabel" if set_aside else "notThisLabel",
        "not_this_hint_key": "notThisUndoHint" if set_aside else "notThisHint",
    }


def founding_steps(phase: str, copy: UiCopy | None = None) -> tuple[int, int, str]:
    """Return (step_index_1based, total_steps, title) for TUI step chrome.

    Titles are catalog state lines (SITTING-FLOWS §7); before the interview has
    started there is no state yet, so the strip uses the setup heading.
    """
    c = copy or UiCopy()
    total = 3
    if phase == "none":
        return 1, total, c.text("foundingHeadTitle")
    if phase == "interviewing":
        return 1, total, c.state("interviewing", "line")
    if phase == "proposing":
        return 2, total, c.state("proposing", "line")
    if phase == "founding":
        return 3, total, c.state("founding", "line")
    return 3, total, c.state("founded", "line")
