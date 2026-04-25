# What's next — post-hackathon roadmap

> Stub list of work to pick up after the Cerebral Valley × Anthropic
> "Built with Opus 4.7" hackathon (April 26, 2026). When you're ready to
> resume, paste the prompt below into a fresh Claude Code session and let
> it run.

---

## 🎯 First milestone: one-click installer for forex traders

Turn APEX from "clone + pip install + edit yaml" into a double-click `.exe`
that any MT5 user can run.

### Prompt for next session

> Draft a PyInstaller spec + first-run wizard for the APEX MT5 Optimizer
> (https://github.com/tonnylegacy/MT5_Optimizer). Goal: turn the project
> into a double-click .exe for forex traders.
>
> **(1)** Create `apex.spec` that bundles `app.py`, all submodules,
> `ui/templates`, `ui/static`, `demo/`, `config.example.yaml`, and the demo
> .set file. Use `--onefile`, set the icon, exclude tests + screenshots.
> Verify `pyinstaller apex.spec` produces a working binary that launches
> the Flask server and opens localhost:5000 in a browser.
>
> **(2)** Add a first-run wizard at `/first_run` that fires when
> `config.yaml` is missing or `anthropic_api_key` is empty: walks the user
> through MT5 path detection (read Windows registry
> `HKLM\SOFTWARE\MetaQuotes Software Corp.`), API key paste, EA selection
> (use the existing `/api/ea/scan`), then writes `config.yaml` and
> redirects to `/dashboard`.
>
> **(3)** Open a PR titled "feat: one-click installer + first-run wizard".

---

## 🧠 Bigger features — what forex traders would actually pay for

Priority order, ship incrementally:

1. **Multi-symbol robustness** — same params validated on EURUSD + GBPUSD
   + XAUUSD in parallel; verdict fails anything that only works on one.
   Adds a "Cross-symbol" badge to the verdict card.

2. **Walk-forward windows** — instead of one OOS test, slide a 6-month
   train / 1-month test window forward 12× and score on consistency.
   Replaces the current single-OOS Phase 3 with a more robust validation.

3. **Live capital protection** — once an EA is deployed, nightly
   re-validation cron checks if the current market regime still matches
   the optimization window. Ping the user (Discord webhook already wired)
   when drift is detected.

4. **Setlist provenance marketplace** — verified `.set` files with their
   full AI evolution path attached as proof of how they were derived.
   Buyers see the reasoning, not a black box. (Opus 4.7's role in this
   becomes the trust layer.)

---

## 🏗️ Hosted SaaS tier

Out of scope for solo build, but the shape:

- Sandbox each user with a Windows VM running MT5
- Queue optimization jobs, stream the same dashboard back over WebSocket
- Per-tenant `config.yaml` + API key vault
- Stripe metering on backtests + AI calls

Real product, not a weekend hack. Park until usage validates the demand.

---

*Saved 2026-04-25 — re-paste the prompt above when ready.*
