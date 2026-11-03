const el = (id) => document.getElementById(id);
const states = {queued:"En cola", running:"Procesando", completed:"Completado", failed:"No completado", cancelled:"Cancelado"};
const sections = ["download", "convert", "transcribe", "study", "services"];
let telegramEnabled = false;
const phases = {preparing:"Preparando audio", transcribing:"Transcribiendo", extracting:"Extrayendo texto", converting:"Convirtiendo", downloading:"Descargando"};
let authenticated = false;
let mustChange = false;
let changingPassword = false;
let busy = false;
let rendering = false;
// Remove the old technical bearer from browsers that used the first version.
sessionStorage.removeItem("omniget-token");

function showAccount(status) {
  authenticated = status.authenticated;
  mustChange = status.must_change;
  el("access").hidden = authenticated;
  el("password-panel").hidden = !authenticated || (!mustChange && !changingPassword);
  el("workspace").hidden = !authenticated || mustChange || changingPassword;
  el("queue-section").hidden = !authenticated || mustChange || changingPassword;
  el("password-title").textContent = mustChange ? "Elige tu contraseña" : "Cambia tu contraseña";
  el("password-hint").textContent = mustChange ? "Cambia la contraseña temporal antes de continuar." : "El cambio cerrará las otras sesiones abiertas.";
  el("password-back").textContent = mustChange ? "Salir" : "Volver";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options, credentials:"same-origin",
    headers:{"Content-Type":"application/json", ...options.headers},
  });
  if (!response.ok) {
    let payload = {};
    try { payload = await response.json(); } catch {}
    if (response.status === 401) showAccount({authenticated:false, must_change:false});
    if (payload.must_change) showAccount({authenticated:true, must_change:true});
    throw new Error(payload.error || "No se pudo completar la solicitud.");
  }
  return response;
}

function node(tag, text, className) {
  const result = document.createElement(tag);
  result.textContent = text;
  if (className) result.className = className;
  return result;
}

function actionButton(label, action, className = "secondary") {
  const button = node("button", label, className);
  button.type = "button";
  button.onclick = async () => {
    button.disabled = true;
    try { await action(); }
    catch (error) { el("message").textContent = error.message; }
    finally { button.disabled = false; }
  };
  return button;
}

async function jobAction(job, action) {
  if (action === "delete" && !confirm("¿Eliminar este trabajo y sus archivos del servidor?")) return;
  await api(`/api/jobs/${job.id}/${action}`, {method:"POST", body:"{}"});
  await refresh();
}

async function saveFile(job, file) {
  const response = await api(`/api/files/${job.id}/${encodeURIComponent(file.name)}`);
  const blob = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = blob;
  link.download = file.name.split("/").pop();
  link.click();
  setTimeout(() => URL.revokeObjectURL(blob), 60000);
}

function renderJob(job) {
  const card = node("article", "", "job");
  const detail = node("div", "");
  let title = job.title || "Trabajo";
  if (!job.kind || job.kind === "download") {
    try { title = new URL(job.url).hostname; } catch { title = "Descarga"; }
  }
  detail.append(node("div", title, "job-title"));
  const descriptions = {convert:`Conversión a ${job.profile?.toUpperCase()}`, transcribe:`Transcripción · ${job.language || "auto"}`, document:"Extracción de texto"};
  detail.append(node("p", descriptions[job.kind] || job.url));
  if (job.error) detail.append(node("p", job.error));
  card.append(detail, node("span", job.state === "running" ? (phases[job.phase] || "Procesando") : states[job.state], `state ${job.state}`));
  const actions = node("div", "", "job-actions");
  if (["queued", "running"].includes(job.state)) actions.append(actionButton("Cancelar", () => jobAction(job, "cancel")));
  if (["failed", "cancelled"].includes(job.state)) actions.append(actionButton("Reintentar", () => jobAction(job, "retry")));
  if (["completed", "failed", "cancelled"].includes(job.state)) actions.append(actionButton("Eliminar", () => jobAction(job, "delete")));
  if (job.files.length) {
    const files = node("div", "", "files");
    for (const file of job.files) {
      const size = file.size < 1048576 ? `${Math.ceil(file.size / 1024)} KB` : `${(file.size / 1048576).toFixed(1)} MB`;
      files.append(actionButton(`${file.name} · ${size}`, () => saveFile(job, file), "file"));
      if (telegramEnabled && file.size <= 100 * 1024 * 1024) {
        files.append(actionButton("Enviar a Telegram", async () => {
          await api("/api/telegram/send", {method:"POST", body:JSON.stringify({job_id:job.id, name:file.name})});
          el("message").textContent = "Archivo entregado al bot de Telegram.";
        }));
      }
      if (/\.(txt|md|srt|vtt)$/i.test(file.name) && file.size <= 2 * 1024 * 1024) {
        const saveNote = actionButton("Guardar en notas", async () => {
          const note = await (await api("/api/study/import", {method:"POST", body:JSON.stringify({job_id:job.id, name:file.name})})).json();
          selectTab("study");
          await loadStudy();
          await openStudyNote(note.id);
          el("message").textContent = "Texto guardado en tus notas.";
        });
        saveNote.setAttribute("aria-label", `Guardar en notas: ${file.name}`);
        files.append(saveNote);
      }
    }
    card.append(files);
  }
  card.append(actions);
  return card;
}

