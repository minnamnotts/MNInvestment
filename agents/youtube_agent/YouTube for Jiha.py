import os
import re
import shutil
import tempfile
import time
import json
from datetime import datetime

import anthropic
import requests
from dotenv import load_dotenv

# --- [설정 영역] ---
_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(os.path.dirname(_script_dir))  # agents/youtube_agent → 프로젝트 루트
load_dotenv(os.path.join(_script_dir, ".env"))
load_dotenv(os.path.expanduser("~/.env"))

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
YOUTUBE_API_KEY   = os.environ.get("YOUTUBE_API_KEY")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
# MNI for Jiha 채널로만 발송 (지하 채널 전용, CHAT_ID 사용 안 함)
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_JIHA_ID")

if not all([ANTHROPIC_API_KEY, YOUTUBE_API_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
    missing = [k for k, v in {
        "ANTHROPIC_API_KEY":   ANTHROPIC_API_KEY,
        "YOUTUBE_API_KEY":     YOUTUBE_API_KEY,
        "TELEGRAM_BOT_TOKEN":  TELEGRAM_TOKEN,
        "TELEGRAM_JIHA_ID":    TELEGRAM_CHAT_ID,
    }.items() if not v]
    print(f"❌ 누락된 환경변수: {', '.join(missing)}")
    exit(1)

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# 이미 처리한 영상 ID 저장 (지하봇 전용, 서머리봇과 분리) — 프로젝트 루트 기준
_PROCESSED_IDS_FILE = os.path.join(_project_root, "youtube_jiha_processed.json")
_LOCK_FILE = os.path.join(_project_root, "youtube_jiha.lock")
_MAX_PROCESSED_IDS = 500  # 최대 보관 개수 (오래된 것부터 삭제)


def _acquire_lock() -> bool:
    """한 번에 하나의 인스턴스만 실행되도록 lock. 성공 시 True."""
    try:
        if os.path.exists(_LOCK_FILE):
            try:
                with open(_LOCK_FILE, "r") as f:
                    pid = int(f.read().strip())
                os.kill(pid, 0)  # 프로세스 존재하면 예외 없음
                print(f"⏭ 다른 인스턴스 실행 중 (PID {pid}) → 종료")
                return False
            except (ValueError, ProcessLookupError, OSError):
                os.remove(_LOCK_FILE)
        with open(_LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        print(f"⚠️  Lock 오류: {e}")
        return True  # 실패해도 진행


def _release_lock() -> None:
    try:
        if os.path.exists(_LOCK_FILE):
            os.remove(_LOCK_FILE)
    except Exception:
        pass


def _load_processed_ids() -> set:
    try:
        with open(_PROCESSED_IDS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_processed_id(video_id: str, current: set) -> None:
    ids = list(current | {video_id})
    if len(ids) > _MAX_PROCESSED_IDS:
        ids = ids[-_MAX_PROCESSED_IDS:]
    with open(_PROCESSED_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f, ensure_ascii=False, indent=0)


# ────────────────────────────────────────────────
# 채널 설정 (최신 영상 기준)
# ────────────────────────────────────────────────
CHANNELS = {
    "소수몽키": {
        "id": "UCC3yfxS5qC6PCwDzetUuEWg",
    },
    "서재형의 투자교실": {
        "id": "UCtmKBFeri9hx9DOaVSSvvvw",
    },
    "경제사냥꾼": {
        "id": "UC7usMJDHmtbs_oegmzQKKMA",
    },
    "올랜도킴": {
        "id": "UCwSSqi-s0wcH6pJbH3YPZqQ",
    },
    "태린이아빠 주식투자": {
        "id": "UCxK-Xc4gLgfweW17kmzKN1g",
    },
    "위즈덤투스": {
        "id": "UCiAM9aJjVTmhtYgj0cyTPzA",
    },
}


# ────────────────────────────────────────────────
# 1) 영상 가져오기
# ────────────────────────────────────────────────
_MAX_DURATION_MIN = 9999  # 제한 없음 (과거 40분 제한 제거)


def _parse_duration_iso8601(duration: str) -> float:
    """PT1H30M15S → 분 단위로 변환"""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration or "")
    if not match:
        return 0
    h, m, s = (int(g) if g else 0 for g in match.groups())
    return h * 60 + m + s / 60


def _resolve_channel_id(handle: str) -> str | None:
    """@핸들 또는 핸들명으로 채널 ID(UC...) 조회"""
    handle = (handle or "").lstrip("@").strip()
    if not handle:
        return None
    try:
        url = "https://www.googleapis.com/youtube/v3/channels"
        params = {"key": YOUTUBE_API_KEY, "part": "id", "forUsername": handle}
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        if "error" not in data and data.get("items"):
            return data["items"][0]["id"]
        # forUsername 실패 시 검색으로 채널 찾기 (@핸들용)
        search_url = "https://www.googleapis.com/youtube/v3/search"
        search_params = {"key": YOUTUBE_API_KEY, "part": "snippet", "q": handle, "type": "channel", "maxResults": 1}
        sr = requests.get(search_url, params=search_params, timeout=10)
        sdata = sr.json()
        if "error" not in sdata and sdata.get("items"):
            return sdata["items"][0]["snippet"]["channelId"]
        return None
    except Exception:
        return None


def get_latest_video(channel_name: str, channel_id: str, keyword: str = None) -> dict | None:
    try:
        search_url = "https://www.googleapis.com/youtube/v3/search"
        search_params = {
            "key":        YOUTUBE_API_KEY,
            "channelId":  channel_id,
            "part":       "snippet",
            "order":      "date",
            "maxResults": 15,
            "type":       "video",
        }
        if keyword:
            search_params["q"] = keyword

        r    = requests.get(search_url, params=search_params, timeout=10)
        data = r.json()
        if "error" in data:
            print(f"    ❌ YouTube API 오류: {data['error'].get('message', '')}")
            return None

        items = data.get("items", [])
        if not items:
            print(f"    ⚠️  {channel_name}: 영상 없음 (키워드: {keyword})")
            return None

        video_ids = [it["id"]["videoId"] for it in items]
        vurl      = "https://www.googleapis.com/youtube/v3/videos"
        vparams   = {"key": YOUTUBE_API_KEY, "part": "contentDetails", "id": ",".join(video_ids)}
        vr        = requests.get(vurl, params=vparams, timeout=10)
        vdata     = vr.json()

        if "error" in vdata:
            print(f"    ❌ YouTube API 오류: {vdata['error'].get('message', '')}")
            return None

        by_id = {x["id"]: x.get("contentDetails", {}).get("duration", "PT0S") for x in vdata.get("items", [])}
        items_by_id = {it["id"]["videoId"]: it for it in items}

        for vid in video_ids:
            dur_min = _parse_duration_iso8601(by_id.get(vid, "PT0S"))
            if dur_min > _MAX_DURATION_MIN:
                print(f"    ⏭ {vid[:8]}... 영상 길이 {dur_min:.0f}분 (>{_MAX_DURATION_MIN}분) → 스킵")
                continue
            item    = items_by_id[vid]
            snippet = item["snippet"]
            return {
                "video_id":    vid,
                "title":       snippet.get("title", ""),
                "description": snippet.get("description", "")[:300],
                "published":   snippet.get("publishedAt", "")[:10],
                "url":         f"https://www.youtube.com/watch?v={vid}",
                "channel":     channel_name,
                "keyword":     keyword or "최신",
            }

        print(f"    ⚠️  {channel_name}: {_MAX_DURATION_MIN}분 이하 영상 없음")
        return None
    except Exception as e:
        print(f"    ❌ YouTube API 오류 ({channel_name}): {e}")
        return None


# ────────────────────────────────────────────────
# 2) 자막 추출 (유튜브 자막 → 없으면 Whisper 자동생성)
# ────────────────────────────────────────────────
def _get_youtube_transcript(video_id: str) -> str | None:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled

        # 0) 언어 무관, 사용 가능한 자막 아무거나
        try:
            transcript = YouTubeTranscriptApi.get_transcript(video_id)
            full_text = " ".join([t["text"] for t in transcript])
            if full_text.strip():
                return full_text[:8000]
        except (NoTranscriptFound, TranscriptsDisabled):
            pass

        # 1) ko → en 순으로 수동/자동 자막 시도
        for lang in ["ko", "en", "ko-KR", "en-US"]:
            try:
                transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=[lang])
                full_text = " ".join([t["text"] for t in transcript])
                return full_text[:8000] if full_text.strip() else None
            except (NoTranscriptFound, TranscriptsDisabled):
                continue

        # 2) list_transcripts로 사용 가능한 자막 중 아무거나 시도
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            for t in transcript_list:
                try:
                    transcript = t.fetch()
                    full_text = " ".join([x["text"] for x in transcript])
                    if full_text.strip():
                        return full_text[:8000]
                except Exception:
                    continue
        except Exception:
            pass
        return None
    except Exception:
        return None


def _get_ffmpeg_path() -> str | None:
    for base in ("/opt/homebrew/bin", "/usr/local/bin"):
        ffmpeg = os.path.join(base, "ffmpeg")
        if os.path.isfile(ffmpeg) or os.path.isfile(ffmpeg + ".exe"):
            return base
    return None


def _get_js_runtime_path() -> dict | None:
    for runtime, exe in (("node", "node"), ("deno", "deno")):
        path = shutil.which(exe)
        if not path:
            for base in ("/opt/homebrew/bin", "/usr/local/bin"):
                candidate = os.path.join(base, exe)
                if os.path.isfile(candidate):
                    path = candidate
                    break
        if path:
            return {runtime: {"path": path}}
    return None


def _generate_transcript_with_whisper(video_id: str) -> str | None:
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        import yt_dlp

        ffmpeg_dir = _get_ffmpeg_path()
        if not ffmpeg_dir:
            return None
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

        js_runtime = _get_js_runtime_path()
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "audio.%(ext)s")
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": out_path,
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
                "quiet": True,
                "ffmpeg_location": ffmpeg_dir,
            }
            if js_runtime:
                ydl_opts["js_runtimes"] = js_runtime

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            m4a_path = os.path.join(tmpdir, "audio.m4a")
            if not os.path.exists(m4a_path):
                return None

            import whisper
            model = whisper.load_model("base")
            result = model.transcribe(m4a_path, language="ko", fp16=False)
            text = (result.get("text") or "").strip()[:8000]
            return text if text else None
    except Exception:
        return None


