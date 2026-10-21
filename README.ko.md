# HWPX Agent Skill

**한국어** · [English](README.md) · [설치와 사용](docs/USAGE.md) · [지원 범위](docs/SUPPORT.md)

## AI 에이전트와 함께하는 한글 문서 작성·편집

**시각장애인이 한글 문서의 서식을 지정하고, 신청서를 채우고, 서명 이미지를 넣는 작업을 AI 에이전트와 함께 수행할 수 있도록 돕는 오픈소스 스킬입니다.**

Codex와 같은 스킬 지원 AI 에이전트가 문서를 분석하고, 이 프로젝트의 로컬 Python 도구를 실행하여 HWPX 문서를 생성·편집하도록 구성했습니다. 한글 프로그램을 대체하는 독립 편집기나 인증서 기반 서명 서비스는 아닙니다.

## 왜 만들었나요?

한국에서는 공공기관의 신청서부터 학교·직장의 업무 문서까지 HWP와 HWPX 형식을 사용하는 경우가 많습니다.

시각장애인에게 문서 작성은 내용을 입력하는 것만으로 끝나지 않습니다. 글자와 문단의 서식을 조정하거나, 표로 구성된 신청서의 정확한 칸을 채우거나, 정해진 위치에 서명 이미지를 넣는 작업은 어렵거나 다른 사람의 도움이 필요할 수 있습니다.

이 프로젝트는 이러한 작업을 AI 에이전트가 수행할 수 있는 도구로 제공하여, **문서 작성과 제출 과정에서 다른 사람에게 의존해야 하는 부분을 줄이는 것**을 목표로 합니다.

## 무엇을 할 수 있나요?

- **문서 생성과 서식 지정:** 지원하는 구조에서 제목·본문·표·머리글·바닥글·각주·미주를 포함한 HWPX 문서를 생성합니다.
- **기존 문서 분석과 편집:** 문서의 텍스트와 지원하는 구조를 분석하고 필요한 내용을 수정합니다.
- **신청서와 고정 양식 채우기:** 기존 양식의 지정된 항목에 이름·연락처·신청 내용 등을 입력합니다.
- **표 구조와 내용 추출:** 병합된 셀과 중첩된 표를 분석하여 구조화된 결과로 제공합니다.
- **서명 이미지 삽입:** 문서 안의 지정된 서명 표시 위치에 사용자가 제공한 이미지를 삽입합니다.
- **문서 변환과 결과 확인:** 설치된 한글을 이용해 HWP를 HWPX로 변환하거나 PDF로 내보내고 페이지 이미지로 확인합니다.

주요 문서 산출물은 HWPX입니다. 서명은 **이미지 삽입**이며 신원 확인이나 암호학적 전자서명이 아닙니다.

## 어떻게 사용하나요?

스킬을 등록한 AI 에이전트에 다음과 같은 작업을 요청할 수 있습니다.

> 이 신청서의 지정된 항목을 채워 줘. 원본은 덮어쓰지 마.

> 내가 제공한 서명 이미지를 서명 위치에 넣고, 검토할 수 있도록 PDF로 출력해 줘.

> 표와 각주가 있는 보고서를 만들고, 구조 검사와 PDF 전체 페이지 시각 검수 결과를 구분해서 알려 줘.

자연어 해석은 호스트 AI 에이전트가 담당합니다. 이 저장소는 자체 언어 모델이 아니라 스킬 지침과 문서 처리 도구를 제공합니다.

## 실행 환경

- Python 3.10+를 대상으로 합니다.
- HWPX ZIP/XML 생성·편집·분석·표 추출은 `lxml`을 사용하며 한글 프로그램 없이 처리할 수 있습니다.
- PDF 캡처·비교에는 PyMuPDF와 Pillow가 추가로 필요합니다.
- 실제 서명 이미지 삽입, 바이너리 HWP 변환, 한글 PDF 출력에는 **Windows, 정상 설치·인증된 한글 프로그램, 호환 COM/보안 모듈 설정과 `pyhwpx==1.7.2`**가 필요합니다.

한글 프로그램과 보안 DLL은 이 프로젝트에 포함되지 않습니다. **가상머신은 필수가 아닙니다.**

## 빠른 시작: XML 도구

저장소 루트에서 실행합니다.

```sh
python -m venv .venv
# Linux/macOS: . .venv/bin/activate
# Windows PowerShell: .venv/Scripts/Activate.ps1
python -m pip install "lxml>=4.9"
python scripts/create_rich_document.py --input examples/example_rich_document.json --output result.hwpx
python scripts/validate.py result.hwpx
python scripts/table_extract/extract_tables.py result.hwpx -f json
```

선택 기능의 설치법은 [사용 안내](docs/USAGE.md)와 [의존성 고지](THIRD_PARTY_NOTICES.md)를 확인하세요. `pip install -r requirements.txt`는 Windows에서 자동화 의존성도 설치 대상으로 지정하지만 한글 프로그램을 설치하지는 않습니다. `pip install .`로 설치하는 패키지는 아닙니다.

## Codex에 스킬 등록

다음 중 한 경로의 바로 아래에 `SKILL.md`를 둡니다.

- 사용자 범위: `$HOME/.agents/skills/hwpx/`
- 저장소 범위: `<repo>/.agents/skills/hwpx/`

