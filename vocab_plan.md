# Plan: bài học từ vựng có hình ảnh và âm thanh (4000 Essential English Words)

Dữ liệu: `data/vocab/vocab_4000.json`, do `scripts/convert.py` tạo ra (3871 từ: 3600 từ Book 1–6, 271 từ Extra).
Media: Firebase Storage, bucket `duo-d298a.firebasestorage.app`, thư mục `vocab/images/` và `vocab/audio/`.

## Các quyết định đã chốt

| Vấn đề | Chọn | Lý do |
|---|---|---|
| Đặt từ vựng ở đâu | **Chèn chặng từ vựng vào course có sẵn "Ngân hàng câu hỏi", xen kẽ 1 chặng TOEIC, 1 chặng từ vựng** | Không cần thêm course, sửa cache Room hay làm tab. Người mới không phải qua hàng trăm bài từ vựng mới gặp bài TOEIC. App tự tải lại cây bài vì ETag đổi. |
| Khoá bài | **Sửa luật khoá trong `LearnViewModel`**: bài đã xong luôn mở, bài chưa làm mở khi bài liền trước đã xong | Luật hiện tại khoá **mọi bài sau bài chưa xong đầu tiên**. Backend không kiểm tra khoá bài, nên chỉ cần sửa app. |
| Lưu media | **Firebase Storage**; database lưu đường dẫn trong bucket (`vocab/images/01_0001.jpg`); app tự ghép URL | Media đã có sẵn trên Firebase. Đổi chỗ lưu sau này chỉ phải sửa phần ghép URL. |
| Quyền đọc | **Rule đọc công khai cho `vocab/**`** | Không phải lưu token trong database. Đây là tài liệu học công khai. |
| Dạng câu đợt đầu | 3 dạng **không cần migration** | `challenge_options` đã có `image_src`/`audio_src`, và API cùng push PvE/Duo đã trả về hai trường này. |

## Chèn vào đâu

Course hiện có 60 chặng TOEIC. Mỗi Book: 600 từ × 3 dạng câu = 1800 câu = 180 bài = 9 chặng từ vựng, tổng 54 chặng.

```
TOEIC 250-450 · Chặng 1
Từ vựng Book 1 · Chặng 1
TOEIC 250-450 · Chặng 2
Từ vựng Book 1 · Chặng 2
…
TOEIC 650-990 · Chặng 6
Từ vựng Book 6 · Chặng 9      ← chặng từ vựng cuối cùng
TOEIC 650-990 · Chặng 7 … 12  ← 6 chặng TOEIC cuối liền nhau
```

Lộ trình tăng từ 60 lên 114 chặng.

---

## Phase 0: Dữ liệu và media (ĐÃ XONG)

- [x] `scripts/convert.py` đọc `collection.anki21` (không đọc file giả `collection.anki2`), lấy field theo tên.
- [x] Mỗi từ có `book`, `unit` (1–30 trong Book), `index` (1–3600), `word`, `ipa`, `meaning`, `meaning_masked`, `example`, `example_masked`, `example_answer`, `image`, `audio_word`, `audio_meaning`, `audio_example`.
- [x] Mỗi media ghi `path` trong bucket (`vocab/images/01_0001.jpg`) và `anki_file` (tên số ban đầu).
- [x] `meaning_masked` che từ ở cả phần in nghiêng lẫn chỗ in nghiêng bỏ sót ("You can ___ an animal"). Kiểm tra: không định nghĩa nào còn lộ từ.
- [x] `example_answer` giữ dạng đã chia ("arrived"). 2 từ không có câu ví dụ (`academic`, `acceptance`, field bị lẫn IPA) → `example_masked = null`.
- [x] `scripts/firebase_vocab_media.py`: copy trong bucket từ tên số sang `vocab/...`, đặt đúng content-type và `Cache-Control: public, max-age=31536000, immutable`. Upload 2 file lúc đầu bị sót (`20_0984.jpg` của "barrier", `23_0460.mp3` của "web"). File số cũ giữ nguyên.
- [x] Test: `tests/test_vocab_convert.py`.
- [ ] **Bạn cần làm:** thêm rule vào Firebase Console → Storage → Rules (giữ các rule đang có):
  ```
  match /vocab/{allPaths=**} {
    allow read: if true;
    allow write: if false;
  }
  ```
  Kiểm tra: mở `https://firebasestorage.googleapis.com/v0/b/duo-d298a.firebasestorage.app/o/vocab%2Fimages%2F01_0001.jpg?alt=media` trên trình duyệt thấy ảnh.
- [ ] (Tuỳ chọn, sau khi app chạy ổn) xoá 14.942 file số ở thư mục gốc bucket.

## Bẫy đã phát hiện, phải xử lý

