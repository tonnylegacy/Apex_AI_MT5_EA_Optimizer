# APEX — Cerebral Valley × Anthropic "Built with Opus 4.7" Hackathon Submission

Copy‑paste blocks for the submission form. Every field below is also a heading
so you can `Ctrl+F` to it.

---

## Project name

**APEX** — Autonomous Performance Evaluator (AI‑driven MT5 EA Optimizer)

---

## One‑liner (≤140 chars)

> An AI trader thinking out loud — Claude Opus 4.7 reads each backtest, decides what to change, and iterates toward profit‑factor / drawdown targets.

## Tagline (≤80 chars)

> Watch Claude Opus 4.7 optimize a trading strategy in real time.

---

## Short description (≤300 chars)

APEX turns Claude Opus 4.7 into an autonomous trading‑strategy optimizer for MetaTrader 5. The model reads every backtest, decides what parameters to change and why, then runs the next test — streaming its reasoning live to a dashboard until quality targets are met.

---

## Long description (~500 words — the "what does it do" field)

Most strategy optimizers are brute‑force grid searches: pick a metric, sweep N parameters, pray. The user gets a winning configuration but no idea *why* it won, no confidence in *whether* it'll generalize, and no transparency into the search process.

**APEX replaces the grid with an AI loop.** Claude Opus 4.7 reads each backtest result, looks at the full iteration history, considers the parameter schema and quality targets, and returns a structured `{changes: [{param, value, reason}], confidence, goal_status}` — concrete, bounds‑checked parameter values for the next test, plus the reasoning that produced them. Every change, every reason, and every Claude token streams live to a dashboard so the user watches the AI think.

The pipeline runs three phases:

1. **Exploration** — Latin‑Hypercube sampling builds a broad map of profitable parameter regions
2. **AI Iteration** — Opus 4.7 takes over, hill‑climbing toward user‑set quality targets (PF ≥ X, DD ≤ Y, Calmar ≥ Z), with stuck‑detection and random‑escape when the loop converges to a local optimum
3. **Validation** — out‑of‑sample backtest on unseen dates + ±20% sensitivity nudge on the top parameter → verdict (RECOMMENDED / RISKY / NOT_RELIABLE)

The dashboard exposes everything the AI is doing as it happens:

- **Live AI Thinking Feed** — Claude's reasoning streams token‑by‑token via SSE with a typing cursor
- **Parameter Changes** — every iteration shows `prev → new` with the reason
- **Validation Activity** — each OOS / sensitivity test renders with live metrics
- **Replay Scrubber** — drag through every step that led to the winning configuration with metrics and AI analysis at each step
- **Compare Runs** — side‑by‑side metric and parameter diff for any 2–4 runs
- **Early Termination** — clear banner when targets are hit, budget exhausted, or the AI is stuck
- **Discord/Slack webhook** when an optimization completes

For judges without MT5: there's a **demo mode** (`python -m demo.run_demo`) that synthesizes deterministic‑but‑realistic backtests so the entire AI loop, validation, and verdict flow run end‑to‑end with no MT5 install. Roughly 35% of Phase 1 samples and 30% of OOS runs realistically fail, so verdicts genuinely span RECOMMENDED / RISKY / NOT_RELIABLE.

The point isn't a better trading strategy — it's a working pattern for **AI‑as‑driver of a long‑running optimization loop**, with the AI's reasoning fully visible. That pattern transfers to any iterative search problem where humans currently grid‑sweep blindly: ML hyperparameter tuning, A/B variant generation, ad‑creative optimization, infrastructure cost tuning.

---

## Built with

Claude Opus 4.7 (the reasoning loop), Flask + Flask‑SocketIO (real‑time UI), Python 3.11, Pandas + NumPy, Pydantic, Anthropic Messages API with SSE streaming, MetaTrader 5 Strategy Tester (real backtests).

---

## How Claude Opus 4.7 is used (the "what AI features did you use" field)

- **`suggest_next_params()` — Opus 4.7 is the loop driver.** It receives the full parameter schema, the iteration history (last 15 runs with metrics + changes), and the user's quality targets, and returns structured JSON with concrete parameter values, per‑change reasoning, a confidence score, and a goal‑status breakdown (which targets are met). This is the load‑bearing call — it's what turns the optimizer from a grid search into an agent.
- **`analyze()` — Per‑run diagnostics.** A second Opus 4.7 call interprets each backtest's metrics + analyzer findings + recent run history and returns headline / diagnosis / patterns / suggestions / risk flags. Powers the AI Summary panel.
- **SSE streaming** — both calls run with `stream=true`. Each text delta forwards to the dashboard as `ai_thinking_chunk` events so the user sees Claude's reasoning type out token‑by‑token. Single growing bubble per call with a blinking cursor that finalises on `end`.
- **Hot model swap** — users can change model (`claude-opus-4-7` ↔ `claude-sonnet-4-6` ↔ `claude-haiku-4-5`) mid‑run via Settings. The next iteration uses the new model without restart.

---

## GitHub URL

https://github.com/tonnylegacy/MT5_Optimizer

---

## Demo URL

No hosted demo (the app runs locally to drive a local MT5 install). For judges:

- **Static**: open `screenshots/apex_demo.gif` in the repo (6‑frame timelapse of one autonomous run)
- **Run it**: `git clone … && pip install -r requirements.txt && python -m demo.run_demo` → the browser opens to the dashboard, a ~3-4 minute optimization auto-starts, and every phase (exploration → AI iteration → validation → verdict) plays out without you touching anything
- **With API key**: set `ANTHROPIC_API_KEY` env var (or fill `ai.anthropic_api_key` in `config.yaml`) to see live Claude reasoning stream into the Thinking Feed
- **Faster / your-own-config**: `python -m demo.run_demo --quick` to skip the auto-run and drive the demo from `/setup` yourself

---

## Video / GIF

`screenshots/apex_demo.gif` (438 KB, GitHub‑embedded in README hero)

---

## Team

Solo build — `tonnylegacy`

---

## Tags

`mt5` `metatrader5` `trading` `quant` `optimization` `claude-opus-4-7` `anthropic` `ai-agents` `agentic-loops` `python` `flask` `socketio` `streaming-ui`

---

## Tech stack

`Python 3.11` · `Anthropic Messages API (SSE streaming)` · `Claude Opus 4.7` · `Flask` · `Flask‑SocketIO` · `Pandas / NumPy / Pydantic` · `MetaTrader 5 Strategy Tester`

---

## License

MIT

---

## Submission checklist

- [x] Repo public on GitHub: <https://github.com/tonnylegacy/MT5_Optimizer>
- [x] LICENSE file (MIT)
- [x] README with pitch + screenshots + install + architecture diagram
- [x] No leaked API keys (config.yaml git‑ignored, env‑var fallback wired, GET /api/settings masks key)
- [x] `config.example.yaml` template for users
- [x] Animated demo GIF in `screenshots/apex_demo.gif`
- [x] Demo mode that runs without MT5 (`python -m demo.run_demo`)
- [x] Opus 4.7 is the default model (config + AIReasoner class default)
- [x] Streaming reasoning visible to the user (SSE → `ai_thinking_chunk`)
- [ ] Recorded a 30–60 second screen capture for any "video" field — *do this last; the GIF can substitute*
- [ ] Submission form filled — *paste blocks above into the Cerebral Valley form when it opens*
