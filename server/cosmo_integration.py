"""Aiogram router loaded by the existing CosmoDownloader bot image overlay."""
import json
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import quote, urlsplit

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters, BufferedInputFile
import httpx
from config import BOT_COMMANDS

router = Router(name="omniget_private_bridge")
CONFIG = Path("/app/omniget-bridge.json")
for name, description in (("omniget", "Abrir OmniGet"), ("omniget_estado", "Cola de OmniGet"),
                          ("omniget_descargar", "Descargar con OmniGet"), ("omniget_cancelar", "Cancelar trabajo OmniGet"),
                          ("herramientas", "OCR, transcripción, conversión y estudio"),
                          ("estado", "Ver trabajos de herramientas"), ("notas", "Buscar mis notas"),
                          ("repasar", "Repasar tarjetas"), ("udemy", "Consultar mis cursos")):
    if not any(command["command"] == name for command in BOT_COMMANDS):
        BOT_COMMANDS.append({"command": name, "description": description})


def config_for(message, user=None):
    if not CONFIG.is_file():
        return None
    config = json.loads(CONFIG.read_text())
    user = user or message.from_user
    if not user or message.chat.type != "private" or user.id != config.get("owner_id"):
        return None
    return config


async def request(config, path, payload=None):
    transport = httpx.AsyncHTTPTransport(uds="/run/omniget/bridge.sock")
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost", timeout=20) as client:
        headers = {"X-OmniGet-Key": config["key"]}
        result = await client.get(path, headers=headers) if payload is None else await client.post(path, json=payload, headers=headers)
        data = result.json()
        if result.status_code >= 400:
            raise ValueError(data.get("error", "OmniGet rechazó la solicitud."))
        return data


@router.message(Command("omniget"))
async def omniget_home(message):
    config = config_for(message)
    if not config:
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Abrir OmniGet", url=config["web_url"])]])
    await message.answer("OmniGet está conectado.\n\n/omniget_estado — ver la cola\n"
                         "/omniget_descargar URL — añadir un enlace\n/omniget_cancelar ID — cancelar un trabajo\n\n"
                         "La web requiere tu Tailscale y tu contraseña de OmniGet.", reply_markup=keyboard, parse_mode=None)


@router.message(Command("omniget_estado", "estado"))
async def omniget_status(message):
    config = config_for(message)
    if not config:
        return
    try:
        data = await request(config, "/status")
        lines = [f"OmniGet · {data['pending']} pendientes · {data['total']} trabajos"]
        for job in data["jobs"]:
            delivery = job.get("delivery", {})
            info = f" · envío {delivery['state']}" if delivery else ""
            lines.append(f"{job['state']} · {job.get('kind', 'download')}{info}\n{job['id']}")
        await message.answer("\n\n".join(lines), parse_mode=None)
    except Exception:
        await message.answer("No se pudo consultar OmniGet. Comprueba que el servicio está activo.", parse_mode=None)


@router.message(Command("omniget_descargar"))
async def omniget_download(message):
    config = config_for(message)
    if not config:
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        return await message.answer("Uso: /omniget_descargar https://…", parse_mode=None)
    try:
        job = await request(config, "/download", {"url": parts[1].strip(), "quality": 720, "reply_to": message.message_id})
        await message.answer(f"Añadido a Cosmo.\nID: {job['id']}\nRecibirás el archivo en este chat al terminar.", parse_mode=None)
    except ValueError as exc:
        await message.answer(str(exc), parse_mode=None)
    except Exception:
        await message.answer("No se pudo añadir la descarga a OmniGet.", parse_mode=None)


@router.message(Command("omniget_cancelar", "cancelar"))
async def omniget_cancel(message):
    config = config_for(message)
    if not config:
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        return await message.answer("Uso: /omniget_cancelar ID", parse_mode=None)
    try:
        await request(config, "/cancel", {"id": parts[1].strip()})
        await message.answer("Trabajo cancelado en OmniGet.", parse_mode=None)
    except ValueError as exc:
        await message.answer(str(exc), parse_mode=None)
    except Exception:
        await message.answer("No se pudo cancelar el trabajo.", parse_mode=None)


