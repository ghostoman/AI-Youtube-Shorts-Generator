"""
YouTube connection: sign in once, then upload on a schedule.

The Google libraries are optional. If they are not installed the app still
runs fine and simply keeps every video local, which is what most people want
on day one anyway.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]


def libraries_installed() -> bool:
    try:
        import googleapiclient  # noqa: F401
        import google_auth_oauthlib  # noqa: F401
        return True
    except ImportError:
        return False


def is_connected(token_file: Path) -> bool:
    return token_file.exists()


def _service(token_file: Path):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


def connect(secrets_file: Path, token_file: Path) -> tuple[bool, str]:
    """Opens the browser for Google sign-in. Blocks until the user finishes."""
    if not libraries_installed():
        return False, "Библиотеки Google не установлены. Запустите установщик ещё раз."
    if not secrets_file.exists():
        return False, f"Файл {secrets_file.name} не найден. Положите его рядом с программой."

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_file), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")
        token_file.write_text(creds.to_json(), encoding="utf-8")
    except Exception as e:
        return False, f"Вход не завершён: {e}"

    try:
        yt = _service(token_file)
        me = yt.channels().list(part="snippet", mine=True).execute()
        name = me["items"][0]["snippet"]["title"]
        return True, f"Подключен канал {name}"
    except Exception:
        return True, "Подключено"


def channel_name(token_file: Path) -> str | None:
    try:
        yt = _service(token_file)
        me = yt.channels().list(part="snippet", mine=True).execute()
        return me["items"][0]["snippet"]["title"]
    except Exception:
        return None


def next_slot(token_file: Path, times: list[str], tz_offset_minutes: int = 0) -> datetime:
    """
    Find the next free posting slot in UTC.

    Walks forward from now through the daily times the user chose, skipping any
    slot already taken by a scheduled upload so a batch spreads out instead of
    landing all at once.
    """
    taken: set[str] = set()
    try:
        yt = _service(token_file)
        resp = yt.search().list(part="id", forMine=True, type="video", maxResults=50,
                                order="date").execute()
        ids = [i["id"]["videoId"] for i in resp.get("items", [])]
        if ids:
            details = yt.videos().list(part="status", id=",".join(ids)).execute()
            for v in details.get("items", []):
                pub = v.get("status", {}).get("publishAt")
                if pub:
                    taken.add(pub[:16])
    except Exception:
        pass

    slots = sorted(times) or ["09:00"]
    local_now = datetime.now(timezone.utc) + timedelta(minutes=tz_offset_minutes)
    for day in range(0, 60):
        base = (local_now + timedelta(days=day)).date()
        for t in slots:
            hh, _, mm = t.partition(":")
            local_dt = datetime(base.year, base.month, base.day, int(hh), int(mm or 0),
                                tzinfo=timezone.utc)
            utc_dt = local_dt - timedelta(minutes=tz_offset_minutes)
            if utc_dt <= datetime.now(timezone.utc) + timedelta(minutes=10):
                continue
            if utc_dt.isoformat()[:16] in taken:
                continue
            return utc_dt
    return datetime.now(timezone.utc) + timedelta(hours=1)


def upload(video_path: Path, title: str, description: str, tags: list[str],
           token_file: Path, privacy: str = "private",
           publish_at: datetime | None = None, log=print) -> tuple[bool, str]:
    if not libraries_installed():
        return False, "Библиотеки Google не установлены."
    if not token_file.exists():
        return False, "YouTube ещё не подключён."

    try:
        from googleapiclient.http import MediaFileUpload
        yt = _service(token_file)

        status: dict = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
        if publish_at is not None:
            # A scheduled video must be uploaded private first; YouTube flips it
            # public itself at publishAt.
            status["privacyStatus"] = "private"
            status["publishAt"] = publish_at.isoformat().replace("+00:00", "Z")

        body = {
            "snippet": {"title": title[:100], "description": description[:4900],
                        "tags": tags[:15], "categoryId": "22"},
            "status": status,
        }
        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True,
                                mimetype="video/mp4")
        request = yt.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            _chunk, response = request.next_chunk()

        vid = response["id"]
        when = f", публикация {publish_at:%d.%m %H:%M} UTC" if publish_at else ""
        log(f"Выложено: https://youtu.be/{vid}{when}")
        return True, vid
    except Exception as e:
        return False, str(e)
