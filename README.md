# ReddiScribe

A PyQt6 desktop application for Korean users to browse Reddit with AI-powered Korean translations and write Reddit-ready English posts. Built with local Ollama LLM (no cloud API, no API keys required).

## Features

- **Bilingual Reddit browsing** — Browse subreddits with auto-translated Korean post titles
- **AI-generated summaries** — Get Korean summaries of English posts automatically
- **Comment translation** — Batch or lazy-loaded comment translations on scroll
- **2-stage writing pipeline** — Korean → English draft → Reddit-tone polished English
- **Language contamination detection** — Auto-retry with fallback models if translation quality degrades
- **Fully offline** — Runs entirely locally with Ollama, no internet dependency after startup
- **Bilingual UI** — Switch between Korean (ko_KR) and English (en_US) interfaces
- **Mock mode** — Test the UI without network access

## Requirements

- Python 3.11 or higher
- Ollama running locally ([https://ollama.ai](https://ollama.ai))
- Required Ollama models:
  - `llama3.1:8b` — Post titles, comment translation
  - `gemma2:9b` — Translation logic and drafting
  - `llama3.1:70b` — Reddit tone polishing

## Quick Start

### Installation

```bash
# Clone repository
git clone https://github.com/mua-vtuber/ReddiScribe.git
cd ReddiScribe

# Install package
pip install -e .
```

### Pull Ollama Models

```bash
ollama pull llama3.1:8b
ollama pull gemma2:9b
ollama pull llama3.1:70b
```

### Run Application

**Linux / macOS:**
```bash
python -m src.main
```

**Windows:**
Double-click `run.bat`

## Configuration

Settings are stored in `config/settings.yaml` and auto-created on first run.

### Edit Settings

1. **Via Settings Tab** — Open the app and go to the Settings tab to adjust:
   - Locale (Korean / English)
   - Request interval for Reddit (default: 6 seconds)
   - Mock mode toggle
   - Log level

2. **Direct Edit** — Edit `config/settings.yaml` for advanced settings:

```yaml
app:
  locale: ko_KR              # ko_KR or en_US
  log_level: INFO            # DEBUG, INFO, WARNING, ERROR

llm:
  default_provider: ollama
  providers:
    ollama:
      host: http://localhost:11434
      timeout: 120

reddit:
  request_interval_sec: 6    # Enforced due to Reddit rate limits
  mock_mode: false           # true for UI testing without network

data:
  db_path: db/history.db
```

**Note:** Model names (logic, persona, summary) are configurable in `settings.yaml` but not yet via the Settings UI in v1.0.

## Project Structure

```
ReddiScribe/
├── src/
│   ├── core/                # Config, database, i18n, logging, types, exceptions
│   │   ├── config_manager.py
│   │   ├── database.py
│   │   ├── i18n_manager.py
│   │   └── ...
│   ├── adapters/            # External API adapters
│   │   ├── public_json_adapter.py      # Reddit public JSON endpoints
│   │   └── ollama_adapter.py           # Ollama REST API wrapper
│   ├── services/            # Business logic layer
│   │   ├── reader_service.py           # Post reading, translation
│   │   └── writer_service.py           # 2-stage writing pipeline
│   ├── gui/                 # PyQt6 UI
│   │   ├── main_window.py
│   │   ├── widgets/
│   │   │   ├── reader_tab.py           # Browse & translate
│   │   │   ├── writer_tab.py           # Write English posts
│   │   │   └── settings_tab.py         # Configure app
│   │   └── workers.py                  # QThread workers for async ops
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
1. Select a subreddit from the sidebar
2. Posts load with Korean-translated titles
3. Click a post to view the full English text and auto-generated Korean summary
4. Comments load lazily with batch translation as you scroll

### Writer Tab
1. **Stage 1:** Write your post in Korean
2. **Stage 2:** AI translates to English and generates a draft
3. **Stage 3:** AI polishes the draft for Reddit tone and style
4. Copy the final result and paste on Reddit

### Behind the Scenes
- Ollama models run locally (no data leaves your machine)
- Reddit data fetched via public JSON endpoints (no API key needed)
- 6-second minimum interval enforced between Reddit requests to respect rate limits
- Translation quality checked automatically; if language contamination detected, retries with fallback models

## Troubleshooting

**"Connection refused to Ollama"**
- Ensure Ollama is running: `ollama serve`
- Check host in `config/settings.yaml` (default: `http://localhost:11434`)

**"Model not found"**
- Pull required models: `ollama pull llama3.1:8b` etc.
- Verify model names in `config/settings.yaml`

**"Reddit requests timing out"**
- Increase `request_interval_sec` in settings
- Check internet connection

**Enable debug logging**
- Set `app.log_level: DEBUG` in `config/settings.yaml`
- Check `logs/` directory for detailed logs

## License

See LICENSE file for details.
