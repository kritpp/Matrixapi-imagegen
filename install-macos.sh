#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$SCRIPT_DIR/skills/Matrixapi-imagegen"
TARGET="$HOME/.codex/skills/Matrixapi-imagegen"
CONFIG_DIR="$HOME/.codex"
CONFIG_FILE="$CONFIG_DIR/Matrixapi-imagegen.env"

if [[ ! -d "$SOURCE" ]]; then
  echo "The Matrixapi-imagegen Skill directory was not found."
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 was not found. Install Python 3 and run this installer again."
  exit 1
fi

mkdir -p "$TARGET" "$CONFIG_DIR"
cp -R "$SOURCE"/. "$TARGET"/

printf "Enter your MatrixAI API key (input is hidden): "
IFS= read -r -s API_KEY
printf "\n"
if [[ -z "$API_KEY" ]]; then
  echo "An API key is required."
  exit 1
fi

umask 077
{
  printf 'IMAGEGEN_API_KEY=%s\n' "$API_KEY"
  printf '%s\n' 'IMAGEGEN_MODEL=gpt-image-2'
} > "$CONFIG_FILE"
chmod 600 "$CONFIG_FILE"

IMAGEGEN_API_KEY="$API_KEY" \
IMAGEGEN_MODEL="gpt-image-2" \
python3 "$TARGET/scripts/generate.py" --check-config

echo ""
echo "Matrixapi-imagegen was installed for Codex. Restart Codex before using it."
echo "Install location: $TARGET"
echo "API URL is fixed inside the Skill: https://matrixapii.com"
echo "Installed version: 1.8.13"
echo "Current model: gpt-image-2"
echo "Supported models: gpt-image-2, gpt-image-2-pro"
read -r -p "Press Enter to close..."