HELP = """Cosmo · Herramientas

Pega enlaces como siempre: recibirás el contenido en este chat.
Envía un PDF o imagen para extraer texto; un audio, voz o vídeo para transcribir o convertir. También puedes responder a un archivo con:
/ocr — extraer texto (español e inglés)
/transcribir — transcribir (idioma automático)
/convertir mp3 — formatos: mp4, mp3, wav, flac, png, jpg

/estado — trabajos de herramientas
/cancelar ID — cancelar un trabajo
/reintentar ID — repetir un trabajo fallido
/resultado ID — volver a enviar sus archivos

/nota Título | Texto — crear una nota
/notas búsqueda — buscar notas
/editar_nota ID Título | Texto — actualizar una nota
/borrar_nota ID — eliminar, con confirmación
/ver_nota ID — leer una nota
/guardar ID archivo.txt — guardar un resultado como nota
/tarjeta Pregunta | Respuesta — crear tarjeta
/repasar — mostrar una tarjeta pendiente
/exportar_estudio — copia de notas y tarjetas

/udemy — consultar biblioteca (requiere sesión válida)
/lecciones ID_CURSO — ver lecciones
/leccion URL — descargar una lección autorizada
/sesiones — estado de servicios, sin mostrar cookies
/omniget — abrir el portal opcional de administración

Herramientas: archivos de hasta 100 MiB. Udemy requiere conectar una sesión válida desde el portal; no se descarga contenido con DRM."""


@router.message(Command("herramientas", "cosmo"))
async def tools_home(message):
    config = config_for(message)
    if not config:
        return
    try:
        capabilities = await request(config, "/capabilities")
        status = " · ".join(f"{label}: {'listo' if capabilities.get(key) else 'no disponible'}"
                            for key, label in (("ocr", "OCR"), ("transcription", "Transcripción"), ("conversion", "Conversión")))
        await message.answer(HELP + "\n\n" + status, parse_mode=None)
    except Exception:
        await message.answer("Las herramientas no están disponibles temporalmente. Las descargas habituales siguen siendo independientes.", parse_mode=None)


def attached_file(message):
    for attr, default in (("document", "document.bin"), ("audio", "audio.mp3"),
                          ("voice", "voice.ogg"), ("video", "video.mp4"), ("video_note", "video.mp4")):
        file = getattr(message, attr, None)
        if file:
            return file, getattr(file, "file_name", None) or default
    if message.photo:
        return message.photo[-1], "photo.jpg"
    raise ValueError("Responde a un archivo, imagen, audio o vídeo.")


def copy_local_input(remote_path, destination, shared_root="/var/lib/telegram-bot-api"):
    shared = Path(shared_root).resolve()
    local = Path(remote_path).resolve()
    if not local.is_relative_to(shared) or not local.is_file():
        raise ValueError("No se pudo acceder al archivo de la API local de Telegram.")
    if not 0 < local.stat().st_size <= 100 * 1024 * 1024:
        raise ValueError("El archivo está vacío o supera 100 MiB.")
    shutil.copyfile(local, destination)


async def submit_file(message, source, action, config, bot=None):
    if action.get("kind") == "convert" and action.get("profile") not in ("mp4", "mp3", "wav", "flac", "png", "jpg"):
        raise ValueError("Formatos: mp4, mp3, wav, flac, png o jpg.")
    file, name = attached_file(source)
    if file.file_size and file.file_size > 100 * 1024 * 1024:
        raise ValueError("Las herramientas admiten archivos de hasta 100 MiB.")
    bot = bot or message.bot
    with tempfile.TemporaryDirectory(prefix="cosmo-tool-") as directory:
        path = Path(directory) / "input"
        metadata = await bot.get_file(file.file_id)
        remote_path = metadata.file_path or ""
        if Path(remote_path).is_absolute():
            # Local Bot API returns an absolute path, not an HTTP download URL.
            # Its data volume is mounted read-only; never accept arbitrary paths.
            copy_local_input(remote_path, path)
        else:
            await bot.download_file(remote_path, destination=path, timeout=120)
        if not path.is_file() or not 0 < path.stat().st_size <= 100 * 1024 * 1024:
            raise ValueError("El archivo está vacío o supera 100 MiB.")
        async def chunks():
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    yield block
        transport = httpx.AsyncHTTPTransport(uds="/run/omniget/bridge.sock")
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost", timeout=120) as client:
            result = await client.post("/upload", content=chunks(), headers={"X-OmniGet-Key": config["key"],
                "X-Filename": quote(name, safe=""), "Content-Length": str(path.stat().st_size)})
            data = result.json()
            if result.status_code >= 400:
                raise ValueError(data.get("error", "No se pudo recibir el archivo."))
        request_id = f"{source.message_id}:{action['kind']}:{action.get('profile', 'default')}"
        payload = dict(action, upload_id=data["upload_id"], reply_to=source.message_id, request_id=request_id)
        job = await request(config, "/tool", payload)
    await message.answer(f"Cosmo · {job['state']}\nID: {job['id']}\nLos resultados se envían a este chat.\n"
                         f"/cancelar {job['id']}\n/resultado {job['id']}", parse_mode=None)
    return job


