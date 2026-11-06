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

- Thêm biến mới → cập nhật `.env.example` trong cùng commit; config load bằng `pydantic-settings`, thiếu biến bắt buộc thì dừng lúc khởi động.
- Test dùng `.env.test` sinh từ fixture; coding agent không được đọc `.env` thật.
- Log/trace/event không chứa giá trị của bất kỳ biến `*_PASSWORD`, `*_KEY`, `EAP_SECRET_CANARY` (SC-04 grep canary).
- **Verifier (P2a-WP9)** khởi chạy test với env rỗng + allowlist (`PATH`, `HOME` tạm, `LANG`, `PYTHONHASHSEED`), không network, workspace tạm. Test bắt buộc: một fixture test in toàn bộ env, output không được chứa `EAP_SECRET_CANARY`.

## Checklist P0-WP1 và điểm mở

Môi trường được coi là xong khi mọi mục dưới có output thật ghi vào `VALIDATION_REPORT.md`.

- [ ] `docker compose pull` thành công cho ba tag; ghi image digest vào `docs/DEV_ENVIRONMENT.md`
- [ ] `docker compose up -d` chỉ bật PostgreSQL; healthcheck xanh; `--profile optional` bật thêm Redis, Qdrant
- [ ] `uv sync` trên Python 3.12 sạch; `uv.lock` commit
- [ ] `uv run python tools/validate_package.py` và `ppm-v2/tools/run_negative_cases.py` PASS trên 3.12
- [ ] `pip install -r ppm-v2/tools/requirements.txt` trong venv trống → validator PPM chạy không cần platform
- [ ] Test import-boundary: `ppm_reducer` không import `eap`/network/subprocess
- [ ] SC-06 migration up/down/up trên PostgreSQL 18.6
- [ ] App khởi động và test xanh khi `REDIS_URL`, `QDRANT_URL` để trống
- [ ] SC-04: canary không xuất hiện trong log

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
