"""
Core generation pipeline.

One call to `generate()` turns a topic into a finished vertical video:
script -> footage -> voiceover -> captions -> render -> metadata.

Every step reports progress through the `on_stage` / `on_log` callbacks so the
UI can show what the machine is doing. Nothing here talks to the network
except through the four APIs the user configured.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# Some antivirus products re-sign HTTPS traffic, which breaks Python's bundled
# CA store. certifi fixes it when present; without it we fall back to default.
try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CTX = ssl.create_default_context()

TARGET_W, TARGET_H = 1080, 1920
CLIP_MIN_SEC, CLIP_MAX_SEC = 2.0, 4.0

# Pexels and several other APIs sit behind Cloudflare, which answers 403 to the
# default "Python-urllib/3.x" agent. Every outbound request must identify itself.
USER_AGENT = "AI-Youtube-Shorts-Generator/1.0 (+https://github.com/ghostoman/AI-Youtube-Shorts-Generator)"

STAGES = ["script", "footage", "voice", "captions", "render", "metadata"]


class PipelineError(Exception):
    pass


# ──────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────

def _post_json(url: str, payload: dict, headers: dict, timeout: int = 90) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"User-Agent": USER_AGENT, **headers},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_json(url: str, headers: dict | None = None, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        return json.loads(r.read().decode("utf-8"))


def _download(url: str, dest: Path, timeout: int = 120) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def media_duration(path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def _slug(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (s[:limit] or "video").rstrip("-")


def _strip_fences(text: str) -> str:
    return re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()


# ──────────────────────────────────────────────────────────────
# 1. script — any OpenAI-compatible provider
# ──────────────────────────────────────────────────────────────

# Every provider below speaks the same /chat/completions dialect, so the only
# things that differ are the URL, the key, the model id and one vendor-specific
# switch for turning reasoning off.
PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "url": "https://api.deepseek.com/chat/completions",
        "key_field": "deepseek_api_key",
        "model_field": "deepseek_model",
        "default_model": "deepseek-v4-pro",
    },
    "openrouter": {
        "label": "OpenRouter",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "key_field": "openrouter_api_key",
        "model_field": "openrouter_model",
        "default_model": "deepseek/deepseek-v4-pro",
    },
    "custom": {
        "label": "Свой адрес",
        "url": None,                      # taken from custom_base_url
        "key_field": "custom_api_key",
        "model_field": "custom_model",
        "default_model": "",
    },
}


def _completions_url(base: str) -> str:
    """Turn whatever the user pasted into a /chat/completions URL."""
    base = (base or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def resolve_provider(cfg: dict) -> dict:
    """Returns {name, label, url, key, model} or raises PipelineError."""
    name = (cfg.get("llm_provider") or "deepseek").strip().lower()
    spec = PROVIDERS.get(name)
    if not spec:
        raise PipelineError(f"Неизвестный провайдер «{name}». Выберите его на экране «Подключения».")

    key = (cfg.get(spec["key_field"]) or "").strip()
    if not key:
        raise PipelineError(f"Ключ {spec['label']} не задан. Добавьте его на экране «Подключения».")

    model = (cfg.get(spec["model_field"]) or spec["default_model"]).strip()
    if not model:
        raise PipelineError("Не задано имя модели. Укажите его на экране «Подключения».")

    url = spec["url"] or _completions_url(cfg.get("custom_base_url"))
    if not url:
        raise PipelineError("Не задан адрес API. Укажите его на экране «Подключения».")
    if not url.startswith(("http://", "https://")):
        raise PipelineError("Адрес API должен начинаться с https://")

    return {"name": name, "label": spec["label"], "url": url, "key": key, "model": model}


def _no_reasoning(provider_name: str) -> dict:
    """
    Reasoning modes ignore `temperature`, and script variety lives on
    temperature — without this the whole channel goes monotone.
    Each gateway spells the switch differently.
    """
    if provider_name == "deepseek":
        return {"thinking": {"type": "disabled"}}
    if provider_name == "openrouter":
        return {"reasoning": {"enabled": False}}
    return {}          # unknown gateway: send nothing it might choke on


def _chat(cfg: dict, prompt: str, max_tokens: int, temperature: float) -> str:
    p = resolve_provider(cfg)

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {p['key']}"}
    if p["name"] == "openrouter":
        # Optional attribution headers; they also make the app visible on
        # OpenRouter's public app rankings.
        headers["HTTP-Referer"] = "https://github.com/ghostoman/AI-Youtube-Shorts-Generator"
        headers["X-Title"] = "AI YouTube Shorts Generator"

    base_payload = {
        "model": p["model"],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    def send(extra: dict) -> dict:
        return _post_json(p["url"], {**base_payload, **extra}, headers)

    switch = _no_reasoning(p["name"])
    try:
        data = send(switch)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")[:400]

        # Some models refuse to have reasoning turned off and answer 400.
        # Losing temperature control is far better than failing outright.
        if e.code == 400 and switch and re.search(r"reason|think", detail, re.I):
            try:
                data = send({})
            except urllib.error.HTTPError as e2:
                raise PipelineError(_llm_http_error(p, e2.code,
                                                   e2.read().decode("utf-8", errors="ignore")[:400]))
        else:
            raise PipelineError(_llm_http_error(p, e.code, detail))
    except urllib.error.URLError as e:
        raise PipelineError(f"Не удалось соединиться с {p['label']}: {e.reason}")

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise PipelineError(f"{p['label']} вернул ответ неожиданного вида: {str(data)[:200]}")
    if not content or not content.strip():
        raise PipelineError(f"{p['label']} вернул пустой ответ. Попробуйте другую модель.")
    return content.strip()


def _llm_http_error(p: dict, code: int, detail: str) -> str:
    label = p["label"]
    if code == 401:
        return f"{label} отклонил ключ. Проверьте его на экране «Подключения»."
    if code == 402:
        return f"На счёте {label} закончились средства. Пополните баланс."
    if code == 403:
        return f"{label} отклонил запрос. Возможно, у ключа нет доступа к этой модели."
    if code == 404:
        return (f"{label} не знает модель «{p['model']}». "
                f"Проверьте имя модели в списке провайдера.")
    if code == 429:
        return f"{label}: слишком много запросов. Подождите и попробуйте снова."
    return f"{label} вернул ошибку {code}: {detail}"


def write_script(cfg: dict, topic: str, log) -> dict:
    """Returns {'script': str, 'hook': str, 'keywords': [str]}."""
    log("Пишу сценарий")
    seconds = int(cfg.get("target_seconds") or 50)
    words = int(seconds * 2.4)
    language = cfg.get("language_name") or "English"

    prompt = f"""You write voiceover scripts for vertical short videos (YouTube Shorts, Reels, TikTok).

