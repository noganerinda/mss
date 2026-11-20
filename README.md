# 미스터리 셀러 - 웹 버전

브라우저에서 바로 하는 버전이에요. 설치 없이 **주소만 공유**하면 친구들이 들어와요.
(파이썬 3.8+ 만 있으면 되고 외부 라이브러리는 필요 없어요.)

## 내 컴퓨터에서 실행

```bash
python web_server.py          # 기본 포트 8080  (python web_server.py 3000 처럼 포트 지정 가능)
```
브라우저에서 http://localhost:8080 을 열면 끝!
같은 와이파이의 친구는 `http://내컴퓨터IP:8080` 으로 접속해요. (방화벽에서 8080 허용 필요)
방 안에서 **방 코드 칸을 누르면 초대 링크**가 복사돼요 → 친구가 링크를 열면 방 코드가 자동 입력돼요.

## 인터넷에 사이트로 올리기

웹소켓(WebSocket)을 지원하는 곳이면 어디든 돼요.

**방법 A. 무료/간편 호스팅 (Render, Railway, Fly.io 등)**
1. 이 폴더를 GitHub 저장소에 올려요.
2. 호스팅에서 새 웹 서비스를 만들고 저장소를 연결해요. (Dockerfile 이 있으니 Docker 로 배포하거나, 시작 명령을 `python web_server.py` 로 지정)
3. 호스팅이 알려주는 `https://...` 주소를 친구들에게 공유하면 끝. HTTPS 는 호스팅이 처리해 주고, 게임은 자동으로 `wss://` 로 연결해요.
   (`PORT` 환경변수를 자동으로 읽어요.)

**방법 B. 내 VPS (오라클 클라우드, AWS Lightsail 등)**
1. 서버에 폴더를 올리고 `python3 web_server.py 8080` 실행 (계속 켜두려면 `tmux` 또는 systemd)
2. 도메인이 있다면 Caddy 로 HTTPS 를 붙이는 게 가장 쉬워요. `/etc/caddy/Caddyfile`:
   ```
   mystery.내도메인.com {
       reverse_proxy localhost:8080
   }
   ```
   (Caddy 는 웹소켓을 자동으로 처리해요. nginx 를 쓴다면 `proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";` 필요)

> 주의: 방과 게임 상태는 서버 메모리에만 있어서, 서버를 재시작하면 모든 방이 사라져요. 서버는 **1개 프로세스**로만 돌려야 해요(여러 개로 늘리면 방 코드가 서로 안 보여요).

## 구성

```
web_server.py   HTTP + WebSocket 서버 (한 포트에서 웹페이지와 게임 통신 모두 처리)
server.py       게임 진행 로직 (방, 투표, 판매, 결과)
common.py       규칙 상수 (판매 시간 8분, 투표 60초, 기본 가격 등)
static/index.html   게임 화면 전체 (HTML/CSS/JS 한 파일)
static/items/       기본 사진 12장
```

규칙 값(판매 시간, 투표 시간, 최대 인원 등)은 `common.py` 에서 바꿀 수 있어요.
화면은 반응형이에요. 휴대폰(좁은 화면)에서는 좌우 여백 없이 꽉 차게, 물건 투표는 2×2 격자로 보이고,
PC(넓은 화면)에서는 가운데 정렬에 물건 투표가 가로 4개로 보여요. 결과 창은 사진 → 통계 → `판 끝내기` 버튼 순서예요.
(휴대폰에서는 키보드가 자동으로 튀어나오지 않도록, 입력창을 직접 눌러야 키보드가 열려요.)
