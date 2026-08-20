============================================================
  EASP (수출 자동 출하 계획) — Export Auto Shipment Planning
  설치 및 실행 가이드
============================================================

[ 사전 요구사항 ]
  - Windows 10 / 11 (64bit)
  - Python 3.10 이상  →  https://www.python.org/downloads/
    * 설치 시 "Add Python to PATH" 반드시 체크
  - Microsoft Edge 브라우저 (포털 SSO 세션 재사용)


[ 폴더 구조 ]
  수출물동_자동화시스템/
  ├── app/
  │   ├── index.html          UI 메인 화면
  │   ├── app.py              Flask 웹 서버
  │   ├── fonts/              LG스마트체 폰트
  │   └── images/             LX Pantos 로고
  ├── crawlers/               포털 자동 크롤링 스크립트
  ├── Raw/                    크롤링/수동 원본 데이터 저장 폴더
  │   └── MMDD/               날짜별 원본 파일 (예: 0519/)
  ├── import_raw.py           Raw → Excel xlsm 자동 입력
  ├── requirements.txt        Python 패키지 목록
  ├── install.bat             최초 1회 설치
  ├── run.bat                 서버 실행 + 브라우저 자동 오픈
  └── open_html.bat           Flask 없이 HTML만 열기


[ 설치 방법 (최초 1회) ]
  1. install.bat 더블클릭
  2. 패키지 설치 완료까지 대기 (인터넷 연결 필요)
  3. 완료 메시지 확인 후 창 닫기


[ 실행 방법 ]
  ▶ 전체 기능 (크롤링 + Import 포함)
     run.bat 더블클릭
     → 브라우저에서 http://localhost:5000 자동 오픈

  ▶ 오프라인 모드로 실행하기 (네트워크 없이)
     1) Raw 데이터가 준비된 경우: run.bat 실행 또는 open_html.bat로 UI 열기
     2) 크롤링 없이 STEP/대시보드/템플릿 기능 사용 가능
     3) 크롤링 기능은 별도 온라인 모드입니다.

  ▶ UI 화면만 빠르게 열기 (Flask 불필요)
     open_html.bat 더블클릭


[ Raw 데이터 수동 입력 방법 ]
  포털에서 직접 다운로드한 경우:
  1. Raw\ 폴더 안에 날짜 폴더 생성 (예: Raw\0527\)
  2. 아래 4개 파일을 해당 날짜 폴더에 저장:
     - Display Sales Order Progress.xlsx
     - 생산계획(PS Order).xlsx
     - display stock by bin (1).xlsx
     - 1779169640580_BookingProgressDetails.xlsx


[ 포털 자동 크롤링 ]
  UI에서 BA 코드 입력 후 [포털 데이터 크롤링 시작] 클릭
  - BA 코드 여러 개 지정 가능 (Enter 또는 쉼표로 구분)
  - 크롤링 전에 Edge 브라우저로 포털에 로그인되어 있어야 함
  - SSO 세션이 유지된 상태에서 자동 실행됨


[ 문의 ]
  담당: CL전자운영개선팀
============================================================
