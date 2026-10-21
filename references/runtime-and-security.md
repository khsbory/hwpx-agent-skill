# 런타임과 보안

## 설치

Python 3.10 이상을 사용한다. 스킬 루트에서 다음을 실행한다.

```powershell
python -m pip install -r .\requirements.txt
```

기능별 의존성은 다음과 같다.

- XML 생성·편집·검증: `lxml>=4.9`
- PDF 캡처·비교: `PyMuPDF>=1.23`, `Pillow>=10.0`
- HWPX 서명 삽입·HWP 변환·PDF 내보내기: Windows의 `pyhwpx==1.7.2`와 Hancom Office. 옵션 없는 엄격 모드는 격리 증명이 없으면 거부한다. 모든 한글을 저장·닫고 자동화 중 한글을 사용하지 않기로 동의한 사용자는 `--exclusive-use`로 전용 작업 모드를 선택한다. 이는 사용자 협조 정책이지 OS/COM 격리 증명이 아니며 가상머신을 요구하지 않는다. [상세 조건](exclusive-use.md).

`PYHWPX_PYTHON`에 `pyhwpx`가 설치된 `python.exe`를 지정하면 HWP 변환·PDF 내보내기·서명 삽입의 런타임을 고정할 수 있다. 명시한 환경이 실패하면 다른 Python으로 몰래 우회하지 않는다. 미지정 시 공통 탐색기를 사용하며 PDF는 PyMuPDF, 서명은 Pillow import까지 확인한다. PDF와 서명 CLI는 선택한 Python으로 자기 자신을 한 번 실행하며 패키지를 자동 설치하거나 업데이트하지 않는다. `--dry-run`과 레지스트리 복원은 Python 재선택을 하지 않는다. 각 환경 탐색과 실제 변환 단계는 `STAGE:` 진행 로그를 즉시 출력하므로 대기 위치를 확인할 수 있다.

## Windows 자동화

Hancom COM 작업은 직렬로 실행한다. 병렬 실행은 RPC 연결 오류를 만들 수 있다. 보안 모듈 DLL과 Hancom 실행 환경의 아키텍처(x86 또는 x64)가 맞는지 확인한다.

`--check-security-only`는 Hancom을 실행하지 않고 등록된 DLL, 설치된 `pyhwpx` 번들 DLL, SHA-256, PE 아키텍처를 확인한다.

```powershell
python .\scripts\export_document_to_pdf.py --check-security-only
```

`export_document_to_pdf.py --diagnose`는 한글 실행 없이 Python 실행 파일·패키지 버전과 32/64비트 COM 등록, 한글 실행 파일·보안 DLL 아키텍처를 확인한다. Python과 한글의 비트 수 차이 자체는 오류가 아니며 DLL과 한글 프로세스가 맞아야 한다. pyhwpx가 선언 최소 버전보다 낮으면 경고하되 환경을 변경하지 않는다. 보안 모듈 경로가 없거나 잘못됐을 때 기본 변환은 중단하며, 자동 등록이나 보안 해제는 하지 않는다. `--repair-security-module`은 예외로 활성화가 거절된 경우도 한 번만 복구·재시도한다.

## 기본 보안 동작

PDF 내보내기의 기본 모드는 레지스트리를 쓰거나 DLL을 다운로드하지 않는다. 라이브러리의 `Hwp(new=True)` 생성자를 실행하지 않고 명시적 DispatchEx 객체를 pyhwpx 1.7.2용 facade로 감싼다. guard가 정확한 COM 창/프로세스와 유지한 조회용 handle을 확인한 뒤에만 숨김·등록·문서 열기를 허용한다. `--exclusive-use`에서는 시작 전/생성 직전 한글 없음과 실행 중 미사용 조건을 적용하며 추가 문서/프로세스를 감지하면 종료를 보류한다. FilePathChecker 활성화 실패는 별도로 문서 열기 전에 중단한다. 기본 엄격 모드는 별도의 격리 증명을 요구한다. 시작 순간 창 표시를 완전히 배제할 수 없으므로 완전한 무창 동작은 보장하지 않는다.

