---
name: hwpx
description: "한글(HWPX) 문서 생성·읽기·편집·고정양식 채우기·표 추출 스킬. .hwpx/.hwp, Hancom, OWPML, 셀 병합, 중첩표, 글상자, 서명, PDF 검증 요청에 사용."
---

# HWPX 문서 작업

HWPX는 ZIP/XML 구조를 직접 다뤄 서식과 문서 구조를 보존한다. HWP 입력은 필요한 경우에만 Windows의 Hancom Office와 `pyhwpx`로 HWPX로 변환한다.

## 현재 후보의 실행 경계

ZIP/XML 생성·편집·추출과 기존 PDF 도구를 native 자동화와 구분한다. 옵션 없는 엄격 모드는 독점 격리 증명이 없으면 COM 생성을 거부한다. 현재 PC의 native 자동화에는 사용자가 모든 한글을 저장·닫고 자동화 중 한글을 사용하지 않는 조건에 동의한 경우에만 `--exclusive-use`를 지정한다. 기존 한글이 있으면 사용자를 대신해 닫거나 강제 종료하지 말고 중단한다. 이 모드는 사용자 협조 정책이며 OS 독점 격리 증명이 아니다. native adapter는 `pyhwpx==1.7.2`로 고정한다. [전용 모드](references/exclusive-use.md)를 따른다.

실제 시각 검증이 필요하지만 native 경로를 사용할 수 없으면 구조 검사 결과와 미완료 시각 검증을 분리해 보고한다. 서명 dry-run은 마커 탐지만 증명한다. 사용자 원본·보고서·이미지의 경로 별칭 충돌을 첫 쓰기 전에 검사하고 원본을 직접 수정하지 않는다. 로그인이 풀려 생긴 대기를 코드 결함으로 단정하지 않는다.

## 작업 원칙

1. 입력과 출력 형식을 먼저 확인한다.
2. 기존 문서는 XML-first 방식으로 분석하고 필요한 내용만 바꾼다.
3. 레퍼런스가 있으면 스타일 ID, 표 구조, 구역 설정, 페이지 수를 보존한다.
4. 새 문서는 구조화한 JSON과 `create_rich_document.py`로 만든다.
5. 문서를 생성하거나 편집한 뒤에는 매번 구조 검증과 전체 페이지 이미지 시각 검증을 모두 필수로 수행한다. 양식 채우기, 텍스트 교체, 서명 삽입도 포함한다.
6. Hancom COM 자동화는 직렬로 실행한다.

## 기존 HWPX 분석과 편집

다음 순서로 작업한다.

1. `scripts/analyze_template.py --json`으로 구조를 파악한다.
2. 단순 텍스트 교체는 `scripts/edit_section.py`를 사용한다.
3. 구조 편집은 `scripts/office/unpack.py`로 푼 뒤 XML을 수정하고 `scripts/office/pack.py`로 묶는다.
4. 레퍼런스 복원은 `scripts/analyze_template.py`로 `header.xml`과 `section0.xml`을 추출하고 `scripts/build_hwpx.py`로 패키징한다.
5. `scripts/validate.py`를 실행한다.
6. 레퍼런스가 있으면 `scripts/page_guard.py`로 페이지 드리프트도 확인한다.

사용자 요청 없이 `hp:p`, `hp:tbl`, `rowCnt`, `colCnt`, `pageBreak`, `secPr`를 바꾸지 않는다. 포맷 세부사항은 [references/hwpx-format.md](references/hwpx-format.md)를 따른다.

## 고정 양식 채우기

지정 필드만 채울 때 다음 도구를 사용한다.

- 스키마 추출: `scripts/extract_fill_schema.py`
- 빠른 채우기: `scripts/quick_fill.py`
- 규칙 기반 채우기: `scripts/fill_fixed_form.py`
- HWP 변환: `scripts/convert_hwp_to_hwpx.py`

