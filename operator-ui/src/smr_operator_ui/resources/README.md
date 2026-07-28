# resources

애플리케이션 실행 중 필요한 이미지와 글꼴 같은 정적 리소스를 관리하는 폴더다. Python 패키지 리소스로 포함되므로 소스 실행과 PyInstaller EXE 실행에서 같은 방식으로 접근할 수 있다.

## 포함 파일

- `amr-cobot.png`
  - 메인 화면의 원주 검사 위젯에서 로봇 위치를 표시할 때 사용한다.
- `malgun.ttf`
  - 애플리케이션 시작 시 등록하는 한글 표시용 글꼴이다.
- `__init__.py`
  - 이 폴더를 Python 리소스 패키지로 인식하게 한다.
- `.gitkeep`
  - 리소스가 없는 초기 상태에서도 폴더를 Git에 유지하기 위한 파일이다.

## 사용 방법

리소스 경로는 현재 작업 디렉터리를 기준으로 조합하지 않고 `importlib.resources`를 사용한다.

```python
from importlib.resources import files

image_path = files("smr_operator_ui.resources").joinpath("amr-cobot.png")
```

## 작성 규칙

- UI에서 사용하는 원본 이미지, 아이콘, 글꼴만 저장한다.
- 빌드 결과물이나 사용 중 생성되는 데이터 파일은 저장하지 않는다.
- 파일명을 변경하면 해당 리소스를 참조하는 코드와 `pyproject.toml`의 패키지 데이터 설정을 함께 확인한다.
- 대용량 파일을 추가하기 전에 EXE 용량과 로딩 시간을 검토한다.