서명 삽입은 `scripts/insert_signature_pyhwpx.py`가 `pyhwpx`의 마커 검색과 `insert_picture(..., embedded=True)`를 사용한다. COM 세션 전에 `scripts/hancom_window_guard.py`를 활성화하고, 연결·확인된 정확한 COM 창 하나만 숨긴다. PID가 새로 생겼다는 사실만으로 같은 프로세스의 모든 창을 숨기지 않는다. 기존 사용자 한글이 있으면 전용 모드 시작을 거부한다. guard를 설치하지 못하거나 실행 중 실패하면 조용히 visible 모드로 전환하지 않고 중단한다.

서명과 PDF 자동화는 위의 명시적 COM 생성/facade와 동일한 guard 정책을 사용한다. FilePathChecker가 세션에 활성화된 뒤에만 문서를 연다. 서명 이미지는 작업 소유 사본을 전달하여 vendor의 tmp 파일 정리 동작으로 원본이 지워지지 않게 한다. 서명 결과는 임시 HWPX에 저장·검증하고 COM 종료 후 원자적으로 교체하며, 실패하면 입력과 기존 출력을 보존한다. 서명 기능은 지정 마커에 서명 이미지를 넣는 전용 기능이며 일반 이미지 자유 삽입 기능이 아니다. native 한글은 일괄 kill하거나 kill-on-close job에 넣지 않는다.

`--repair-security-module`을 지정한 경우에만 다음 두 HKCU 값을 한 번 복구한다.

- `HKCU\Software\HNC\HwpAutomation\Modules\FilePathCheckerModule`
- `HKCU\Software\Hnc\HwpUserAction\Modules\FilePathCheckerModule`

복구 대상 DLL은 설치된 `pyhwpx` 번들의 DLL이거나 그 DLL과 SHA-256이 같은 기존 등록 파일만 허용한다. HKLM, 시스템 보안 정책, 관리자 권한, DLL 다운로드를 사용하지 않는다.

## 백업과 복원

명시적 복구 전에 `%LOCALAPPDATA%\hwpx-skill\backups`에 JSON 백업을 만든다. `--security-backup-dir`로 위치를 바꿀 수 있다. 백업에는 두 키의 존재 여부, 값의 존재 여부와 이전 값, 대상 DLL 검증 정보, 절대 경로 복원 명령만 저장한다.

두 값을 쓰는 중 하나라도 실패하면 저장한 직전 상태로 자동 롤백한다. 이전에 없던 값은 삭제하고, 복구 과정에서 새로 만든 키가 비어 있으면 그 키도 삭제한다. 관련 없는 값이나 하위 키가 있으면 키를 보존한다.

백업 JSON의 `restore_command`를 실행하거나 다음 형식을 사용한다.

```powershell
python .\scripts\hancom_security.py --restore-security-backup .\backup.json
```

서명 dry-run은 `--input`만 필수이며 `--output`은 생략할 수 있다. 실제 삽입은 `--input`, `--output`, `--signature`가 모두 필요하다.

서명 입력은 PNG/JPEG/BMP/GIF/TIFF 이미지 무결성을 확인한다. `--preserve-aspect`는 `--width-mm`·`--height-mm`를 최대 크기로 삼아 종횡비를 보존한다. 마커가 여러 텍스트 run에 나뉘어도 같은 문단이면 dry-run에서 센다. 실제 검색에서 삽입이 0건이면 출력을 교체하지 않는다.

## 테스트

테스트는 레지스트리와 Hancom을 모킹한다. 실제 레지스트리, Hancom, PDF 변환을 호출하지 않는다.

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest discover .\scripts\tests -p 'test_*.py'
python .\scripts\table_extract\tests\test_merge_and_nested.py
python .\scripts\table_extract\tests\test_shapes.py
python .\scripts\table_extract\tests\test_fixes.py
python .\scripts\table_extract\tests\test_real_file.py
```

실제 문서를 이용한 통합 스모크 테스트는 기본으로 건너뛴다. 명시적으로 실행할 때만 `HWPX_TABLE_TEST_FILE`을 설정하고, 선택적으로 `HWPX_TABLE_EXPECTED_TABLES`, `HWPX_TABLE_EXPECTED_TEXT`를 설정한다.