HWP 입력은 임시 HWPX로 변환하고 결과는 HWPX로 저장한다. 필드 규약은 [references/fixed-form-fill.md](references/fixed-form-fill.md)를 따른다.

## 새 문서 생성

JSON 입력을 만든 뒤 다음을 실행한다.

```powershell
python .\scripts\create_rich_document.py --input .\content.json --output .\result.hwpx
```

글자·문단 스타일, 표와 셀 병합, 페이지 설정, 머리글·바닥글, 각주·미주를 지원한다. 입력 명세는 [references/rich-document-spec.md](references/rich-document-spec.md)를 따른다.

## 표 추출

표 구조가 필요하면 `scripts/table_extract/extract_tables.py`를 사용한다. HWPX는 XML로 직접 읽고, HWP는 임시 HWPX로 변환한다. `grid`, `markdown`, `json`, `matrix`, `validate`, `shapes` 출력을 선택할 수 있다.

```powershell
python .\scripts\table_extract\extract_tables.py .\input\document.hwpx -f json
```

플랫 텍스트만 필요하면 `scripts/text_extract.py`를 사용한다.

## 서명 삽입

HWPX 양식의 서명 마커에는 Windows, Hancom Office, `pyhwpx`와 `scripts/insert_signature_pyhwpx.py`를 사용한다. 도구는 마커를 검색한 뒤 Hancom의 `insert_picture(..., embedded=True)`로 서명 이미지를 삽입한다. 일반 이미지나 임의 위치 그림 삽입 도구가 아니다. 이미지 형식·손상 여부와 양수 크기를 COM 실행 전에 확인한다. 긴 서명은 `--preserve-aspect`로 지정한 너비·높이 안에 원래 비율대로 맞춘다. 기존 크기 동작은 기본값으로 유지한다. XML 또는 실제 한글 검색에서 마커가 없거나 그림 삽입이 실패하면 성공으로 처리하지 않는다.

자동화 전에 새 Hwp 프로세스의 창만 숨기는 guard를 먼저 활성화한다. 기존 사용자 Hwp 프로세스와 창은 건드리지 않는다. guard 또는 FilePathChecker 활성화에 실패하면 문서를 열지 않고 중단한다. `--dry-run`은 HWPX XML에서 마커만 세며 Hancom, COM, guard를 실행하지 않고 `--output`을 생략할 수 있다. 출력은 같은 폴더의 임시 HWPX로 저장·검증한 뒤 원자적으로 교체한다.

마커 문구는 실제 문서에서 확인한 문자열로 지정한다. 아래 경로는 작업 파일에 맞춰 바꾼다.

```powershell
python .\scripts\insert_signature_pyhwpx.py --input .\input\form.hwpx --marker '(서명 또는 인)' --dry-run
python .\scripts\insert_signature_pyhwpx.py --input .\input\form.hwpx --output .\output\signed.hwpx --signature .\input\signature.png --marker '(서명 또는 인)' --width-mm 24 --height-mm 8 --preserve-aspect --max-count 1
python .\scripts\validate.py .\output\signed.hwpx
python .\scripts\export_document_to_pdf.py --input .\output\signed.hwpx --output .\output\signed.pdf
python .\scripts\capture_pdf_pages.py --input .\output\signed.pdf --output-dir .\output\signed_pages --dpi 300
```

이미지 생성 후에는 전체 페이지 이미지를 실제로 열어 서명 위치·비율과 문서 배치를 확인한다.

## PDF 내보내기와 시각 검증

`scripts/export_document_to_pdf.py`는 새 숨김 Hancom 세션에서 PDF를 만든다. 다른 PC에서는 먼저 `--diagnose`로 Python 패키지, 두 레지스트리 뷰의 한글 COM 등록, 실행 파일과 보안 DLL 아키텍처를 읽기 전용으로 확인한다. 실제 변환은 임시 PDF를 열어 전체 페이지를 읽을 수 있는지 확인한 뒤 출력 파일을 교체한다. 실패 시 기존 PDF를 보존한다. 환경·COM 시작·보안 활성화·문서 열기·PDF 저장·PDF 검증의 실패 단계를 보고하고 보안 복구가 필요하면 사용자에게 진단 내용을 먼저 제시한다. 기본 실행은 레지스트리를 변경하지 않는다. FilePathChecker 활성화가 실패하면 문서를 열지 않고 중단한다.

