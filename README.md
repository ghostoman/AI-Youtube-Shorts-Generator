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
| Script | Writes a spoken script from your channel brief | your AI provider |
| Footage | Searches and downloads matching vertical stock clips | Pexels |
| Voice | Records the voiceover in the voice you picked | ElevenLabs |
| Captions | Times the words to the audio and writes the subtitles | local |
| Render | Cuts, crops to 9:16, burns in captions, mixes audio | FFmpeg |
| Metadata | Writes the title, description and tags | your AI provider |

Output is a finished 1080x1920 MP4 plus a JSON file with its metadata, in
`output/videos/`. Nothing is uploaded unless you switch uploading on.

## What you need

Two or three free accounts, depending on which AI provider you pick. The panel
has a **Test** button for each one, so you find problems during setup rather
than at three in the morning.

**An AI provider** to write the scripts. Pick one on the Connections screen:

- **DeepSeek** — connect directly. Cheapest option; add a few dollars of credit
  at platform.deepseek.com to start.
- **OpenRouter** — one key, hundreds of models. Create a key at
  openrouter.ai/keys and put the model id from openrouter.ai/models in the model
  field, in `provider/model` form. Handy if you already have an OpenRouter
  account or want to try a different model without changing anything else.
- **Custom** — any OpenAI-compatible API. Paste the base URL, key and model
  name. Works with other gateways, a company proxy, or a model running on your
  own machine.

Only text goes through this provider. Footage, voice and rendering are handled
by the services below.

**Pexels** — supplies the footage. Free, no card.

**ElevenLabs** — records the voice. Free tier is enough to try this properly.

Full walkthrough with where each key lives: **[docs/SETUP.md](docs/SETUP.md)**

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

## Help and community

Stuck, or found a bug? The Telegram group is the fastest place to get an answer,
and it is where updates get posted:

**ТЕЛЕГРАМ_ССЫЛКА**

Bug reports are welcome as GitHub issues too.

If you would rather skip the setup entirely, I configure this for people as a
paid service — keys connected, brief written with you, schedule running. Message
me at **[@ghostoman](https://t.me/ghostoman)**.

## Support the author

This tool is free and always will be. If it saves you time, a tip keeps it
maintained.

**ERC20 (USDT / USDC / ETH):**

```
0xbce8dd116ecdb5895cd9bf837c7e95d2350c31ba
```

A star on the repo helps too — it is how other people find it.

## License

MIT. Fork it, change it, sell it.
