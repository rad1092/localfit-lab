# whago.net 운영 배포

현재 운영 구조는 Cloudflare와 AWS Lightsail을 연결한 단일 서버 구성입니다.

## 연결 구조

1. Cloudflare DNS의 루트 `A` 레코드가 Lightsail의 고정 IPv4를 가리킵니다.
2. `www`는 루트 도메인을 가리키는 `CNAME`입니다.
3. 두 레코드 모두 Cloudflare 프록시를 사용합니다.
4. Cloudflare SSL/TLS 모드는 `Full (Strict)`입니다.
5. Lightsail 방화벽은 웹 트래픽용 TCP 80·443을 허용합니다.
6. Nginx가 외부 요청을 받아 `/api/`는 FastAPI로, 나머지는 Next.js로 전달합니다.

원본 서버의 실제 IP, 관리자 계정, API 키는 저장소에 기록하지 않습니다.

## 서버 경로

```text
/srv/localfit/
├─ final_proj/
│  ├─ backend/
│  ├─ frontend/
│  ├─ resources/
│  ├─ .venv/
│  └─ runtime/
├─ datacorpus/
├─ deploy/lightsail/
└─ private/
   ├─ backend.env
   └─ key.md
```

## 운영 프로세스

- `localfit-frontend.service`: Next.js, `127.0.0.1:3000`
- `localfit-backend.service`: FastAPI, `127.0.0.1:8000`
- Nginx: 외부 80·443, 리버스 프록시
- Certbot: 원본 서버 인증서 자동 갱신

서비스 정의와 Nginx 설정은 [`deploy/lightsail`](deploy/lightsail)에 있습니다.

## 배포 순서

로컬 Windows PC에서 앱·데이터 번들을 분리 생성합니다.

```powershell
.\deploy\lightsail\build_bundles.ps1
```

생성된 번들을 서버의 임시 업로드 경로에 전송한 뒤, 서버에서 설치 스크립트를 실행합니다.

```bash
sudo bash /srv/localfit/deploy/lightsail/install.sh
```

배포 후 검증합니다.

```bash
bash /srv/localfit/deploy/lightsail/verify.sh
curl -I https://whago.net
curl -I https://www.whago.net
curl https://whago.net/healthz
```

## 운영 데이터 보호

- 코드 번들과 데이터 번들은 별도 파일로 배포합니다.
- 새 운영 DB 생성 과정에서 로컬 회원·리포트·댓글·로그는 복사하지 않습니다.
- `private/backend.env`와 `private/key.md`는 Git과 앱 번들에서 제외합니다.
- 런타임 DB·PDF·로그는 `/srv/localfit/final_proj/runtime`에만 저장합니다.

