# 시험과 증거 / Testing and evidence

## 비COM 회귀

```sh
python -m unittest discover -s scripts/tests -p "test_*.py"
python scripts/table_extract/tests/run_all.py
```

`run_all.py`는 표 suite A(선택적 실문서), B(병합/중첩), C(도형/글상자), D(회귀)를 실행하고 `scripts/table_extract/tests/results/last_run.txt`에 로컬 로그를 씁니다. `HWPX_TABLE_TEST_FILE`이 없으면 A는 SKIP입니다. SKIP은 실패가 아니지만 실문서 검증 성공도 아닙니다. 공개 CI에는 비공개 fixture를 넣지 않습니다.

`.github/workflows/tests.yml`은 Ubuntu의 Python 3.10/3.12에서 lxml/PyMuPDF/Pillow만 직접 설치합니다. Windows 전용 pyhwpx와 Hancom을 설치하거나 실행하지 않습니다. CI 설정을 작성한 것과 GitHub Actions가 실제 성공한 것은 다릅니다. 환경별 실행 결과를 별도로 기록하세요. 이 저장소는 `pip install .`로 설치되는 패키지를 제공한다고 주장하지 않습니다.

## Windows 수용 — 별도 승인/실행

유효한 Hancom 환경에서 직렬로 수행합니다. 고객/원본 비공개 자료가 아닌 합성 문서와 가상 서명만 사용합니다.

1. 읽기 전용 진단 결과와 Python/한글/보안 DLL 구성을 기록합니다.
2. 생성, 고정 양식 채움, 서명 이미지 삽입을 별개 사례로 실행합니다.
3. 원본해시, 입력 데이터, 출력, exit code, 구조 검사와 내용 대조를 기록합니다.
4. 최신 HWPX를 실제 한글로 PDF 내보내고 모든 페이지를 300 DPI PNG로 만듭니다.
5. 모든 이미지를 열어 잘림·넘침·겹침·여백·불필요한 빈 페이지·서명 위치를 확인하고 전체/검수 페이지 수를 기록합니다.
6. 기존 사용자 창/프로세스 비간섭, 실패 시 원본/기존 출력 보존을 확인합니다. 보안 설정은 자동 변경하지 않습니다.

COM 모킹·PyMuPDF로 자체 생성한 PDF·구조 검사만으로 한글의 실제 PDF 출력 성공을 대신하지 않습니다. 차단되면 정확한 실패 단계와 미완료 범위를 남깁니다. 이 문서 자체는 실기 검증 완료 증거가 아닙니다.

## 공개 전 검토

권리 보류 해소, 합성 자료/메타데이터 검사, 허용 파일만 포함하는 후보 아카이브, 제외 파일 검사, 새 경로에서 압축 해제 후 재시험, 독립 검토가 필요합니다. 내부 `evidence/`, 검토 로그·절대경로·레지스트리 백업을 그대로 공개하지 마세요. 공개 문서에는 비식별화한 결과만 따로 작성합니다.
