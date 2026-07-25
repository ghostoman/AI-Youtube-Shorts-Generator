# Setup

About twenty minutes, most of it waiting on sign-up emails. Nothing here needs
any coding.

Stuck at any point? Message [@ghostoman](https://t.me/ghostoman) on Telegram.
Paid setup is also available if you would rather have it done for you.

---

## 1. Start the app

Double-click **START_WINDOWS.bat**.

On the first run it checks for Python and FFmpeg and downloads whichever is
missing into a local `runtime` folder. That is around 100 MB, so it takes a few
minutes on a normal connection. Nothing is installed system-wide.

When it finishes, the panel opens in your browser. Leave the blue terminal
window open, since closing it stops the app.

On macOS or Linux, run `bash start.sh` instead. There you do need Python 3.10+
and FFmpeg installed already: `brew install python ffmpeg` on macOS, or
`sudo apt install python3 ffmpeg` on Ubuntu.

---

## 2. DeepSeek — writes the scripts

1. Sign up at [platform.deepseek.com](https://platform.deepseek.com)
2. **API keys** → **Create new API key** → copy it immediately, it is shown once
3. **Top up balance** and add a small amount to start
4. Paste the key into **Connections → DeepSeek** and press **Test**

Two models are available. `deepseek-v4-pro` writes better. `deepseek-v4-flash`
is cheaper and faster. Start with pro, drop to flash if your bill annoys you.

---

## 3. Pexels — supplies the footage

Free, no card.

1. Sign up at [pexels.com/api](https://www.pexels.com/api/)
2. Create a key and copy it
3. Paste into **Connections → Pexels** and press **Test**

Pixabay is optional. Adding a second key from
[pixabay.com/api/docs](https://pixabay.com/api/docs/) widens the pool so
consecutive videos repeat less footage.

---

## 4. ElevenLabs — speaks the script

1. Sign up at [elevenlabs.io](https://elevenlabs.io)
2. Profile menu → **API keys** → create one → copy it
3. Go to **Voices**, find a voice you like, open it, and copy the **Voice ID**
   (a string like `21m00Tcm4TlvDq8ikWAM`, not the name)
4. Paste both into **Connections → ElevenLabs** and press **Test**

The test tells you how many characters you have left this month. A 50-second
script is roughly 600 characters, so you can work out your ceiling.

---

## 5. Write the channel brief

**Channel** screen. This is the part that decides whether any of this is worth
running, so give it real thought.

A weak brief:

> videos about cooking

A brief that produces watchable videos:

> This channel is for people who can already cook a few things and want to get
> genuinely better, not beginners looking for recipes. Every video takes one
> technique and explains why it works — why resting meat matters, why you salt
> pasta water, why a cold pan is sometimes right.
>
> Tone: confident, plain, a bit blunt. Like a line cook explaining something
> during a slow shift. No gushing, no "amazing", no exclamation marks.
>
> Always: one technique per video, the reason behind it, a specific temperature
> or timing the viewer can copy.
> Never: recipe listicles, "you've been doing X wrong" framing, health claims,
> anything about weight loss.

Set the language and the length, then **Save channel**.

---

## 6. Make one video

**Studio** → **Test with one**.

Watch the six lamps move across the transport bar. It takes roughly two to four
minutes, mostly downloading footage. When it finishes, open **Library** and
watch the video all the way through.

If it is not good, the brief is nearly always why. Adjust it and run one more.
Do not queue fifty videos until one is right.

---

## 7. Connect YouTube — optional

Skip this if you would rather download videos and upload them yourself. It is
the fiddliest part of the setup, and it is Google Cloud Console — not Search
Console, which is a different product entirely.

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project (any name)
3. **APIs & Services → Library** → search **YouTube Data API v3** → **Enable**
4. **APIs & Services → OAuth consent screen** → External → fill in the app name
   and your email → under **Test users**, add your own Google account
5. **APIs & Services → Credentials** → **Create credentials** → **OAuth client
   ID** → application type **Desktop app**
6. Download the JSON, rename it to `client_secret.json`, and put it in the same
   folder as `app.py`
7. In the panel: **Schedule → Connect YouTube**, then finish the Google sign-in
   in the window that opens

While your consent screen is in testing mode, only the accounts you listed as
test users can sign in. That is fine — you are the only user.

The API gives you a limited number of uploads per day. It resets on a daily
quota, so batches of a few videos are fine and batches of fifty are not.

---

## 8. Let it run

**Schedule** screen: turn on automatic runs, add the times of day, set how many
videos per run.

Uploads always go up **private with a scheduled publish time**, so a video never
appears on your channel without you having had the chance to look at it. Change
that on the same screen once you trust the output.

The app has to stay open for scheduled runs to fire.

---

## When something breaks

**"ffmpeg missing" in the corner** — the automatic download did not finish.
Delete the `runtime` folder and run START_WINDOWS.bat again; it will retry.

**DeepSeek says the key was rejected** — you copied the key ID from the list
rather than the key itself. Keys start with `sk-`.

**ElevenLabs says the voice was not found** — you pasted the voice name instead
of the voice ID. Open the voice in your library and copy the ID string.

**Stock search came back empty** — your brief is too abstract for a stock
library to match. Add concrete, filmable subjects to it.

**Captions are evenly spaced and drift out of sync** — that is the fallback
timing. Install `faster-whisper` for word-accurate captions:
`pip install faster-whisper`

**Videos all feel the same** — that is the brief again. Add more range to it:
several angles, several formats, things you want covered.

**Something else entirely** — message [@ghostoman](https://t.me/ghostoman) on
Telegram. Setup help is a paid service, and it is usually faster than fighting
with it alone.

---

If this saved you time, a tip is appreciated.
ERC20 (USDT / USDC / ETH): `0xbce8dd116ecdb5895cd9bf837c7e95d2350c31ba`