@router.message(F.document | F.photo | F.audio | F.voice | F.video | F.video_note)
async def offer_tools(message):
    if not config_for(message):
        return
    _file, name = attached_file(message)
    suffix = Path(name).suffix.lower()
    buttons = []
    if suffix in (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".txt", ".md", ".srt", ".vtt"):
        buttons.append([InlineKeyboardButton(text="Extraer texto / OCR", callback_data="cosmotool:document")])
    if suffix in (".mp4", ".mkv", ".webm", ".mp3", ".wav", ".m4a", ".flac", ".mov", ".avi", ".ogg", ".opus"):
        buttons.append([InlineKeyboardButton(text="Transcribir", callback_data="cosmotool:transcribe")])
    if suffix not in (".pdf", ".txt", ".md", ".srt", ".vtt", ".bin"):
        buttons.append([InlineKeyboardButton(text="Convertir formato…", callback_data="cosmotool:formats")])
    if buttons:
        await message.answer("¿Qué quieres hacer con este archivo?", reply_parameters=ReplyParameters(message_id=message.message_id),
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=None)


@router.callback_query(F.data.startswith("cosmotool:"))
async def choose_tool(callback):
    message = callback.message
    config = config_for(message, callback.from_user) if message else None
    if not config:
        return await callback.answer("Acceso privado.", show_alert=True)
    action = callback.data.split(":", 1)[1]
    source = message.reply_to_message
    if not source:
        return await callback.answer("Responde al archivo con /ocr, /transcribir o /convertir.", show_alert=True)
    await callback.answer()
    if action == "formats":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=fmt.upper(), callback_data="cosmotool:convert:" + fmt)]
                                                         for fmt in ("mp4", "mp3", "wav", "flac", "png", "jpg")])
        return await message.edit_reply_markup(reply_markup=keyboard)
    if action.startswith("convert:"):
        payload = {"kind": "convert", "profile": action.split(":", 1)[1]}
    elif action in ("document", "transcribe"):
        payload = {"kind": action}
    else:
        return
    try:
        await submit_file(message, source, payload, config, bot=callback.bot)
        await message.edit_reply_markup(reply_markup=None)
    except ValueError as exc:
        await message.answer(str(exc), parse_mode=None)
    except Exception:
        await message.answer("No se pudo iniciar la herramienta. Inténtalo de nuevo con un comando en respuesta al archivo.", parse_mode=None)


@router.message(Command("ocr", "transcribir", "convertir"))
async def tool_command(message):
    config = config_for(message)
    if not config:
        return
    try:
        command, *args = message.text.split(maxsplit=1)
        command = command.split("@", 1)[0][1:]
        action = {"kind": {"ocr": "document", "transcribir": "transcribe", "convertir": "convert"}[command]}
        if command == "convertir":
            action["profile"] = args[0].strip().lower() if args else "mp3"
        if not message.reply_to_message:
            raise ValueError("Envía el archivo y responde a ese mensaje con el comando.")
        await submit_file(message, message.reply_to_message, action, config)
    except ValueError as exc:
        await message.answer(str(exc), parse_mode=None)
    except Exception:
        await message.answer("No se pudo iniciar la herramienta. Comprueba /estado y vuelve a intentarlo.", parse_mode=None)


