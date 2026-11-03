OMNIGET WEB — servicio privado para un usuario

FUNCIONES DISPONIBLES
- Entrada con contraseña; la temporal exige un cambio antes de usar la API.
- Descargas por enlace con calidad o solo audio, mediante omniget-cli.
- Cola serial con cancelar, reintentar y eliminar trabajos y sus archivos.
- Subida de audio, vídeo e imágenes de hasta 1 GB para convertir con FFmpeg.
- Salidas MP4, MP3, WAV, FLAC, PNG y JPG; PNG/JPG toman el primer fotograma.
- Descarga autenticada de resultados e historial persistente.
- Transcripción local multilingüe de audio/vídeo a TXT, SRT, VTT y JSON.
- Extracción de texto de PDF, OCR local de PDF escaneados e imágenes, e importación de TXT/MD/subtítulos.
- Biblioteca de notas con búsqueda, edición, exportación y tarjetas de repaso.
- Integración con el bot existente de CosmoDownloader: comandos privados,
  avisos de trabajos y envío de resultados de hasta 100 MB.
- Gestión privada de cookies por servicio y conexión de biblioteca Udemy
  condicionada a una sesión autenticada que responda correctamente.

La adaptación web ya incluye descargas, conversión, transcripción y estudio.
La integración Telegram de esta edición usa el bot existente, sin iniciar
otro poller ni entrar a una cuenta personal MTProto. El chat y otras
herramientas exclusivas de Tauri todavía no están conectados a esta interfaz. La disponibilidad de cada plataforma de
vídeo depende de la CLI, sus extractores y las restricciones del servicio.
La pestaña Servicios permite importar cookies Netscape. Udemy no se declara
conectado hasta que una consulta real de la biblioteca devuelve los cursos.

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

TRANSCRIPCIÓN Y PDF
Para instalar las dependencias del sistema (Ubuntu/Debian):
  sudo apt-get install cmake build-essential poppler-utils
Para compilar el motor y descargar un modelo multilingüe verificado:
  python3 server/install-transcription.py
  systemctl --user restart omniget-web
El instalador fija whisper.cpp v1.9.5 al commit
 d1be6fde11ac6e0407606b4e42fe72d34add8037 y comprueba el SHA-256 del modelo
contra valores fijados de la revisión Hugging Face
5359861c739e955e79d9a303bcbc70fb988958b1. El modelo base-q5_1 ocupa unos 60 MB.
Para añadir el modelo small-q5_1 (unos 190 MB):
  python3 server/install-transcription.py --model small-q5_1

El audio se normaliza con FFmpeg a PCM de 16 kHz mono y whisper.cpp usa dos
hilos de CPU. No se envía el audio a servicios externos; las descargas de
código y modelos solo ocurren durante la instalación. El trabajo comparte el
límite total de 45 minutos entre preparación y transcripción, admite cancelación
y limpia el WAV intermedio al terminar. OMNIGET_WHISPER_CLI y
OMNIGET_WHISPER_MODELS permiten configurar rutas diferentes. Los modelos viven
en <datos>/models/whisper y el motor en ~/.local/bin/whisper-cli.

La pestaña Transcribir genera transcript.txt, transcript.srt, transcript.vtt y
transcript.json. El resultado depende del modelo, el idioma y la calidad de
la grabación; una transcripción no garantiza fidelidad literal.

En Estudiar se puede subir PDF, PNG/JPG/WEBP, TXT, MD, SRT o VTT. El modo
OCR automático conserva el texto de cada página y reconoce las páginas sin
texto suficiente con Tesseract. Forzar OCR reconoce todas las páginas; Solo
texto existente no aplica OCR. Se admiten hasta 50 páginas por trabajo.
Para instalar los motores y los idiomas usados en Ubuntu:
  sudo apt-get install tesseract-ocr tesseract-ocr-spa tesseract-ocr-eng poppler-utils ffmpeg
El OCR permite español, inglés o ambos. No abre PDF cifrados con contraseña.
Genera document.txt y ocr-info.json con las páginas reconocidas; los archivos
intermedios se borran al terminar. El reconocimiento puede contener errores. «Guardar en notas» copia un resultado
de texto de hasta 2 MB a la biblioteca, por lo que eliminar el trabajo original
no elimina la nota. La biblioteca usa SQLite en <datos>/study.sqlite3 (0600),
con límite de 1000 notas, 64 MB de texto total y 5000 tarjetas.

Las tarjetas se crean manualmente y pueden vincularse a la nota abierta.
El repaso usa intervalos simples: Repetir vuelve a programar en diez minutos;
Bien duplica el intervalo (mínimo dos días) y Fácil lo triplica (mínimo tres),
con máximo de 365 días. No usa generación de respuestas ni resúmenes con IA.
Eliminar una nota borra sus tarjetas asociadas. Se puede exportar cada nota
como Markdown o la biblioteca completa, con tarjetas e intervalos, como JSON.
Estas exportaciones contienen tus textos: guardarlas como datos privados.

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

