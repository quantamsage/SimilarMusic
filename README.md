# SimilarMusic

A web app for discovering music: paste a link to a song you like and get similar songs back.

> **Status:** Phase 2 (music link processing) is done. Music discovery, AI ranking and the results page come in later phases.

## Requirements

- Python 3.10+

## Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env   # then edit values as needed
```

## API credentials

Song lookups need API credentials in `.env`. See `.env.example` for each variable.

| Service | Used for | How to get credentials |
| --- | --- | --- |
| Spotify Web API | Track title, artists, album, artwork, release date | Create an app at the [Spotify developer dashboard](https://developer.spotify.com/dashboard) and copy its Client ID and Client Secret. Development Mode apps need the owner to have Spotify Premium. |
| YouTube Data API v3 | Video title, channel, thumbnail, duration, tags | Enable the API in [Google Cloud Console](https://console.cloud.google.com/apis/library/youtube.googleapis.com) and create an API key. |

Without credentials for a platform, links from that platform return a clear "not configured" error. Everything else keeps working.

Looked-up songs are cached in SQLite, so repeat lookups don't use API quota.

## Run

```bash
python run.py
```

Open http://127.0.0.1:8000.

## Supported links

- Spotify: `https://open.spotify.com/track/<id>` (including `?si=` share parameters, `intl-xx/` paths and `embed/` links) and `spotify:track:<id>` URIs. Shortened `spotify.link` links aren't supported because resolving them would require fetching a user-supplied URL.
- YouTube: `youtube.com/watch?v=<id>`, `youtu.be/<id>`, `music.youtube.com`, `m.youtube.com`, `/shorts/`, `/embed/` and `/live/` links.

YouTube has no structured song data, so the artist and title are taken from the video title (`Artist - Title`) or from auto-generated `Artist - Topic` channels. The raw title and channel name are returned too.

## API

`POST /api/songs/lookup` with `{"url": "<song link>"}` returns the song's metadata. Errors come back as `{"error": "<code>", "message": "<explanation>"}`:

| Status | `error` | When |
| --- | --- | --- |
| 400 | `invalid_url` | The link is malformed or has an invalid ID |
| 400 | `unsupported_platform` | Not a Spotify/YouTube song link (e.g. an album or playlist) |
| 404 | `song_not_found` | The platform has no such song |
| 502 | `provider_unavailable` | The platform API failed or timed out |
| 503 | `provider_not_configured` | Credentials for that platform are missing or rejected |
| 503 | `rate_limited` | The platform's rate limit or quota was hit |

## Test

```bash
pytest
```