@router.message(Command("reintentar", "resultado", "nota", "notas", "editar_nota", "borrar_nota", "ver_nota", "guardar", "tarjeta", "repasar", "exportar_estudio", "udemy", "lecciones", "leccion", "sesiones"))
async def extra_command(message):
    config = config_for(message)
    if not config:
        return
    command, *parts = message.text.split(maxsplit=1)
    command = command.split("@", 1)[0][1:]
    arg = parts[0].strip() if parts else ""
    try:
        if command in ("reintentar", "resultado"):
            if not re.fullmatch(r"[a-f0-9]{32}", arg):
                raise ValueError("Indica el ID del trabajo que aparece en /estado.")
            result = await request(config, "/retry" if command == "reintentar" else "/redeliver", {"id": arg, "reply_to": message.message_id})
            text = f"En cola: {result['id']}" if command == "reintentar" else "Volveré a enviar los resultados en este chat."
        elif command in ("nota", "editar_nota"):
            payload = {}
            if command == "editar_nota":
                note_id, _, arg = arg.partition(" ")
                if not re.fullmatch(r"[a-f0-9]{32}", note_id):
                    raise ValueError("Uso: /editar_nota ID Título | Texto")
                payload["id"] = note_id
            title, sep, body = arg.partition("|")
            if not sep:
                raise ValueError("Uso: /nota Título | Texto")
            result = await request(config, "/notes", dict(payload, title=title.strip(), body=body.strip()))
            text = f"Nota guardada.\n/ver_nota {result['id']}"
        elif command == "borrar_nota":
            if not re.fullmatch(r"[a-f0-9]{32}", arg):
                raise ValueError("Uso: /borrar_nota ID")
            note = await request(config, "/notes/" + arg)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Eliminar nota y tarjetas asociadas", callback_data="cosmonote:delete:" + arg)]])
            return await message.answer("¿Eliminar esta nota y sus tarjetas?\n" + note["title"], reply_markup=keyboard, parse_mode=None)
        elif command == "notas":
            notes = await request(config, "/notes?q=" + quote(arg))
            text = "\n\n".join(f"{n['title']}\n/ver_nota {n['id']}" for n in notes[:15]) or "No hay notas que coincidan."
        elif command == "ver_nota":
            if not re.fullmatch(r"[a-f0-9]{32}", arg):
                raise ValueError("Uso: /ver_nota ID")
            note = await request(config, "/notes/" + arg)
            body = (note["title"] + "\n\n" + note["body"])
            if len(body) > 3900:
                await message.answer_document(BufferedInputFile(body.encode(), filename="nota.txt"))
                return
            text = body
        elif command == "guardar":
            job_id, *name = arg.split(maxsplit=1)
            if not re.fullmatch(r"[a-f0-9]{32}", job_id):
                raise ValueError("Uso: /guardar ID document.txt (o transcript.txt)")
            result = await request(config, "/import-note", {"id": job_id, "name": name[0] if name else "document.txt"})
            text = f"Resultado guardado como nota.\n/ver_nota {result['id']}"
        elif command == "tarjeta":
            question, sep, answer = arg.partition("|")
            if not sep:
                raise ValueError("Uso: /tarjeta Pregunta | Respuesta")
            await request(config, "/cards", {"question": question.strip(), "answer": answer.strip()})
            text = "Tarjeta guardada. Usa /repasar."
        elif command == "repasar":
            cards = await request(config, "/cards")
            card = next((c for c in cards if c["due"]), None)
            if not card:
                return await message.answer("No hay tarjetas pendientes.", parse_mode=None)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Ver respuesta", callback_data="cosmocard:show:" + card["id"])]])
            return await message.answer(card["question"], reply_markup=keyboard, parse_mode=None)
        elif command == "exportar_estudio":
            result = await request(config, "/study-export")
            await message.answer_document(BufferedInputFile(json.dumps(result, ensure_ascii=False, indent=2).encode(), filename="cosmo-estudio.json"))
            return
        elif command == "sesiones":
            result = await request(config, "/services")
            text = json.dumps(result, ensure_ascii=False, indent=2)
        elif command == "udemy":
            if arg and not arg.isdecimal():
                raise ValueError("Uso: /udemy [página]")
            result = await request(config, "/udemy/courses?page=" + (arg or "1"))
            text = "\n\n".join(f"{c['title']}\n/lecciones {c['id']}" for c in result["courses"])
            text = (text or "No hay cursos en esta página.") + "\n\nOtra página: /udemy N"
        elif command == "lecciones":
            args = arg.split()
            if not 1 <= len(args) <= 2 or not all(a.isdecimal() for a in args):
                raise ValueError("Uso: /lecciones ID_CURSO [página]")
            result = await request(config, "/udemy/courses/" + args[0] + "?page=" + (args[1] if len(args) > 1 else "1"))
            text = "\n\n".join(f"{l['title']} {'[DRM]' if l.get('drm') else ''}\nID: {l['id']}" for l in result["lectures"])
            text = (text or "No hay lecciones en esta página.") + "\n\nUsa /leccion URL; no se elude DRM."
        elif command == "leccion":
            result = await request(config, "/tool", {"kind": "udemy", "url": arg, "reply_to": message.message_id})
            text = f"Lección en cola.\nID: {result['id']}"
        await message.answer(text[:4000], parse_mode=None)
    except ValueError as exc:
        await message.answer(str(exc) or "Faltan argumentos. Consulta /herramientas.", parse_mode=None)
    except Exception:
        await message.answer("No se pudo completar la solicitud. Comprueba /herramientas o la sesión del servicio en el portal.", parse_mode=None)


