#!/usr/bin/env bash
# Runs everything locally: Firebase emulators (Firestore, Auth, Hosting), bank-a on
# :8081, bank-b on :8082. Needs firebase-tools (npm i -g firebase-tools), Java 11+
# for the emulators (21+; SDKMAN installs are picked up automatically), and uv. Seed in another terminal:
#   uv run --project services/bank python seed/seed.py
set -euo pipefail
cd "$(dirname "$0")/.."

export GOOGLE_CLOUD_PROJECT=second-voice-demo
export FIRESTORE_EMULATOR_HOST=localhost:8080
export FIREBASE_AUTH_EMULATOR_HOST=localhost:9099
export GEMINI_MODE="${GEMINI_MODE:-stub}"
export GEMINI_LOCATION="${GEMINI_LOCATION:-us-central1}"

# firebase-tools needs Java 21+. Prefer an SDKMAN-installed 21 if the default is older.
if ! java -version 2>&1 | grep -qE 'version "(2[1-9]|[3-9][0-9])'; then
  J21=$(ls -d "$HOME"/.sdkman/candidates/java/21* 2>/dev/null | head -1)
  [ -n "$J21" ] && export JAVA_HOME="$J21" PATH="$J21/bin:$PATH"
fi

[ -f web/public/shared/config.js ] || cp web/public/shared/config.example.js web/public/shared/config.js

firebase emulators:start --project "$GOOGLE_CLOUD_PROJECT" --only firestore,auth,hosting &
EMU=$!
cleanup() { kill "$EMU" "${A:-}" "${B:-}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

# wait for Firestore emulator
for _ in $(seq 1 30); do
  curl -sf "http://$FIRESTORE_EMULATOR_HOST/" >/dev/null 2>&1 && break
  sleep 1
done

(cd services/bank && BANK_ID=A BANK_NAME="Bank A" PEER_BANK_ID=B PEER_BANK_URL=http://localhost:8082 \
  uv run uvicorn app.main:app --port 8081 --reload) &
A=$!
(cd services/bank && BANK_ID=B BANK_NAME="Bank B" PEER_BANK_ID=A PEER_BANK_URL=http://localhost:8081 \
  uv run uvicorn app.main:app --port 8082 --reload) &
B=$!

echo
echo "  app        http://localhost:5000/app"
echo "  console A  http://localhost:5000/console-a"
echo "  console B  http://localhost:5000/console-b"
echo "  emulators  http://localhost:4000"
echo
wait
