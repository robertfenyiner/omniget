OMNIGET WEB — servicio privado para un usuario

Esta interfaz ejecuta omniget-cli en una cola de una descarga a la vez.
Permite introducir enlaces, escoger calidad o solo audio, consultar el estado
y recuperar los archivos. No incluye el escritorio Tauri, sus plugins de
cursos, la extensión del navegador ni el inicio de sesión en plataformas.
La disponibilidad real de cada sitio depende de la CLI y sus extractores.

Requisitos: Linux, Python 3.12+, systemd de usuario, Rust 1.97.0 para compilar,
compilador C, pkg-config y libssl-dev. Algunas funciones multimedia requieren
FFmpeg. yt-dlp se obtiene automáticamente cuando un extractor lo necesita;
OmniGet verifica su SHA-256 publicado. Los binarios gestionados se guardan
fuera del checkout, en el directorio de datos.

Compilar e instalar (desde la raíz del repo):
  cargo build --locked --manifest-path src-tauri/Cargo.toml -p omniget-cli
  python3 -m unittest discover -s server -v
  python3 server/install.py

Acceso desde otros equipos de la misma red Tailscale:
  python3 server/install.py --bind <IPv4-Tailscale-del-servidor>
  Abrir http://<IPv4-Tailscale-del-servidor>:8790
El tráfico entre nodos viaja cifrado por Tailscale. El HTTP de este servicio
no incorpora TLS; no abrir el puerto a Internet ni reenviarlo en el router.
También se puede conservar loopback y usar un túnel SSH:
  ssh -L 8790:127.0.0.1:8790 usuario@servidor
  Abrir http://127.0.0.1:8790 en el equipo cliente.

Clave y ajustes: ~/.config/omniget-web/environment (permisos 0600).
Introducir la clave OMNIGET_WEB_TOKEN en la interfaz. Nunca añadir ese archivo
al repositorio. La clave da acceso a toda la cola y sus archivos; no hay roles
ni cuentas separadas. Rotarla editando ese archivo y reiniciando el servicio.
Los cambios de configuración requieren:
  systemctl --user restart omniget-web

Operación:
  systemctl --user status omniget-web
  journalctl --user -u omniget-web
  systemctl --user stop omniget-web
  systemctl --user disable --now omniget-web
El servicio queda habilitado en la sesión de usuario. Para arrancar tras un
reinicio sin login, el administrador puede habilitar linger para ese usuario:
  sudo loginctl enable-linger "$USER"
La CLI local queda instalada en ~/.local/bin/omniget-cli.

Datos: ~/.local/share/omniget-web. Cada trabajo tiene una carpeta files y un
output.log local. La cola persiste en jobs.json. Al reiniciar, un trabajo que
estaba pendiente o ejecutándose queda marcado como fallido; volver a enviarlo
si se quiere reintentar. OMNIGET_WEB_DATA permite elegir otro directorio.

Controles y límites:
- Clave obligatoria, API sin CORS, CSP sin scripts externos, ninguna shell
  para construir órdenes, parámetros de CLI cerrados y nombres en textContent.
- URLs HTTPS en dominios habilitados; se rechazan credenciales, otros puertos
  y resoluciones DNS privadas en la URL inicial. OMNIGET_ALLOWED_HOSTS permite
  configurar una lista separada por comas. No usar dominios de terceros sin
  confianza: la CLI y sus extractores pueden seguir redirecciones y acceder
  a recursos secundarios. La comprobación inicial no es un cortafuegos contra
  SSRF ni evita DNS rebinding. Para usuarios no confiables se necesita aislar
  el proceso y filtrar la red saliente antes de exponer este servicio.
- Descargas confinadas por la API a la carpeta de cada trabajo; se rechazan
  rutas de lectura que escapen por traversal o enlaces simbólicos.
- Una descarga simultánea, 20 pendientes, 500 trabajos en el historial,
  45 minutos por descarga y al menos 1 GB libre para aceptar un trabajo.
- La unidad systemd limita memoria a 3 GB, tareas a 128 y tamaño por archivo
  a 20 GiB. No hay cuota total ni limpieza automática: vigilar el disco y
  retirar carpetas antiguas con el servicio detenido.
- El cliente guarda la clave solo en sessionStorage. Los archivos se recuperan
  mediante fetch autenticado y un blob en el navegador: los archivos muy
  grandes pueden exigir bastante memoria en el equipo cliente.

Alcance: es una primera adaptación privada y comprobable, no una migración
de todas las funciones del escritorio ni un servicio multiusuario público.
Las vulnerabilidades de las dependencias de upstream siguen requiriendo
actualización y evaluación; compilar con éxito no certifica la seguridad.
