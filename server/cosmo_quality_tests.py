"""Native Cosmo quality regression checks, run in its isolated image."""
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from handlers import youtube
from services.platforms.youtube_media import build_ytdlp_youtube_options, get_quality_format


class QualityTests(unittest.IsolatedAsyncioTestCase):
    def test_client_options_expose_adaptive_formats_and_respect_override(self):
        with patch.dict("os.environ", {"YTDLP_YOUTUBE_PLAYER_CLIENT": ""}):
            clients = build_ytdlp_youtube_options()["extractor_args"]["youtube"]["player_client"]
            self.assertIn("web_embedded", clients)
            self.assertIn("android", clients)
        with patch.dict("os.environ", {"YTDLP_YOUTUBE_PLAYER_CLIENT": "web_safari"}):
            clients = build_ytdlp_youtube_options()["extractor_args"]["youtube"]["player_client"]
            self.assertEqual(clients, ["web_safari"])

    def test_quality_limits_respect_portrait_and_landscape(self):
        self.assertEqual(get_quality_format("best", {}), "bestvideo+bestaudio/best")
        self.assertIn("width<=720", get_quality_format("balanced", {"width": 1080, "height": 1920}))
        self.assertIn("height<=720", get_quality_format("balanced", {"width": 1920, "height": 1080}))
        self.assertIn("width<=480", get_quality_format("saver", {"width": 1080, "height": 1920}))
        self.assertIn("height<=480", get_quality_format("saver", {"width": "unknown"}))

    async def test_best_bypasses_low_progressive_and_requests_merge(self):
        stream = {"height": 640, "width": 360, "url": "https://example.invalid/video"}
        with patch.object(youtube, "download_stream", AsyncMock()) as direct, patch.object(youtube, "download_with_ytdlp_metrics", AsyncMock(return_value=Mock())) as merged:
            await youtube._download_youtube_media(stream, {"webpage_url": "https://youtu.be/example"}, "test.mp4", None,
                                                   1, 1, None, None, format_selector="bestvideo+bestaudio/best")
            direct.assert_not_awaited()
            self.assertEqual(merged.call_args.args[2], "bestvideo+bestaudio/best")

    async def test_saved_quality_reaches_downloader_and_cache_is_separated(self):
        keys = set()
        for quality in ("best", "balanced", "saver"):
            for document in ("on", "off"):
                settings = {"video_quality": quality, "as_document": document, "captions": "off", "delete_message": "off"}
                message = SimpleNamespace(from_user=SimpleNamespace(id=1, username="owner"),
                    chat=SimpleNamespace(id=1, type="private"), message_id=42, business_connection_id=None,
                    text="https://www.youtube.com/watch?v=example", caption=None,
                    answer=AsyncMock(return_value=Mock()))
                async def flow(**kwargs):
                    keys.add(kwargs["cache_key"])
                    self.assertIn("quality-v2:" + quality, kwargs["cache_key"])
                    await kwargs["download_media"]()
                    return None
                with ExitStack() as stack:
                    for name, value in {
                        "should_skip_duplicate_business_message": AsyncMock(return_value=False),
                        "claim_message_request": AsyncMock(return_value=Mock()),
                        "send_analytics": AsyncMock(), "react_to_message": AsyncMock(),
                        "load_user_settings": AsyncMock(return_value=settings),
                        "get_bot_url": AsyncMock(return_value="https://t.me/example_bot"),
                        "_get_youtube_video_with_timeout": AsyncMock(return_value={"id": "example", "webpage_url": message.text, "title": "Video", "width": 1080, "height": 1920}),
                        "safe_edit_text": AsyncMock(), "safe_delete_message": AsyncMock(),
                        "update_info": AsyncMock(), "run_single_media_flow": AsyncMock(side_effect=flow),
                        "handle_download_error": AsyncMock(), "_download_youtube_media": AsyncMock(),
                    }.items():
                        stack.enter_context(patch.object(youtube, name, value))
                    await youtube.download_video(message)
                    youtube.handle_download_error.assert_not_awaited()
                    youtube._download_youtube_media.assert_awaited_once()
                    selector = youtube._download_youtube_media.call_args.kwargs["format_selector"]
                    self.assertEqual(selector, get_quality_format(quality, {"width": 1080, "height": 1920}))
        self.assertEqual(len(keys), 6)


if __name__ == "__main__":
    unittest.main()
