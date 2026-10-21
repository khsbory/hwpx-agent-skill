# Quick Fill Mode

`Quick Fill Mode`는 이미 존재하는 `.hwpx` 양식을 빠르게 채우는 경로다.  
`hwpx1`의 강점이던 `고정 양식 빠른 채우기 + 선택적 서명 삽입`을 `hwpx2` 안으로 흡수한 워크플로우다.

## 언제 쓰는가

- 이미 완성된 `.hwpx` 양식이 있고 구조를 유지한 채 값만 바꿔야 할 때
- 문서 전체를 재구성하지 않고 `section0.xml`의 텍스트만 치환하면 될 때
- 서명은 필요 시 Windows Hancom/pyhwpx 기반 `insert_signature_pyhwpx.py` 단계로 후처리할 때

## 언제 기본 워크플로우로 승격하는가

- 표 구조, 셀 병합, 문단 수 자체를 바꿔야 할 때
- 레퍼런스 문서의 레이아웃을 거의 그대로 복원해야 할 때
- 값 치환만으로는 페이지 드리프트를 제어하기 어려울 때

## 입력 파일

- `templates/fill_data_template.json`
  - 실제 값 파일
- `templates/fill_rules_template.json`
  - 치환 규칙 파일
- `templates/quick_fill_config.template.json`
  - `fill_fixed_form.py`를 직접 호출할 때 쓰는 compiled config 예시

## 기본 흐름

1. `extract_fill_schema.py`로 플레이스홀더나 서명 마커 후보를 확인
2. JSON 데이터/규칙 파일 작성
3. `quick_fill.py`로 결과 `.hwpx` 생성
4. 필요하면 같은 실행 안에서 선택적 서명 삽입 수행
5. 저수준 제어가 필요하면 `fill_fixed_form.py`를 직접 실행

## PowerShell 예시

```powershell
$skillRoot = "C:\path\to\hwpx2"
Set-Location $skillRoot

python .\scripts\extract_fill_schema.py .\input\form.hwpx --output .\output\schema.json

python .\scripts\quick_fill.py `
  --input .\input\form.hwpx `
  --output .\output\form_filled.hwpx `
  --data .\templates\fill_data_template.json `
  --rules .\templates\fill_rules_template.json `
  --report-json .\output\quick_fill_report.json
```

기본 템플릿은 page guard를 비활성 상태로 시작한다. 빈 칸을 실제 값으로 채우는 정상 시나리오에서도 길이 편차가 크게 날 수 있기 때문이다.

## 규칙 설계 원칙

- 단순 치환은 `text_rules` 사용
- 구조 의존 치환은 `special_rules` 사용
- `insert_after_text`는 기준 텍스트 뒤에서 빈 칸 또는 지정한 노드를 찾아 값을 넣는다
- `replace_fwspace_tail`은 날짜처럼 `fwSpace` tail에 걸린 값을 교체할 때 쓴다
- `required: true`를 걸면 해당 규칙이 한 번도 적용되지 않을 때 즉시 실패한다
- `preflight.minimum_total_replacements`로 전체 치환 수 하한을 걸 수 있다
- Quick Fill에서 page guard는 `--page-guard-reference`를 줄 때만 수행한다
- 자유 텍스트 전체 검색/대충 치환은 금지한다

## 서명 삽입

- `quick_fill.py`는 `fill_data.json`의 `signature.enabled=true`일 때 서명 단계를 이어서 실행할 수 있다
- `insert_signature_pyhwpx.py`는 별도 직접 호출도 가능한 선택 기능이다
- Windows, Hancom Office, `pyhwpx`가 필요하다
- FilePathChecker와 새 Hwp 창 guard가 먼저 활성화되지 않으면 문서를 열지 않는다
- `--dry-run`으로 출력 변경 없이 마커 탐지만 먼저 수행한다
- 지정 마커에 `insert_picture(..., embedded=True)`로 서명 이미지를 넣는 기능이며 일반 이미지 자유 삽입 기능은 아니다

## 제약사항

- **다중 섹션 미지원**: 현재 Quick Fill 엔진은 `section0.xml`만 처리한다. 다중 섹션 문서의 경우 `section1.xml` 이상의 필드는 처리되지 않는다.
