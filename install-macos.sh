#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$SCRIPT_DIR/skills/Matrixapi-imagegen"
SKILLS_ROOT="$HOME/.codex/skills"
TARGET="$SKILLS_ROOT/Matrixapi-imagegen"
LEGACY="$SKILLS_ROOT/api-imagegen"
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

mkdir -p "$SKILLS_ROOT" "$CONFIG_DIR"
INSTALL_ID="$$-$(date +%s)"
STAGE="$SKILLS_ROOT/.Matrixapi-imagegen.install-$INSTALL_ID"
BACKUP="$HOME/.codex/.Matrixapi-imagegen.backup-$INSTALL_ID"
mkdir "$STAGE"
cp -R "$SOURCE"/. "$STAGE"/

MOVED_OLD=0
if [[ -d "$TARGET" ]]; then
  mv "$TARGET" "$BACKUP"
  MOVED_OLD=1
fi
if ! mv "$STAGE" "$TARGET" || [[ ! -f "$TARGET/scripts/generate.py" ]]; then
  rm -rf "$TARGET" "$STAGE"
  if [[ "$MOVED_OLD" -eq 1 && -d "$BACKUP" ]]; then
    mv "$BACKUP" "$TARGET"
  fi
  echo "The replacement Skill failed validation."
  exit 1
fi
rm -rf "$BACKUP"

if [[ -d "$LEGACY" ]]; then
  if [[ -f "$LEGACY/SKILL.md" && -f "$LEGACY/scripts/generate.py" ]] \
    && grep -Eq 'name:[[:space:]]*api-imagegen' "$LEGACY/SKILL.md" \
    && grep -Fq 'api-imagegen-skill/' "$LEGACY/scripts/generate.py"; then
    rm -rf "$LEGACY"
  else
    echo "Warning: the existing api-imagegen directory is not a recognized legacy Skill and was left unchanged."
  fi
fi

printf "Enter your MatrixAI API key (input is hidden): "
IFS= read -r -s API_KEY
printf "\n"
if [[ -z "$API_KEY" ]]; then
  echo "An API key is required."
  exit 1
fi

umask 077
{
  printf '%s\n' 'IMAGEGEN_BASE_URL=https://eos.manyuvip.com'
  printf 'IMAGEGEN_API_KEY=%s\n' "$API_KEY"
  printf '%s\n' 'IMAGEGEN_MODEL=gpt-image-2'
} > "$CONFIG_FILE"
chmod 600 "$CONFIG_FILE"

IMAGEGEN_BASE_URL="https://eos.manyuvip.com" \
IMAGEGEN_API_KEY="$API_KEY" \
IMAGEGEN_MODEL="gpt-image-2" \
python3 "$TARGET/scripts/generate.py" --check-config

echo ""
echo "Matrixapi-imagegen was installed for Codex. Restart Codex before using it."
echo "Install location: $TARGET"
echo "Supported models: gpt-image-2, gpt-image-2-pro. Current model: gpt-image-2"
echo "The Skill accepts only https://eos.manyuvip.com."
read -r -p "Press Enter to close..."
