#!/usr/bin/env bash
# khuthon 2026 - local environment setup
# Usage: chmod +x setup.sh && ./setup.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${BLUE}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
step()    { echo -e "\n${BOLD}${CYAN}> $*${RESET}"; }
divider() { echo -e "${CYAN}────────────────────────────────────────────────────────${RESET}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

find_python() {
  if command -v python >/dev/null 2>&1; then
    echo "python"
  elif command -v python3 >/dev/null 2>&1; then
    echo "python3"
  else
    return 1
  fi
}

venv_bin() {
  local exe=$1
  if [ -x "backend/.venv/Scripts/$exe.exe" ]; then
    echo "backend/.venv/Scripts/$exe.exe"
  elif [ -x "backend/.venv/Scripts/$exe" ]; then
    echo "backend/.venv/Scripts/$exe"
  else
    echo "backend/.venv/bin/$exe"
  fi
}

check_command() {
  local cmd=$1
  local install_hint=$2
  if command -v "$cmd" >/dev/null 2>&1; then
    success "$cmd $($cmd --version 2>&1 | head -1)"
  else
    error "$cmd 가 설치되어 있지 않습니다."
    echo "       설치 방법: $install_hint"
    exit 1
  fi
}

divider
echo -e "${BOLD}${CYAN}  khuthon 2026 · 로컬 환경 셋업${RESET}"
divider

step "필수 도구 확인"
PYTHON_CMD="$(find_python)" || {
  error "python 또는 python3 가 설치되어 있지 않습니다."
  echo "       설치 방법: https://www.python.org/downloads/"
  exit 1
}
if ! "$PYTHON_CMD" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info[:2] == (3, 14) else 1)
PY
then
  error "Python 3.14가 필요합니다. 현재: $($PYTHON_CMD --version)"
  echo "       현재 고정 의존성은 Python 3.14 기준으로 설치합니다."
  exit 1
fi
success "$($PYTHON_CMD --version)"
check_command git "https://git-scm.com/downloads"
check_command node "https://nodejs.org/"
check_command npm "https://nodejs.org/"

step "프로젝트 구조 확인"
REQUIRED_DIRS=("backend" "frontend")
REQUIRED_FILES=("backend/requirements.txt" "backend/main.py" "frontend/package.json")

for dir in "${REQUIRED_DIRS[@]}"; do
  if [ ! -d "$dir" ]; then
    error "필수 폴더 '$dir' 이 없습니다."
    exit 1
  fi
  success "'$dir' 폴더 확인"
done

for file in "${REQUIRED_FILES[@]}"; do
  if [ ! -f "$file" ]; then
    error "필수 파일 '$file' 이 없습니다."
    exit 1
  fi
  success "'$file' 확인"
done

step "환경변수(.env) 준비"
if [ -f ".env" ]; then
  warn ".env 파일이 이미 존재합니다. 덮어쓰지 않습니다."
elif [ -f ".env.example" ]; then
  cp .env.example .env
  info ".env.example -> .env 복사 완료"
else
  {
    echo "# Spotify API -> https://developer.spotify.com/dashboard"
    echo "SPOTIFY_CLIENT_ID="
    echo "SPOTIFY_CLIENT_SECRET="
    echo ""
    echo "# Last.fm API -> https://www.last.fm/api/account/create"
    echo "LASTFM_API_KEY="
    echo "LASTFM_API_SECRET="
    echo ""
    echo "# Optional Gemini API"
    echo "GEMINI_API_KEY="
    echo "GEMINI_MODEL=gemini-3-flash-preview"
  } > .env
  info ".env 기본 템플릿 생성 완료"
fi

set +u
source .env 2>/dev/null || true
set -u

MISSING_KEYS=()
[ -z "${LASTFM_API_KEY:-}" ] && MISSING_KEYS+=("LASTFM_API_KEY")

if [ ${#MISSING_KEYS[@]} -gt 0 ]; then
  warn "라이브 추천 호출 전에 .env에 다음 값을 채워야 합니다: ${MISSING_KEYS[*]}"
else
  success "필수 API 키 확인 완료"
fi

step "백엔드 Python 가상환경 준비"
if [ ! -d "backend/.venv" ]; then
  "$PYTHON_CMD" -m venv backend/.venv
  success "backend/.venv 생성 완료"
else
  success "backend/.venv 확인"
fi

VENV_PY="$(venv_bin python)"
VENV_PIP="$(venv_bin pip)"
"$VENV_PY" -m pip install --upgrade pip
"$VENV_PIP" install -r backend/requirements.txt
success "백엔드 의존성 설치 완료"

step "프론트엔드 React 의존성 준비"
(cd frontend && npm install)
success "React 의존성 설치 완료"

divider
echo -e "${BOLD}${GREEN}  셋업 완료${RESET}"
divider
echo -e "  ${BOLD}백엔드 실행${RESET}"
echo -e "    cd backend"
echo -e "    . .venv/Scripts/activate  # Windows Git Bash"
echo -e "    uvicorn main:app --reload --host 127.0.0.1 --port 8000"
echo ""
echo -e "  ${BOLD}프론트엔드 실행${RESET}"
echo -e "    cd frontend"
echo -e "    npm run dev"
echo ""
echo -e "  ${BOLD}헬스체크${RESET}"
echo -e "    curl http://127.0.0.1:8000/health"
divider