1. **Duo bốc câu ngẫu nhiên từ toàn bộ ngân hàng** (`list_random_filtered`), nên vừa import xong là câu chọn hình xuất hiện trong Duo. → Chỉ chạy import sau bước 2.3.
2. **Bài thi sát hạch cũng bốc câu từ các chặng đã học.** → Bước 2.3 sửa luôn `BenchmarkExamScreen`.
3. **Độ mạnh quái đi theo vị trí chặng** (`tier_for_unit`: 8 chặng lên 1 bậc, tối đa bậc 6). Chèn thêm chặng thì quái mạnh lên sớm hơn. Nên chơi thử.
4. **`text` của option bắt buộc khác rỗng.** Với câu chọn hình, `text` là từ đó, nên app **phải ẩn chữ** khi option có ảnh.
5. **Đáp án trùng nhau** (giống lỗi 29 câu mc4): sinh đáp án nhiễu phải loại trùng, không phân biệt hoa thường.
6. **`uq_units_course_id_order_index`**: đánh số lại chặng phải làm hai bước trong một transaction (cộng tạm +10000 rồi mới gán số mới).
7. **Không dùng `import_quiz_bank --reset`**: lệnh này xoá tiến độ người chơi.
8. **Đường dẫn trong URL Firebase phải mã hoá `/` thành `%2F`**: `o/vocab%2Fimages%2F01_0001.jpg?alt=media`.

---

## Phase 1: Backend: script import `scripts/import_vocab_deck.py`

Làm theo mẫu `import_quiz_bank.py` (SQLAlchemy Core, insert theo lô, `_get_or_create_topics`). Đọc `data/vocab/vocab_4000.json`, chỉ lấy các từ có `book`.

**Chèn chặng**
- Tìm course theo `COURSE_TITLE` ("Ngân hàng câu hỏi"); lấy các chặng TOEIC theo `order_index`.
- Thứ tự mới: `TOEIC 1, Từ vựng 1, TOEIC 2, Từ vựng 2, …`; chặng TOEIC còn dư đứng cuối.
- Đánh số lại hai bước (bẫy 6). Bài và câu của chặng cũ giữ nguyên id, nên tiến độ không mất.
- Tên chặng: `Từ vựng Book 1 · Chặng 3`. Mô tả: `4000 Essential Words · Book 1 · agree, alcohol, arrive, …`.

**Bài trong chặng**
- Đi lần lượt từng unit sách (20 từ, sắp theo `index`). Mỗi unit sinh 6 bài theo vòng `Chọn hình → Định nghĩa → Điền câu → Chọn hình → Định nghĩa → Điền câu` (từ 1–10, rồi từ 11–20).
- Xếp bài nối tiếp vào chặng, đủ 20 bài thì sang chặng mới: 30 unit × 6 = 180 bài = 9 chặng mỗi Book.
- Từ không có `example_masked` thì dạng "Điền câu" thay bằng một câu "Định nghĩa" để bài vẫn đủ 10 câu.

**Ba dạng câu (đều là `SELECT`)**

| Dạng | `question` | Option |
|---|---|---|
| Chọn hình | `Chọn hình ảnh cho từ "backpack"` | 4 option: `text` = từ, `image_src` = `image.path` |
| Định nghĩa → từ | `meaning_masked` | 4 option: `text` = từ, `audio_src` = `audio_word.path` của từ đó |
| Điền câu | `example_masked` | 4 option: `text` = `example_answer` |

- **Đáp án nhiễu:** 3 từ khác trong cùng unit sách, `Random(seed)` cố định, loại trùng không phân biệt hoa thường.
- **Challenge:** `source_ref = "eew:<note_id>:<kind>"`, `tags = ["vocabulary", "<kind>"]`, `topic_id` = topic `vocabulary`, `correct_text`, `explanation = "agree /əˈɡriː/ — To agree is… — The students agree…"`, `cefr_level` ước lượng (Book 1 A1, Book 2 A2, Book 3–4 B1, Book 5 B2, Book 6 C1), `difficulty` (Book 1–2 EASY, 3–4 MEDIUM, 5–6 HARD).

**Lệnh**
- `python -m scripts.import_vocab_deck`: đã có chặng từ vựng thì dừng và báo lỗi.
- `python -m scripts.import_vocab_deck --remove`: xoá các chặng từ vựng rồi đánh số lại chặng TOEIC về 1–60 (in cảnh báo: mất tiến độ của các chặng từ vựng).

**Test `tests/test_import_vocab_deck.py`** (hàm thuần):
- mỗi câu có đúng 4 option, 1 đáp án đúng, không trùng
- 54 chặng × 20 bài × 10 câu; hai bài liền nhau khác dạng
- thứ tự xen kẽ đúng; thứ tự tương đối giữa các chặng TOEIC giữ nguyên
- cùng seed thì cho ra cùng kết quả

---

## Phase 2: App Android

### 2.1 Ghép URL media
- [ ] `buildConfigField("String", "MEDIA_BASE_URL", "\"https://firebasestorage.googleapis.com/v0/b/duo-d298a.firebasestorage.app/o/\"")`.
- [ ] `fun storageMediaUrl(path: String): String = BuildConfig.MEDIA_BASE_URL + URLEncoder.encode(path, "UTF-8") + "?alt=media"` (đặt cạnh `toAbsoluteMediaUrl` trong `MediaUrl.kt`).
- [ ] Unit test: `vocab/images/01_0001.jpg` → `…/o/vocab%2Fimages%2F01_0001.jpg?alt=media`.

