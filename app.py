"""
AutoShorts — local control panel.

Starts a small web server on your own machine and opens the panel in your
browser. Nothing is sent anywhere except to the APIs you configure, and your
keys never leave config.json on this computer.
"""

from __future__ import annotations

import json
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

import pipeline
import youtube as yt

ROOT = Path(__file__).parent.resolve()
CONFIG_FILE = ROOT / "config.json"
OUTPUT = ROOT / "output"
VIDEOS = OUTPUT / "videos"
THUMBS = OUTPUT / "thumbs"
TOKEN_FILE = ROOT / "youtube_token.json"
SECRETS_FILE = ROOT / "client_secret.json"

DEFAULTS = {
    "channel_topic": "",
    "language": "en",
    "language_name": "English",
    "deepseek_api_key": "",
    "deepseek_model": "deepseek-v4-pro",
    "pexels_api_key": "",
    "pixabay_api_key": "",
    "elevenlabs_api_key": "",
    "elevenlabs_voice_id": "",
    "target_seconds": 50,
    "clips_per_video": 12,
    "words_per_line": 3,
    "whisper_model": "base",
    "caption_font": "Arial Black",
    "caption_size": 90,
    "caption_margin_v": 420,
    "videos_per_run": 3,
    "schedule_enabled": False,
    "schedule_times": ["09:00", "18:00"],
    "timezone_offset_minutes": 0,
    "upload_to_youtube": False,
    "privacy_status": "private",
}

app = Flask(__name__, static_folder=None)

# ── shared state ──────────────────────────────────────────────
state_lock = threading.Lock()
state = {
    "running": False,
    "stop_requested": False,
    "queue_total": 0,
    "queue_done": 0,
    "current_stage": None,
    "stages": {s: "idle" for s in pipeline.STAGES},
    "last_error": None,
    "last_run_at": None,
}
log_lines: deque[str] = deque(maxlen=400)


def log(message: str) -> None:
    line = f"{datetime.now():%H:%M:%S}  {message}"
    with state_lock:
        log_lines.append(line)
    print(line, flush=True)


def set_stage(stage: str, status: str) -> None:
    with state_lock:
        state["stages"][stage] = status
        state["current_stage"] = stage if status == "running" else state["current_stage"]


def reset_stages() -> None:
    with state_lock:
        state["stages"] = {s: "idle" for s in pipeline.STAGES}
        state["current_stage"] = None


