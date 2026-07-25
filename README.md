# AI YouTube Shorts Generator

A video factory that runs on your own computer.

You describe your channel once. It writes the script, finds the footage, records
the voiceover, burns in captions, renders a vertical video, and — if you let it —
schedules the upload to YouTube. Then it does it again, on a timer, for as long
as you leave it open.

No coding, and no terminal. You set everything up in a browser panel and press
Generate.

---

## Install

**Windows:** download this repo, unzip it, double-click **START_WINDOWS.bat**.

That is the whole install. On the first run it checks whether you already have
Python and FFmpeg, downloads whichever is missing into a local `runtime` folder,
installs the packages, and opens the panel at `http://127.0.0.1:8730`. Roughly
100 MB and a few minutes. After that it just opens.

Nothing is installed system-wide and nothing touches your PATH. Delete the
`runtime` folder to undo all of it.

**macOS / Linux:** `bash start.sh` — needs Python 3.10+ and FFmpeg installed
already (`brew install python ffmpeg` or `apt install python3 ffmpeg`).

## What it does

Every video moves through six stages, and you watch them light up as it works:

| Stage | What happens | Service |
|---|---|---|
| Script | Writes a spoken script from your channel brief | DeepSeek |
| Footage | Searches and downloads matching vertical stock clips | Pexels |
| Voice | Records the voiceover in the voice you picked | ElevenLabs |
| Captions | Times the words to the audio and writes the subtitles | local |
| Render | Cuts, crops to 9:16, burns in captions, mixes audio | FFmpeg |
| Metadata | Writes the title, description and tags | DeepSeek |

Output is a finished 1080x1920 MP4 plus a JSON file with its metadata, in
`output/videos/`. Nothing is uploaded unless you switch uploading on.

## What you need

Three free accounts. The panel has a **Test** button for each one, so you find
problems during setup rather than at three in the morning.

- **DeepSeek** — writes the scripts. Costs a small amount per video; add a few
  dollars of credit to start.
- **Pexels** — supplies the footage. Free, no card.
- **ElevenLabs** — records the voice. Free tier is enough to try this properly.

Full walkthrough with screenshots of where each key lives:
**[docs/SETUP.md](docs/SETUP.md)**

## The one thing that matters

The channel brief. Everything the machine makes comes out of that text box, and
a vague brief produces vague videos that nobody watches.

Write several paragraphs: the subject, who it is for, the angle, the tone, and a
plain list of what you never want to see. Treat it like briefing a person who
will make three hundred videos for you and will never once ask a follow-up
question. There is a worked example in the setup guide.

Make **one** video first and watch it all the way through. If the first one is
good, the hundredth will be too. If it is not, fix the brief, not the settings.

## Optional

**Word-level captions.** Install `faster-whisper` and the captions get timed to
the actual audio instead of spaced evenly:

```
pip install faster-whisper
```

**YouTube uploading.** Needs a Google Cloud project and a `client_secret.json`
placed next to `app.py`. The setup guide covers it. Uploads always go up private
with a scheduled publish time, so nothing appears on your channel without you
seeing it first.

## Your keys

They live in `config.json` on your machine and go nowhere else. That file is
gitignored — if you fork this, keep it that way.

## Honest limits

- Stock footage is stock footage. It illustrates, it does not tell your story.
- Volume is not a strategy. A hundred mediocre videos stay mediocre.
- YouTube's policy on mass-produced content is real. Read it, and treat this as
  a drafting tool with a human in the loop, not a fire-and-forget spam machine.
- The app must stay open for scheduled runs to fire.

## Need help, or want it set up for you

If you get stuck, message me on Telegram: **[@ghostoman](https://t.me/ghostoman)**

I also set this up for people as a paid service — the whole thing configured
around your channel, keys connected, brief written with you, schedule running.
If you would rather skip the setup entirely, that is the fastest route.

## Support the author

This tool is free and always will be. If it saves you time, a tip keeps it
maintained.

**ERC20 (USDT / USDC / ETH):**

```
0xbce8dd116ecdb5895cd9bf837c7e95d2350c31ba
```

Stars on the repo help too.

## License

MIT. Fork it, change it, sell it.
