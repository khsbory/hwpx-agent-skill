# 한글 전용 작업 모드 / Cooperative exclusive-use mode

## 목적과 전제

가상머신을 필수로 요구하지 않고 현재 Windows PC의 설치·구독된 한글로 문서를 처리하는 모드입니다. 합성 양식의 실제 서명 삽입·PDF 출력, 전체 3페이지 시각 검수와 HWP/HWPX 왕복 변환을 확인했습니다. 모든 문서/버전에 대한 보장이 아니며 지원 범위는 README를 따릅니다.

실행 전에 모든 한글 문서를 저장하고 한글을 종료해야 합니다. 자동화가 끝날 때까지 다른 한글 문서를 열지 않습니다. 이 약속은 사용자 협조 조건이지 Windows/COM이 제공하는 강제 격리가 아닙니다. 다른 작업과 동시에 한글을 사용하는 용도로 지원하지 않습니다.

## 명시적 동의

실제 HWP 변환·서명 삽입·PDF 내보내기 CLI에 `--exclusive-use`를 지정합니다. 옵션 없이 실행하는 기존 엄격 모드는 독점 격리 증명이 없는 환경에서 여전히 거부할 수 있습니다. 숨은 환경 변수로 동의를 대신하지 않습니다.

```powershell
python scripts/insert_signature_pyhwpx.py --exclusive-use --input output/acceptance/filled.hwpx --output output/acceptance/signed.hwpx --signature examples/acceptance/test-signature.png --marker '(서명 또는 인)' --width-mm 32 --height-mm 8 --preserve-aspect --max-count 1
python scripts/export_document_to_pdf.py --exclusive-use --input output/acceptance/signed.hwpx --output output/acceptance/signed.pdf
python scripts/convert_hwp_to_hwpx.py --exclusive-use --input source.hwp --output source.hwpx
```

현재 코드의 `--help`와 실제 수용 범위를 함께 확인합니다. 로그인·사용권·호환 보안 모듈이 준비되어 있어야 합니다. 보안 모듈 등록 실패를 이 옵션으로 무시하지 않으며 레지스트리를 자동 복구하지 않습니다. 고수준 도구가 이 옵션을 받지 않는다면 HWP 변환을 먼저 명시적으로 실행한 뒤 HWPX 경로를 사용합니다.

## 시작·종료 정책

- 시작 전과 COM 생성 직전에 한글 실행 여부를 검사합니다. 실행 중이면 사용자 문서를 닫거나 종료하지 않고 안내 후 중단합니다.
- 직렬 lock 안에서 새 COM 객체의 정확한 창·프로세스와 유지한 process handle을 연결합니다. 모호한 연결을 추정 PID로 허용하지 않습니다.
- native 한글 프로세스 전체 kill, 전체 taskkill, kill-on-close Job Object를 사용하지 않습니다. 소유 보조 Python 트리의 job containment와 한글 자체 소유권을 혼동하지 않습니다.
- 정상 종료는 확인된 COM 객체에 요청합니다. 추가 문서/프로세스나 identity 이상을 감지하면 Quit을 보류하고 안내합니다. 사용자가 전제를 어기고 작업 도중 한글을 사용하는 모든 경쟁 상황까지 보호한다고 보장하지 않습니다.
- 허가된 정상 Quit 이후에는 원래 held handle로 프로세스 종료를 최대 10초 기다립니다. 종료 실패/시간초과도 실패 결과로 전달하여 기존 출력 파일을 교체하지 않습니다. 강제 종료하지 않으므로 사용자가 한글을 확인하고 정상 종료해야 할 수 있습니다. native 앱이 작업 소유 임시파일을 잠그고 있으면 그 임시파일은 남을 수 있습니다.
- 원본 HWPX·서명 이미지·기존 결과는 별도 보호합니다. XML 검증·파일 저장 성공과 실제 조판/서명 위치 검수는 다릅니다.

## 공개 전 실제 수용

합성 문서 생성 → 양식 채우기 → TEST ONLY 이미지 삽입 → Hancom PDF 내보내기 → 모든 페이지 300DPI 캡처/시각 검수 → 원본 hash 보존/정상 종료를 기록합니다. mock/dry-run으로 이 단계를 완료 처리하지 않습니다. 실패하면 발생 단계와 남은 목표를 명시합니다.
