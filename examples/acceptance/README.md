# 합성 문서 수용 시험

이 폴더는 실존 인물·실제 신청 정보·실제 사인을 사용하지 않습니다. test-signature.png는 TEST ONLY라고 표시한 가상 이미지입니다.

프로젝트 루트에서 순서대로 실행합니다. 출력은 Git에서 제외되는 output/acceptance에 보관합니다.

```powershell
python scripts/create_rich_document.py --input examples/acceptance/report-spec.json --output output/acceptance/report.hwpx
python scripts/create_rich_document.py --input examples/acceptance/form-spec.json --output output/acceptance/form.hwpx
python scripts/quick_fill.py --input output/acceptance/form.hwpx --output output/acceptance/filled.hwpx --data examples/acceptance/fill-data.json --rules examples/acceptance/fill-rules.json --report-json output/acceptance/fill-report.json
python scripts/validate.py output/acceptance/report.hwpx
python scripts/validate.py output/acceptance/filled.hwpx
python scripts/table_extract/extract_tables.py output/acceptance/filled.hwpx -f json -o output/acceptance/tables.json
python scripts/insert_signature_pyhwpx.py --input output/acceptance/filled.hwpx --marker '(서명 또는 인)' --dry-run
```

확인할 내용: 모든 명령이 성공하는지, 표의 가상 사용자·연락처·신청 내용이 정확한지, 원본 form.hwpx가 보존되는지, 서명 마커가 1개 검출되는지, 반복 문구가 삭제되지 않는지. JSON/XML 검사는 실제 조판 검증이 아닙니다.

## Windows + 한글 설치 환경의 별도 시험

모든 한글 문서를 저장하고 한글을 닫은 뒤, 실행 중 다른 한글을 사용하지 않는 조건으로 `--exclusive-use`를 지정합니다. 가상머신은 필수가 아닙니다. 합성 양식 1페이지와 보고서 2페이지의 실제 PDF 수용을 확인했지만, 다른 입력의 시각 검증을 대신하지 않습니다. XML 검사나 서명 dry-run으로 시각 검수를 대체하지 않습니다.

아래 단계는 한컴오피스 로그인·사용권·보안 모듈·pyhwpx 실행 환경이 준비되어야 합니다. 초기 로그인 대기를 코드 장애로 단정하지 않습니다. 인증 정보는 기록하지 않습니다. 보안 설정 복구 옵션은 자동으로 추가하지 않습니다.

```powershell
python scripts/insert_signature_pyhwpx.py --exclusive-use --input output/acceptance/filled.hwpx --output output/acceptance/signed.hwpx --signature examples/acceptance/test-signature.png --marker '(서명 또는 인)' --width-mm 32 --height-mm 8 --preserve-aspect --max-count 1
python scripts/export_document_to_pdf.py --exclusive-use --input output/acceptance/signed.hwpx --output output/acceptance/signed.pdf
python scripts/export_document_to_pdf.py --exclusive-use --input output/acceptance/report.hwpx --output output/acceptance/report.pdf
python scripts/capture_pdf_pages.py --input output/acceptance/signed.pdf --output-dir output/acceptance/signed-pages --dpi 300
python scripts/capture_pdf_pages.py --input output/acceptance/report.pdf --output-dir output/acceptance/report-pages --dpi 300
```

서명 이미지는 법적 인증서 서명이 아닙니다. 실제 PDF의 모든 페이지 이미지를 열어 내용 잘림·겹침·표 넘침·서명 위치/비율·불필요한 빈 페이지를 검수하고, 페이지 수와 검수한 페이지 수를 별도로 기록합니다. 이미지 렌더링 성공만으로 시각 검수를 완료했다고 하지 않습니다.

일반 unittest는 이 Windows 실기 단계나 화면낭독기 사용자 수용을 대체하지 않습니다.
