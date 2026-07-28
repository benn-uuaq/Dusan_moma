# styles

애플리케이션 전체에서 사용하는 색상, 글꼴, 여백, 상태별 표현을 중앙 관리하는 폴더다. 화면과 위젯에 반복적인 인라인 스타일을 작성하지 않도록 공통 디자인 기준을 제공한다.

## 포함 파일

- `tokens.py`
  - 브랜드 색상, 상태 색상, 배경색 등 Python 코드에서 사용할 디자인 토큰을 정의한다.
- `app.qss`
  - 버튼, 상단 바, 카드, 상태 텍스트, 표, 설정 화면, 터치 입력창의 공통 Qt 스타일을 정의한다.
- `__init__.py`
  - 패키지에 포함된 `app.qss`를 읽어 반환하는 `load_stylesheet()`를 제공한다.

## 적용 흐름

`create_application()`에서 글꼴을 등록한 후 `load_stylesheet()`로 QSS를 읽어 `QApplication` 전체에 적용한다.

```python
app.setStyleSheet(load_stylesheet())
```

위젯은 `objectName`이나 동적 속성을 사용해 필요한 스타일을 선택한다.

```python
label.setObjectName("StatusGood")
step.setProperty("active", True)
```

## 작성 규칙

- 공통 색상은 `tokens.py`에 정의하고 의미가 드러나는 이름을 사용한다.
- 반복되는 위젯 스타일은 `app.qss`에서 관리한다.
- 상태는 색상만으로 구분하지 않고 텍스트나 형태를 함께 사용한다.
- 새 `objectName`을 추가할 때 코드와 QSS의 이름이 일치하는지 확인한다.
- 터치 버튼과 입력 영역은 프로젝트의 최소 크기 기준을 유지한다.

