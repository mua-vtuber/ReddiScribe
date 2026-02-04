# ReddiScribe

A PyQt6 desktop application for Korean users to browse Reddit with AI-powered Korean translations and write Reddit-ready English posts. Built with local Ollama LLM (no cloud API, no API keys required).

## Features

- **Bilingual Reddit browsing** — Browse subreddits with auto-translated Korean post titles and body
- **AI translation** — Automatic Korean translation of posts and comments
- **Lazy comment translation** — Top-level comments auto-translated, replies translated on demand
- **2-stage writing pipeline** — Korean → English draft (Stage 1) → Reddit-tone polished English (Stage 2)
- **Refine Chat** — Stage 3 chat panel to iteratively refine translations through AI conversation
- **Dual write mode** — Write new posts, comments, or replies with context-aware translation
- **Browser integration** — Submit button opens Reddit with pre-filled content or clipboard copy
- **Fully offline** — Runs entirely locally with Ollama, no internet dependency after Reddit fetch
- **Bilingual UI** — Switch between Korean (ko_KR) and English (en_US) interfaces

## Requirements

- Python 3.11 or higher
- [Ollama](https://ollama.ai) installed and running locally
- At least one Ollama model pulled (see Model Setup below)

## Quick Start

### Installation

```bash
# Clone repository
git clone https://github.com/mua-vtuber/ReddiScribe.git
cd ReddiScribe

# Install package
pip install -e .
```

### Model Setup

ReddiScribe uses two model roles, configured in the Settings tab:

| Role | Purpose | Recommendation |
|------|---------|----------------|
| **Logic (1차)** | Title/comment translation, English draft generation | Lightweight model recommended (e.g., `gemma2:9b`, `llama3.1:8b`) |
| **Persona (2차)** | Reddit tone polishing, refine chat | Higher quality model recommended (e.g., `llama3.1:70b`, `qwen2.5:32b`) |

```bash
# Example: pull models
ollama pull gemma2:9b
ollama pull llama3.1:70b
```

> **Note:** Higher quality models produce better results but are slower. Adjust based on your hardware.
>
> **Tip:** When first setting up, verify translation quality by having an English-proficient person, AI assistant, or at minimum a machine translator review the Stage 2 output. Check that the final polished text matches your intended tone and meaning before posting to Reddit.

### Run Application

**Windows:**
Double-click `run.bat`

**Linux / macOS:**
```bash
python -m src.main
```

## Configuration

Settings are stored in `config/settings.yaml` and auto-created on first run. Most settings are configurable via the **Settings tab** in the app.

### Key Settings

| Setting | Description | Default |
|---------|-------------|---------|
| Locale | UI language (Korean / English) | ko_KR |
| Logic model | Model for translation and drafting | (set in Settings) |
| Persona model | Model for tone polishing | (set in Settings) |
| Persona prompt | Custom prompt for Stage 2 polishing | (editable in Settings) |
| Ollama host | Ollama server address | http://localhost:11434 |
| Request interval | Minimum seconds between Reddit requests | 6 |
| Subreddit list | Managed via top bar (+) or Settings tab | (default list) |

## Project Structure

```
ReddiScribe/
├── src/
│   ├── core/                # Config, database, i18n, logging, types, exceptions
│   ├── adapters/            # External API adapters
│   │   ├── public_json_adapter.py      # Reddit public JSON endpoints
│   │   └── ollama_adapter.py           # Ollama REST API wrapper
│   ├── services/            # Business logic layer
│   │   ├── reader_service.py           # Post fetching, translation
│   │   └── writer_service.py           # 2-stage writing + refine chat
│   ├── gui/                 # PyQt6 UI
│   │   ├── main_window.py
│   │   ├── task_coordinator.py         # Async task coordination
│   │   ├── workers.py                  # QThread workers
│   │   └── widgets/
│   │       ├── reader_widget.py        # Browse & translate
│   │       ├── writer_widget.py        # Write with dual mode
│   │       ├── refine_chat_widget.py   # Stage 3 refine chat
│   │       ├── settings_widget.py      # Configure app
│   │       ├── top_bar_widget.py       # Global subreddit selector
│   │       └── content_view_dialog.py  # Original content viewer
│   ├── resources/
│   │   └── locales/
│   │       ├── ko_KR.json
│   │       └── en_US.json
│   └── main.py              # Application entry point
├── tests/                   # Unit tests (pytest)
├── config/
│   └── settings.yaml        # Generated on first run
├── db/
│   └── history.db           # SQLite history & cache
├── pyproject.toml
└── README.md
```

## Testing

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v
```

## How It Works

### Reader Tab
1. Select a subreddit from the top bar dropdown (or add new ones with "+")
2. Posts load with Korean-translated titles
3. Click a post to view AI-translated Korean body text
4. Toggle between original English and Korean translation
5. Comments auto-translate as you scroll; reply translations available on demand
6. Click "Write Comment" or "Reply" to switch to Writer with context

### Writer Tab
1. **Stage 1 (Draft):** Write in Korean → AI translates to English draft
2. **Stage 2 (Polish):** AI rewrites the draft in natural Reddit tone
3. **Stage 3 (Refine Chat):** Chat with AI to iteratively adjust the translation
4. **Submit:** Opens Reddit in browser with content ready to post/paste

### Behind the Scenes
- Ollama models run locally (no data leaves your machine)
- Reddit data fetched via public JSON endpoints (no API key needed)
- 6-second minimum interval between Reddit requests to respect rate limits
- Translations cached in local SQLite database

## Troubleshooting

**"Connection refused to Ollama"**
- Ensure Ollama is running: `ollama serve`
- Check host in Settings or `config/settings.yaml` (default: `http://localhost:11434`)

**"Model not found"**
- Pull a model: `ollama pull gemma2:9b`
- Set model names in the Settings tab (Logic / Persona)

**"Reddit requests timing out"**
- Increase request interval in Settings
- Check internet connection

**Posts not loading (403 error)**
- Some subreddits block unauthenticated "hot" sort; the app automatically falls back to "new" sort

**Enable debug logging**
- Set log level to DEBUG in Settings or `config/settings.yaml`

## License

See LICENSE file for details.