# ── config ────────────────────────────────────────────────────
def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            log("config.json повреждён, возвращаю настройки по умолчанию")
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ── library ───────────────────────────────────────────────────
def list_videos() -> list[dict]:
    items = []
    for meta_file in sorted(VIDEOS.glob("*.json"), reverse=True):
        try:
            record = json.loads(meta_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if (VIDEOS / record.get("file", "")).exists():
            items.append(record)
    return items


def update_record(video_id: str, changes: dict) -> dict | None:
    for meta_file in VIDEOS.glob("*.json"):
        try:
            record = json.loads(meta_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if record.get("id") == video_id:
            record.update(changes)
            meta_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            return record
    return None


# ── worker ────────────────────────────────────────────────────
def run_batch(count: int) -> None:
    cfg = load_config()
    with state_lock:
        state.update({"running": True, "stop_requested": False,
                      "queue_total": count, "queue_done": 0, "last_error": None})

    log(f"Запускаю прогон: роликов {count}")
    made = []

    for n in range(count):
        with state_lock:
            if state["stop_requested"]:
                log("Остановлено после текущего ролика")
                break
        reset_stages()
        log(f"Ролик {n + 1} из {count}")
        try:
            record = pipeline.generate(cfg, OUTPUT, on_log=log, on_stage=set_stage)
            made.append(record)
            log(f"Готов: {record['title']}")
        except pipeline.PipelineError as e:
            log(f"Остановлено: {e}")
            with state_lock:
                state["last_error"] = str(e)
            break
        except Exception as e:
            log(f"Неожиданная ошибка: {e}")
            with state_lock:
                state["last_error"] = str(e)
            break
        finally:
            with state_lock:
                state["queue_done"] = n + 1

    if cfg.get("upload_to_youtube") and made:
        if not yt.is_connected(TOKEN_FILE):
            log("YouTube не подключён, оставляю ролики на компьютере")
        else:
            for record in made:
                slot = yt.next_slot(TOKEN_FILE, cfg.get("schedule_times") or ["09:00"],
                                    int(cfg.get("timezone_offset_minutes") or 0))
                ok, result = yt.upload(
                    VIDEOS / record["file"], record["title"], record["description"],
                    record["tags"], TOKEN_FILE, cfg.get("privacy_status", "private"),
                    publish_at=slot, log=log)
                if ok:
                    update_record(record["id"], {"uploaded": True, "youtube_id": result,
                                                 "publish_at": slot.isoformat()})
                else:
                    log(f"Не удалось выложить: {result}")

    with state_lock:
        state.update({"running": False, "current_stage": None,
                      "last_run_at": datetime.now().isoformat(timespec="seconds")})
    log("Прогон завершён")


def start_batch(count: int) -> bool:
    with state_lock:
        if state["running"]:
            return False
    threading.Thread(target=run_batch, args=(count,), daemon=True).start()
    return True


# ── scheduler ─────────────────────────────────────────────────
def scheduler_loop() -> None:
    fired: set[str] = set()
    while True:
        time.sleep(20)
        try:
            cfg = load_config()
            if not cfg.get("schedule_enabled"):
                fired.clear()
                continue
            offset = int(cfg.get("timezone_offset_minutes") or 0)
            local = datetime.now(timezone.utc) + timedelta(minutes=offset)
            stamp = f"{local:%Y-%m-%d %H:%M}"
            hhmm = f"{local:%H:%M}"
            if hhmm in (cfg.get("schedule_times") or []) and stamp not in fired:
                fired.add(stamp)
                with state_lock:
                    busy = state["running"]
                if busy:
                    log(f"Прогон по расписанию в {hhmm} пропущен, другой уже идёт")
                else:
                    log(f"Запускаю прогон по расписанию, {hhmm}")
                    start_batch(int(cfg.get("videos_per_run") or 1))
            if len(fired) > 50:
                fired.clear()
        except Exception as e:
            log(f"Сбой планировщика: {e}")


# ── routes ────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(ROOT / "ui", "index.html")


@app.route("/api/state")
def api_state():
    cfg = load_config()
    with state_lock:
        snapshot = {
            "running": state["running"],
            "queue_total": state["queue_total"],
            "queue_done": state["queue_done"],
            "stages": dict(state["stages"]),
            "last_error": state["last_error"],
            "last_run_at": state["last_run_at"],
            "log": list(log_lines)[-120:],
        }
    ffmpeg_ok, ffmpeg_note = pipeline.check_ffmpeg()
    return jsonify({
        "config": cfg,
        "status": snapshot,
        "videos": list_videos(),
        "environment": {
            "ffmpeg": {"ok": ffmpeg_ok, "note": ffmpeg_note},
            "youtube_libs": yt.libraries_installed(),
            "youtube_connected": yt.is_connected(TOKEN_FILE),
            "youtube_secrets": SECRETS_FILE.exists(),
        },
    })


@app.route("/api/config", methods=["POST"])
def api_config():
    cfg = load_config()
    incoming = request.get_json(force=True) or {}
    for key in DEFAULTS:
        if key in incoming:
            cfg[key] = incoming[key]
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/check/<service>", methods=["POST"])
def api_check(service: str):
    cfg = load_config()
    incoming = request.get_json(silent=True) or {}
    cfg.update({k: v for k, v in incoming.items() if k in DEFAULTS})

    checks = {
        "deepseek": lambda: pipeline.check_deepseek(cfg),
        "pexels": lambda: pipeline.check_pexels(cfg),
        "elevenlabs": lambda: pipeline.check_elevenlabs(cfg),
        "ffmpeg": pipeline.check_ffmpeg,
    }
    if service not in checks:
        return jsonify({"ok": False, "message": "Неизвестный сервис"}), 404
    ok, message = checks[service]()
    return jsonify({"ok": ok, "message": message})


@app.route("/api/generate", methods=["POST"])
def api_generate():
    body = request.get_json(silent=True) or {}
    count = max(1, min(int(body.get("count") or 1), 50))
    if not start_batch(count):
        return jsonify({"ok": False, "message": "Прогон уже идёт"}), 409
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    with state_lock:
        state["stop_requested"] = True
    log("Запрошена остановка, сначала доделаю текущий ролик")
    return jsonify({"ok": True})


@app.route("/api/youtube/connect", methods=["POST"])
def api_youtube_connect():
    ok, message = yt.connect(SECRETS_FILE, TOKEN_FILE)
    log(message)
    return jsonify({"ok": ok, "message": message})


@app.route("/api/youtube/disconnect", methods=["POST"])
def api_youtube_disconnect():
    TOKEN_FILE.unlink(missing_ok=True)
    log("YouTube отключён")
    return jsonify({"ok": True})


@app.route("/api/upload/<video_id>", methods=["POST"])
def api_upload(video_id: str):
    cfg = load_config()
    record = next((v for v in list_videos() if v["id"] == video_id), None)
    if not record:
        return jsonify({"ok": False, "message": "Такого ролика больше нет"}), 404
    if not yt.is_connected(TOKEN_FILE):
        return jsonify({"ok": False, "message": "Сначала подключите YouTube"}), 400

    slot = yt.next_slot(TOKEN_FILE, cfg.get("schedule_times") or ["09:00"],
                        int(cfg.get("timezone_offset_minutes") or 0))
    ok, result = yt.upload(VIDEOS / record["file"], record["title"], record["description"],
                           record["tags"], TOKEN_FILE, cfg.get("privacy_status", "private"),
                           publish_at=slot, log=log)
    if ok:
        update_record(video_id, {"uploaded": True, "youtube_id": result,
                                 "publish_at": slot.isoformat()})
        return jsonify({"ok": True, "message": f"Запланировано на {slot:%d.%m %H:%M} UTC"})
    return jsonify({"ok": False, "message": result}), 500


@app.route("/api/videos/<video_id>", methods=["DELETE"])
def api_delete(video_id: str):
    for meta_file in VIDEOS.glob("*.json"):
        try:
            record = json.loads(meta_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if record.get("id") == video_id:
            (VIDEOS / record.get("file", "")).unlink(missing_ok=True)
            if record.get("thumb"):
                (THUMBS / record["thumb"]).unlink(missing_ok=True)
            meta_file.unlink(missing_ok=True)
            return jsonify({"ok": True})
    return jsonify({"ok": False}), 404


@app.route("/media/video/<path:name>")
def media_video(name: str):
    return send_file(VIDEOS / name, conditional=True)


@app.route("/media/thumb/<path:name>")
def media_thumb(name: str):
    return send_file(THUMBS / name, conditional=True)


def main() -> None:
    for folder in (VIDEOS, THUMBS, OUTPUT / "work"):
        folder.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        save_config(dict(DEFAULTS))

    threading.Thread(target=scheduler_loop, daemon=True).start()

    port = 8730
    url = f"http://127.0.0.1:{port}"
    print("\n  Программа запущена")
    print(f"  Если браузер не открылся сам, откройте {url}")
    print("  Закроете это окно — программа остановится.\n")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
