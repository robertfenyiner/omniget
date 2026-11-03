"""Run with the Cosmo image: aiogram is intentionally absent on the web host."""
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

try:
    from aiogram import Bot, Dispatcher
    from aiogram.client.session.base import BaseSession
    from aiogram.types import Message, Update
except ImportError:
    Bot = None


if Bot:
    class RecordingSession(BaseSession):
        def __init__(self):
            super().__init__()
            self.methods = []

        async def close(self):
            pass

        async def make_request(self, bot, method, timeout=None):
            self.methods.append(method)
            if method.__api_method__ in ("answerCallbackQuery", "deleteMessage"):
                return True
            return Message.model_validate({"message_id": 100, "date": 1,
                "chat": {"id": 123, "type": "private"}, "from": {"id": bot.id, "is_bot": True, "first_name": "Bot"},
                "text": getattr(method, "text", None) or "Result"}, context={"bot": bot})

        async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
            yield b"fixture"


@unittest.skipUnless(Bot, "Requires the Cosmo aiogram runtime")
class CosmoRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import cosmo_integration
        self.module = importlib.reload(cosmo_integration)
        self.temp = tempfile.TemporaryDirectory()
        config = Path(self.temp.name) / "config.json"
        config.write_text(json.dumps({"owner_id": 123, "key": "fake-private-key", "web_url": "http://localhost"}))
        self.module.CONFIG = config
        self.session = RecordingSession()
        self.bot = Bot("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi", session=self.session)
        self.dp = Dispatcher()
        self.dp.include_router(self.module.router)

    async def asyncTearDown(self):
        await self.bot.session.close()
        self.temp.cleanup()

    def message(self, text=None, owner=123, **extra):
        return Message.model_validate(dict(message_id=42, date=1,
            chat={"id": 123, "type": "private"}, from_user={"id": owner, "is_bot": False, "first_name": "Owner"},
            text=text, **extra), context={"bot": self.bot})

    async def feed(self, message):
        return await self.dp.feed_update(self.bot, Update(update_id=1, message=message))

    async def test_help_uses_actual_capabilities(self):
        with patch.object(self.module, "request", AsyncMock(return_value={"ocr": True, "conversion": True})):
            await self.feed(self.message("/herramientas"))
        self.assertIn("OCR: listo", self.session.methods[-1].text)
        self.assertIn("Transcripción: no disponible", self.session.methods[-1].text)

    async def test_other_users_cannot_use_tools_or_study(self):
        with patch.object(self.module, "request", AsyncMock()) as request:
            await self.feed(self.message("/nota Private | text", owner=999))
            request.assert_not_awaited()
        self.assertFalse(self.session.methods)

    async def test_normal_platform_links_remain_cosmo_responsibility(self):
        for url in ("https://youtu.be/id", "https://instagram.com/p/id", "https://open.spotify.com/track/id", "https://fb.watch/id"):
            self.assertFalse(self.module.extra_link(self.message(url)))
        self.assertTrue(self.module.extra_link(self.message("https://vimeo.com/123")))
        self.assertFalse(self.module.extra_link(self.message("https://vimeo.com.evil.invalid/123")))

    async def test_file_buttons_reply_to_source(self):
        await self.feed(self.message(document={"file_id": "fixture", "file_unique_id": "u1", "file_name": "scan.pdf", "file_size": 10}))
        method = self.session.methods[-1]
        self.assertEqual(method.reply_parameters.message_id, 42)
        self.assertEqual(method.reply_markup.inline_keyboard[0][0].callback_data, "cosmotool:document")

    async def test_button_uses_clicking_owner_and_original_file(self):
        source = self.message(document={"file_id": "fixture", "file_unique_id": "u1", "file_name": "scan.pdf", "file_size": 10})
        button_message = self.message("Choose", owner=self.bot.id, reply_to_message=source)
        update = Update.model_validate({"update_id": 2, "callback_query": {"id": "callback-id", "chat_instance": "private",
            "from": {"id": 123, "is_bot": False, "first_name": "Owner"}, "message": button_message.model_dump(by_alias=True),
            "data": "cosmotool:document"}}, context={"bot": self.bot})
        with patch.object(self.module, "submit_file", AsyncMock()) as submit:
            await self.dp.feed_update(self.bot, update)
            self.assertEqual(submit.call_args.args[1].message_id, 42)
            self.assertEqual(submit.call_args.args[2], {"kind": "document"})
            self.assertIs(submit.call_args.kwargs["bot"], self.bot)

    async def test_other_users_cannot_click_private_actions(self):
        message = self.message("Choose", owner=self.bot.id)
        update = Update.model_validate({"update_id": 2, "callback_query": {"id": "callback-id", "chat_instance": "private",
            "from": {"id": 999, "is_bot": False, "first_name": "Other"}, "message": message.model_dump(by_alias=True),
            "data": "cosmotool:document"}}, context={"bot": self.bot})
        with patch.object(self.module, "submit_file", AsyncMock()) as submit:
            await self.dp.feed_update(self.bot, update)
            submit.assert_not_awaited()
        self.assertEqual(self.session.methods[-1].text, "Acceso privado.")

    async def test_voice_offers_transcription(self):
        await self.feed(self.message(voice={"file_id": "fixture", "file_unique_id": "u1", "duration": 1, "file_size": 10}))
        buttons = self.session.methods[-1].reply_markup.inline_keyboard
        self.assertEqual(buttons[0][0].callback_data, "cosmotool:transcribe")

    async def test_local_api_file_paths_are_confined_and_copied(self):
        root = Path(self.temp.name) / "shared"
        root.mkdir()
        source = root / "audio.wav"
        source.write_bytes(b"fixture")
        destination = Path(self.temp.name) / "copy.wav"
        self.module.copy_local_input(str(source), destination, str(root))
        self.assertEqual(destination.read_bytes(), b"fixture")
        outside = Path(self.temp.name) / "secret.txt"
        outside.write_text("private")
        (root / "link.txt").symlink_to(outside)
        for path in (outside, root / "link.txt", root / "missing"):
            with self.assertRaises(ValueError):
                self.module.copy_local_input(str(path), destination, str(root))

    async def test_command_requires_reply_and_rejects_unknown_profiles(self):
        with self.assertRaises(ValueError):
            await self.module.submit_file(self.message(), self.message(), {"kind": "convert", "profile": "shell"}, {})
        await self.feed(self.message("/ocr"))
        self.assertIn("responde", self.session.methods[-1].text)
        source = self.message(document={"file_id": "fixture", "file_unique_id": "u1", "file_name": "scan.pdf", "file_size": 10})
        with patch.object(self.module, "submit_file", AsyncMock()) as submit:
            await self.feed(self.message("/ocr", reply_to_message=source))
            self.assertEqual(submit.call_args.args[2], {"kind": "document"})

    async def test_note_command_routes_to_shared_library(self):
        with patch.object(self.module, "request", AsyncMock(return_value={"id": "a" * 32})) as request:
            await self.feed(self.message("/nota Test | Content"))
            self.assertEqual(request.call_args.args[1:], ("/notes", {"title": "Test", "body": "Content"}))
        self.assertIn("Nota guardada", self.session.methods[-1].text)

    async def test_download_command_passes_reply_context(self):
        with patch.object(self.module, "request", AsyncMock(return_value={"id": "a" * 32})) as request:
            await self.feed(self.message("/omniget_descargar https://youtu.be/id"))
            self.assertEqual(request.call_args.args[2]["reply_to"], 42)

    async def test_missing_udemy_session_is_reported_as_error(self):
        with patch.object(self.module, "request", AsyncMock(side_effect=ValueError("Primero importa una sesión válida de Udemy."))):
            await self.feed(self.message("/udemy"))
        self.assertIn("sesión válida", self.session.methods[-1].text)

    async def test_card_review_and_export_commands(self):
        with patch.object(self.module, "request", AsyncMock(return_value=[{"id": "a" * 32, "question": "Q", "due": True}])):
            await self.feed(self.message("/repasar"))
        self.assertIn("cosmocard:show:", self.session.methods[-1].reply_markup.inline_keyboard[0][0].callback_data)
        with patch.object(self.module, "request", AsyncMock(return_value={"notes": [], "cards": []})):
            await self.feed(self.message("/exportar_estudio"))
        self.assertEqual(self.session.methods[-1].__api_method__, "sendDocument")


if __name__ == "__main__":
    unittest.main()