SERVICIOS, COOKIES Y UDEMY
Los archivos reales de cookies se guardan en ~/.config/omniget-web/services,
con permisos 0600. Cada archivo se filtra por servicio y frontera exacta de
dominio, conserva HttpOnly y cookies de sesión, y descarta las caducadas. Las
cookies no se devuelven a la interfaz ni se incluyen en exportaciones de notas.
El trabajador genera el jar de ejecución en <datos>/runtime/cookies/cookies.txt;
yt-dlp aplica el ámbito de dominio al usar las sesiones.

Se pueden reutilizar los archivos de cookies de Cosmo sin modificar el original.
Importar cookies confirma el almacenamiento, no que la plataforma las acepte.
La biblioteca Udemy usa solo HTTPS en www.udemy.com y rechaza redirecciones
fuera de ese origen. El botón Verificar y ver mis cursos debe cargar resultados
reales antes de permitir trabajos de lecciones. La página de acceso puede
pedir Cloudflare o un código; resolverlo en Udemy y exportar/importar la sesión
si el servidor no puede entrar. Guardar email/contraseña no equivale a verificar
la cuenta. No se muestran esos secretos en el navegador.

Las credenciales opcionales de Udemy se conservan fuera de Git, en
~/.config/omniget-web/udemy-credentials.json (0600). La integración no automatiza
la resolución de CAPTCHA ni presupone que una cookie de invitado sea sesión
válida. Las lecciones marcadas como DRM quedan deshabilitadas; no se evita DRM.
Las descargas de lecciones usan el yt-dlp verificado del directorio runtime,
sin argumentos libres, y requieren haber consultado la biblioteca con éxito.

TELEGRAM Y COSMODOWNLOADER
Instalación en esta máquina, con Cosmo ubicado en /opt/Downloader-Bot:
  python3 server/install-cosmo.py
El script lee la configuración privada existente, verifica la identidad del
bot, copia sesiones multimedia filtradas y crea una imagen fina basada en
la imagen local downloader-bot-robert:private-v2. No modifica el Compose
original, la base de datos ni los otros servicios de Cosmo. Solo recrea el
contenedor del bot con un override y conserva la imagen anterior.

Configuración privada de OmniGet: telegram.json y bot-bridge.json bajo
~/.config/omniget-web (0600). La copia para el contenedor se monta de solo
lectura desde /opt/Downloader-Bot/private y queda excluida de Git. El canal
local es un socket Unix autenticado, no un puerto público. Solo permite
consultar el estado, añadir enlaces y cancelar trabajos: no exporta notas,
contraseñas ni cookies. El router de Cosmo admite solo ADMIN_ID en chat privado.

Comandos del bot existente:
  /omniget
  /omniget_estado
  /omniget_descargar https://...
  /omniget_cancelar ID
Los comandos habituales /start, /help, /settings y /stats siguen disponibles.
No se ejecuta getUpdates desde OmniGet: el poller de Cosmo sigue siendo el único.
Las notificaciones no incluyen cookies ni URLs firmadas; los archivos solo se
envían al chat privado fijo configurado. El botón Enviar a Telegram admite
hasta 100 MB y transmite por bloques. Para tamaños mayores usar la descarga web.
La integración no enumera chats ni descarga contenido privado de una cuenta
personal de Telegram; es una conexión con el bot ya existente.

Arranque manual de Cosmo con la integración:
  sudo docker compose --project-name downloader-bot -f /opt/Downloader-Bot/docker-compose.yml -f /opt/Downloader-Bot/docker-compose.omniget.yml up -d --no-deps --no-build downloader-bot
Restaurar la imagen original sin borrar datos:
  sudo docker compose --project-name downloader-bot -f /opt/Downloader-Bot/docker-compose.yml up -d --no-deps --no-build downloader-bot
Si se recrea la API local de Telegram y cambia su IP de Docker, ejecutar otra
vez install-cosmo.py para actualizar y volver a comprobar la conexión privada.

DESARROLLO Y SIGUIENTES ETAPAS
Comprobar backend: python3 -m unittest discover -s server -v
Comprobar interfaz: pnpm lint:ui server/static/index.html
Antes de push: pnpm test y node scripts/e2e_verify.mjs (AGENTS.md).

Los siguientes módulos requieren adaptaciones propias, no solo activar un
interruptor: las funciones completas del plugin de cursos, una cuenta personal
MTProto de Telegram y las otras herramientas de escritorio.
Las funciones que requieren micrófono, cámara o el navegador del usuario
necesitarán decidir qué dispositivo las ejecuta. Esta versión no afirma que
esas funciones estén disponibles todavía. Los hallazgos de seguridad del
código original siguen pendientes: esta adaptación no los corrige por sí sola.
