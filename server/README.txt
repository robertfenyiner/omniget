OMNIGET WEB — servicio privado para un usuario

FUNCIONES DISPONIBLES
- Entrada con contraseña; la temporal exige un cambio antes de usar la API.
- Descargas por enlace con calidad o solo audio, mediante omniget-cli.
- Cola serial con cancelar, reintentar y eliminar trabajos y sus archivos.
- Subida de audio, vídeo e imágenes de hasta 1 GB para convertir con FFmpeg.
- Salidas MP4, MP3, WAV, FLAC, PNG y JPG; PNG/JPG toman el primer fotograma.
- Descarga autenticada de resultados e historial persistente.

Esta es la primera etapa de la adaptación web. Los plugins de cursos,
Telegram, estudio, transcripción, chat y el resto de herramientas Tauri todavía
no están conectados a esta interfaz. La disponibilidad de cada plataforma de
vídeo depende de la CLI, sus extractores y las restricciones del servicio.
La web no incorpora todavía importación de cookies ni login en plataformas.

INSTALACIÓN
Requisitos: Linux, Python 3.12+, systemd de usuario, Rust 1.97.0 para compilar,
compilador C, pkg-config y libssl-dev. Conversiones y procesamiento multimedia
requieren FFmpeg. yt-dlp se obtiene cuando un extractor lo necesita y OmniGet
verifica su SHA-256 publicado. No hay dependencias Python adicionales.

Desde la raíz del repo:
  cargo build --locked --manifest-path src-tauri/Cargo.toml -p omniget-cli
  python3 -m unittest discover -s server -v
  python3 server/install.py

Para escuchar en la IP privada de Tailscale, en la primera instalación:
  python3 server/install.py --bind <IPv4-Tailscale-del-servidor>
  Abrir http://<IPv4-Tailscale-del-servidor>:8790

Las siguientes instalaciones conservan la IP y ajustes existentes. La
contraseña temporal inicial está en ~/.config/omniget-web/initial-password
(0600). Entrar con ella y elegir una nueva de al menos 12 caracteres. La
temporal queda revocada y su archivo se elimina tras el cambio. Para recuperar
el acceso si se olvida la contraseña, desde una terminal del servidor:
  python3 server/password.py --reset
Esto genera otra temporal, reinicia el servicio e invalida las sesiones.

La contraseña elegida se guarda como hash scrypt con sal aleatoria en
~/.config/omniget-web/password.json (0600), nunca en texto plano. Se usa
scrypt N=32768, r=8, p=1 con límite de memoria de 64 MiB por verificación.
Las sesiones se guardan en memoria, duran como máximo ocho horas (diez
minutos si aún falta cambiar la temporal) y caducan al reiniciar. El cambio
de contraseña revoca todas las sesiones y crea otra para el navegador actual.
La cookie es HttpOnly y SameSite=Strict. Si se termina TLS en un proxy de
confianza, OMNIGET_SECURE_COOKIES=1 añade Secure a la cookie. El servicio
limita los intentos de autenticación y rechaza POST desde otros orígenes.
La antigua clave bearer ya no es aceptada y el instalador la retira del env.

ACCESO REMOTO Y OPERACIÓN
El HTTP de este servicio no incorpora TLS. Los nodos Tailscale cifran el
tráfico entre equipos; no abrir el puerto a Internet ni reenviarlo en el
router. Otra opción es loopback con un túnel SSH:
  ssh -L 8790:127.0.0.1:8790 usuario@servidor
  Abrir http://127.0.0.1:8790 en el equipo cliente.

Ajustes: ~/.config/omniget-web/environment (0600).
Datos: ~/.local/share/omniget-web (o variable OMNIGET_WEB_DATA).
Directorio de autenticación: ~/.config/omniget-web (o OMNIGET_AUTH_DIR).
CLI local: ~/.local/bin/omniget-cli.

  systemctl --user status omniget-web
  journalctl --user -u omniget-web
  systemctl --user restart omniget-web
  systemctl --user stop omniget-web
  systemctl --user disable --now omniget-web

Para arrancar sin login tras reiniciar, el administrador puede habilitar:
  sudo loginctl enable-linger "$USER"

Cada trabajo tiene una carpeta files y un output.log local. El historial se
guarda en jobs.json. Un trabajo interrumpido por reinicio se marca como fallido
para poder reintentarlo. Eliminar un trabajo borra también sus resultados y,
para una conversión, la entrada subida si no la referencia otro trabajo.
Una subida que no llega a encolarse queda en uploads y puede retirarse con el
servicio detenido. No se muestra el registro crudo a clientes del navegador.

CONTROLES Y LÍMITES
- Servicio para una persona de confianza, con una contraseña compartida,
  sin roles separados. La aplicación procesa archivos como ese usuario Unix.
- Una tarea simultánea, 20 pendientes, 500 trabajos en el historial,
  45 minutos por tarea y al menos 1 GB libre para admitir nuevos trabajos.
- Unidad systemd: memoria 3 GB, tareas 128 y tamaño por archivo 20 GiB.
  No hay cuota total ni limpieza automática: vigilar el espacio libre.
- URLs HTTPS en dominios habilitados. OMNIGET_ALLOWED_HOSTS permite configurar
  una lista separada por comas. Se rechazan credenciales, puertos distintos
  de 443 y DNS privado en la URL inicial. La CLI puede seguir redirecciones
  y recursos secundarios: esa comprobación no es un cortafuegos contra SSRF
  ni evita DNS rebinding. Para usuarios no confiables se necesita aislamiento
  del proceso y filtrado de red saliente antes de exponer el servicio.
- Subidas solo multimedia; rutas generadas en el servidor, sin argumentos
  FFmpeg libres. FFmpeg recibe listas cerradas de protocolos y formatos de
  entrada para impedir usar las subidas como listas de reproducción remotas.
  Los archivos siguen pasando por decodificadores nativos: mantener FFmpeg
  actualizado y no ofrecer este trabajador a usuarios no confiables.
- Sin shell para ejecutar órdenes; parámetros cerrados; API sin CORS, CSP
  sin scripts externos, texto de archivos y enlaces insertado con textContent.
- Archivos leídos solo de trabajos terminados y dentro de sus carpetas;
  se rechazan escapes por traversal y enlaces simbólicos.
- El navegador recupera archivos con fetch autenticado y un blob: archivos
  grandes pueden consumir bastante memoria en el equipo cliente.

DESARROLLO Y SIGUIENTES ETAPAS
Comprobar backend: python3 -m unittest discover -s server -v
Comprobar interfaz: pnpm lint:ui server/static/index.html
Antes de push: pnpm test y node scripts/e2e_verify.mjs (AGENTS.md).

Los siguientes módulos requieren adaptaciones propias, no solo activar un
interruptor: cursos con sus sesiones, Telegram con su autenticación, estudio
y transcripción con sus binarios/modelos, y otras herramientas de escritorio.
Las funciones que requieren micrófono, cámara o el navegador del usuario
necesitarán decidir qué dispositivo las ejecuta. Esta versión no afirma que
esas funciones estén disponibles todavía. Los hallazgos de seguridad del
código original siguen pendientes: esta adaptación no los corrige por sí sola.
