# Second Voice

A coercion-aware payment interruptor for UPI.

Fraud systems ask "is this really the account holder?". In digital-arrest and
investment scams the answer is yes: the victim authorises the payment under
coercion, so rules see nothing. Second Voice sits in the payer bank's
authorisation hook, scores whether the account holder is being manipulated
right now, and answers with a conversation instead of a banner. The receiving
bank runs the same service on its own accounts and shares one word back: a
receiver risk band.

## Google services

| Service | Role |
|---|---|
| Cloud Run | One container, deployed twice: `bank-a` (payer) and `bank-b` (receiver). Payment API, scorer, Gemini agent, address validation, credit hold. |
| Firestore | Customers, payees, 90-day history, receiver accounts, cases, holds, conversation turns. Live listeners drive the phone UI and both consoles. |
| Firebase Hosting + Auth | Mock bank app and the two ops consoles. Anonymous auth for the app, email/password demo login for consoles. |
| Gemini on Vertex AI | Names the scam script from the signal pattern, runs the intervention conversation, writes the case summary. |

## Layout

```
services/bank/      FastAPI service, one codebase, BANK_ID=A or B
  app/scoring.py    payer-side coercion score and tiering
  app/receiver.py   receiver-side mule score and band
  app/gemini.py     Gemini client; GEMINI_MODE=stub for offline dev
  app/conversation.py intervention flow
  prompts/          system prompts and the scam script library
seed/seed.py        seeds both banks (Sunita, Rakesh Kumar, 12 fan-in payers)
web/public/         Firebase Hosting site: /app, /console-a, /console-b
scripts/dev.sh      emulators + both services locally
scripts/deploy.sh   Cloud Run + Hosting deploy
```

## Run locally

```bash
npm install -g firebase-tools          # once; ships the Firestore emulator (needs Java 21+)
cp web/public/shared/config.example.js web/public/shared/config.js
cd services/bank && uv sync && cd ../..
./scripts/dev.sh                       # emulators, bank-a :8081, bank-b :8082, web :5000
uv run --project services/bank python seed/seed.py   # in another terminal
```

Open http://localhost:5000/app and pick the **Digital arrest** scenario.

Gemini runs in `stub` mode by default so the whole flow works offline. Set
`GEMINI_MODE=vertex` plus `GOOGLE_CLOUD_PROJECT` after
`gcloud auth application-default login` to use the real model.

## Deploy

See `scripts/deploy.sh`. Needs `gcloud` and `firebase` logged in and a project
with billing. The script enables the APIs, deploys both Cloud Run services with
one minimum instance each, and deploys Hosting.
