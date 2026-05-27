### Pre-commit 설정 방법
commit하기 전에 코드 검사하는 tool.
현재 프로젝트에서는 breakpoint()같이 디버깅 코드가 올라가는게 싫어서 검사하는 용도로 사용
`poetry install` 이후에 `poetry run pre-commit install`만 쳐주기
디버킹 코드가 있는 경우는 커밋이 강제 취소된다!!

### Poetry 설정 방법

pip로 하는 것 보다 의존성 관리가 편해서 poetry로 사용하길 권장.

---

#### 1. Poetry 설치

🍏 macOS / 🐧 Linux (zsh, bash)
```bash
curl -sSL https://install.python-poetry.org | python3 -
```


> 💡 **설치 확인**: 터미널을 완전히 **종료 후 다시 열어** 아래 명령어가 잘 작동하는지 확인합니다.
> ```bash
> poetry --version
> ```
> *만약 명령어를 찾을 수 없다고 나온다면 환경 변수(PATH)에 Poetry 설치 경로(`$HOME/.local/bin` 또는 `%USERPROFILE%\AppData\Roaming\Python\Scripts`)를 추가해야 합니다.*

---

#### ⚙️ 2. 전역 설정 (초기 1회 필수)

프로젝트 루트 폴더(또는 어디서든)에서 아래 설정을 실행합니다. 이 설정은 가상환경(`.venv`)을 각 프로젝트 폴더 내부에 생성하도록 강제하여, VS Code나 PyCharm 같은 IDE가 가상환경을 자동으로 인식할 수 있게 해줍니다.

```bash
poetry config virtualenvs.in-project true
```

---

#### 📦 3. 의존성 설치 및 가상환경 빌드

백엔드 폴더(`backend/`)로 이동한 후 `poetry install`을 실행하면 `pyproject.toml`과 `poetry.lock` 파일을 기반으로 정확한 버전의 가상환경이 자동으로 구축됩니다.

```bash
# 1. 백엔드 디렉토리로 이동
cd backend

# 2. 패키지 설치 및 가상환경 생성 (.venv 생성됨)
poetry install
```

---

#### ➕ 4. 개발 중 새로운 패키지 추가하기

작업 중 새로운 외부 라이브러리 설치가 필요할 때는 `pip install` 대신 반드시 **`poetry add`**를 사용해야 `pyproject.toml`과 `poetry.lock`이 함께 업데이트되어 다른 팀원들에게도 공유됩니다.

```bash
# 일반 패키지 추가 (예: 패키지명 입력)
poetry add 패키지이름

# ⚠️ [zsh 주의] 대괄호([])가 포함된 패키지(예: uvicorn[standard]) 설치 시 반드시 따옴표로 감싸기
poetry add "uvicorn[standard]"

# 개발용 패키지 추가 (테스트 툴, 린터 등)
poetry add pytest --group dev
```

---

#### 🤝 5. 협업 시 주의사항 (Git)

1. **`poetry.lock` 파일은 반드시 Git에 커밋**해야 합니다. 이 파일이 있어야 모든 팀원이 소수점 자리까지 완벽히 일치하는 동일한 환경에서 버그 없이 개발할 수 있습니다.
2. 다른 팀원이 패키지를 추가하여 내가 레포지토리를 `git pull` 받았을 때는, `backend/` 폴더에서 다시 한번 **`poetry install`**만 입력해 주면 새 패키지가 내 로컬 가상환경에 즉시 반영됩니다.

### Ruff 설정 방법

python formatter인데 원래 isort+black으로 했었는데 poetry랑 연동 + 요즘 ruff가 좋다는 것 같아서 추가해봤음. ruff 세팅은(최대 줄 길이, 린터 등등..)은 project.toml에서 하면 됨. 기본적인건 추가해서 그냥 쓰면 될듯

#### 설치방법
1. extension에서 ruff 설치
2. poetry init해서 ruff install
3. setting.json에 추가
```json
{
    "[python]": {
        "editor.formatOnSave": true,
        "editor.defaultFormatter": "charliermarsh.ruff",
        "editor.codeActionsOnSave": {
            "source.fixAll": "explicit",
            "source.organizeImports": "explicit"
        }
    },
    "ruff.importStrategy": "fromEnvironment"
}
```
4. ctrl + s로 formatting 되는지 확인
5. 전체 파일에 formatting 적용하고 싶은 경우에는
`poetry run ruff check --fix . && poetry run ruff format .`
> ruff format은 코드 스타일 전체 적용 / check --fux는 immport 순서 수정 및 논리적 오류 수정