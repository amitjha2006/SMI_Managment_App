"""Auto-update check and staged download.

Reads the app version from a repo-root `VERSION` file and compares it to the
latest GitHub release. Best-effort: network/GitHub failures never break the app
and simply report "no update info available".

Staged install (silent auto-update):
- POST /update/download downloads the latest release zip to update-staged/
  next to the exe and extracts it.
- main_window.py applies the staged swap at next startup (before opening the
  window) because Windows locks in-use files (exe, DLLs) and AGENTS.md forbids
  swapping into a running _internal.
- After applying, main_window.py launches the new exe instance and exits, so
  the user just restarts once and lands on the new version.
"""
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

_GITHUB_REPO = "amitjha2006/SMI_Managment_App"
_LATEST_RELEASE_URL = f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest"


def _repo_root() -> Path:
    """Repository root: next to the exe when frozen, else up from this file."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # backend/app/routes/updates.py -> parents[3] = repo root
    return Path(__file__).resolve().parents[3]


def _staging_dir() -> Path:
    return _repo_root() / "update-staged"


def _read_version_file() -> str | None:
    for cand in (_repo_root() / "VERSION", Path.cwd() / "VERSION"):
        try:
            return cand.read_text(encoding="utf-8").strip() or None
        except (OSError, FileNotFoundError):
            continue
    return None


def _fetch_latest_release() -> dict | None:
    """Return parsed GitHub latest-release JSON, or None on any failure."""
    req = urllib.request.Request(
        _LATEST_RELEASE_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "smi-updater"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None
    assets = []
    for a in data.get("assets", []):
        name = a.get("name", "")
        if name.lower().endswith((".zip", ".exe")):
            assets.append({
                "name": name,
                "url": a.get("browser_download_url") or "",
            })
    return {
        "tag_name": data.get("tag_name") or "",
        "html_url": data.get("html_url") or "",
        "body": data.get("body") or "",
        "published_at": data.get("published_at") or "",
        "assets": assets,
    }


def _parse_tag(tag: str) -> str:
    """Strip a leading 'v' so 'v1.0.1' -> '1.0.1' for comparison."""
    return tag[1:] if tag.startswith("v") else tag


def _is_newer(latest: str, current: str) -> bool:
    """Naive dotted-integer version comparison."""
    try:
        lv = tuple(int(x) for x in latest.split("."))
        cv = tuple(int(x) for x in current.split("."))
        return lv > cv
    except ValueError:
        return latest != current


@router.get("/update/check")
def update_check():
    """Check whether a newer release is available on GitHub.

    Open (no auth) read-only check. Returns current version, latest release
    info, and whether an update is available. Never raises on network failure.
    """
    current = _read_version_file() or "0.1.0"
    latest_rel = _fetch_latest_release()
    latest = _parse_tag(latest_rel["tag_name"]) if latest_rel else None
    available = bool(latest_rel) and latest and _is_newer(latest, current)
    return {
        "current_version": current,
        "latest_version": latest or current,
        "update_available": available,
        "release_url": latest_rel["html_url"] if latest_rel else "",
        "release_notes": latest_rel["body"] if latest_rel else "",
        "asset_url": latest_rel["assets"][0]["url"] if latest_rel and latest_rel["assets"] else "",
    }


@router.post("/update/download")
def update_download():
    """Download the latest release zip and stage it for silent install.

    Stages to update-staged/extracted/ next to the exe. main_window.py applies
    the swap at next startup. Returns the staged version.
    """
    latest_rel = _fetch_latest_release()
    if not latest_rel or not latest_rel["asset_url"]:
        raise HTTPException(status_code=502, detail="No release asset available")

    tag = _parse_tag(latest_rel["tag_name"])
    asset_url = latest_rel["asset_url"]
    staging = _staging_dir()
    extracted = staging / "extracted"

    # Clean any previous staging
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    asset_path = staging / "update.bin"
    req = urllib.request.Request(
        asset_url,
        headers={"Accept": "application/octet-stream", "User-Agent": "smi-updater"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            with asset_path.open("wb") as fh:
                shutil.copyfileobj(resp, fh)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Download failed: {exc}")

    # Unpack the asset: zip extracts into extracted/; exe is copied directly
    # (alongside the current _internal so startup swap finds both).
    is_zip = asset_path.suffix.lower() == ".zip"
    try:
        if is_zip:
            with zipfile.ZipFile(asset_path) as zf:
                zf.extractall(extracted)
        else:
            extracted.mkdir(parents=True, exist_ok=True)
            shutil.copy2(asset_path, extracted / asset_path.name)
            # Carry the current _internal tree so _apply_staged_update finds both
            current_internal = Path(sys.executable).parent / "_internal"
            if current_internal.is_dir():
                shutil.copytree(current_internal, extracted / "_internal", dirs_exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(staging, ignore_errors=True)
        raise HTTPException(status_code=502, detail=f"Extract failed: {exc}")

    asset_path.unlink(missing_ok=True)

    return {
        "downloaded": True,
        "version": tag,
        "staging": str(staging),
    }
