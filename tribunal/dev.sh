#!/bin/bash
# Start API + UI in one tmux session. Usage: tribunal/dev.sh [api-port] [ui-port]
set -e
cd "$(dirname "$0")/.."
API=${1:-8000}; UI=${2:-5173}; S="dev-tribunal-$(date +%s)"
tmux new-session -d -s "$S" -n api -e ZSH_DOTENV_PROMPT=false
tmux send-keys -t "$S:api" ".venv/bin/uvicorn tribunal.api:app --port $API --reload 2>&1 | tee logs/tribunal-api-$API.log" C-m
tmux new-window -t "$S" -n ui
tmux send-keys -t "$S:ui" "cd tribunal/ui && VITE_API=http://localhost:$API npx vite --port $UI 2>&1 | tee ../../logs/tribunal-ui-$UI.log" C-m
echo "{\"session\":\"$S\",\"api\":$API,\"ui\":$UI}" > .tmux-dev-session.json
echo "tmux: $S   api: http://localhost:$API   ui: http://localhost:$UI"