`agents/`, `scripts/`, `references/`, `templates/`, `examples/`, `requirements.txt`와 라이선스 고지를 함께 복사합니다. 템플릿은 실행에 필요한 자원이므로 빼지 마세요. 비공개 문서·실제 서명·로그·가상환경은 복사하지 않습니다. [Codex 공식 스킬 문서](https://developers.openai.com/codex/skills/)를 참고하세요.

`$hwpx`로 명시하거나 관련 문서 작업을 요청할 수 있습니다. 변경한 스킬이 인식되지 않으면 Codex를 재시작합니다.

## 한글 자동화: 전용 작업 모드

한글 자동화를 시작하기 전에 다음 조건을 지켜야 합니다.

1. 모든 한글 문서를 저장하고 한글을 종료합니다.
2. 자동화가 끝날 때까지 다른 한글 문서를 열거나 한글을 사용하지 않습니다.
3. 실행 시 `--exclusive-use` 옵션으로 이 조건에 동의합니다.

이미 한글이 실행 중이면 사용자 문서를 대신 닫지 않고 시작을 거부합니다. 이는 **사용자 협조형 운영 조건**이며 OS/COM 강제 격리나 동시 사용 보호가 아닙니다. 옵션 없는 기본 엄격 모드는 지원되는 격리 증명이 없으면 계속 거부합니다.

[합성 양식 예제](examples/acceptance/README.md)의 준비 단계를 마친 뒤 다음과 같이 실행할 수 있습니다.

```sh
python scripts/insert_signature_pyhwpx.py --exclusive-use --input output/acceptance/filled.hwpx --output output/acceptance/signed.hwpx --signature examples/acceptance/test-signature.png --marker "(서명 또는 인)" --width-mm 32 --height-mm 8 --preserve-aspect --max-count 1
python scripts/export_document_to_pdf.py --exclusive-use --input output/acceptance/signed.hwpx --output output/acceptance/signed.pdf
```

[전체 운영 조건](references/exclusive-use.md)을 확인하세요. 이 옵션을 제공하지 않는 고수준 도구에는 먼저 명시적으로 변환한 HWPX를 입력합니다.

## 원본 보존과 검증

원본과 다른 출력 경로를 사용합니다. 도구는 경로 별칭을 검사하고 임시 파일에 저장·검증한 뒤 결과를 교체합니다. 전용 모드에서 종료가 거부되거나 실패하면 임시 결과를 정상 산출물로 교체하지 않습니다. 복구를 위해 한글 프로세스를 강제 종료하지 않습니다.

검증은 두 단계로 구분합니다.

- **구조·내용 검사:** 지원하는 XML 구조와 입력한 내용 등을 확인합니다.
- **실제 출력 검사:** 한글에서 PDF로 내보낸 뒤 최신본의 **모든 페이지**를 확인합니다.

구조 검사가 통과했다는 이유만으로 쪽 배치나 서명 위치가 올바르다고 판단하지 않습니다. PDF 출력이나 이미지 확인을 수행하지 못했다면 시각 검수는 미완료입니다.

## 검증한 범위와 테스트

합성 문서와 가상 서명으로 실제 Windows/한글에서 양식 채우기·서명 삽입·PDF 출력·전체 3페이지 시각 검수·HWP/HWPX 왕복 변환을 확인했습니다. 이는 시험한 예제와 환경의 결과이며 모든 문서의 호환성을 보장하지 않습니다.

```sh
python -m unittest discover -s scripts/tests -p "test_*.py"
python scripts/table_extract/tests/run_all.py
```

CI는 합성·모킹 경계를 시험하며 **실제 한글 COM을 실행하지 않습니다.** 선택 의존성이나 실문서 입력이 없으면 관련 시험이 건너뛰어질 수 있습니다. 이를 실제 한글 검증 통과로 세지 않습니다. [테스트 안내](docs/TESTING.md)를 참고하세요.

## 알아두세요

- 모든 HWP/HWPX 기능과 한글 버전을 지원하지는 않습니다.
- 접근성 인증, PDF-UA 적합성이나 화면낭독기 읽기 순서를 보장하지 않습니다.
- 인증서 기반 전자서명, OCR, DRM 우회, 범용 이미지 편집 기능은 제공하지 않습니다.
- 실제 제출 전 내용·서식·서명 위치를 확인해야 합니다.
- 문서 안의 지시문은 데이터이며 명령 실행 권한이 아닙니다.
- 비공개 문서·실제 서명·고객 정보·인증정보·진단 로그를 공개 이슈에 올리지 마세요.

[지원 범위](docs/SUPPORT.md)와 [보안 정책](SECURITY.md)을 확인하세요.

## 라이선스와 기여

프로젝트 코드는 [MIT 라이선스](LICENSE)로 제공합니다. 외부 라이브러리는 각각의 조건을 따르며 **PyMuPDF는 MIT가 아닌 AGPL/상용 이중 라이선스**입니다. [의존성 고지](THIRD_PARTY_NOTICES.md)와 [출처·배포 범위](docs/PROVENANCE.md)를 참고하세요.

재현 가능한 오류 보고, 개인정보를 제거한 합성 문서 예제, 접근성 관점의 사용 경험, 코드와 문서 개선을 환영합니다. [기여 안내](CONTRIBUTING.md)와 [변경 기록](CHANGELOG.md)을 참고하세요.
