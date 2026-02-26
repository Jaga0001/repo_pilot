# RepoPilot — CI/CD AI Fix Agent

RepoPilot is an AI-powered agent that listens for CI/CD pipeline failures via GitHub webhooks, analyzes errors, generates code fixes, and opens pull requests automatically. It leverages semantic search (Elasticsearch) and CrewAI agents for intelligent troubleshooting and repair.

---

## Features

- GitHub webhook integration for CI/CD events
- Automated error analysis and code fixing
- Pull request creation with detailed fix explanations
- Semantic search of past fixes using Elasticsearch
- Configurable agents and tasks via YAML
- FastAPI backend for extensibility

---

## Quick Start

### Requirements
- Python 3.13
- GitHub App credentials (App ID, private key)
- Elasticsearch instance (API key)
- Gemini API key (for AI model)

### Installation

```bash
# Clone the repository
$ git clone https://github.com/jaga0001/repo_pilot.git
$ cd repopilot

# Create and activate a virtual environment
$ python3.13 -m venv .venv
$ .venv\Scripts\activate  # Windows

# Install dependencies
$ pip install -r requirements.txt
```

### Configuration

Create a `.env` file with the following variables:

```
GEMINI_API_KEY=your-gemini-api-key
ELASTIC_URL=https://your-elastic-url
ELASTIC_API_KEY=your-elastic-api-key
ELASTIC_INDEX=sage-ci-fixes
MODEL=gemini/gemini-2.5-flash

GITHUB_APP_ID=your-app-id
GITHUB_PRIVATE_KEY_PATH=./your-app-key.pem
GITHUB_WEBHOOK_SECRET=your-webhook-secret
```

---

## Running the Agent

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

---

## GitHub Setup

1. Create a GitHub App with repository and workflow permissions.
2. Set webhook URL to `/webhook` endpoint (e.g., `https://your-domain/webhook`).
3. Set `GITHUB_WEBHOOK_SECRET` for signature verification.

---

## Project Structure

```
repo_pilot/
├── main.py                # FastAPI entrypoint
├── routes/
│   └── webhook.py         # Webhook handler
├── services/
│   ├── github_service.py  # GitHub API integration
│   ├── github_auth.py     # GitHub App authentication
│   └── es_service.py      # Elasticsearch integration
├── crew/
│   ├── crew.py            # CrewAI agent/task definitions
│   └── config/
│       ├── agents.yaml    # Agent configs
│       └── tasks.yaml     # Task configs
├── pyproject.toml
├── README.md
└── .env
```

---

## How It Works

1. Receives webhook events from GitHub.
2. Monitors CI/CD workflow runs.
3. Analyzes failures and fetches logs/source code.
4. Runs CrewAI agents to generate fixes.
5. Commits fixes and opens PRs.
6. Stores fix details in Elasticsearch for future reference.

---

## Customization

- Edit `crew/config/agents.yaml` and `crew/config/tasks.yaml` to tune agent behavior.
- Update supported file types in `services/github_service.py`.

---

## License

MIT License

---

## Credits

- CrewAI
- FastAPI
- Elasticsearch
- Gemini

---

## Contributing

Pull requests and issues are welcome!

---

## Contact

For support or questions, contact your-email@domain.com.

---

**RepoPilot** — Automated CI/CD Fix Agent powered by AI.