### 2.2 Sửa luật khoá bài
File: `ui/screens/learn/LearnViewModel.kt`, hàm `toUnitUi`. Xét từng bài theo thứ tự lộ trình:
- đã hoàn thành → `COMPLETE`
- chưa hoàn thành và (là bài đầu tiên, hoặc bài liền trước đã hoàn thành) → `ACTIVE`
- còn lại → `LOCKED`

Nếu có chỗ tự cuộn tới bài ACTIVE thì cuộn tới bài ACTIVE **cuối cùng**. Unit test:
- người mới → chỉ bài đầu ACTIVE
- tiến độ liên tục → như luật cũ
- khoảng chưa làm xen giữa các bài đã xong → bài đầu của khoảng ACTIVE, bài đã xong phía sau vẫn COMPLETE

### 2.3 Đáp án dạng ảnh
Chỗ vẽ option: `BattleScreen.kt` (`QuestionBody`), `DuoMatchScreen.kt` (`QuestionBody`), `BenchmarkExamScreen.kt` (`ChallengeOptionCard`).
- [ ] Option có `imageSrc` thì hiện `AsyncImage` (Coil) tỉ lệ vuông và **ẩn chữ**; sau khi chấm thì hiện chữ nhỏ bên dưới. Giữ màu viền theo trạng thái.
- [ ] Tải trước ảnh khi nhận câu (`imageLoader.enqueue`), để không ăn vào đồng hồ.
- [ ] Ảnh lỗi thì hiện chữ.

### 2.4 Âm thanh
- [ ] `AudioPlayer` bọc `MediaPlayer`: một instance, phát file mới thì dừng file cũ, giải phóng khi thoát màn.
- [ ] Nút loa trên option có `audioSrc`, vùng bấm tách khỏi vùng chọn đáp án.

**Xong Phase 2 khi:** trên emulator chơi được bài "Chọn hình" và bài "Định nghĩa" ở cả PvE lẫn Duo; tài khoản cũ không bị khoá bài đã học.

---

## Phase 3: Kỹ năng Nghe (cần migration)

- [ ] Alembic: thêm `challenges.image_src` và `challenges.audio_src`; cập nhật model, schema và `challenge_presenter.py`.
- [ ] Importer thêm dạng **Nghe → chọn từ** và **Nhìn hình → chọn từ**, tag `listening`; dùng 271 từ Extra.
- [ ] App: thêm `imageSrc`/`audioSrc` vào `ChallengeDto`; phần đầu câu hiện ảnh hoặc nút phát to.

## Phase 4: Làm sau

- Radar kỹ năng (`yc.md` Sub-task 7) theo `challenges.tags`.
- Nhiệm vụ ngày "Học 20 từ mới", thành tựu "Master 1000 từ vựng" (đếm theo `source_ref` `eew:<note_id>:*`).

## Thứ tự làm tóm tắt

```
Phase 0  ✓ dữ liệu + media Firebase   → CÒN: bạn thêm Storage rule (hiện đọc vẫn 403)
Phase 1  ✓ scripts/import_vocab_deck.py (+ tests/test_import_vocab_deck.py)
Phase 2  ✓ 2.1 storageMediaUrl → ✓ 2.2 luật khoá → ✓ 2.3 đáp án ảnh → ✓ 2.4 nút loa
         ✓ đã import vào DB local (114 chặng) → CÒN: chơi thử trên emulator
Phase 3  câu Nghe / Nhìn hình
Phase 4  radar, nhiệm vụ
```

### Trạng thái 2026-09-15
- Firebase: 14.942/14.942 file ở `vocab/images|audio/…`, đúng content-type và Cache-Control. File số cũ ở thư mục gốc vẫn giữ.
- DB local: 54 chặng từ vựng, 1.080 bài, 10.800 câu, 43.200 đáp án; không đáp án trùng; tiến độ cũ còn nguyên (38 dòng).
- Test: backend đạt toàn bộ; app 168/168 unit test đạt (có `MediaUrlTest`, `LessonUnlockTest`).
- Gỡ ra: `python -m scripts.import_vocab_deck --remove`.
- Ảnh đóng gói trong app: tải từ Firebase mất ~1 s/ảnh vì bucket ở US-EAST1, nên `scripts/export_vocab_images.py` (chạy bằng `python3`, cần Pillow) xuất 3.870 ảnh WebP q65 (36,6 MB) vào `duo-game-app/app/src/main/assets/vocab/images/`. App đọc `file:///android_asset/vocab/images/<tên>.webp`, thiếu thì mới tải Firebase. Thêm hoặc đổi ảnh thì chạy lại script này rồi build lại app.
- Audio vẫn tải từ Firebase (có thể chậm ~1 s lần đầu bấm loa).
- Chưa làm: tự phát âm sau khi chấm.
- Lưu ý: lần khởi động backend tới, ultimate sẽ gắn lại vào các chặng đầu lộ trình theo thứ tự mới (có cả chặng từ vựng); quái lên bậc sớm hơn vì lộ trình dài hơn.