def _get_transcript_via_ytdlp(video_id: str) -> str | None:
    """yt-dlp로 자막 파일 다운로드 (YouTube API 실패 시 보조 수단)"""
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        import yt_dlp
        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts = {
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": ["ko", "en", "ko.*", "en.*"],
                "outtmpl": os.path.join(tmpdir, "%(id)s"),
                "quiet": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            for f in os.listdir(tmpdir):
                if f.endswith((".vtt", ".srt")):
                    path = os.path.join(tmpdir, f)
                    with open(path, "r", encoding="utf-8", errors="ignore") as fp:
                        raw = fp.read()
                    lines = [
                        ln.strip() for ln in raw.split("\n")
                        if ln.strip() and not ln.startswith("WEBVTT") and "-->" not in ln and not re.match(r"^\d+$", ln.strip())
                    ]
                    text = " ".join(lines).strip()[:8000]
                    if text:
                        return text
        return None
    except Exception:
        return None


def get_transcript(video_id: str) -> str | None:
    text = _get_youtube_transcript(video_id)
    if text:
        return text
    print(f"    📝 API 자막 없음 → yt-dlp 자막 다운로드 시도...")
    text = _get_transcript_via_ytdlp(video_id)
    if text:
        return text
    print(f"    📝 yt-dlp 자막 없음 → Whisper 음성 인식 시도...")
    return _generate_transcript_with_whisper(video_id)


# ────────────────────────────────────────────────
# 3) Claude 요약
# ────────────────────────────────────────────────
def summarize_with_claude(video_info: dict, transcript: str) -> dict:
    prompt = f"""다음은 유튜브 채널 [{video_info['channel']}]의 영상입니다.

제목: {video_info['title']}
날짜: {video_info['published']}
URL: {video_info['url']}

자막 내용:
{transcript}

투자자 관점에서 아래 JSON 형식으로만 요약하세요.
JSON 외 다른 텍스트 없이 순수 JSON만 출력하세요.

[지시사항]
- key_topics: 핵심 주제를 3~4줄로 자세히 서술. 영상에서 다룬 주요 논점·배경·결론을 구체적으로 설명.
- stock_mentions: 자막에서 언급된 종목을 최대한 많이 포함. 5~8개 정도 수준으로 추출 (언급이 적으면 실제 개수만).

{{
  "key_topics": "3~4줄로 핵심 주제를 자세히 설명한 문단",
  "market_view": "시장 전반 전망 요약 (2-3문장)",
  "stock_mentions": [
    {{
      "name": "종목명",
      "view": "긍정/중립/부정",
      "reason": "이유 한 줄"
    }}
  ],
  "macro_points": "매크로 관련 내용 (금리, 환율, 지표 등) (2문장 이내)",
  "action_items": ["투자자 주목 포인트 1", "포인트 2"],
  "overall_sentiment": "긍정/중립/부정",
  "one_line_summary": "영상 전체 한 줄 요약"
}}"""

    try:
        message = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
        text   = message.content[0].text.strip()
        text   = text.replace("```json", "").replace("```", "").strip()
        result = json.loads(text)
        return result
    except Exception as e:
        print(f"    ❌ Claude 요약 오류: {e}")
        return {
            "one_line_summary":  "요약 실패",
            "market_view":       "",
            "key_topics":        "",
            "stock_mentions":    [],
            "macro_points":      "",
            "action_items":      [],
            "overall_sentiment": "중립",
        }


# ────────────────────────────────────────────────
# 4) 텔레그램 발송
# ────────────────────────────────────────────────
SENTIMENT_EMOJI = {"긍정": "🟢", "중립": "🟡", "부정": "🔴"}


def format_youtube_message(video_info: dict, summary: dict) -> str:
    sentiment = SENTIMENT_EMOJI.get(summary.get("overall_sentiment", "중립"), "🟡")

    lines = [
        f"🎬 *{video_info['channel']}* | 🔍 {video_info['keyword']}",
        f"📹 {video_info['title']}",
        f"📅 {video_info['published']} | {sentiment} {summary.get('overall_sentiment', '')}",
        f"🔗 {video_info['url']}",
        "─────────────────────",
        "💬 *한줄 요약*",
        summary.get("one_line_summary", ""),
    ]

    topics = summary.get("key_topics", "")
    if topics:
        if isinstance(topics, list):
            topics = "\n".join(topics)
        lines += [f"\n🏷 *핵심 주제*", topics]

    if summary.get("market_view"):
        lines += [f"\n📊 *시장 전망*", summary["market_view"]]

    stocks = summary.get("stock_mentions", [])
    if stocks:
        lines.append(f"\n📌 *언급 종목*")
        for s in stocks[:8]:
            e = SENTIMENT_EMOJI.get(s.get("view", "중립"), "🟡")
            lines.append(f"{e} {s.get('name', '')} — {s.get('reason', '')}")

    if summary.get("macro_points"):
        lines += [f"\n🌍 *매크로*", summary["macro_points"]]

    actions = summary.get("action_items", [])
    if actions:
        lines.append(f"\n⚡ *주목 포인트*")
        for a in actions:
            lines.append(f"• {a}")

    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    try:
        url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print(f"    ✈️  텔레그램 발송 완료")
            return True
        else:
            print(f"    ❌ 텔레그램 오류: {r.text[:100]}")
            return False
    except Exception as e:
        print(f"    ❌ 텔레그램 오류: {e}")
        return False


# ────────────────────────────────────────────────
# 메인 실행
# ────────────────────────────────────────────────
def run_youtube_summary():
    processed_ids = _load_processed_ids()

    for channel_name, config in CHANNELS.items():
        print(f"\n{'='*60}")
        print(f"📺 {channel_name} | 키워드: {config.get('keyword', '최신')}")
        print("=" * 60)

        channel_id = config.get("id")
        if not channel_id and config.get("username"):
            channel_id = _resolve_channel_id(config["username"])
            if not channel_id:
                print(f"    ⚠️  채널 ID 조회 실패 (username: {config['username']})")
                continue
        if not channel_id:
            print(f"    ⚠️  채널 id 또는 username 없음")
            continue

        video_info = get_latest_video(channel_name, channel_id, config.get("keyword"))
        if not video_info:
            continue

        if video_info["video_id"] in processed_ids:
            print(f"  ⏭ 이미 처리됨 (건너뜀): {video_info['title'][:40]}...")
            continue

        print(f"  ✓ 영상: {video_info['title'][:50]}...")
        print(f"  📝 자막 추출 중...")
        transcript = get_transcript(video_info["video_id"])

        if not transcript:
            print(f"  ⏭ 자막 추출 불가 → 영상 스킵")
            continue

        print(f"  ✓ 자막 {len(transcript)}자 추출 완료")
        print(f"  🤖 Claude 요약 중...")
        summary = summarize_with_claude(video_info, transcript)
        msg = format_youtube_message(video_info, summary)
        print(msg)
        if send_telegram(msg):
            processed_ids.add(video_info["video_id"])
            _save_processed_id(video_info["video_id"], processed_ids)
        time.sleep(3)


if __name__ == "__main__":
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        print("📦 youtube-transcript-api 설치 중...")
        os.system("pip3 install youtube-transcript-api")

    if not _acquire_lock():
        exit(0)
    try:
        run_youtube_summary()
    finally:
        _release_lock()
