#!/usr/bin/env bash
# Deploys both Cloud Run services and Firebase Hosting.
#   ./scripts/deploy.sh <gcp-project-id> [region]
# Needs: gcloud + firebase logged in, billing enabled on the project, Firebase
# added to the project (console.firebase.google.com -> Add project -> pick this one).
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT="${1:?project id}"
REGION="${2:-asia-south1}"
GEMINI_LOCATION="${GEMINI_LOCATION:-us-central1}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-2.5-flash}"

gcloud config set project "$PROJECT" >/dev/null
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  firestore.googleapis.com aiplatform.googleapis.com firebase.googleapis.com

# Firestore database (ignore "already exists")
gcloud firestore databases create --location="$REGION" 2>/dev/null || true

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
# Runtime roles (Gemini, Firestore) plus the roles Cloud Build needs to build from
# source on a new project, where the default compute SA no longer has Editor.
for role in roles/aiplatform.user roles/datastore.user roles/cloudbuild.builds.builder \
            roles/storage.objectViewer roles/artifactregistry.writer roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" --role="$role" --quiet >/dev/null
done

# Firebase on the project, a web app, and anonymous sign-in (the browser pages use it).
firebase projects:addfirebase "$PROJECT" >/dev/null 2>&1 || true
gcloud services enable identitytoolkit.googleapis.com >/dev/null
TOKEN=$(gcloud auth print-access-token)
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "x-goog-user-project: $PROJECT" -H "content-type: application/json" \
  "https://identitytoolkit.googleapis.com/v2/projects/$PROJECT/identityPlatform:initializeAuth" -d '{}' >/dev/null || true
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H "x-goog-user-project: $PROJECT" -H "content-type: application/json" \
  "https://identitytoolkit.googleapis.com/admin/v2/projects/$PROJECT/config?updateMask=signIn.anonymous.enabled" \
  -d '{"signIn":{"anonymous":{"enabled":true}}}' >/dev/null

COMMON="GOOGLE_CLOUD_PROJECT=$PROJECT,GEMINI_MODE=vertex,GEMINI_MODEL=$GEMINI_MODEL,GEMINI_LOCATION=$GEMINI_LOCATION"

deploy() { # name bank_id peer_url
  gcloud run deploy "$1" --source services/bank --region "$REGION" --allow-unauthenticated \
    --min-instances 1 --max-instances 3 --memory 512Mi --cpu 1 --timeout 60 \
    --set-env-vars "BANK_ID=$2,BANK_NAME=Bank $2,PEER_BANK_ID=$([ "$2" = A ] && echo B || echo A),PEER_BANK_URL=$3,$COMMON" \
    --quiet
  gcloud run services describe "$1" --region "$REGION" --format='value(status.url)'
}

echo "== deploying bank-b (no peer yet)"
B_URL=$(deploy bank-b B "")
echo "== deploying bank-a -> peer $B_URL"
A_URL=$(deploy bank-a A "$B_URL")
echo "== updating bank-b -> peer $A_URL"
gcloud run services update bank-b --region "$REGION" --update-env-vars "PEER_BANK_URL=$A_URL" --quiet >/dev/null

echo "== writing web config"
APP=$(firebase apps:list WEB --project "$PROJECT" --json 2>/dev/null | python3 -c 'import sys,json; r=json.load(sys.stdin)["result"]; print(r[0]["appId"] if r else "")')
[ -n "$APP" ] || { firebase apps:create WEB "Second Voice" --project "$PROJECT" >/dev/null; APP=$(firebase apps:list WEB --project "$PROJECT" --json | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"][0]["appId"])'); }
WEB_CFG=$(firebase apps:sdkconfig WEB "$APP" --project "$PROJECT" --json 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin)["result"]["sdkConfig"]; print(json.dumps({k:d[k] for k in ("apiKey","authDomain","projectId","storageBucket","messagingSenderId","appId") if k in d}))' || true)
if [ -z "$WEB_CFG" ]; then
  echo "!! could not read the Firebase web config. Create a web app in the Firebase console, then paste its"
  echo "   config object into web/public/shared/config.js (see config.example.js) and re-run: firebase deploy --only hosting"
  WEB_CFG='{ /* paste firebase web config here */ }'
fi
cat > web/public/shared/config.js <<CFG
window.SV_CONFIG = {
  firebase: $WEB_CFG,
  useEmulators: false,
  bankA: "$A_URL",
  bankB: "$B_URL",
};
CFG

sed -i "s/\"default\": \"[^\"]*\"/\"default\": \"$PROJECT\"/" .firebaserc
firebase deploy --project "$PROJECT" --only firestore:rules,firestore:indexes,hosting

echo
echo "bank-a  $A_URL/health"
echo "bank-b  $B_URL/health"
echo "web     https://$PROJECT.web.app/app"
echo
echo "Seed the live database:  GOOGLE_CLOUD_PROJECT=$PROJECT uv run --project services/bank python seed/seed.py"