현재 사용자 HKCU의 두 모듈 값 복구를 명시적으로 승인할 때만 `--repair-security-module`을 추가한다. 이 옵션은 변경 전에 `%LOCALAPPDATA%\hwpx-skill\backups`에 최소 복원 정보를 저장하고, 쓰기 중 일부가 실패하면 자동으로 원상 복구한다. HKLM, 시스템 정책, 관리자 권한, 네트워크 다운로드는 사용하지 않는다.

```powershell
python .\scripts\export_document_to_pdf.py --diagnose
python .\scripts\export_document_to_pdf.py --input .\input\source.hwpx --output .\output\source.pdf
python .\scripts\export_document_to_pdf.py --repair-security-module --input .\input\source.hwpx --output .\output\source.pdf
python .\scripts\export_document_to_pdf.py --restore-security-backup .\backup.json
```

PDF 페이지 이미지는 `scripts/capture_pdf_pages.py`, 두 PDF 비교는 `scripts/compare_rendered_pdfs.py`를 사용한다. 런타임과 보안 동작은 [references/runtime-and-security.md](references/runtime-and-security.md)를 따른다.

문서를 생성하거나 편집한 뒤에는 작업 규모나 시각 품질의 중요도와 관계없이 다음 검수를 반드시 수행한다.

1. 저장한 최신 HWPX를 PDF로 내보낸다.
2. PDF의 전체 페이지를 300 DPI PNG 이미지로 렌더링한다. 일부 페이지만 선택하거나 이전 작업의 이미지를 재사용하지 않는다.
3. 생성한 모든 페이지 이미지를 실제로 열어 글자와 내용의 잘림, 표·셀의 넘침, 글자·표·이미지의 겹침, 여백·정렬·페이지 배치, 의도하지 않은 빈 페이지를 확인한다. 이미지 생성 성공이나 XML 검증만으로 시각 검수를 대신하지 않는다.
4. 문제가 있으면 HWPX를 수정하고, 수정한 최신본의 구조 검증과 PDF 내보내기·전체 페이지 렌더링·이미지 검수를 다시 수행한다. 최종 저장 후 추가로 수정했으면 다시 검수한다.
5. 최종 보고에 구조 검증 결과, 전체 페이지 수와 실제 이미지 검수 페이지 수, 발견한 문제와 수정 결과, 남은 제한사항을 명시한다.

```powershell
python .\scripts\capture_pdf_pages.py --input .\output\source.pdf --output-dir .\output\source_pages --dpi 300
```

PDF 변환, 이미지 렌더링 또는 이미지 열람을 할 수 없으면 시각 검증을 완료했다고 보고하지 않는다. 가능한 구조 검증 결과와 함께 시각 검증 미완료 사실, 실패 단계와 이유를 명시한다. 검수를 위해 보안 설정을 임의로 변경하지 않는다.

## 완료 확인

- `scripts/validate.py`로 HWPX 구조를 검증한다.
- 레퍼런스가 있으면 `scripts/page_guard.py`로 페이지 수와 배치를 확인한다.
- 표 작업은 추출 결과와 `validate` 출력을 확인한다.
- 생성·편집한 최신 문서는 예외 없이 위의 필수 이미지 검수 절차를 수행한다.
- 최종 PDF의 전체 페이지를 300 DPI PNG로 렌더링하고 모든 이미지를 실제로 확인했는지 보고한다.
- 변환·렌더링·이미지 열람을 할 수 없으면 시각 검증 미완료와 이유를 명시하고 검증 완료로 처리하지 않는다.
