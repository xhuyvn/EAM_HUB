> Translated from docs/DEV_ENVIRONMENT.md @ d7c9e91

# EAM_HUB — Dev Environment

06/11/2026 · Huynn · Bản gốc: [Claude Docs](https://claude.ai/artifact/CC31A9ymgUtReLd1gPEpmK)

## Phạm vi và nguyên tắc

Platform (`src/eap`, `web/hud`) và PPM v2 (`ppm-v2/`) dùng chung một repo `EAM_HUB`, một Python 3.12 và một bộ dịch vụ local. Môi trường này dùng cho dev và test trên máy owner; private server tính sau Local-pilot.

- **Một repo, một lock file Python**; Node chỉ cho `web/hud`.
- **PostgreSQL là source of truth duy nhất** cho task, run, journal, assessment và audit. Redis và Qdrant chỉ giữ dữ liệu dẫn xuất, mất là dựng lại được.
- **`ppm_reducer` không I/O, không đọc `.env`, không import `eap`**; chiều import chỉ `eap.ppm → ppm_reducer`.
- **Verifier chạy test không tin cậy trong env sạch**, không thừa hưởng `.env` của dev.
- **Bộ validator PPM standalone** chạy được chỉ với `ppm-v2/tools/requirements.txt`, không cần dịch vụ nào.

## Phiên bản

Bốn thành phần được chốt theo yêu cầu của owner. Mọi image Docker pin đúng tag, không dùng `latest`; kiểm lại tag và digest bằng `docker pull` ở P0-WP1.

| Thành phần | Phiên bản / image | Vai trò | Bắt buộc cho D1 | Kiểm chứng (06/11/2026) |
| --- | --- | --- | --- | --- |
| Python | 3.12 (`requires-python = ">=3.12,<3.13"`) | Platform + `ppm_reducer` + validator | Có | Validator hiện mới chạy trên 3.10; CI phải chạy lại trên 3.12 |
| PostgreSQL | `postgres:18.6-trixie` | Source of truth, work queue (`SKIP LOCKED`), outbox, FTS | Có | 18.6 phát hành 13/08/2026 ([postgresql.org](https://www.postgresql.org/)); tag image chưa kiểm |
| Redis | `redis:8.10.1-trixie` | Cache/rate limit dẫn xuất (xem mục sau) | Không | 8.10.1 là bản vá bảo mật tháng 8/2026 ([release notes](https://redis.io/docs/latest/operate/oss_and_stack/stack-with-enterprise/release-notes/redisce/redisos-8.10-release-notes/)); tag image chưa kiểm |
| Qdrant | `qdrant/qdrant:v1.20.0` | Vector index dẫn xuất cho retrieval | Không | Client Python v1.20.0 phát hành 22/10/2026, hỗ trợ Qdrant 1.20 ([qdrant-client](https://github.com/qdrant/qdrant-client/releases/tag/v1.20.0)); release server v1.20.0 chưa mở trang kiểm |
| Node | 26 LTS | `web/hud` (Vite + React + TS) | Có | Node 26 vào Active LTS 28/10/2026, EOL 30/04/2029 ([DevX](https://www.devx.com/coding/nodejs-26-lts-new-features-release-date/)) |
| Ollama | bản cài local | Model local | Không (fake model ở D1) | Spike P0-WP6 |

PostgreSQL 18 thay cho 16 (plan rev 1.5, ADR-026); P0-WP1 chạy SC-06 (migration up/down/up) trên 18.6. Redis 8 phân phối theo bộ license AGPL-3.0/RSAL/SSPL; dùng nội bộ thường không vướng, nhưng cần đối chiếu chính sách license của ngân hàng trước khi lên private server.

## Redis và Qdrant trong kiến trúc

Redis và Qdrant có trong môi trường từ đầu nhưng không nằm trên đường tới D1-fixture. Review đã chọn PostgreSQL `SKIP LOCKED` cho work queue và PostgreSQL FTS cho tìm kiếm, với quy tắc không thêm hạ tầng chỉ vì hiệu năng trước khi có số đo.

| Dịch vụ | Được dùng cho | Không được dùng cho | Điều kiện bật |
| --- | --- | --- | --- |
| Redis | Cache projection HUD, rate limit, fan-out SSE khi có nhiều API process | Queue của run/dispatch, journal, lease, idempotency key, cursor HUD (tất cả ở PostgreSQL) | Số đo P1a-WP7 / P3a-WP4 vượt ngưỡng, hoặc chạy >1 API process |
| Qdrant | Index embedding cho retrieval trên notes/tài liệu, dựng lại được từ PostgreSQL + CAS | Lưu trạng thái criterion, quyết định verified, nguồn duy nhất của bất kỳ bản ghi nào | Benchmark retrieval cho thấy FTS không đủ (H1), embedding model được egress policy cho phép |

- Hai dịch vụ nằm trong compose profile `optional`; `docker compose up` mặc định chỉ bật PostgreSQL.
- Code chỉ gọi Redis/Qdrant qua interface (`CacheBackend`, `VectorIndex`) có bản in-memory/no-op cho test.
- Embedding cho Qdrant chịu cùng egress policy với model (binding 1.1): mặc định local-only.
- Trước khi một trong hai thành phần bắt buộc, ghi ADR mới trong `docs/01` kèm số đo.

## Repo và workspace

Một uv workspace với hai member Python cài editable vào cùng `.venv`; Node tách riêng trong `web/hud`.

```
EAM_HUB/
  pyproject.toml            # workspace root: package eap, requires-python >=3.12,<3.13
  uv.lock                   # một lock duy nhất, commit
  src/eap/                  # platform (FastAPI, Run Service, PPM integration ...)
  ppm-v2/reducer/           # workspace member: package ppm_reducer (stdlib + jsonschema + PyYAML)
  ppm-v2/tools/             # validator standalone, requirements.txt riêng
  web/hud/                  # Vite + React + TS, package-lock.json commit
  compose.yaml              # postgres (mặc định), redis + qdrant (profile optional)
  .env.example              # commit; .env thật không commit
```

```bash
uv sync                         # tạo .venv, cài eap + ppm_reducer editable
uv run pytest -q
uv run python tools/validate_package.py
uv run python ppm-v2/tools/run_negative_cases.py
npm --prefix web/hud ci && npm --prefix web/hud run build
```

Công cụ dev cài trong `[dependency-groups] dev` của root: `pytest`, `ruff`, `mypy`. `ppm_reducer` khai báo dependency tối thiểu của riêng nó; test kiểm nó không import `eap`, `requests`, `socket`, `subprocess`.

## Dịch vụ local (Docker Compose)

PostgreSQL chạy mặc định; Redis và Qdrant chỉ bật với `--profile optional`. Mọi port bind `127.0.0.1`, không ra mạng LAN.

```yaml
services:
  postgres:
    image: postgres:18.6-trixie
    environment:
      POSTGRES_DB: eap_dev
      POSTGRES_USER: eap
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set in .env}
    ports: ["127.0.0.1:5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U eap -d eap_dev"]
      interval: 5s

  redis:
    image: redis:8.10.1-trixie
    profiles: ["optional"]
    command: ["redis-server", "--requirepass", "${REDIS_PASSWORD:?set in .env}", "--appendonly", "no"]
    ports: ["127.0.0.1:6379:6379"]

  qdrant:
    image: qdrant/qdrant:v1.20.0
    profiles: ["optional"]
    environment:
      QDRANT__SERVICE__API_KEY: ${QDRANT_API_KEY:?set in .env}
      QDRANT__TELEMETRY_DISABLED: "true"
    ports: ["127.0.0.1:6333:6333", "127.0.0.1:6334:6334"]
    volumes: ["qdrantdata:/qdrant/storage"]

volumes:
  pgdata:
  qdrantdata:
```

- Test dùng database riêng `eap_test`, tạo và xóa theo session pytest; không chạy test trên `eap_dev`.
- PostgreSQL 18 image dùng thư mục dữ liệu theo phiên bản dưới `/var/lib/postgresql`; kiểm lại đường mount trong README của image ở P0-WP1.
- Telemetry Qdrant tắt vì telemetry nằm trong egress policy (ARCH-15).

## `.env` và secrets

Chỉ platform đọc `.env`; reducer, validator và test code của project được verify không bao giờ thấy nó.

```dotenv
# .env.example — commit file này, không commit .env
EAP_ENV=dev
EAP_BIND=127.0.0.1:8000
# Local authentication (token hashes only; sha256 hex; tokens/hashes never logged, SC-04)
# Generate token: openssl rand -hex 32
# Generate hash:  python -c "import hashlib; print(hashlib.sha256(b'<token>').hexdigest())"
EAP_OWNER_TENANT_ID=tenant-owner
EAP_OWNER_PRINCIPAL_ID=principal-owner
EAP_OWNER_TOKEN_HASH=
EAP_SECOND_TENANT_ID=tenant-second
EAP_SECOND_PRINCIPAL_ID=principal-second
EAP_SECOND_TOKEN_HASH=
DATABASE_URL=postgresql+psycopg://eap:${POSTGRES_PASSWORD}@127.0.0.1:5432/eap_dev
TEST_DATABASE_URL=postgresql+psycopg://eap:${POSTGRES_PASSWORD}@127.0.0.1:5432/eap_test
POSTGRES_PASSWORD=
REDIS_URL=            # để trống = CacheBackend no-op
REDIS_PASSWORD=
QDRANT_URL=           # để trống = VectorIndex tắt
QDRANT_API_KEY=
OLLAMA_BASE_URL=http://127.0.0.1:11434
EAP_EGRESS_DEFAULT=local_only
EAP_TELEMETRY=local_only
EAP_SECRET_CANARY=     # sinh ngẫu nhiên mỗi máy, dùng cho SC-04
```

- **Local identity and authentication (P1a-WP5 / ISS-020)**:
  - Local authentication operates strictly without database user tables. Local principals (at least an owner and a second principal) are defined in configuration with bearer tokens.
  - Plaintext tokens are **never** stored in `.env` or settings. Only their SHA-256 hex digests (e.g. `sha256:<hex>` or raw `<hex>`) are stored in `EAP_OWNER_TOKEN_HASH` and `EAP_SECOND_TOKEN_HASH` (or `EAP_PRINCIPALS_JSON`) as `SecretStr`.
  - Clients provide tokens in `Authorization: Bearer <token>`. Identity is resolved via constant-time comparison against configured hashes. Missing, malformed, unknown, or wrong tokens return HTTP 401 with identical `"detail": "Unauthorized"` responses.
  - Tenant identity always comes from the resolved principal record; client-declared tenant/principal headers, queries, or body fields are strictly rejected with HTTP 422.
  - **Loopback-only restriction**: Server startup refuses non-loopback `EAP_BIND` addresses (e.g. `0.0.0.0`, `192.168.x.x`, `::`), permitting only `127.0.0.1`, `::1`, or `localhost`.
- Thêm biến mới → cập nhật `.env.example` trong cùng commit; config load bằng `pydantic-settings`, thiếu biến bắt buộc thì dừng lúc khởi động.
- Test dùng `.env.test` sinh từ fixture; coding agent không được đọc `.env` thật.
- Log, trace, và event không chứa giá trị của bất kỳ biến `*_PASSWORD`, `*_KEY`, `*_TOKEN_HASH`, hoặc `EAP_SECRET_CANARY` (SC-04 grep canary).
- **Verifier (P2a-WP9)** khởi chạy test với env rỗng + allowlist (`PATH`, `HOME` tạm, `LANG`, `PYTHONHASHSEED`), không network, workspace tạm. Test bắt buộc: một fixture test in toàn bộ env, output không được chứa `EAP_SECRET_CANARY`.

## Running the server and access logging (P1a-WP6 / SC-04)

API server của platform chạy qua `uvicorn`:

```bash
uv run uvicorn eap.api.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

- **Request logging**: Platform bao gồm `RequestLoggingMiddleware` (`src/eap/observability/middleware.py`) ghi log các request hoàn tất dưới dạng JSON có cấu trúc (`timestamp`, `level`, `logger="eap.api.request"`, `message`, `tenant_id`, `principal_id`, `request_id`, `method`, `path`, `status`, `duration_ms`). Nghiêm ngặt bỏ qua body và header của request/response.
- **Access logging**: Truyền `--no-access-log` sẽ tắt access logger mặc định của uvicorn, tránh các dòng log phi cấu trúc trùng lặp.
- **Query string redaction**: Nếu không truyền `--no-access-log`, `RedactionFilter` và `AccessFormatter` được vá của platform tự động loại bỏ query string (ví dụ `?token=...`, `?canary=...`) khỏi các bản ghi log của `uvicorn.access`, ngăn rò rỉ credential trong tham số query của URL.
- **Universal handler filter**: `configure_logging()` tự động gắn `RedactionFilter` vào tất cả handler của mọi logger có sẵn từ trước (bao gồm `uvicorn`, `uvicorn.access`, `uvicorn.error`, và `logging.lastResort`), redacting canaries, bearer tokens, passwords, DSN credentials, và traceback ngoại lệ trên toàn bộ handler.

## Checklist P0-WP1 và điểm mở

Môi trường được coi là xong khi mọi mục dưới có output thật ghi vào `VALIDATION_REPORT.md`.

- [x] `docker compose pull` thành công cho ba tag; ghi image digest vào `docs/DEV_ENVIRONMENT.md`:
  - `postgres:18.6-trixie`: `sha256:bd5dfa494a5a259704365813dbfa5fa6b3e69be7348d43b547f540c730478583`
  - `redis:8.10.1-trixie`: `sha256:8a1efc5f479551822b47424ccae982026b633f28818eab0387348120a61e10e2`
  - `qdrant/qdrant:v1.20.0`: `sha256:9bfc89f9914fc14876a88c05270685921a55b8877b254a98bcc1cc2f0a424cd2`
- [x] `docker compose up -d` chỉ bật PostgreSQL; healthcheck xanh; `--profile optional` bật thêm Redis, Qdrant
- [x] `uv sync` trên Python 3.12 sạch; `uv.lock` commit
- [x] `uv run python tools/validate_package.py` và `ppm-v2/tools/run_negative_cases.py` PASS trên 3.12
- [x] `pip install -r ppm-v2/tools/requirements.txt` trong venv trống → validator PPM chạy không cần platform
- [x] Test import-boundary: `ppm_reducer` không import `eap`/network/subprocess
- [x] SC-06 migration up/down/up trên PostgreSQL 18.6
- [x] App khởi động và test xanh khi `REDIS_URL`, `QDRANT_URL` để trống
- [x] SC-04: canary không xuất hiện trong log

Điểm mở:

- Plan rev 1.5 đã cập nhật PostgreSQL 18.6 / Python 3.12 / Node 26 LTS (ADR-026).
- Release server Qdrant v1.20.0 và các tag `-trixie` chưa được mở trang kiểm trực tiếp; P0-WP1 xác nhận bằng `docker pull`.
- License Redis 8 cần đối chiếu chính sách ngân hàng trước private server.
- Dung lượng: volume Qdrant/PostgreSQL tính vào ngân sách 100 GB dùng chung; đo ở P5-WP4.

## Nguồn

- [PostgreSQL 18.6 release (postgresql.org)](https://www.postgresql.org/)
- [Redis Open Source 8.10 release notes](https://redis.io/docs/latest/operate/oss_and_stack/stack-with-enterprise/release-notes/redisce/redisos-8.10-release-notes/)
- [qdrant-client v1.20.0](https://github.com/qdrant/qdrant-client/releases/tag/v1.20.0)
- [Node.js 26 LTS (DevX)](https://www.devx.com/coding/nodejs-26-lts-new-features-release-date/)