async function refresh() {
  if (!authenticated || mustChange || changingPassword || rendering) return;
  rendering = true;
  try {
    const jobs = await (await api("/api/jobs")).json();
    el("count").textContent = `${jobs.length} ${jobs.length === 1 ? "trabajo" : "trabajos"}`;
    const nodes = jobs.map(renderJob);
    el("jobs").replaceChildren(...(nodes.length ? nodes : [node("div", "Todavía no hay trabajos. Descarga un enlace o convierte un archivo.", "empty")]));
  } catch (error) { el("message").textContent = error.message; }
  finally { rendering = false; }
}

async function loadWorkspace() {
  if (!authenticated || mustChange) return;
  const capabilities = await (await api("/api/capabilities")).json();
  el("convert-submit").disabled = !capabilities.conversion;
  if (!capabilities.conversion) el("conversion-hint").textContent = "La conversión no está disponible: falta FFmpeg en el servidor.";
  el("allowed-hosts").textContent = capabilities.allowed_hosts.join(" · ");
  const installed = capabilities.models.filter(model => model.installed);
  el("transcribe-model").replaceChildren(...installed.map(model => {
    const option = node("option", model.id === "base-q5_1" ? "Base multilingüe" : "Small multilingüe");
    option.value = model.id;
    return option;
  }));
  el("transcribe-submit").disabled = !capabilities.transcription || !installed.length;
  if (el("transcribe-submit").disabled) el("transcription-hint").textContent = "El motor o el modelo de transcripción aún no están instalados en este servidor.";
  await loadStudy();
  await loadServices();
  await refresh();
}

el("login").onsubmit = async (event) => {
  event.preventDefault();
  const button = el("login").querySelector("button");
  button.disabled = true;
  try {
    const password = el("password").value;
    const status = await (await api("/api/auth/login", {method:"POST", body:JSON.stringify({password})})).json();
    el("current-password").value = status.must_change ? password : "";
    el("password").value = "";
    el("message").textContent = "";
    showAccount(status);
    await loadWorkspace();
  } catch (error) { el("message").textContent = error.message; }
  finally { button.disabled = false; }
};

async function logout() {
  await api("/api/auth/logout", {method:"POST", body:"{}"});
  changingPassword = false;
  el("password-form").reset();
  showAccount({authenticated:false, must_change:false});
  el("message").textContent = "";
}

