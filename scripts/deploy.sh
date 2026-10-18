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
for role in roles/aiplatform.user roles/datastore.user; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" --role="$role" --quiet >/dev/null
done

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
WEB_CFG=$(firebase apps:sdkconfig web --project "$PROJECT" 2>/dev/null | sed -n '/{/,/}/p' || true)
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
echo "Enable Anonymous sign-in: Firebase console -> Authentication -> Sign-in method -> Anonymous"
