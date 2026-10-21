# Third-party notices / 의존성 라이선스

이 문서는 확인한 출처와 사용 경계를 정리한 고지입니다. **모든 의존성을 MIT로 재라이선스하지 않습니다.** 원 작성자 확인과 소스 전용 배포 범위는 [PROVENANCE](docs/PROVENANCE.md)를 따릅니다. 외부 패키지는 pip로 설치하며 이 후보에 가상환경·wheel·DLL을 포함하지 않습니다. 실제 의존성 재배포 시 해당 버전의 전체 라이선스/NOTICE와 전이 의존성도 보존·검토해야 합니다.

| 구성요소 | 확인한 라이선스 | 역할 / 주의 |
|---|---|---|
| lxml | BSD 3-Clause | XML 처리. 포함된 libxml2/libxslt 등과 일부 파일의 별도 고지도 확인 |
| PyMuPDF / MuPDF | GNU AGPL v3 계열 또는 별도 상용 계약 | PDF 도구. MIT가 아님; 결합/배포/서비스 방식에 따른 의무 검토 필요 |
| Pillow | MIT-CMU | 이미지 처리. 일반 MIT 텍스트와 동일하다고 가정하지 않음; 이미지 코덱 등 고지 별도 |
| pyhwpx 1.7.2 | 배포물의 MIT 형식 `license.txt`; GitHub API도 MIT 식별 | Windows/Hancom 자동화. 하위 바이너리까지 일괄 허가한다고 해석하지 않음 |
| Hancom Office | 별도 상용/벤더 약관 | 포함하지 않음. 사용자 설치/사용 권한 필요 |
| FilePathChecker DLL 및 기타 보안 모듈 | 파일별 권리 확인 필요 | 다운로드·복사·동봉하지 않음. pyhwpx 최상위 MIT만으로 DLL 재배포 권한을 추정하지 않음 |

## 검증 출처

- lxml 공식 [소개/License](https://lxml.de/index.html#license), 저장소의 [BSD.txt](https://github.com/lxml/lxml/blob/master/doc/licenses/BSD.txt): `Copyright (c) 2004 Infrae. All rights reserved.`와 세 조건 확인.
- PyMuPDF 벤더 [licensing](https://pymupdf.io/licensing/), 저장소 [COPYING](https://github.com/pymupdf/PyMuPDF/blob/main/COPYING): AGPL/상용 경로 확인. 이 프로젝트의 MIT 표시는 이 의무를 없애지 않습니다. 정확한 배포 계획에 대한 법률 판단은 별도로 필요합니다.
- Pillow 저장소 [LICENSE](https://github.com/python-pillow/Pillow/blob/main/LICENSE): PIL의 `Copyright © 1997-2011 Secret Labs AB`, `Copyright © 1995-2011 Fredrik Lundh and contributors`, Pillow의 `Copyright © 2010 Jeffrey 'Alex' Clark and contributors`, MIT-CMU 문구 확인.
- pyhwpx 공식 [문서](https://martiniifun.github.io/pyhwpx/), [PyPI 메타데이터](https://pypi.org/pypi/pyhwpx/json), [라이선스 API](https://api.github.com/repos/martiniifun/pyhwpx/license), [실제 license.txt](https://github.com/martiniifun/pyhwpx/blob/master/license.txt).

pyhwpx 1.7.2 설치 배포물의 `pyhwpx-1.7.2.dist-info/licenses/license.txt`도 확인했습니다. 내용은 `Copyright (C) 2012 Yoshimasa Niwa`로 시작하는 MIT 허가문입니다. 이 저작권 문구는 실제 배포물 그대로 기록한 것이며 프로젝트 작성자 이름으로 임의 치환하지 않았습니다. METADATA에는 `License-File: license.txt`가 있지만 SPDX 표현식은 없고 PyPI JSON의 license 필드도 null이었습니다. 따라서 null만으로 “라이선스 없음”이라 하지 않고 실제 파일을 근거로 적었습니다. upstream 현재 파일과 배포 버전의 모든 코드/바이너리 권리관계를 보증하는 것은 아닙니다.

pyhwpx METADATA는 numpy, pandas, pywin32, openpyxl, pyperclip, Pillow를 전이 의존성으로 선언합니다. 위 표는 전체 SBOM이나 전이 라이선스 검토 완료 선언이 아닙니다. 라이브러리/한글/보안 DLL을 동봉하는 배포는 별도 조사 없이 진행하지 마세요.