el("logout").onclick = () => logout().catch(error => { el("message").textContent = error.message; });
el("change-password").onclick = () => {
  changingPassword = true;
  showAccount({authenticated, must_change:mustChange});
};
el("password-back").onclick = async () => {
  if (mustChange) return logout();
  changingPassword = false;
  el("password-form").reset();
  showAccount({authenticated, must_change:false});
  await refresh();
};
el("password-form").onsubmit = async (event) => {
  event.preventDefault();
  if (el("new-password").value !== el("confirm-password").value) {
    el("message").textContent = "Las contraseñas nuevas no coinciden.";
    return;
  }
  el("save-password").disabled = true;
  try {
    const status = await (await api("/api/auth/password", {method:"POST", body:JSON.stringify({
      current_password:el("current-password").value, new_password:el("new-password").value,
    })})).json();
    el("password-form").reset();
    changingPassword = false;
    showAccount(status);
    el("message").textContent = "Contraseña guardada. Ya puedes usar tu servidor.";
    await loadWorkspace();
  } catch (error) { el("message").textContent = error.message; }
  finally { el("save-password").disabled = false; }
};

function selectTab(name) {
    for (const item of sections) {
      el(`${item}-panel`).hidden = item !== name;
      el(`tab-${item}`).setAttribute("aria-pressed", String(item === name));
    }
    el("message").textContent = "";
}
for (const name of sections) el(`tab-${name}`).onclick = () => selectTab(name);

el("download").onsubmit = async (event) => {
  event.preventDefault();
  if (busy) return;
  busy = true;
  el("submit").disabled = true;
  try {
    await api("/api/jobs", {method:"POST", body:JSON.stringify({url:el("url").value.trim(), quality:Number(el("quality").value), audio_only:el("audio").checked})});
    el("url").value = "";
    el("message").textContent = "Descarga añadida a la cola.";
    await refresh();
  } catch (error) { el("message").textContent = error.message; }
  finally { busy = false; el("submit").disabled = false; }
};

el("convert").onsubmit = async (event) => {
  event.preventDefault();
  const file = el("media-file").files[0];
  if (!file || busy) return;
  if (file.size > 1024 ** 3) { el("message").textContent = "El archivo supera el límite de 1 GB."; return; }
  busy = true;
  el("convert-submit").disabled = true;
  try {
    el("message").textContent = "Subiendo el archivo al servidor…";
    const upload = await (await api(`/api/uploads?name=${encodeURIComponent(file.name)}`, {
      method:"POST", body:file, headers:{"Content-Type":"application/octet-stream"},
    })).json();
    await api("/api/jobs", {method:"POST", body:JSON.stringify({kind:"convert", upload_id:upload.upload_id, profile:el("profile").value})});
    el("media-file").value = "";
    el("message").textContent = "Conversión añadida a la cola.";
    await refresh();
  } catch (error) { el("message").textContent = error.message; }
  finally { busy = false; el("convert-submit").disabled = false; }
};

async function uploadToolJob(file, details, button) {
  if (!file || busy) return;
  if (file.size > 1024 ** 3) throw new Error("El archivo supera el límite de 1 GB.");
  busy = true;
  button.disabled = true;
  try {
    el("message").textContent = "Subiendo el archivo al servidor…";
    const upload = await (await api(`/api/uploads?name=${encodeURIComponent(file.name)}`, {
      method:"POST", body:file, headers:{"Content-Type":"application/octet-stream"},
    })).json();
    await api("/api/jobs", {method:"POST", body:JSON.stringify({...details, upload_id:upload.upload_id})});
    el("message").textContent = "Trabajo añadido a la cola.";
    await refresh();
  } finally { busy = false; button.disabled = false; }
}

el("transcribe").onsubmit = async (event) => {
  event.preventDefault();
  try {
    await uploadToolJob(el("transcribe-file").files[0], {kind:"transcribe", model:el("transcribe-model").value, language:el("transcribe-language").value}, el("transcribe-submit"));
    el("transcribe-file").value = "";
  } catch (error) { el("message").textContent = error.message; }
};
el("document").onsubmit = async (event) => {
  event.preventDefault();
  try {
    await uploadToolJob(el("document-file").files[0], {kind:"document", ocr:el("ocr-mode").value, ocr_language:el("ocr-language").value}, el("document-submit"));
    el("document-file").value = "";
  } catch (error) { el("message").textContent = error.message; }
};

window.addEventListener("DOMContentLoaded", async () => {
  try {
    showAccount(await (await api("/api/auth/status")).json());
    await loadWorkspace();
  } catch (error) { el("message").textContent = error.message; }
});
setInterval(refresh, 5000);
