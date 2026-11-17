# SimilarMusic

A web app for discovering music: paste a link to a song you like and get similar songs back.

> **Status:** Phase 1 (project foundation) is done. Link processing, music discovery and AI ranking come in later phases.

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

## Run

```bash
python run.py
```

Open http://127.0.0.1:8000.

## Test

```bash
pytest
```
