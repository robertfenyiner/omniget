"""Aiogram router loaded by the existing CosmoDownloader bot image overlay."""
import json
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import httpx
from config import BOT_COMMANDS

router = Router(name="omniget_private_bridge")
CONFIG = Path("/app/omniget-bridge.json")
for name, description in (("omniget", "Abrir OmniGet"), ("omniget_estado", "Cola de OmniGet"),
                          ("omniget_descargar", "Descargar con OmniGet"), ("omniget_cancelar", "Cancelar trabajo OmniGet")):
    if not any(command["command"] == name for command in BOT_COMMANDS):
        BOT_COMMANDS.append({"command": name, "description": description})


def config_for(message):
    if not CONFIG.is_file():
        return None
    config = json.loads(CONFIG.read_text())
    if not message.from_user or message.chat.type != "private" or message.from_user.id != config.get("owner_id"):
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


@router.message(Command("omniget_estado"))
async def omniget_status(message):
    config = config_for(message)
    if not config:
        return
    try:
        data = await request(config, "/status")
        lines = [f"OmniGet · {data['pending']} pendientes · {data['total']} trabajos"]
        for job in data["jobs"]:
            lines.append(f"{job['state']} · {job['kind']}\n{job['id']}")
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
        job = await request(config, "/download", {"url": parts[1].strip(), "quality": 720})
        await message.answer(f"Añadido a OmniGet.\nID: {job['id']}\nRecibirás un aviso al terminar.", parse_mode=None)
    except ValueError as exc:
        await message.answer(str(exc), parse_mode=None)
    except Exception:
        await message.answer("No se pudo añadir la descarga a OmniGet.", parse_mode=None)


@router.message(Command("omniget_cancelar"))
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
