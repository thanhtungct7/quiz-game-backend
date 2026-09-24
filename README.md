# Duo Quiz – Backend

Máy chủ FastAPI cho ứng dụng học tiếng Anh **Duo Quiz**: cung cấp lộ trình luyện thi TOEIC
và từ vựng, chấm điểm bài học, đấu 1v1 thời gian thực, đánh quái (PvE), hệ thống nhân vật
và luyện hội thoại với AI. Phần lớn nội dung học và luật chơi đều nằm ở đây, nên client
không thể tự sửa điểm hay phần thưởng.

![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.140-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16+-4169E1?logo=postgresql&logoColor=white)
![Version](https://img.shields.io/badge/version-0.1.0-blue)

> Ứng dụng Android đi kèm: [quiz-game-front](https://github.com/thanhtungct7/quiz-game-front)

## Tính năng (Features)

- **Xác thực**: đăng ký/đăng nhập email + mật khẩu (Argon2), đăng nhập Google, JWT access +
  refresh token có xoay vòng, quên mật khẩu qua email (link mở thẳng app `quizgame://`).
- **Lộ trình học**: ngân hàng câu hỏi TOEIC xếp thành 60 chặng theo 5 mức điểm (250 → 990),
  xen kẽ các chặng từ vựng *4000 Essential English Words* (câu chọn hình, định nghĩa, ví dụ).
  Cây khoá học trả về trong một lần gọi, có ETag để cache.
- **Đấu 1v1 (PvP)** qua WebSocket: ghép trận ngẫu nhiên hoặc phòng mời bạn, thời gian trả lời
  đo trên máy chủ, xếp hạng Elo, mùa giải và bảng xếp hạng.
- **Đánh quái (PvE)**: mỗi bài học có một quái canh giữ (6 bậc, có boss), dùng cùng hệ chiến
  đấu với PvP; thắng lần đầu nhận thưởng đầy đủ, chơi lại nhận 30%.
- **Hệ thống nhân vật**: cấp độ và kinh nghiệm, vàng, 3 lớp nhân vật, cây kỹ năng, trang bị,
  trang phục, rương thưởng, năng lượng và chuỗi ngày học (streak).
- **Bài thi sát hạch (Benchmark)**: chặn cấp độ ở các mốc CEFR, phải thi đạt 80% mới lên tiếp.
- **Nhiệm vụ ngày** và rương mốc điểm, thành tựu.
- **Luyện hội thoại với AI** theo tình huống thực tế (gọi đồ, hỏi đường, phỏng vấn…) qua
  DeepSeek, kèm nhận xét sau buổi nói.
- **Thông báo đẩy** qua Firebase Cloud Messaging (nhắc giữ streak, nhắc nhiệm vụ 21:30).
- **Ảnh đại diện** lưu trên Google Drive; **API quản trị** ngân hàng câu hỏi cho tài khoản admin.
- Health check, rate limit, CORS / TrustedHost, Docker, Alembic migration, test và lint.

## Demo / Screenshot

Kiến trúc tổng thể của hệ thống:

![Kiến trúc tổng thể](docs/architecture.png)

Khi chạy ở môi trường `development`, tài liệu API tương tác có tại
`http://127.0.0.1:8000/docs` (Swagger UI) và `http://127.0.0.1:8000/redoc`.

## Yêu cầu (Prerequisites)

- Python 3.12 (khuyến nghị cài qua Conda: Miniconda, Anaconda hoặc Miniforge)
- PostgreSQL 16+ (hoặc Docker + Docker Compose)
- Tuỳ chọn, cho các tính năng tương ứng:
  - Tài khoản Firebase (FCM, Storage) – thông báo đẩy và ảnh từ vựng
  - API key DeepSeek – luyện hội thoại với AI
  - OAuth client Google – đăng nhập Google và lưu ảnh đại diện trên Drive
  - Máy chủ SMTP – gửi email đặt lại mật khẩu (môi trường dev dùng Mailpit)

## Cài đặt (Installation)

```bash
git clone https://github.com/thanhtungct7/quiz-game-backend.git
cd quiz-game-backend

cp .env.example .env                      # rồi sửa các giá trị cần thiết
conda env update -n backend -f environment.yml
conda activate backend

alembic upgrade head                      # tạo bảng
python -m scripts.import_quiz_bank --reset   # nạp ngân hàng câu hỏi TOEIC
python -m scripts.import_vocab_deck          # nạp các chặng từ vựng
```

Không dùng Conda thì có thể cài bằng `pip install -e ".[dev]"` trong một virtualenv Python 3.12.

## Sử dụng (Usage)

Chạy máy chủ phát triển:

```bash
uvicorn app.main:app --reload
# hoặc
make run
```

Chạy bằng Docker (API + Mailpit):

```bash
docker compose up --build
```

> `compose.yaml` nối vào mạng Docker có sẵn `tt-backend_tt-net`, nơi chạy container
> PostgreSQL `tt-postgres`. Nếu máy chưa có, hãy tạo mạng và container Postgres đó trước,
> hoặc sửa `compose.yaml` cho phù hợp.

Tạo tài khoản demo đã mở khoá toàn bộ (dùng khi thuyết trình):

```bash
python -m scripts.create_demo_account
python -m scripts.create_demo_account --email a1@kma.edu.vn --username A1Learner --level 10 --units 2
```

Ví dụ gọi API cơ bản:

```bash
# Đăng nhập, lấy token
curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@quizgame.dev", "password": "admin123"}'

# Gọi API cần đăng nhập
curl http://127.0.0.1:8000/api/v1/profile/me \
  -H "Authorization: Bearer <access_token>"
```

WebSocket nhận token qua query string, ví dụ `ws://127.0.0.1:8000/api/v1/duo/ws?token=<access_token>`.
Có sẵn script kiểm thử nhanh luồng đấu khi máy chủ đang chạy:

```bash
python -m scripts.duo_smoke     # đấu 1v1
python -m scripts.pve_smoke     # đánh quái
```

Các nhóm API chính (tiền tố `/api/v1`):

| Nhóm | Tiền tố | Nội dung |
| --- | --- | --- |
| Health | `/health` | `live`, `ready` (kiểm tra kết nối DB) |
| Xác thực | `/auth`, `/users` | Đăng ký, đăng nhập, Google, refresh, logout, quên/đặt lại mật khẩu, thông tin tài khoản, ảnh đại diện |
| Nội dung | `/courses`, `/units`, `/lessons`, `/challenges`, `/topics` | Cây lộ trình, câu hỏi, nộp bài |
| Tiến độ, hồ sơ | `/progress`, `/profile` | Tiến độ từng bài, hồ sơ cá nhân và hồ sơ người chơi khác |
| Đấu 1v1 | `/duo` | Lịch sử trận, thống kê, bảng xếp hạng, phòng, WebSocket `/duo/ws` |
| Nhân vật | `/game` | Cấp độ, lớp, kỹ năng, loadout, trang bị, cửa hàng, mùa giải, bài thi sát hạch |
| Nhiệm vụ | `/quests` | Nhiệm vụ ngày, nhận thưởng và rương mốc |
| Đánh quái | `/battles` | Danh sách quái, quái canh bài học, lịch sử, WebSocket `/battles/ws` |
| Hội thoại AI | `/conversations` | Tình huống, phiên hội thoại, kết thúc và nhận xét |
| Thông báo | `/notifications` | Đăng ký / huỷ token FCM của thiết bị |
| Quản trị | `/admin/...` | CRUD ngân hàng câu hỏi (chỉ admin) |

Mô tả chi tiết luật chơi, giao thức WebSocket và các quyết định thiết kế nằm ở
[`docs/DESIGN.md`](docs/DESIGN.md).

Kiểm tra chất lượng mã:

```bash
pytest            # hoặc: make test
ruff check .      # make lint chạy cả ruff và mypy
mypy app
```

Sau khi sửa model, tạo migration mới:

```bash
alembic revision --autogenerate -m "mo ta thay doi"
alembic upgrade head
```

## Cấu hình (Configuration)

Mọi cấu hình đọc từ file `.env` (mẫu đầy đủ trong `.env.example`). Các biến quan trọng:

| Biến | Ý nghĩa |
| --- | --- |
| `ENVIRONMENT` | `development` / `staging` / `production`. Ở `production` tài liệu API bị tắt |
| `SECRET_KEY` | Khoá ký JWT. Tạo bằng `openssl rand -hex 32`, **bắt buộc đổi khi triển khai** |
| `DATABASE_URL` | Chuỗi kết nối PostgreSQL, dạng `postgresql+asyncpg://user:pass@host:5432/db` |
| `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS` | Thời hạn token |
| `CORS_ORIGINS`, `ALLOWED_HOSTS` | Danh sách JSON. Tên miền ngrok hoặc IP LAN mà app gọi tới phải có trong `ALLOWED_HOSTS` |
| `GOOGLE_WEB_CLIENT_ID` | OAuth client loại *Web*, trùng với `GOOGLE_WEB_CLIENT_ID` bên app Android |
| `PASSWORD_RESET_URL` | Link trong email đặt lại mật khẩu (mặc định `quizgame://reset-password`) |
| `FIRST_ADMIN_EMAIL`, `FIRST_ADMIN_PASSWORD`, `FIRST_ADMIN_USERNAME` | Tạo sẵn một admin khi khởi động (để trống thì bỏ qua) |
| `SMTP_*` | Máy chủ gửi mail. Dev dùng Mailpit (`localhost:1025`, xem thư tại `http://localhost:8025`) |
| `GOOGLE_DRIVE_*` | Lưu ảnh đại diện trên Drive. Lấy refresh token bằng `python -m scripts.google_drive_authorize <client_secret.json>`; để trống thì ảnh lưu tạm trong bộ nhớ |
| `FIREBASE_CREDENTIALS_FILE` | Đường dẫn file service account Firebase (để ngoài repo). Để trống thì thông báo chỉ được ghi log |
| `STREAK_REMINDER_HOUR` | Giờ (giờ Việt Nam) bắt đầu nhắc người chưa học trong ngày |
| `DEEPSEEK_API_KEY`, `DEEPSEEK_*` | Khoá và model cho hội thoại AI. Để trống thì `/conversations` trả `503` |
| `RATE_LIMIT_ENABLED`, `TRUSTED_PROXIES` | Giới hạn tần suất gọi API và danh sách proxy tin cậy |

> Máy chủ giữ trạng thái trận đấu trong bộ nhớ tiến trình, nên chỉ chạy **một worker**
> Uvicorn. Không commit file `.env`, `client_secret_*.json` hay khoá Firebase lên Git.

## Cấu trúc thư mục (Project Structure)

```
duo-game-back/
├── app/
│   ├── main.py              # Khởi tạo FastAPI, middleware, seed catalog khi khởi động
│   ├── api/
│   │   ├── router.py        # Gộp toàn bộ router dưới /api/v1
│   │   ├── dependencies.py  # Dependency: phiên DB, người dùng hiện tại, quyền admin, service
│   │   └── routes/          # Endpoint REST + WebSocket, chia theo miền:
│   │                        #   auth, content, progress, profile, duo, game, pve,
│   │                        #   conversation, notification
│   ├── core/                # Cấu hình, bảo mật (JWT, Argon2), logging, rate limit, lỗi
│   ├── db/                  # Base SQLAlchemy và async session
│   ├── models/              # Model ORM theo miền
│   ├── schemas/             # Schema Pydantic cho request/response
│   ├── repository/          # Truy vấn cơ sở dữ liệu
│   └── services/            # Nghiệp vụ: chấm điểm, ghép trận, chiến đấu, phần thưởng,
│                            #   mùa giải, nhiệm vụ, AI, email, thông báo đẩy...
├── alembic/                 # Migration cơ sở dữ liệu
├── data/
│   ├── quiz/                # Ngân hàng câu hỏi TOEIC (JSON)
│   ├── vocab/               # Bộ từ vựng 4000 Essential English Words
│   └── resources/           # Từ điển, danh sách từ Oxford 5000...
├── scripts/                 # Nạp dữ liệu, sửa dữ liệu, tạo tài khoản demo, smoke test
├── tests/                   # Pytest
├── docs/                    # Sơ đồ kiến trúc, ghi chú thiết kế chi tiết
├── compose.yaml             # API + Mailpit
├── Dockerfile
├── environment.yml          # Môi trường Conda
├── Makefile                 # run, test, lint, format, migrate, docker-up...
├── pyproject.toml
└── .env.example
```