@router.callback_query(F.data.startswith("cosmocard:"))
async def review_card(callback):
    if not callback.message or not config_for(callback.message, callback.from_user):
        return await callback.answer("Acceso privado.", show_alert=True)
    config = config_for(callback.message, callback.from_user)
    await callback.answer()
    try:
        _, action, card_id = callback.data.split(":")
        if action == "show":
            cards = await request(config, "/cards")
            card = next((c for c in cards if c["id"] == card_id), None)
            if not card:
                raise ValueError("La tarjeta ya no existe.")
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=f"cosmocard:{rating}:{card_id}")
                for rating, label in (("again", "Repetir"), ("good", "Bien"), ("easy", "Fácil"))]])
            if len(card["answer"]) > 3900:
                await callback.message.answer_document(BufferedInputFile(card["answer"].encode(), filename="respuesta.txt"))
            else:
                await callback.message.answer(card["answer"], parse_mode=None)
            await callback.message.edit_reply_markup(reply_markup=keyboard)
        elif action in ("again", "good", "easy"):
            await request(config, "/review", {"id": card_id, "rating": action})
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer("Repaso guardado. /repasar", parse_mode=None)
    except Exception:
        await callback.message.answer("No se pudo consultar o guardar la tarjeta.", parse_mode=None)


@router.callback_query(F.data.startswith("cosmonote:delete:"))
async def delete_note(callback):
    if not callback.message or not config_for(callback.message, callback.from_user):
        return await callback.answer("Acceso privado.", show_alert=True)
    await callback.answer()
    try:
        note_id = callback.data.split(":")[-1]
        await request(config_for(callback.message, callback.from_user), "/delete-note", {"id": note_id})
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Nota eliminada.", parse_mode=None)
    except Exception:
        await callback.message.answer("No se pudo eliminar la nota.", parse_mode=None)


def extra_link(message):
    text = (message.text or "").strip()
    if text.startswith("/"):
        return False
    try:
        host = (urlsplit(text).hostname or "").lower()
        return any(host == domain or host.endswith("." + domain) for domain in
                   ("vimeo.com", "reddit.com", "bilibili.com", "b23.tv", "twitch.tv", "udemy.com"))
    except ValueError:
        return False


@router.message(extra_link)
async def download_extra_link(message):
    config = config_for(message)
    if not config:
        return
    try:
        url = message.text.strip()
        udemy = (urlsplit(url).hostname or "").endswith("udemy.com")
        result = await request(config, "/tool" if udemy else "/download",
                               dict(url=url, reply_to=message.message_id, **({"kind": "udemy"} if udemy else {})))
        await message.answer(f"Cosmo · En cola\nID: {result['id']}\nRecibirás el resultado aquí.", parse_mode=None)
    except ValueError as exc:
        await message.answer(str(exc), parse_mode=None)
    except Exception:
        await message.answer("No se pudo iniciar la descarga adicional.", parse_mode=None)