CHANNEL TOPIC (the creator's own description):
{topic}

Write ONE script for a single short video on this channel.

Requirements:
- Language: {language}
- Length: {words - 15} to {words + 15} words, so it reads in about {seconds} seconds at a natural pace
- Open with a hook in the first sentence that makes scrolling feel like a mistake
- Give real, specific substance: a concrete number, example, contrast or step. No vague motivation.
- Spoken rhythm: short sentences, contractions, one idea per line
- Close with a single clear takeaway or question
- Plain spoken text only. No headings, no stage directions, no emoji, no markdown, no speaker labels.

Also pick 6 stock-footage search phrases in ENGLISH (2-4 words each) that visually match the
EMOTION and subject of this script. They will be used to search a stock video library, so they
must describe filmable scenes (people, places, actions), never abstract concepts.

Return ONLY valid JSON, no code fences:
{{"hook": "the opening sentence", "script": "the full voiceover text", "keywords": ["...", "...", "...", "...", "...", "..."]}}"""

    raw = _strip_fences(_chat(cfg, prompt, max_tokens=1200, temperature=0.9))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Model ignored the format. Salvage the text rather than failing the run.
        data = {"hook": "", "script": raw, "keywords": []}

    script = (data.get("script") or "").strip()
    if len(script.split()) < 25:
        raise PipelineError("Модель вернула слишком короткий сценарий. Попробуйте ещё раз.")

    keywords = [k for k in (data.get("keywords") or []) if isinstance(k, str) and k.strip()]
    if not keywords:
        keywords = ["person working laptop", "city street people", "hands typing keyboard",
                    "person thinking window", "team meeting office", "sunrise time lapse"]

    log(f"Сценарий готов: {len(script.split())} слов")
    return {"script": script, "hook": (data.get("hook") or "").strip(), "keywords": keywords[:6]}


# ──────────────────────────────────────────────────────────────
# 2. footage (Pexels, optional Pixabay)
# ──────────────────────────────────────────────────────────────

def _search_pexels(key: str, query: str, per_page: int, log) -> list[dict]:
    if not key:
        return []
    try:
        url = ("https://api.pexels.com/videos/search"
               f"?query={urllib.parse.quote(query)}&orientation=portrait"
               f"&size=medium&per_page={per_page}&page={random.randint(1, 5)}")
        data = _get_json(url, {"Authorization": key})
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise PipelineError("Pexels отклонил ключ. Проверьте его на экране «Подключения».")
        if e.code == 429:
            log("Pexels: исчерпан часовой лимит запросов")
            return []
        log(f"Ошибка Pexels по запросу «{query}»: HTTP {e.code}")
        return []
    except Exception as e:
        log(f"Ошибка Pexels по запросу «{query}»: {e}")
        return []

    out = []
    for v in data.get("videos", []):
        files = v.get("video_files", [])
        portrait = [f for f in files if f.get("height", 0) > f.get("width", 0) and f.get("height", 0) >= 720]
        best = portrait or sorted(files, key=lambda f: f.get("height", 0), reverse=True)
        if best:
            out.append({"url": best[0]["link"], "source": "pexels"})
    return out


def _search_pixabay(key: str, query: str, per_page: int, log) -> list[dict]:
    if not key:
        return []
    try:
        url = (f"https://pixabay.com/api/videos/?key={key}&q={urllib.parse.quote(query)}"
               f"&video_type=film&per_page={per_page}&page={random.randint(1, 5)}&safesearch=true")
        data = _get_json(url)
    except Exception as e:
        log(f"Ошибка Pixabay по запросу «{query}»: {e}")
        return []

    out = []
    for v in data.get("hits", []):
        for quality in ("large", "medium", "small"):
            vf = v.get("videos", {}).get(quality, {})
            if vf.get("url"):
                out.append({"url": vf["url"], "source": "pixabay"})
                break
    return out


def fetch_footage(cfg: dict, keywords: list[str], video_dir: Path, want: int, log) -> list[dict]:
    log(f"Ищу футаж по {len(keywords)} запросам")
    pexels_key = (cfg.get("pexels_api_key") or "").strip()
    pixabay_key = (cfg.get("pixabay_api_key") or "").strip()
    if not pexels_key and not pixabay_key:
        raise PipelineError("Нет источника футажа. Добавьте ключ Pexels на экране «Подключения».")

    pool: list[dict] = []
    for kw in keywords:
        a = _search_pexels(pexels_key, kw, 4, log)
        b = _search_pixabay(pixabay_key, kw, 4, log)
        for i in range(max(len(a), len(b))):        # interleave so one source can't dominate
            if i < len(a):
                pool.append(a[i])
            if i < len(b):
                pool.append(b[i])

    seen, unique = set(), []
    for v in pool:
        h = hashlib.md5(v["url"].encode()).hexdigest()[:10]
        if h not in seen:
            seen.add(h)
            unique.append(v)
    random.shuffle(unique)

    if not unique:
        raise PipelineError("Поиск футажа ничего не нашёл. Сделайте описание канала конкретнее.")

    log(f"Найдено клипов: {len(unique)}, скачиваю {min(want, len(unique))}")
    got = []
    for i, v in enumerate(unique[:want]):
        dest = video_dir / f"clip_{i + 1:02d}_{v['source']}.mp4"
        try:
            _download(v["url"], dest)
            if dest.stat().st_size < 10_000:
                dest.unlink(missing_ok=True)
                continue
            v["local_path"] = str(dest)
            got.append(v)
        except Exception as e:
            log(f"Клип {i + 1} не скачался: {e}")
        time.sleep(0.2)

    if not got:
        raise PipelineError("Ни один клип не скачался. Проверьте интернет.")
    log(f"Скачано клипов: {len(got)}")
    return got


# ──────────────────────────────────────────────────────────────
# 3. voiceover (ElevenLabs)
# ──────────────────────────────────────────────────────────────

def make_voiceover(cfg: dict, script: str, out_path: Path, log) -> Path:
    key = (cfg.get("elevenlabs_api_key") or "").strip()
    voice = (cfg.get("elevenlabs_voice_id") or "").strip()
    if not key or not voice:
        raise PipelineError("Не задан ключ или ID голоса ElevenLabs. Добавьте оба на экране «Подключения».")

    log("Записываю озвучку")
    payload = {
        "text": script,
        "model_id": cfg.get("elevenlabs_model") or "eleven_turbo_v2_5",
        "voice_settings": {
            "stability": 0.50,
            "similarity_boost": 0.75,
            "style": 0.0,
            "speed": float(cfg.get("voice_speed") or 1.06),
            "use_speaker_boost": True,
        },
    }
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "xi-api-key": key, "Accept": "audio/mpeg"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120, context=SSL_CTX) as r:
            audio = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")[:300]
        if e.code == 401:
            raise PipelineError("ElevenLabs отклонил ключ. Проверьте его на экране «Подключения».")
        if e.code == 422:
            raise PipelineError(f"ElevenLabs не принял ID голоса. Скопируйте его заново из библиотеки голосов. ({detail})")
        raise PipelineError(f"ElevenLabs вернул ошибку {e.code}: {detail}")

    out_path.write_bytes(audio)
    log(f"Озвучка готова: {len(audio) // 1024} КБ")
    return out_path


# ──────────────────────────────────────────────────────────────
# 4. captions
# ──────────────────────────────────────────────────────────────

def _srt_time(s: float) -> str:
    h, m = int(s // 3600), int((s % 3600) // 60)
    sec, ms = int(s % 60), int((s % 1) * 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def _write_srt(chunks: list[dict], path: Path) -> Path:
    lines = []
    for i, c in enumerate(chunks, 1):
        lines += [str(i), f"{_srt_time(c['start'])} --> {_srt_time(c['end'])}", c["text"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def make_captions(cfg: dict, audio_path: Path, script: str, out_path: Path, log) -> Path:
    """Word-level timing via faster-whisper when installed, even spacing otherwise."""
    per_line = int(cfg.get("words_per_line") or 3)

    try:
        from faster_whisper import WhisperModel
        log("Распознаю речь для точных субтитров")
        model_size = cfg.get("whisper_model") or "base"
        try:
            model = WhisperModel(model_size, device="cuda", compute_type="float16")
        except Exception:
            model = WhisperModel(model_size, device="cpu", compute_type="int8")

        segments, _info = model.transcribe(
            str(audio_path), word_timestamps=True, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
        )
        words = [{"word": w.word.strip(), "start": w.start, "end": w.end}
                 for seg in segments if seg.words for w in seg.words]

        if words:
            chunks = []
            for i in range(0, len(words), per_line):
                group = words[i:i + per_line]
                if chunks:
                    chunks[-1]["end"] = min(chunks[-1]["end"], group[0]["start"] - 0.03)
                chunks.append({
                    "text": " ".join(w["word"] for w in group),
                    "start": group[0]["start"],
                    "end": group[-1]["end"],
                })
            log(f"Субтитры: {len(chunks)} строк, тайминг по словам")
            return _write_srt(chunks, out_path)
        log("Распознавание не вернуло слов, ставлю равномерные субтитры")
    except ImportError:
        log("faster-whisper не установлен, ставлю равномерные субтитры")
    except Exception as e:
        log(f"Распознавание не сработало ({e}), ставлю равномерные субтитры")

    duration = media_duration(audio_path)
    words = script.split()
    groups = [" ".join(words[i:i + per_line]) for i in range(0, len(words), per_line)]
    per = duration / max(len(groups), 1)
    chunks = [{"text": g, "start": i * per, "end": (i + 1) * per - 0.04}
              for i, g in enumerate(groups)]
    log(f"Субтитры: {len(chunks)} строк, равномерный тайминг")
    return _write_srt(chunks, out_path)


def _srt_to_ass(cfg: dict, srt_path: Path, ass_path: Path) -> Path:
    font = cfg.get("caption_font") or "Arial Black"
    size = int(cfg.get("caption_size") or 90)
    margin_v = int(cfg.get("caption_margin_v") or 420)
    margin_lr = 90

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {TARGET_W}\nPlayResY: {TARGET_H}\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&HA8000000,-1,0,0,0,"
        f"100,100,0,0,1,4,1,2,{margin_lr},{margin_lr},{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    def t(x: str) -> str:
        x = x.replace(",", ".")
        h, m, rest = x.split(":")
        sec, _, ms = rest.partition(".")
        return f"{int(h)}:{m}:{sec}.{(ms or '00')[:2]}"

    events = []
    for block in [b.strip() for b in srt_path.read_text(encoding="utf-8").split("\n\n") if b.strip()]:
        lines = block.split("\n")
        if len(lines) < 3 or " --> " not in lines[1]:
            continue
        start, end = [p.strip() for p in lines[1].split(" --> ")]
        text = "\\N".join(lines[2:])
        events.append(f"Dialogue: 0,{t(start)},{t(end)},Default,,0,0,0,,{text}")

    ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return ass_path


# ──────────────────────────────────────────────────────────────
# 5. render (ffmpeg)
# ──────────────────────────────────────────────────────────────

def render_video(cfg: dict, clips: list[dict], audio_path: Path, srt_path: Path,
                 out_path: Path, log) -> Path:
    if not ffmpeg_available():
        raise PipelineError("FFmpeg не установлен или не найден. Смотрите инструкцию по настройке.")

    audio_dur = media_duration(audio_path)
    if audio_dur <= 0:
        raise PipelineError("Файл озвучки не читается.")
    log(f"Монтирую, целевая длина {audio_dur:.1f} сек")

    valid = []
    for c in clips:
        d = media_duration(c["local_path"])
        if d >= CLIP_MIN_SEC:
            c["real_dur"] = d
            valid.append(c)
    if not valid:
        raise PipelineError("Ни один из скачанных клипов не подходит.")

    # Cut the footage into segments until it covers the voiceover, cycling
    # through the clips and taking a random slice of each so repeats don't show.
    target = audio_dur + 1.0
    segments, total, i, guard = [], 0.0, 0, 0
    while total < target and guard < 400:
        guard += 1
        clip = valid[i % len(valid)]
        i += 1
        ss = round(random.uniform(0, max(0.0, clip["real_dur"] - CLIP_MIN_SEC)), 2)
        take = round(min(CLIP_MAX_SEC, target - total, clip["real_dur"] - ss), 2)
        if take < CLIP_MIN_SEC:
            continue
        segments.append((clip["local_path"], ss, take))
        total += take
    log(f"Нарезано сегментов: {len(segments)}, покрывают {total:.1f} сек")

    ass_path = Path(tempfile.gettempdir()) / f"autoshorts_{out_path.stem}.ass"
    _srt_to_ass(cfg, srt_path, ass_path)
    # ffmpeg's subtitles filter needs forward slashes and an escaped drive colon.
    ass_arg = str(ass_path).replace("\\", "/")
    if len(ass_arg) > 1 and ass_arg[1] == ":":
        ass_arg = ass_arg[0] + "\\:" + ass_arg[2:]

    inputs = []
    for path, ss, take in segments:
        inputs += ["-ss", str(ss), "-t", str(take), "-i", str(path)]
    vo_idx = len(segments)
    inputs += ["-i", str(audio_path)]

    music_idx = None
    music = cfg.get("music_path")
    if music and Path(music).exists():
        inputs += ["-i", str(music)]
        music_idx = vo_idx + 1

    scale = (f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
             f"crop={TARGET_W}:{TARGET_H},setsar=1,fps=30")
    fc = [f"[{i}:v]{scale}[v{i}]" for i in range(len(segments))]
    fc.append("".join(f"[v{i}]" for i in range(len(segments))) + f"concat=n={len(segments)}:v=1:a=0[vcat]")
    fc.append(f"[vcat]subtitles='{ass_arg}'[vout]")

    if music_idx is not None:
        db = cfg.get("music_db", -22)
        fc.append(f"[{music_idx}:a]volume={db}dB,aloop=loop=-1:size=2e+09[bed]")
        fc.append(f"[{vo_idx}:a][bed]amix=inputs=2:duration=first:weights=1 0.25[aout]")
        amap = "[aout]"
    else:
        amap = f"{vo_idx}:a"

    cmd = ["ffmpeg", *inputs,
           "-filter_complex", ";".join(fc),
           "-map", "[vout]", "-map", amap,
           "-c:v", "libx264", "-preset", "fast", "-crf", "22",
           "-c:a", "aac", "-b:a", "192k",
           "-t", str(round(audio_dur + 0.5, 2)),
           "-movflags", "+faststart", "-avoid_negative_ts", "make_zero",
           "-y", str(out_path)]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().split("\n")[-6:])
        raise PipelineError(f"FFmpeg завершился с ошибкой:\n{tail}")

    log(f"Готово: {out_path.name} ({out_path.stat().st_size / 1048576:.1f} МБ)")
    return out_path


def make_thumbnail(video_path: Path, out_path: Path) -> Path | None:
    try:
        subprocess.run(
            ["ffmpeg", "-ss", "1", "-i", str(video_path), "-frames:v", "1",
             "-vf", "scale=360:-1", "-y", str(out_path)],
            capture_output=True, timeout=60,
        )
        return out_path if out_path.exists() else None
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────
# 6. metadata
# ──────────────────────────────────────────────────────────────

def write_metadata(cfg: dict, topic: str, script: str, log) -> dict:
    log("Пишу заголовок, описание и теги")
    language = cfg.get("language_name") or "English"
    prompt = f"""Write YouTube Shorts metadata in {language} for this video.

Channel topic: {topic}

Video voiceover:
{script}

Rules:
- title: under 70 characters, curiosity without lying about the content, no hashtags in the title
- description: 2 or 3 short sentences describing what the viewer gets, then a blank line, then the hashtags
- tags: 8 to 12 short search terms, no # symbol

Return ONLY valid JSON, no code fences:
{{"title": "...", "description": "...", "tags": ["...", "..."]}}"""

    try:
        raw = _strip_fences(_chat(cfg, prompt, max_tokens=700, temperature=0.8))
        data = json.loads(raw)
        title = (data.get("title") or "").strip()[:100]
        desc = (data.get("description") or "").strip()
        tags = [t for t in (data.get("tags") or []) if isinstance(t, str)][:12]
    except Exception as e:
        log(f"Метаданные не сгенерировались ({e}), беру заголовок из сценария")
        first = script.strip().split(".")[0]
        title, desc, tags = first[:90], script[:300], []

    if not title:
        title = script.strip().split(".")[0][:90]
    if "#" not in desc and tags:
        desc = desc.rstrip() + "\n\n" + " ".join("#" + re.sub(r"\W+", "", t) for t in tags[:6])

    return {"title": title, "description": desc, "tags": tags}


# ──────────────────────────────────────────────────────────────
# orchestration
# ──────────────────────────────────────────────────────────────

def generate(cfg: dict, output_root: Path, on_log=None, on_stage=None,
             keep_workdir: bool = False) -> dict:
    """Produce one video. Returns a record dict describing the result."""
    log = on_log or (lambda m: None)
    stage = on_stage or (lambda s, status: None)

    topic = (cfg.get("channel_topic") or "").strip()
    if not topic:
        raise PipelineError("Описание канала пустое. Заполните его на экране «Канал».")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    work = output_root / "work" / stamp
    clips_dir = work / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    (output_root / "videos").mkdir(parents=True, exist_ok=True)
    (output_root / "thumbs").mkdir(parents=True, exist_ok=True)

    try:
        stage("script", "running")
        written = write_script(cfg, topic, log)
        (work / "script.txt").write_text(written["script"], encoding="utf-8")
        stage("script", "done")

        stage("footage", "running")
        clips = fetch_footage(cfg, written["keywords"], clips_dir,
                              int(cfg.get("clips_per_video") or 12), log)
        stage("footage", "done")

        stage("voice", "running")
        audio = make_voiceover(cfg, written["script"], work / "voice.mp3", log)
        stage("voice", "done")

        stage("captions", "running")
        srt = make_captions(cfg, audio, written["script"], work / "captions.srt", log)
        stage("captions", "done")

        stage("render", "running")
        name = f"{stamp}_{_slug(written['hook'] or topic)}"
        video = render_video(cfg, clips, audio, srt, output_root / "videos" / f"{name}.mp4", log)
        thumb = make_thumbnail(video, output_root / "thumbs" / f"{name}.jpg")
        stage("render", "done")

        stage("metadata", "running")
        meta = write_metadata(cfg, topic, written["script"], log)
        stage("metadata", "done")

        record = {
            "id": stamp,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "file": video.name,
            "thumb": thumb.name if thumb else None,
            "duration": round(media_duration(video), 1),
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta["tags"],
            "script": written["script"],
            "keywords": written["keywords"],
            "uploaded": False,
            "youtube_id": None,
        }
        (output_root / "videos" / f"{name}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record

    finally:
        if not keep_workdir:
            shutil.rmtree(work, ignore_errors=True)


# ──────────────────────────────────────────────────────────────
# connection checks used by the Test buttons in the UI
# ──────────────────────────────────────────────────────────────

def check_llm(cfg: dict) -> tuple[bool, str]:
    """Asks the configured provider to say one word. Nothing else proves it works."""
    try:
        p = resolve_provider(cfg)
    except PipelineError as e:
        return False, str(e)

    try:
        answer = _chat(cfg, "Reply with the single word: ready", max_tokens=10, temperature=0.1)
    except PipelineError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)

    note = f"Подключено через {p['label']}, модель {p['model']}"

    # OpenRouter can also tell us what is left on the key. Purely a nicety, so a
    # refusal here must not turn a working connection into a failure.
    if p["name"] == "openrouter":
        try:
            info = _get_json("https://openrouter.ai/api/v1/key",
                             {"Authorization": f"Bearer {p['key']}"}).get("data", {})
            limit, used = info.get("limit"), info.get("usage")
            if limit is None and used is not None:
                note += f". Потрачено ${float(used):.2f}, лимит не задан"
            elif limit is not None and used is not None:
                note += f". Остаток ${max(float(limit) - float(used), 0):.2f}"
        except Exception:
            pass

    return True, note


# Kept so older configs and the /api/check/deepseek route keep working.
check_deepseek = check_llm


def check_pexels(cfg: dict) -> tuple[bool, str]:
    key = (cfg.get("pexels_api_key") or "").strip()
    if not key:
        return False, "Ключ не задан"
    try:
        data = _get_json("https://api.pexels.com/videos/search?query=city&per_page=1",
                         {"Authorization": key})
        found = f"{data.get('total_results', 0):,}".replace(",", "\u00a0")
        return True, f"Подключено, по тестовому запросу найдено {found} клипов"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "Pexels отклонил ключ. Скопируйте его заново с pexels.com/api целиком, без пробелов."
        if e.code == 429:
            return False, "Pexels: исчерпан часовой лимит. Попробуйте позже."
        return False, f"Pexels вернул ошибку {e.code}"
    except Exception as e:
        return False, str(e)


def check_elevenlabs(cfg: dict) -> tuple[bool, str]:
    key = (cfg.get("elevenlabs_api_key") or "").strip()
    if not key:
        return False, "Ключ не задан"
    voice = (cfg.get("elevenlabs_voice_id") or "").strip()
    head = {"xi-api-key": key}

    # Quota lives behind the "User" permission. A key scoped to text-to-speech
    # only still does the job, so a refusal here must not fail the whole check.
    quota = ""
    try:
        data = _get_json("https://api.elevenlabs.io/v1/user/subscription", head)
        left = max(data.get("character_limit", 0) - data.get("character_count", 0), 0)
        pretty = f"{left:,}".replace(",", "\u00a0")
        quota = f", осталось {pretty} символов в этом месяце"
    except urllib.error.HTTPError as e:
        if e.code not in (401, 403):
            return False, f"ElevenLabs вернул ошибку {e.code}"
        quota = ", остаток символов не виден — у ключа нет права User"
    except Exception as e:
        return False, str(e)

    if not voice:
        return True, "Ключ принят" + quota + ". Добавьте ID голоса, чтобы озвучивать."

    # The voice directory needs its own "voices_read" permission, so a refusal
    # here says nothing about whether the key can actually speak.
    name = None
    try:
        name = _get_json(f"https://api.elevenlabs.io/v1/voices/{voice}", head).get("name")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, "Ключ рабочий, но такого ID голоса в вашем аккаунте нет."
        if e.code not in (401, 403):
            return False, f"ElevenLabs вернул ошибку {e.code}"
    except Exception as e:
        return False, str(e)

    if name:
        return True, f"Подключено{quota}. Голос: {name}"

    # Both read endpoints were refused. Settle it the only way that is not a
    # permission question: ask the key to speak two characters.
    try:
        payload = json.dumps({
            "text": "ok",
            "model_id": cfg.get("elevenlabs_model") or "eleven_turbo_v2_5",
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
            data=payload,
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json",
                     "xi-api-key": key, "Accept": "audio/mpeg"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45, context=SSL_CTX) as r:
            got = len(r.read())
        if got < 500:
            return False, "ElevenLabs вернул пустое аудио. Проверьте ID голоса."
        return True, "Подключено, озвучка работает. Ключ урезан в правах, поэтому имя голоса и остаток символов не видны."
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")[:200]
        if e.code in (401, 403):
            return False, ("ElevenLabs отклонил ключ. Выпустите новый на elevenlabs.io и не урезайте ему права при создании.")
        if e.code == 422:
            return False, f"ElevenLabs не принял ID голоса. Скопируйте его заново через Copy voice ID. ({detail})"
        return False, f"ElevenLabs вернул ошибку {e.code}"
    except Exception as e:
        return False, str(e)


def check_ffmpeg() -> tuple[bool, str]:
    if ffmpeg_available():
        try:
            out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10)
            return True, out.stdout.split("\n")[0][:80]
        except Exception:
            return True, "Установлен"
    return False, "Не найден. Смотрите инструкцию по настройке."
