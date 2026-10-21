# 설치·사용 예 / Setup and usage

아래 명령은 스킬 루트 기준이며 PowerShell, bash에서 사용할 수 있도록 `/` 경로를 사용합니다. 기존 파일과 겹치지 않는 출력 경로를 선택하세요. `python`은 활성화한 가상환경의 실행 파일이어야 합니다.

## XML 우선

```sh
python -m venv .venv
# 환경 활성화 후 / after activation:
python -m pip install "lxml>=4.9"
python scripts/create_rich_document.py --input examples/example_rich_document.json --output result.hwpx
python scripts/validate.py result.hwpx
python scripts/analyze_template.py result.hwpx --json
python scripts/table_extract/extract_tables.py result.hwpx -f json
```

위 JSON 예제는 저장소의 예시 입력입니다. 예제·템플릿의 원본 권리 확인 상태는 [PROVENANCE](PROVENANCE.md)를 따릅니다. 생성 시 `templates/base` 등 런타임 자원을 유지해야 합니다. 새 데이터 형식은 [rich-document-spec](../references/rich-document-spec.md), 양식 규칙은 [fixed-form-fill](../references/fixed-form-fill.md)를 참고하세요. `--help`로 각 CLI의 현재 인자를 확인하고 원본 XML/추출 결과와 함께 분석합니다.

## Windows 선택 경로

한글 Office 자체는 포함하지 않습니다. 합법적으로 설치된 한글과 pyhwpx/보안 모듈 구성이 필요합니다. `PYHWPX_PYTHON`을 사용하는 경우 현재 환경에서 검증한 python.exe의 정확한 경로로 지정합니다. 명시한 인터프리터가 실패하면 패키지 자동 설치나 임의 다른 환경으로의 우회가 해결책이 아닙니다.

```sh
python -m pip install -r requirements.txt
python scripts/export_document_to_pdf.py --diagnose
python scripts/insert_signature_pyhwpx.py --input form.hwpx --marker "(서명 또는 인)" --dry-run
```

`form.hwpx`는 본인이 승인한 합성 양식 사본으로 바꾸세요. dry-run은 마커 확인이며 서명 삽입 완료가 아닙니다. 진단 결과에서 COM/보안 구성이 확인되고 별도 작업 승인이 있을 때만 실제 서명·변환을 진행합니다. 이미지 삽입 예:

```sh
python scripts/insert_signature_pyhwpx.py --input form.hwpx --output signed.hwpx --signature fictional-signature.png --marker "(서명 또는 인)" --width-mm 24 --height-mm 8 --preserve-aspect --max-count 1
python scripts/validate.py signed.hwpx
python scripts/export_document_to_pdf.py --input signed.hwpx --output signed.pdf
python scripts/capture_pdf_pages.py --input signed.pdf --output-dir signed_pages --dpi 300
```

위 파일명은 자리표시자이며 저장소에 실제 사용자 서명이 있다는 뜻이 아닙니다. 가상 서명만 사용하세요. 이미지 생성 후 **전체 페이지를 실제로 열어** 잘림·넘침·겹침·빈 페이지·서명 위치/비율을 확인합니다. 작업 후 추가 수정이 있으면 최신본으로 다시 검수합니다.

기본 경로에서는 레지스트리를 쓰지 않습니다. 읽기 전용 진단이 실패하면 그 단계와 이유를 보고하고 중단합니다. `--repair-security-module`은 일반 설치 단계가 아니며 별도 명시 승인·백업/복원 확인 없이 실행하지 않습니다. [보안 정책](../SECURITY.md)을 따릅니다.

## 결과 보고

입력/출력 경로, 원본 보존 여부, 실행 명령과 exit code, 구조 검사, 내용 대조, 실제 PDF 페이지 수와 열람한 페이지 수, 실패/skip/미검증을 구분합니다. 구조 통과나 렌더링 파일 생성만으로 시각 검수 완료를 선언하지 않습니다.
