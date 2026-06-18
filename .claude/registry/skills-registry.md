# Skills Registry — Hermes Runtime Skills
> Subject: the **Hermes runtime skills-hub** (`~/.hermes/.skills_prompt_snapshot.json`, 42,569 B ≈ 10,642 tok, 73 skills) injected into EVERY Telegram turn.
> Verified read-only on the VPS, 2026-06-15. NOT the `.claude/` operating skills (those cost 0 runtime tokens).

> Central routing registry: what each skill is, how the router should trigger it, and its load priority.
> **P0**=always-load core · **P1**=on-demand (high relevance) · **P2**=on-demand (low) · **P3**=archived/off.

| Skill | Load | Description | Trigger keywords | Required context | Related |
|---|---|---|---|---|---|
| hermes-agent | P0 | Configure, extend, or contribute to Hermes Agent. | agent, autonomous-ai-agents, hermes | telegram; autonomous-ai-agents | autonomous-ai-agents |
| kanban-orchestrator | P0 |  | devops, dispatch, kanban, orchestrator, task | telegram; devops | devops |
| kanban-worker | P0 |  | devops, execute, kanban, task, worker | telegram; devops | devops |
| claude-code | P1 | Delegate coding to Claude Code CLI (features, PRs). | autonomous-ai-agents, claude, code | telegram; autonomous-ai-agents | coding-agents |
| codex | P1 | Delegate coding to OpenAI Codex CLI (features, PRs). | autonomous-ai-agents, codex | telegram; autonomous-ai-agents | coding-agents |
| opencode | P1 | Delegate coding to OpenCode CLI (features, PR review). | autonomous-ai-agents, opencode | telegram; autonomous-ai-agents | coding-agents |
| himalaya | P1 | Himalaya CLI: IMAP/SMTP email from terminal. | email, himalaya, mail | telegram; email | email |
| codebase-inspection | P1 | Inspect codebases w/ pygount: LOC, languages, ratios. | codebase, github, inspection | telegram; github | github |
| github-auth | P1 | GitHub auth setup: HTTPS tokens, SSH keys, gh CLI login. | auth, github | telegram; github | github |
| github-code-review | P1 | Review PRs: diffs, inline comments via gh or REST. | code, github, review | telegram; github | github |
| github-issues | P1 | Create, triage, label, assign GitHub issues via gh or REST. | github, issues | telegram; github | github |
| github-pr-workflow | P1 | GitHub PR lifecycle: branch, commit, open, CI, merge. | github, workflow | telegram; github | github |
| github-repo-management | P1 | Clone/create/fork repos; manage remotes, releases. | github, management, repo | telegram; github | github |
| airtable | P1 | Airtable REST API via curl. Records CRUD, filters, upserts. | airtable, productivity | telegram; productivity | productivity |
| google-workspace | P1 | Gmail, Calendar, Drive, Docs, Sheets via gws CLI or Python. | calendar, docs, gmail, google, productivity, sheets | telegram; productivity | productivity |
| maps | P1 | Geocode, POIs, routes, timezones via OpenStreetMap/OSRM. | maps, productivity | telegram; productivity | productivity |
| nano-pdf | P1 | Edit PDF text/typos/titles via nano-pdf CLI (NL prompts). | nano, pdf, productivity | telegram; productivity | productivity |
| notion | P1 | Notion API + ntn CLI: pages, databases, markdown, Workers. | notion, productivity | telegram; productivity | productivity |
| ocr-and-documents | P1 | Extract text from PDFs/scans (pymupdf, marker-pdf). | and, documents, ocr, pdf, productivity, scan | telegram; productivity | productivity |
| powerpoint | P1 | Create, read, edit .pptx decks, slides, notes, templates. | powerpoint, productivity | telegram; productivity | productivity |
| teams-meeting-pipeline | P1 | Operate the Teams meeting summary pipeline via Hermes CLI... | meeting, pipeline, productivity, teams | telegram; productivity | productivity |
| arxiv | P1 | Search arXiv papers by keyword, author, category, or ID. | arxiv, research | telegram; research | research |
| blogwatcher | P1 | Monitor blogs and RSS/Atom feeds via blogwatcher-cli tool. | blogwatcher, research | telegram; research | research |
| llm-wiki | P1 | Karpathy's LLM Wiki: build/query interlinked markdown KB. | llm, research, wiki | telegram; research | research |
| polymarket | P1 | Query Polymarket: markets, prices, orderbooks, history. | market, polymarket, prediction, research | telegram; research | research |
| research-paper-writing | P1 | Write ML papers for NeurIPS/ICML/ICLR: design→submit. | paper, research, writing | telegram; research | research |
| hermes-agent-skill-authoring | P1 | Author in-repo SKILL.md: frontmatter, validator, structure. | agent, authoring, hermes, skill, software-development | telegram; software-development | software-development |
| node-inspect-debugger | P1 | Debug Node.js via --inspect + Chrome DevTools Protocol CLI. | debugger, inspect, node, software-development | telegram; software-development | debugging |
| plan | P1 | Plan mode: write an actionable markdown plan to .hermes/p... | plan, software-development | telegram; software-development | software-development |
| python-debugpy | P1 | Debug Python: pdb REPL + debugpy remote (DAP). | debugpy, python, software-development | telegram; software-development | debugging |
| requesting-code-review | P1 | Pre-commit review: security scan, quality gates, auto-fix. | code, requesting, review, software-development | telegram; software-development | software-development |
| simplify-code | P1 | Parallel 3-agent cleanup of recent code changes. | code, simplify, software-development | telegram; software-development | software-development |
| spike | P1 | Throwaway experiments to validate an idea before build. | software-development, spike | telegram; software-development | software-development |
| systematic-debugging | P1 | 4-phase root cause debugging: understand bugs before fixing. | debugging, software-development, systematic | telegram; software-development | debugging |
| test-driven-development | P1 | TDD: enforce RED-GREEN-REFACTOR, tests before code. | development, driven, software-development, test | telegram; software-development | software-development |
| architecture-diagram | P2 | Dark-themed SVG architecture/cloud/infra diagrams as HTML. | architecture, creative, diagram | telegram; creative | creative |
| design-md | P2 | Author/validate/export Google's DESIGN.md token spec files. | creative, design | telegram; creative | creative |
| humanizer | P2 | Humanize text: strip AI-isms and add real voice. | creative, humanizer | telegram; creative | creative |
| youtube-content | P2 | YouTube transcripts to summaries, threads, blogs. | content, media, youtube | telegram; media | media |
| obsidian | P2 | Read, search, create, and edit notes in the Obsidian vault. | note-taking, obsidian | telegram; note-taking | note-taking |
| xurl | P2 | X/Twitter via xurl CLI: post, search, DM, media, v2 API. | post, social-media, twitter, x, xurl | telegram; social-media | social-media |
| apple-notes | P3 |  | apple, notes | telegram; apple | apple |
| apple-reminders | P3 |  | apple, reminders | telegram; apple | apple |
| findmy | P3 |  | apple, findmy | telegram; apple | apple |
| imessage | P3 |  | apple, imessage | telegram; apple | apple |
| macos-computer-use | P3 |  | apple, computer, macos, use | telegram; apple | apple |
| ascii-art | P3 | ASCII art: pyfiglet, cowsay, boxes, image-to-ascii. | art, ascii, creative | telegram; creative | creative |
| ascii-video | P3 | ASCII video: convert video/audio to colored ASCII MP4/GIF. | ascii, creative, video | telegram; creative | creative |
| baoyu-infographic | P3 | Infographics: 21 layouts x 21 styles (信息图, 可视化). | baoyu, creative, infographic | telegram; creative | creative |
| claude-design | P3 | Design one-off HTML artifacts (landing, deck, prototype). | claude, creative, design | telegram; creative | creative |
| comfyui | P3 | Generate images, video, and audio with ComfyUI — install,... | comfyui, creative | telegram; creative | creative |
| excalidraw | P3 | Hand-drawn Excalidraw JSON diagrams (arch, flow, seq). | creative, excalidraw | telegram; creative | creative |
| manim-video | P3 | Manim CE animations: 3Blue1Brown math/algo videos. | creative, manim, video | telegram; creative | creative |
| p5js | P3 | p5.js sketches: gen art, shaders, interactive, 3D. | creative, p5js | telegram; creative | creative |
| popular-web-designs | P3 | 54 real design systems (Stripe, Linear, Vercel) as HTML/CSS. | creative, designs, popular, web | telegram; creative | creative |
| pretext | P3 | Use when building creative browser demos with @chenglou/p... | creative, pretext | telegram; creative | creative |
| sketch | P3 | Throwaway HTML mockups: 2-3 design variants to compare. | creative, sketch | telegram; creative | creative |
| songwriting-and-ai-music | P3 | Songwriting craft and Suno AI music prompts. | and, creative, music, songwriting | telegram; creative | creative |
| touchdesigner-mcp | P3 | Control a running TouchDesigner instance via twozero MCP ... | creative, mcp, touchdesigner | telegram; creative | creative |
| jupyter-live-kernel | P3 | Iterative Python via live Jupyter kernel (hamelnb). | data-science, jupyter, kernel, live | telegram; data-science | data-science |
| dogfood | P3 | Exploratory QA of web apps: find bugs, evidence, reports. | dogfood | telegram; dogfood | dogfood |
| gif-search | P3 | Search/download GIFs from Tenor via curl + jq. | gif, media, search | telegram; media | media |
| heartmula | P3 | HeartMuLa: Suno-like song generation from lyrics + tags. | heartmula, media | telegram; media | media |
| songsee | P3 | Audio spectrograms/features (mel, chroma, MFCC) via CLI. | media, songsee | telegram; media | media |
| huggingface-hub | P3 | HuggingFace hf CLI: search/download/upload models, datasets. | hub, huggingface, mlops | telegram; mlops | mlops |
| lm-evaluation-harness | P3 | lm-eval-harness: benchmark LLMs (MMLU, GSM8K, etc.). | evaluation, harness, mlops | telegram; mlops/evaluation | mlops |
| weights-and-biases | P3 | W&B: log ML experiments, sweeps, model registry, dashboards. | and, biases, mlops, weights | telegram; mlops/evaluation | mlops |
| llama-cpp | P3 | llama.cpp local GGUF inference + HF Hub model discovery. | cpp, llama, mlops | telegram; mlops/inference | mlops |
| vllm | P3 | vLLM: high-throughput LLM serving, OpenAI API, quantization. | mlops, vllm | telegram; mlops/inference | mlops |
| audiocraft | P3 | AudioCraft: MusicGen text-to-music, AudioGen text-to-sound. | audiocraft, mlops | telegram; mlops/models | mlops |
| segment-anything | P3 | SAM: zero-shot image segmentation via points, boxes, masks. | anything, mlops, segment | telegram; mlops/models | mlops |
| openhue | P3 | Control Philips Hue lights, scenes, rooms via OpenHue CLI. | openhue, smart-home | telegram; smart-home | smart-home |
| yuanbao | P3 | Yuanbao (元宝) groups: @mention users, query info/members. | yuanbao | telegram; yuanbao | yuanbao |
